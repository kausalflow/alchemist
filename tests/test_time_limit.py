"""How long a run may work: the default, an admin's choice, a person's choice at approval, and the stop."""

import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from alchemist import run as run_module
from alchemist.agent import ScriptedModel
from alchemist.demo import script
from alchemist.run import RunBudget, ScriptedReviewer, TimeLimitError, analyse, ask, start_approval

from alchemist.api.app import create_app, services_factory_for
from alchemist.api.jobs import LocalLauncher, run_step

ADMIN = {"X-Alchemist-User": "data_admin"}
ENGINEER = {"X-Alchemist-User": "eng_p1"}


class InlineLauncher(LocalLauncher):
    def _submit(self, run_id, step, user=None):
        run_step(self.services, run_id, step, user)


@pytest.fixture(scope="module")
def client(svc):
    os.environ["ALC_API_MODEL"] = "scripted"
    factory = services_factory_for(svc.settings)
    app = create_app(svc.settings, launcher=InlineLauncher(factory), services_factory=factory, web_dist="/nonexistent")
    with TestClient(app) as c:
        yield c
    os.environ.pop("ALC_API_MODEL", None)


@pytest.fixture(autouse=True)
def resin_approved(svc):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")


def planned(svc, project="time-limit"):
    return ask(svc, user="eng_p1", workspace="quality", project=project, question="Time limit test",
               scope=script.SCOPE, model=ScriptedModel(script.PLAN_TURNS))["run_id"]


def test_the_time_limit_counts_working_time_from_before_a_pause():
    b = RunBudget(60, 400_000, max_seconds=600)
    assert b.exceeded() is None
    b.seconds_before = 599.9
    b.started -= 1
    assert b.exceeded() == "time limit of 10 minutes used"
    assert b.as_dict()["seconds"] >= 600
    b.steps = 60
    assert "step budget" in b.exceeded()  # the other budgets are checked first, as before


class Clock:
    """Working time that moves only when the model is called: `per_call` seconds a call."""

    def __init__(self, monkeypatch, per_call=60):
        self.now, self.per_call = 0.0, per_call
        monkeypatch.setattr(run_module, "time", SimpleNamespace(monotonic=lambda: self.now))

    def model(self, inner):
        clock = self

        class Timed:
            name = inner.name

            def create(self, **kw):
                clock.now += clock.per_call
                return inner.create(**kw)

            if hasattr(inner, "advance"):
                def advance(self, n):
                    inner.advance(n)
        return Timed()


def transcript_steering(path):
    lines = [json.loads(x) for x in (run_module.as_path(path) / "logs" / "transcript.jsonl").read_text().splitlines()]
    return [n for x in lines for n in x.get("steering", [])]


def test_near_the_limit_the_agent_concludes_and_the_run_pauses_with_a_preliminary_report(svc, monkeypatch):
    rid = planned(svc, "time-limit-wrap-up")
    start_approval(svc, rid, user="eng_p1", time_limit_minutes=11)
    clock = Clock(monkeypatch)
    out = analyse(svc, rid, user="eng_p1", model=clock.model(ScriptedModel(script.ANALYSIS_TURNS)),
                  reviewer=ScriptedReviewer(script.REVIEW))
    # Each model call takes a minute; the last tenth of the limit is for wrapping up, so the agent is told before its
    # 11th call, and builds its report in it.
    assert out["status"] == "paused" and out["analysis_ended"] == "paused: time limit of 11 minutes reached"
    assert [n["kind"] for n in transcript_steering(out["path"])] == ["time_limit"]
    run = run_module.as_path(out["path"])
    assert run_module.paused_at_time_limit(run)
    inp = run_module.read_json(run / "logs" / "report_input.json")
    assert inp["title"] == script.ANALYSIS_TURNS[10][0][1]["title"]  # the agent's own conclusion, marked preliminary
    assert inp["limitations"][0].startswith("Preliminary: the run reached its time limit of 11 minutes")
    assert (run / "report" / "conclusion.md").exists() and not (run / "SEALED").exists()

    # More time: the same run continues from where it stopped, the agent is told, and its report replaces the
    # preliminary one.
    run_module.resume(svc, rid, user="eng_p1", more_minutes=5)
    plan = run_module.read_yaml(run.parent.parent / "question.yaml")["plan"]
    assert plan["time_limit_minutes"] == 11 + 5  # 11 minutes used, then 5 more
    finish = ScriptedModel([script.ANALYSIS_TURNS[9], script.ANALYSIS_TURNS[10]])
    out = analyse(svc, rid, user="eng_p1", model=clock.model(SimpleNamespace(name="scripted", create=finish.create)),
                  reviewer=ScriptedReviewer(script.REVIEW))
    assert out["status"] == "completed" and out["seal_sha256"]
    assert [n["kind"] for n in transcript_steering(out["path"])] == ["time_limit", "more_time"]
    assert not any(x.startswith("Preliminary:")
                   for x in run_module.read_json(run / "logs" / "report_input.json")["limitations"])
    assert run_module.read_json(run / "manifest.json")["usage"]["seconds"] == 13 * 60


