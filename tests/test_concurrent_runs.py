"""Many people and runs at once: cancelling, run limits, races, steps that die, and throttled model calls."""

import json
from types import SimpleNamespace

import pytest

from alchemist.agent import ScriptedModel
from alchemist.api.jobs import LocalLauncher
from alchemist.demo import script
from alchemist.llm import is_retryable, with_retries
from alchemist.paths import as_path, read_yaml
from alchemist.run import RunLimit, cancel, create, plan, start_approval
from alchemist.sfn_launcher import StepFunctionsLauncher


@pytest.fixture(autouse=True)
def resin_approved(svc):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")


def new_run(svc, user="eng_p1"):
    return create(svc, user=user, workspace="quality", project="concurrency", question="Test run for concurrency",
                  scope=script.SCOPE if user == "eng_p1" else {})["run_id"]


def planned(svc, user="eng_p1"):
    rid = new_run(svc, user)
    assert plan(svc, rid, model=ScriptedModel(script.PLAN_TURNS))["status"] == "awaiting_approval"
    return rid


def question_status(svc, rid):
    return read_yaml(as_path(svc.catalog.run(rid)["path"]).parent.parent / "question.yaml")["status"]


def test_cancel_while_waiting_for_approval(svc):
    rid = planned(svc)
    assert cancel(svc, rid, user="eng_p1") == "cancelled"
    assert svc.catalog.run(rid)["status"] == "cancelled" and question_status(svc, rid) == "cancelled"
    with pytest.raises(ValueError):
        start_approval(svc, rid, user="eng_p1")
    with pytest.raises(ValueError):
        cancel(svc, rid, user="eng_p1")


def test_only_the_owner_or_an_admin_cancels(svc):
    from alchemist.access import AccessDenied

    rid = planned(svc)
    with pytest.raises(AccessDenied):
        cancel(svc, rid, user="eng_p2")
    cancel(svc, rid, user="eng_p1")


def test_cancel_while_planning_is_kept_when_the_plan_arrives(svc):
    rid = new_run(svc)
    assert cancel(svc, rid, user="eng_p1") == "cancelled"
    out = plan(svc, rid, model=ScriptedModel(script.PLAN_TURNS))  # a step that was already queued
    assert out["status"] == "cancelled" and svc.catalog.run(rid)["status"] == "cancelled"


def test_a_plan_is_approved_once(svc):
    rid = planned(svc)
    start_approval(svc, rid, user="eng_p1")
    with pytest.raises(ValueError, match="not awaiting approval"):
        start_approval(svc, rid, user="eng_p1")  # a second click, or a second tab
    svc.catalog.transition(rid, "running", "failed")


def test_active_runs_per_user_are_limited(svc):
    before = svc.settings.max_active_runs_per_user
    svc.settings.max_active_runs_per_user = 1
    try:
        first = new_run(svc, "eng_p2")
        with pytest.raises(RunLimit):
            new_run(svc, "eng_p2")
        new_run_other = new_run(svc, "eng_p1")  # other people are not held up
        cancel(svc, first, user="eng_p2")
        cancel(svc, new_run(svc, "eng_p2"), user="eng_p2")  # room again once one is cancelled
        cancel(svc, new_run_other, user="eng_p1")
    finally:
        svc.settings.max_active_runs_per_user = before


def test_total_active_runs_are_limited(svc):
    before = svc.settings.max_active_runs
    svc.settings.max_active_runs = len(svc.catalog.active_runs()) + 1
    try:
        rid = new_run(svc, "eng_p1")
        with pytest.raises(RunLimit):
            new_run(svc, "eng_p2")
        cancel(svc, rid, user="eng_p1")
    finally:
        svc.settings.max_active_runs = before


def test_local_launcher_closes_runs_left_by_a_restarted_server(svc):
    from alchemist.api.app import services_factory_for

    factory = services_factory_for(svc.settings, ttl=0)
    before, after = LocalLauncher(factory, workers=1), LocalLauncher(factory, workers=1)
    orphan, mine = new_run(svc), new_run(svc)
    for launcher, rid in ((before, orphan), (after, mine)):
        f = factory()
        f.catalog.audit("system", "run.launch", rid, {"launcher": "local", "instance": launcher.instance})
        f.catalog.db.close()
    after.reconcile()
    assert svc.catalog.run(orphan)["status"] == "failed"
    assert svc.catalog.run(mine)["status"] == "planning"
    cancel(svc, mine, user="eng_p1")
    for l in (before, after):
        l.shutdown()


class ExecutionDoesNotExist(Exception):
    pass


class FakeSfn:
    exceptions = SimpleNamespace(ExecutionDoesNotExist=ExecutionDoesNotExist, TaskTimedOut=Exception,
                                 InvalidToken=Exception)

    def __init__(self, statuses):
        self.statuses, self.stopped, self.started = statuses, [], []

    def start_execution(self, **kw):
        self.started.append(kw)

    def stop_execution(self, executionArn, cause):
        self.stopped.append(executionArn)

    def describe_execution(self, executionArn):
        name = executionArn.rsplit(":", 1)[1]
        if name not in self.statuses:
            raise ExecutionDoesNotExist(name)
        return {"status": self.statuses[name], "error": "States.Timeout", "cause": "task timed out"}


