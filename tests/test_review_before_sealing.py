"""Asking for more after seeing progress: a note that reaches the agent as it finishes reopens the analysis, and a run
approved with "review before sealing" pauses with its report so a person can ask for more, or finish it."""

import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from alchemist import run as run_module, steering
from alchemist.agent import ScriptedModel
from alchemist.api.app import create_app, services_factory_for
from alchemist.api.jobs import LocalLauncher, run_step
from alchemist.demo import script
from alchemist.run import ScriptedReviewer, analyse, ask, start_approval

ENGINEER = {"X-Alchemist-User": "eng_p1"}
REPORT_AGAIN = [script.ANALYSIS_TURNS[9], script.ANALYSIS_TURNS[10]]  # finish_analysis, then build_report


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


def planned(svc, project):
    return ask(svc, user="eng_p1", workspace="quality", project=project, question="Ask for more test",
               scope=script.SCOPE, model=ScriptedModel(script.PLAN_TURNS))["run_id"]


def steering_in(path):
    lines = [json.loads(x) for x in (run_module.as_path(path) / "logs" / "transcript.jsonl").read_text().splitlines()]
    return [n for x in lines for n in x.get("steering", [])]


def last_user_texts(path):
    """The steering texts the model read (appended to the user turn before a model call)."""
    cp = run_module.checkpoint_of(path)
    return [b["text"] for m in cp["messages"] if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "text"]


def tool_calls(path):
    lines = [json.loads(x) for x in (run_module.as_path(path) / "logs" / "transcript.jsonl").read_text().splitlines()]
    return [x["tool"] for x in lines if "tool" in x]


def test_a_note_that_arrives_as_the_agent_reports_reopens_the_analysis(svc):
    rid = planned(svc, "more-reopen")
    start_approval(svc, rid, user="eng_p1")
    inner = ScriptedModel(script.ANALYSIS_TURNS + REPORT_AGAIN)

    def create(**kw):
        if inner.i == 10:  # the agent is about to build its report: the person asks for more meanwhile
            steering.add(svc, rid, author="eng_p1", text="Also compare Line 1 over the same week.")
        return inner.create(**kw)

    out = analyse(svc, rid, user="eng_p1", model=SimpleNamespace(name="scripted", create=create),
                  reviewer=ScriptedReviewer(script.REVIEW))
    assert out["status"] == "completed" and out["seal_sha256"]
    assert [n["kind"] for n in steering_in(out["path"])] == ["note"]
    # It reported, read the note, then had the work reviewed and reported again.
    assert tool_calls(out["path"])[-3:] == ["build_report", "finish_analysis", "build_report"]
    rec = steering.record(svc, rid, out["path"])
    assert rec[0]["delivered"] and rec[0]["delivered"]["phase"] == "analysis"


def test_a_run_paused_for_review_continues_with_what_the_person_asks_then_finishes(client, svc):
    rid = planned(svc, "more-review")
    plan = start_approval(svc, rid, user="eng_p1", review_before_sealing=True)
    assert plan["review_before_sealing"] is True
    out = analyse(svc, rid, user="eng_p1", model=ScriptedModel(script.ANALYSIS_TURNS),
                  reviewer=ScriptedReviewer(script.REVIEW))
    run = run_module.as_path(out["path"])
    assert out["status"] == "paused" and out["analysis_ended"].startswith(run_module.REVIEW_PAUSE)
    assert run_module.paused_for_review(run) and not run_module.paused_at_time_limit(run)
    assert (run / "report" / "conclusion.md").exists() and not (run / "SEALED").exists()
    got = client.get(f"/api/runs/{rid}", headers=ENGINEER).json()
    assert got["review_pause"] and got["conclusion_html"] and not got["time_limit"]["reached"]

    # Resuming for review needs to say what more to do.
    r = client.post(f"/api/runs/{rid}:resume", headers=ENGINEER, json={})
    assert r.status_code == 409 and "what more" in r.text
    run_module.resume(svc, rid, user="eng_p1", note="Check whether the supplier change lines up with the drop.")
    assert [n["kind"] for n in steering.notes(svc, rid)] == ["follow_up"]

    inner = ScriptedModel([script.ANALYSIS_TURNS[8], *REPORT_AGAIN])
    out = analyse(svc, rid, user="eng_p1", model=SimpleNamespace(name="scripted", create=inner.create),
                  reviewer=ScriptedReviewer(script.REVIEW))
    assert out["status"] == "paused" and run_module.paused_for_review(run)  # it reports again, and waits again
    assert any("read your report and asked for more" in t for t in last_user_texts(run))
    assert tool_calls(run)[-3:] == ["run_python", "finish_analysis", "build_report"]

    # Finishing it seals the report as reviewed: complete, not partial.
    before = run_module.read_json(run / "logs" / "report_input.json")
    assert run_module.cancel(svc, rid, user="eng_p1") == "finishing"
    out = analyse(svc, rid, user="eng_p1", model=ScriptedModel([]), reviewer=ScriptedReviewer(script.REVIEW),
                  should_stop=lambda: "stop")
    assert out["status"] == "completed" and out["seal_sha256"]
    assert run_module.read_json(run / "logs" / "report_input.json") == before


def test_without_review_before_sealing_the_run_seals_as_before(svc):
    rid = planned(svc, "more-default")
    assert start_approval(svc, rid, user="eng_p1")["review_before_sealing"] is False
    out = analyse(svc, rid, user="eng_p1", model=ScriptedModel(script.ANALYSIS_TURNS),
                  reviewer=ScriptedReviewer(script.REVIEW))
    assert out["status"] == "completed" and out["seal_sha256"]
    with pytest.raises(ValueError, match="not paused"):
        run_module.resume(svc, rid, user="eng_p1", note="more")