def test_a_run_that_runs_out_of_time_mid_analysis_pauses_then_can_be_finished(svc, monkeypatch):
    rid = planned(svc, "time-limit-hard")
    start_approval(svc, rid, user="eng_p1", time_limit_minutes=5)
    clock = Clock(monkeypatch)
    out = analyse(svc, rid, user="eng_p1", model=clock.model(ScriptedModel(script.ANALYSIS_TURNS)),
                  reviewer=ScriptedReviewer(script.REVIEW))
    assert out["status"] == "paused" and out["analysis_ended"] == "paused: time limit of 5 minutes reached"
    run = run_module.as_path(out["path"])
    inp = run_module.read_json(run / "logs" / "report_input.json")
    assert inp["title"].startswith("Preliminary result") and inp["limitations"][0].startswith("Preliminary:")

    # Finishing it (stop) seals the report as it stands, no longer preliminary.
    assert run_module.cancel(svc, rid, user="eng_p1") == "finishing"
    out = analyse(svc, rid, user="eng_p1", model=clock.model(ScriptedModel(script.ANALYSIS_TURNS)),
                  reviewer=ScriptedReviewer(script.REVIEW), should_stop=lambda: "stop")
    assert out["status"] == "partial" and out["seal_sha256"]
    limitations = run_module.read_json(run / "logs" / "report_input.json")["limitations"]
    assert not any(x.startswith("Preliminary:") for x in limitations)
    assert any("finished without more time" in x for x in limitations)


def test_more_time_is_capped_and_defaults_for_a_run_paused_at_its_limit(client, svc, monkeypatch):
    rid = planned(svc, "time-limit-more")
    start_approval(svc, rid, user="eng_p1", time_limit_minutes=5)
    clock = Clock(monkeypatch)
    analyse(svc, rid, user="eng_p1", model=clock.model(ScriptedModel(script.ANALYSIS_TURNS)),
            reviewer=ScriptedReviewer(script.REVIEW))
    with pytest.raises(TimeLimitError, match="between 1 and 480"):
        run_module.resume(svc, rid, user="eng_p1", more_minutes=500)
    assert svc.catalog.run(rid)["status"] == "paused"
    r = client.post(f"/api/runs/{rid}:resume", headers=ENGINEER, json={"more_minutes": 500})
    assert r.status_code == 422 and "between 1 and 480" in r.text
    got = client.get(f"/api/runs/{rid}", headers=ENGINEER).json()
    assert got["time_limit"]["reached"] and got["permissions"]["resume"] and got["conclusion_md"]
    run_module.resume(svc, rid, user="eng_p1")
    run = run_module.as_path(svc.catalog.run(rid)["path"])
    plan = run_module.read_yaml(run.parent.parent / "question.yaml")["plan"]
    assert plan["time_limit_minutes"] == 5 + svc.settings.max_run_minutes
    svc.catalog.transition(rid, "running", "failed")


