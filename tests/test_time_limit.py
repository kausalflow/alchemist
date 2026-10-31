"""How long a run may work: the default, an admin's choice, a person's choice at approval, and the stop."""

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


def test_a_run_over_its_time_limit_stops_with_a_partial_report(svc, monkeypatch):
    rid = planned(svc)
    start_approval(svc, rid, user="eng_p1", time_limit_minutes=1)
    clock = iter(range(0, 10_000, 15))  # each look at the clock is 15 s later
    monkeypatch.setattr(run_module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    out = analyse(svc, rid, user="eng_p1", model=ScriptedModel(script.ANALYSIS_TURNS),
                  reviewer=ScriptedReviewer(script.REVIEW))
    assert out["status"] == "partial"
    assert out["analysis_ended"] == "budget: time limit of 1 minutes used"
    manifest = run_module.read_json(run_module.as_path(out["path"]) / "manifest.json")
    assert manifest["usage"]["seconds"] >= 60


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
