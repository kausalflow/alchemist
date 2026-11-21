"""Demo mode (PA-35): the paced offline script answers notes, cell comments, a plan sent back, asking for more at
review, an attached file and the suggested follow-up, all through the real run loop."""

import json

import pytest

from alchemist import steering
from alchemist.demo import script
from alchemist.demo.live import FOLLOW_UP_QUESTION, Conversation, DemoModel, DemoReviewer, _note_text
from alchemist.paths import as_path, read_json
from alchemist.run import analyse, ask, plan, resume, send_back, start_approval


@pytest.fixture(scope="module", autouse=True)
def resin_approved(svc):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")


def transcript(path, name="transcript.jsonl"):
    return [json.loads(x) for x in (as_path(path) / "logs" / name).read_text().splitlines()]


def tools(path):
    return [r["tool"] for r in transcript(path) if "tool" in r]


def errors(path):
    return [(r["tool"], r["output"][:300]) for r in transcript(path) if r.get("is_error") and r["tool"] != "run_query"]


def test_note_text_strips_the_framing():
    block = steering.for_model([{"author": "eng_p1", "at": "2026-11-21T10:00:00Z", "kind": "note",
                                 "text": "About cell c12: split this by crew please"}])
    assert _note_text(block) == "split this by crew please"
    back = steering.for_model([{"author": "eng_p1", "at": "t", "kind": "send_back", "text": "Add the night shift."}])
    assert _note_text(back) == "Add the night shift."


def test_conversation_reads_position_from_tool_ids():
    msgs = [{"role": "user", "content": "Question: x"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_d0_0", "name": "run_query", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_d0_0", "content": []},
                                         {"type": "text", "text": "Note from eng_p1 (t), who is following this run:\n"
                                                                  "check speed\nTake it into account from your next step."}]}]
    conv = Conversation(msgs)
    assert conv.played == {0} and [n["n"] for n in conv.pending()] == [0]


def test_demo_run_answers_send_back_notes_and_ask_for_more(svc):
    out = ask(svc, user="eng_p1", workspace="quality", project="demo-mode", question=script.QUESTION,
              scope=script.SCOPE, model=DemoModel("plan", seconds=0))
    rid = out["run_id"]
    assert out["status"] == "awaiting_approval"
    send_back(svc, rid, user="eng_p1", text="Please also check whether the night shift crew made a difference.")
    replanned = plan(svc, rid, model=DemoModel("plan", seconds=0))
    added = replanned["plan"]["hypotheses"][-1]
    assert added["id"] == "H6" and "night shift" in added["cause"], replanned["plan"]["hypotheses"]

    start_approval(svc, rid, user="eng_p1", review_before_sealing=True)
    calls = []

    def should_stop():
        calls.append(1)
        if len(calls) == 3:  # a note while it works, about a notebook cell
            steering.add(svc, rid, author="eng_p1", text="About cell c4: what about line speed?")
        return None

    res = analyse(svc, rid, user="eng_p1", model=DemoModel("analysis", seconds=0), reviewer=DemoReviewer(),
                  should_stop=should_stop)
    assert res["status"] == "paused", res  # ready for review
    log = transcript(res["path"])
    said = " ".join(b.get("text", "") for r in log if r.get("message", {}).get("role") == "assistant"
                    for b in r["message"]["content"] if b.get("type") == "text")
    assert "what about line speed?" in said and "H6" in said
    assert tools(res["path"]).count("build_report") == 1 and not errors(res["path"]), errors(res["path"])

    resume(svc, rid, user="eng_p1", note="Can you also look at the melt pressure?")
    res = analyse(svc, rid, user="eng_p1", model=DemoModel("analysis", seconds=0), reviewer=DemoReviewer(),
                  should_stop=lambda: None)
    assert res["status"] == "paused", res
    t = tools(res["path"])
    assert t.count("build_report") == 2 and t.count("finish_analysis") == 2, t
    report = read_json(as_path(res["path"]) / "logs" / "report_input.json")
    assert "melt pressure" in report["method_notes"] and "line speed" in report["method_notes"]
    assert len(read_json(as_path(res["path"]) / "findings.json")["findings"]) == 6


def test_demo_follow_up_plays_its_own_investigation(svc):
    first = ask(svc, user="eng_p1", workspace="quality", project="demo-follow", question=script.QUESTION,
                scope=script.SCOPE, model=DemoModel("plan", seconds=0))
    start_approval(svc, first["run_id"], user="eng_p1")
    done = analyse(svc, first["run_id"], user="eng_p1", model=DemoModel("analysis", seconds=0), reviewer=DemoReviewer())
    assert done["status"] == "completed", done
    fu = ask(svc, user="eng_p1", workspace="quality", project="demo-follow", question=FOLLOW_UP_QUESTION, scope={},
             follow_up_of=first["run_id"], model=DemoModel("plan", seconds=0))
    assert fu["plan"]["hypotheses"][0]["cause"].startswith("R-118 was used only")
    start_approval(svc, fu["run_id"], user="eng_p1")
    res = analyse(svc, fu["run_id"], user="eng_p1", model=DemoModel("analysis", seconds=0), reviewer=DemoReviewer())
    assert res["status"] == "completed" and not errors(res["path"]), errors(res["path"])
    found = read_json(as_path(res["path"]) / "findings.json")["findings"]
    assert [f["unit"] for f in found] == ["lots", "lot-equivalents"] and found[0]["value"] > 10
    review = read_json(as_path(res["path"]) / "logs" / "review.json")
    assert "equal output per lot" in json.dumps(review)