def test_step_functions_reconcile_closes_runs_whose_workflow_ended(svc):
    from alchemist.api.app import services_factory_for

    waiting, running, alive = planned(svc), planned(svc), planned(svc)
    start_approval(svc, running, user="eng_p1")
    sfn = FakeSfn({f"{waiting}-run": "TIMED_OUT", f"{running}-run": "FAILED", f"{alive}-run": "RUNNING"})
    l = StepFunctionsLauncher(services_factory_for(svc.settings, ttl=0), state_machine_arn=
                              "arn:aws:states:eu-central-1:123:stateMachine:alc-runs", approvals="s3://ws/_approvals/",
                              sfn=sfn, s3=object())
    for rid in (waiting, running, alive):
        l.plan(rid)
    l.reconcile()
    assert svc.catalog.run(waiting)["status"] == "expired"
    assert svc.catalog.run(running)["status"] == "failed"
    assert svc.catalog.run(alive)["status"] == "awaiting_approval"
    l.cancel(alive)
    assert sfn.stopped == [f"arn:aws:states:eu-central-1:123:execution:alc-runs:{alive}-run"]
    cancel(svc, alive, user="eng_p1")


def test_worker_marks_the_run_failed_when_its_step_crashes(svc, monkeypatch):
    import alchemist.run as run_mod
    from alchemist import cli

    rid = new_run(svc)
    monkeypatch.setattr(cli, "_svc", lambda: svc)
    monkeypatch.setattr(run_mod, "plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ECS task lost")))
    with pytest.raises(RuntimeError):
        cli.main(["worker", "plan", "--run-id", rid])
    assert svc.catalog.run(rid)["status"] == "failed"
    rows = svc.catalog.db.execute("select detail from audit_events where action='run.fail' and target=?", (rid,))
    assert "ECS task lost" in json.loads(rows.fetchall()[-1]["detail"])["error"]


class Throttled(Exception):
    status_code = 429


def test_throttled_model_calls_are_retried():
    calls, waits = [], []

    def call():
        calls.append(1)
        if len(calls) < 3:
            raise Throttled("ThrottlingException: Too many tokens")
        return "ok"

    assert with_retries(call, budget_seconds=600, sleep=waits.append) == "ok"
    assert len(calls) == 3 and len(waits) == 2


def test_other_model_errors_are_not_retried():
    assert not is_retryable(ValueError("bad request: prompt too long"))
    with pytest.raises(ValueError):
        with_retries(lambda: (_ for _ in ()).throw(ValueError("bad")), budget_seconds=600, sleep=lambda s: None)


def test_retries_stop_at_the_budget():
    def call():
        raise Throttled("throttled")

    with pytest.raises(Throttled):
        with_retries(call, budget_seconds=0, sleep=lambda s: None)


def test_step_functions_resume_starts_a_new_execution_and_reconcile_pauses_a_dead_one(svc):
    from alchemist.api.app import services_factory_for
    from alchemist.paths import write_json

    rid = planned(svc)
    start_approval(svc, rid, user="eng_p1")
    write_json(as_path(svc.catalog.run(rid)["path"]) / "logs" / "checkpoint.json", {"schema": "test"})
    sfn = FakeSfn({})
    l = StepFunctionsLauncher(services_factory_for(svc.settings, ttl=0), state_machine_arn=
                              "arn:aws:states:eu-central-1:123:stateMachine:alc-runs", approvals="s3://ws/_approvals/",
                              sfn=sfn, s3=object())
    l.resume(rid)
    (started,) = sfn.started
    assert json.loads(started["input"]) == {"run_id": rid, "mode": "resume"}
    assert started["name"].startswith(f"{rid}-resume-")
    sfn.statuses[started["name"]] = "TIMED_OUT"  # every attempt of the analysis timed out
    l.reconcile()
    assert svc.catalog.run(rid)["status"] == "paused"  # it has a checkpoint: resumable, not failed
    svc.catalog.transition(rid, "paused", "failed")



def test_demo_run_that_crashes_does_not_hold_a_run_slot(tmp_path, monkeypatch):
    import alchemist.run as run_mod
    from alchemist import cli

    for var in ("ALC_DB", "ALC_DB_HOST", "ALC_WORKSPACE", "ALC_ACCESS", "ALC_KNOWLEDGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ALC_HOME", str(tmp_path))  # its own home, so the shared test runs are untouched
    cli.main(["demo", "setup"])

    def approve_then_crash(svc, run_id, **kw):  # the analysis started, then its kernel failed
        svc.catalog.transition(run_id, "awaiting_approval", "running")
        raise RuntimeError("kernel died")

    monkeypatch.setattr(run_mod, "approve", approve_then_crash)
    with pytest.raises(RuntimeError):
        cli.main(["demo", "run"])
    (row,) = cli._svc().catalog.runs()
    assert row["status"] == "failed"


def test_demo_run_says_why_an_analysis_did_not_finish(tmp_path, monkeypatch):
    import alchemist.run as run_mod
    from alchemist import cli

    for var in ("ALC_DB", "ALC_DB_HOST", "ALC_WORKSPACE", "ALC_ACCESS", "ALC_KNOWLEDGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ALC_HOME", str(tmp_path))
    cli.main(["demo", "setup"])
    monkeypatch.setattr(run_mod, "approve", lambda svc, run_id, **kw: {
        "status": "partial", "analysis_ended": "error: OSError: disk full", "path": "the-run"})
    with pytest.raises(SystemExit, match="did not finish \\(partial\\): error: OSError: disk full"):
        cli.main(["demo", "run"])
