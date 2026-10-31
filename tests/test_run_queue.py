"""The run queue: the API queues steps, workers take them, and steps whose worker died are queued again."""

import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from alchemist import runqueue
from alchemist.api.app import create_app, launcher_for, services_factory_for
from alchemist.api.jobs import LocalLauncher
from alchemist.demo import script
from alchemist.run import cancel, create
from alchemist.runqueue import QueueLauncher, Worker


@pytest.fixture(autouse=True)
def empty_queue(svc, monkeypatch):
    monkeypatch.setattr(svc.settings, "max_active_runs_per_user", 0)
    monkeypatch.setattr(svc.settings, "max_active_runs", 0)
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")
    svc.catalog.db.execute("delete from run_jobs")
    svc.catalog.db.commit()
    os.environ["ALC_API_MODEL"] = "scripted"
    yield
    os.environ.pop("ALC_API_MODEL", None)


@pytest.fixture()
def factory(svc):
    return services_factory_for(svc.settings, ttl=0)


@pytest.fixture()
def client(svc, factory):
    app = create_app(svc.settings, launcher=QueueLauncher(factory, embedded=0), services_factory=factory,
                     web_dist="/nonexistent")
    with TestClient(app) as c:
        yield c


H = {"X-Alchemist-User": "eng_p1"}


def jobs(svc, run_id):
    return [dict(r) for r in svc.catalog.db.execute("select * from run_jobs where run_id = ? order by id", (run_id,))]


def ask(client):
    r = client.post("/api/questions", headers=H, json={"workspace": "quality", "project": "queue",
                                                       "question": script.QUESTION, "scope": script.SCOPE})
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def test_steps_wait_in_the_queue_until_a_worker_takes_them(svc, client, factory):
    rid = ask(client)
    run = client.get(f"/api/runs/{rid}", headers=H).json()
    assert run["run"]["status"] == "planning"
    assert run["queue"] == {"step": "plan", "attempt": 1, "state": "queued", "ahead": 0}

    # A note sent while the step waits reaches the agent once a worker runs it.
    assert client.post(f"/api/runs/{rid}/notes", headers=H, json={"text": "Look at line 2 first"}).status_code < 300

    Worker(factory, name="w1").run(drain=True)
    run = client.get(f"/api/runs/{rid}", headers=H).json()
    assert run["run"]["status"] == "awaiting_approval" and run["queue"] is None
    assert [(j["step"], j["status"], j["worker"]) for j in jobs(svc, rid)] == [("plan", "done", "w1")]

    assert client.post(f"/api/runs/{rid}/plan:approve", headers=H, json={}).status_code == 200
    assert client.get(f"/api/runs/{rid}", headers=H).json()["queue"]["step"] == "analyse"
    Worker(factory, name="w2").run(drain=True)
    assert svc.catalog.run(rid)["status"] == "completed"
    assert [(j["step"], j["status"]) for j in jobs(svc, rid)] == [("plan", "done"), ("analyse", "done")]


