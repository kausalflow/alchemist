"""Steering a run: notes people leave while it works, and sending a plan back with a note."""

import json
from pathlib import Path

import pytest

from alchemist import steering
from alchemist.access import AccessDenied
from alchemist.agent import ScriptedModel
from alchemist.api.jobs import stop_requested
from alchemist.demo import script
from alchemist.paths import read_json
from alchemist.run import (ScriptedReviewer, analyse, ask, note, pause, plan, resume, send_back, start_approval)

NOTE = "The melt-flow sensor drifted in March; treat its March readings with care."


@pytest.fixture(scope="module", autouse=True)
def resin_approved(svc):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")


class Recording(ScriptedModel):
    """The scripted model, keeping the conversation it was shown at each call."""

    def __init__(self, turns, on_call=None, die_at=None):
        super().__init__(turns)
        self.seen, self.on_call, self.die_at = [], on_call, die_at

    def create(self, *, system, tools, messages):
        if self.die_at is not None and self.i == self.die_at:
            raise KeyboardInterrupt("task killed")  # nothing in the step catches it
        self.seen.append(json.dumps(messages))
        return super().create(system=system, tools=tools, messages=messages)


def asked(svc, project="steer"):
    return ask(svc, user="eng_p1", workspace="quality", project=project, question="Steering test",
               scope=script.SCOPE, model=ScriptedModel(script.PLAN_TURNS))["run_id"]


def analysis(svc, rid, model=None, before_call=None):
    calls = []

    def should_stop():
        calls.append(1)
        if before_call:
            before_call(len(calls))
        return stop_requested(svc, rid)

    model = model or Recording(script.ANALYSIS_TURNS)
    out = analyse(svc, rid, user="eng_p1", model=model, reviewer=ScriptedReviewer(script.REVIEW), should_stop=should_stop)
    return out, model


def hashes(path):
    return {f["id"]: f["value_sha256"] for f in read_json(Path(path) / "findings.json")["findings"]}


@pytest.fixture(scope="module")
def reference(svc):
    rid = asked(svc, "steer-ref")
    start_approval(svc, rid, user="eng_p1")
    return hashes(analysis(svc, rid)[0]["path"])


def test_a_note_reaches_the_agent_at_its_next_step_and_stays_in_the_record(svc, reference):
    rid = asked(svc)
    start_approval(svc, rid, user="eng_p1")
    out, model = analysis(svc, rid, before_call=lambda n: n == 3 and note(svc, rid, user="eng_p1", text=NOTE))
    run = Path(out["path"])
    assert out["status"] == "completed" and hashes(run) == reference
    assert [NOTE in s for s in model.seen[:4]] == [False, False, True, True]  # from the third call on, once
    assert sum(s.count(NOTE) for s in model.seen[-1:]) == 1

    delivered = read_json(run / steering.FILE)["delivered"]
    assert [(d["phase"], d["model_call"], d["author"], d["text"]) for d in delivered] == [("analysis", 3, "eng_p1", NOTE)]
    lines = [json.loads(x) for x in (run / "logs" / "transcript.jsonl").read_text().splitlines()]
    assert [x["steering"][0]["text"] for x in lines if "steering" in x] == [NOTE]
    m = read_json(run / "manifest.json")
    assert m["steering"][0]["text"] == NOTE and m["steering"][0]["delivered"]["model_call"] == 3
    assert "drifted in March" in (run / "report" / "report.html").read_text()


def test_a_note_left_while_paused_or_awaiting_approval_is_given_when_work_continues(svc):
    rid = asked(svc)
    note(svc, rid, user="eng_p1", text="Compare against all of 2024 Q4 as well.")
    start_approval(svc, rid, user="eng_p1")
    out, model = analysis(svc, rid, before_call=lambda n: n == 4 and pause(svc, rid, user="eng_p1"))
    assert out["status"] == "paused" and "2024 Q4" in model.seen[0]  # with the approved plan
    note(svc, rid, user="eng_p1", text=NOTE)
    resume(svc, rid, user="eng_p1")
    model2 = Recording(script.ANALYSIS_TURNS)
    out, model2 = analysis(svc, rid, model=model2)
    assert out["status"] == "completed"
    assert NOTE in model2.seen[0] and model2.seen[-1].count(NOTE) == 1 and model2.seen[-1].count("2024 Q4") == 1
    record = steering.record(svc, rid, out["path"])
    assert [(n["n"], n["delivered"]["model_call"]) for n in record] == [(1, 1), (2, 4)]


def test_a_note_given_in_a_turn_that_never_finished_is_given_again(svc):
    rid = asked(svc)
    start_approval(svc, rid, user="eng_p1")
    dying = Recording(script.ANALYSIS_TURNS, die_at=3)
    with pytest.raises(KeyboardInterrupt):
        analysis(svc, rid, model=dying, before_call=lambda n: n == 4 and note(svc, rid, user="eng_p1", text=NOTE))
    run = Path(svc.catalog.run(rid)["path"])
    assert read_json(run / steering.FILE)["delivered"]  # handed over, but the turn was lost with the task
    out, model = analysis(svc, rid)  # what the workflow's retry does: resume from the checkpoint
    assert out["status"] == "completed" and NOTE in model.seen[0] and model.seen[-1].count(NOTE) == 1
    assert len(read_json(run / steering.FILE)["delivered"]) == 1


def test_who_can_steer_and_when(svc):
    rid = asked(svc)
    with pytest.raises(AccessDenied):
        note(svc, rid, user="eng_p2", text="not mine to steer")
    with pytest.raises(ValueError):
        note(svc, rid, user="eng_p1", text="   ")
    start_approval(svc, rid, user="eng_p1")
    assert analysis(svc, rid)[0]["status"] == "completed"
    with pytest.raises(ValueError, match="still working"):
        note(svc, rid, user="eng_p1", text="too late")


def test_sending_the_plan_back_with_a_note_gives_a_revised_plan(svc):
    rid = asked(svc)
    with pytest.raises(AccessDenied):
        send_back(svc, rid, user="eng_p2", text="add the dryer")
    send_back(svc, rid, user="eng_p1", text="Add the resin dryer as a candidate cause.")
    row = svc.catalog.run(rid)
    assert row["status"] == "planning"
    with pytest.raises(ValueError, match="not awaiting approval"):
        send_back(svc, rid, user="eng_p1", text="again")

    model = Recording(script.PLAN_TURNS)
    out = plan(svc, rid, model=model)  # what the launcher's replan runs
    run = Path(out["path"])
    assert out["status"] == "awaiting_approval"
    assert out["plan"]["version"] == 2 and out["plan"]["revises"] == 1
    assert "resin dryer" in model.seen[0] and "sent the plan back" in model.seen[0]
    assert "Steering test" in model.seen[0]  # the earlier planning conversation continues
    assert [p["version"] for p in read_json(run / "logs" / "plan_history.json")] == [1]
    assert any(json.loads(x).get("resumed") for x in (run / "logs" / "plan_transcript.jsonl").read_text().splitlines())
    [n] = steering.record(svc, rid, run)
    assert n["kind"] == "send_back" and n["delivered"]["phase"] == "planning"

    start_approval(svc, rid, user="eng_p1")
    out, model = analysis(svc, rid)
    assert out["status"] == "completed" and "resin dryer" not in model.seen[0]  # already acted on in planning