def test_a_person_chooses_a_limit_up_to_the_cap(svc):
    rid = planned(svc, "time-limit-choice")
    with pytest.raises(TimeLimitError, match="between 1 and 480"):
        start_approval(svc, rid, user="eng_p1", time_limit_minutes=481)
    assert svc.catalog.run(rid)["status"] == "awaiting_approval"  # nothing changed
    plan = start_approval(svc, rid, user="eng_p1", time_limit_minutes=30)
    assert plan["time_limit_minutes"] == 30 and run_module.time_limit(svc, plan) == 30
    svc.catalog.transition(rid, "running", "failed")
    rid = planned(svc, "time-limit-default")
    assert start_approval(svc, rid, user="eng_p1")["time_limit_minutes"] == svc.settings.max_run_minutes
    svc.catalog.transition(rid, "running", "failed")


def test_admins_set_the_default_and_the_cap(client, svc):
    from alchemist.run import Services

    assert client.put("/api/admin/limits", headers=ENGINEER, json={"limits": {"max_run_minutes": 30}}).status_code == 403
    got = client.get("/api/admin/limits", headers=ADMIN).json()["limits"]
    assert {x["name"]: x["value"] for x in got} == {"max_run_minutes": 120, "max_run_minutes_cap": 480,
                                                     "max_steps": 60, "max_output_tokens_total": 400_000}
    try:
        r = client.put("/api/admin/limits", headers=ADMIN, json={"limits": {"max_run_minutes": 45, "max_run_minutes_cap": 90}})
        assert r.status_code == 200, r.text
        row = next(x for x in r.json()["limits"] if x["name"] == "max_run_minutes")
        assert row["value"] == 45 and row["source"] == "admin" and row["updated_by"] == "data_admin"
        budgets = client.get("/api/meta", headers=ENGINEER).json()["budgets"]
        assert budgets["max_run_minutes"] == 45 and budgets["max_run_minutes_cap"] == 90

        # Runs (and workers) read them from the database; a choice over the cap is refused.
        s = Services(client.app.state.settings)
        try:
            assert (s.settings.max_run_minutes, s.settings.max_run_minutes_cap) == (45, 90)
            with pytest.raises(TimeLimitError, match="between 1 and 90"):
                run_module.check_time_limit(s, 120)
        finally:
            s.catalog.db.close()

        bad = client.put("/api/admin/limits", headers=ADMIN, json={"limits": {"max_run_minutes": 120}})
        assert bad.status_code == 422 and "longer than the longest" in bad.text
        assert client.put("/api/admin/limits", headers=ADMIN, json={"limits": {"max_steps": 0}}).status_code == 422
        assert client.put("/api/admin/limits", headers=ADMIN, json={"limits": {"speed": 1}}).status_code == 422
    finally:
        r = client.put("/api/admin/limits", headers=ADMIN, json={"limits": {"max_run_minutes": None, "max_run_minutes_cap": None}})
    assert all(x["source"] == "configuration" for x in r.json()["limits"])


def test_the_run_page_shows_the_limit_and_the_time_used(client, svc):
    rid = planned(svc, "time-limit-api")
    r = client.post(f"/api/runs/{rid}/plan:approve", headers=ENGINEER, json={"time_limit_minutes": 9999})
    assert r.status_code == 422 and "between 1 and 480" in r.text
    r = client.post(f"/api/runs/{rid}/plan:approve", headers=ENGINEER, json={"time_limit_minutes": 20})
    assert r.status_code == 200 and r.json()["plan"]["time_limit_minutes"] == 20
    run = client.get(f"/api/runs/{rid}", headers=ENGINEER).json()
    assert run["run"]["status"] == "completed"
    assert run["time_limit"]["minutes"] == 20 and run["time_limit"]["used_seconds"] is not None