def test_many_workers_take_each_step_once(svc, factory):
    rids = [create(svc, user="eng_p1", workspace="quality", project="queue-many", question=script.QUESTION,
                   scope=script.SCOPE)["run_id"] for _ in range(4)]
    for rid in rids:
        runqueue.enqueue(svc, rid, "plan")
    workers = [Worker(factory, concurrency=2, poll=0.05, name=f"w{i}") for i in range(2)]
    threads = [threading.Thread(target=w.run, kwargs={"drain": True}) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    for rid in rids:
        assert [j["status"] for j in jobs(svc, rid)] == ["done"]
        assert svc.catalog.run(rid)["status"] == "awaiting_approval"
        cancel(svc, rid, user="eng_p1")


def test_a_run_has_one_step_running_at_a_time(svc):
    rid = create(svc, user="eng_p1", workspace="quality", project="queue-one", question="One at a time",
                 scope=script.SCOPE)["run_id"]
    first = runqueue.enqueue(svc, rid, "plan")
    runqueue.enqueue(svc, rid, "plan")
    assert runqueue.claim(svc, "a")["id"] == first
    assert runqueue.claim(svc, "b") is None  # the second waits until the first is finished
    runqueue.finish(svc, first)
    assert runqueue.claim(svc, "b")["id"] != first
    cancel(svc, rid, user="eng_p1")


def test_a_step_whose_worker_died_is_queued_again_then_given_up(svc):
    rid = create(svc, user="eng_p1", workspace="quality", project="queue-lost", question="Lost worker",
                 scope=script.SCOPE)["run_id"]
    runqueue.enqueue(svc, rid, "plan")
    for attempt in (1, 2):
        job = runqueue.claim(svc, f"dead-{attempt}")
        assert job["attempt"] == attempt
        assert runqueue.recover_lost(svc, lease=60, max_retries=1) == []  # still has a fresh heartbeat
        svc.catalog.db.execute("update run_jobs set heartbeat_at = ? where id = ?", (time.time() - 61, job["id"]))
        svc.catalog.db.commit()
        out = runqueue.recover_lost(svc, lease=60, max_retries=1)
        assert [o["outcome"] for o in out] == (["requeued"] if attempt == 1 else ["failed"])
    assert [(j["status"], j["attempt"]) for j in jobs(svc, rid)] == [("lost", 1), ("lost", 2)]
    assert svc.catalog.run(rid)["status"] == "failed"


def test_stopping_a_queued_run_drops_its_step(svc, client):
    rid = ask(client)
    r = client.post(f"/api/runs/{rid}:stop", headers=H)
    assert r.status_code == 200 and r.json()["result"] == "cancelled"
    assert [j["status"] for j in jobs(svc, rid)] == ["cancelled"]
    assert runqueue.claim(svc, "w") is None


def test_queue_depth_goes_to_cloudwatch_when_asked(svc, monkeypatch):
    rid = create(svc, user="eng_p1", workspace="quality", project="queue-depth", question="Depth",
                 scope=script.SCOPE)["run_id"]
    runqueue.enqueue(svc, rid, "plan")
    runqueue.enqueue(svc, rid, "plan")
    runqueue.claim(svc, "w")
    d = runqueue.depth(svc)
    assert d == {"queued": 1, "running": 1}

    class FakeCloudWatch:
        sent = []

        def put_metric_data(self, **kw):
            self.sent.append(kw)

    cw = FakeCloudWatch()
    runqueue.publish_depth(d, cloudwatch=cw)
    assert cw.sent == []  # off unless a namespace is set
    monkeypatch.setenv("ALC_QUEUE_METRICS_NAMESPACE", "Alchemist")
    runqueue.publish_depth(d, cloudwatch=cw)
    assert {m["MetricName"]: m["Value"] for m in cw.sent[0]["MetricData"]} == \
        {"QueuedSteps": 1, "RunningSteps": 1, "Backlog": 2}
    cancel(svc, rid, user="eng_p1")


def test_embedded_workers_run_steps_inside_the_api(svc, factory):
    launcher = QueueLauncher(factory, embedded=1)
    launcher.worker.poll = 0.05
    try:
        rid = create(svc, user="eng_p1", workspace="quality", project="queue-embedded", question=script.QUESTION,
                     scope=script.SCOPE)["run_id"]
        launcher.plan(rid)
        deadline = time.time() + 60
        while svc.catalog.run(rid)["status"] == "planning" and time.time() < deadline:
            time.sleep(0.1)
        assert svc.catalog.run(rid)["status"] == "awaiting_approval"
    finally:
        launcher.shutdown()
    cancel(svc, rid, user="eng_p1")


def test_launcher_names(factory):
    assert isinstance(launcher_for(None, factory), LocalLauncher)
    assert isinstance(launcher_for("queue", factory), QueueLauncher)
    assert isinstance(launcher_for("alchemist.runqueue:QueueLauncher", factory), QueueLauncher)
