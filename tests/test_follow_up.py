"""Follow-up questions: a new question in the same project that builds on an earlier answer."""

import json

import pytest

from alchemist.access import AccessDenied
from alchemist.agent import ScriptedModel
from alchemist.demo import script
from alchemist.paths import as_path, read_json, read_yaml
from alchemist.run import ScriptedReviewer, approve, ask, follow_up_context

PLAN = {"metric": "Melt temperature by zone", "scope": {"plants": ["P01"]},
        "hypotheses": [{"id": "H1", "cause": "Zone 3 setpoint", "method": "Compare", "datasets": ["historian_line2"]}]}


@pytest.fixture(scope="module")
def answer(svc):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")
    out = ask(svc, user="eng_p1", workspace="quality", project="follow-ups", question=script.QUESTION,
              scope=script.SCOPE, model=ScriptedModel(script.PLAN_TURNS))
    res = approve(svc, out["run_id"], user="eng_p1", model=ScriptedModel(script.ANALYSIS_TURNS),
                  reviewer=ScriptedReviewer(script.REVIEW))
    assert res["status"] == "completed", res
    return res


def test_follow_up_carries_the_earlier_answer(svc, answer):
    out = ask(svc, user="eng_p1", workspace="quality", project="follow-ups",
              question="Would raising the zone 3 setpoint have kept R-118 lots in spec?", scope={},
              follow_up_of=answer["run_id"], model=ScriptedModel([[("submit_plan", PLAN)]]))
    assert out["status"] == "awaiting_approval", out
    rdir = as_path(out["path"])
    q = read_yaml(rdir.parent.parent / "question.yaml")
    fu = q["follow_up_of"]
    assert fu["run_id"] == answer["run_id"] and fu["question"] == script.QUESTION
    assert "R-118" in fu["conclusion"] and {f["id"] for f in fu["findings"]} >= {"F1", "F2"}
    # The earlier scope (line, period, plants) carries over.
    assert q["scope"]["plants"] == ["P01"] and q["scope"].get("period") == script.SCOPE.get("period")
    first = json.loads((rdir / "logs" / "plan_transcript.jsonl").read_text().splitlines()[0])
    text = json.dumps(first)
    assert "earlier_conclusion" in text and "R-118" in text


def test_follow_up_needs_an_answer_in_the_same_project(svc, answer):
    with pytest.raises(ValueError, match="same project"):
        ask(svc, user="eng_p1", workspace="quality", project="elsewhere", question="And then what happened?",
            scope={}, follow_up_of=answer["run_id"], model=ScriptedModel([]))
    with pytest.raises(ValueError, match="no run"):
        ask(svc, user="eng_p1", workspace="quality", project="follow-ups", question="And then what happened?",
            scope={}, follow_up_of="0" * 26, model=ScriptedModel([]))
    pending = ask(svc, user="eng_p1", workspace="quality", project="follow-ups", question="Not finished yet?",
                  scope={}, model=ScriptedModel([[("submit_plan", PLAN)]]))
    with pytest.raises(ValueError, match="conclusion"):
        ask(svc, user="eng_p1", workspace="quality", project="follow-ups", question="Based on the pending one?",
            scope={}, follow_up_of=pending["run_id"], model=ScriptedModel([]))
    # Sealing does not matter: an answer with a conclusion (one waiting for review, say) can be followed up.
    report = as_path(pending["path"]) / "report"
    report.mkdir(exist_ok=True)
    (report / "conclusion.md").write_text("Yield fell on R-118 lots.")
    ctx = follow_up_context(svc, svc.access.user("eng_p1"), "quality", "follow-ups", pending["run_id"])
    assert ctx["seal_sha256"] is None and ctx["conclusion"] == "Yield fell on R-118 lots."


def test_follow_up_needs_access_to_the_earlier_answer(svc, answer):
    with pytest.raises(AccessDenied):
        ask(svc, user="eng_p2", workspace="quality", project="follow-ups", question="Can I build on it?",
            scope={}, follow_up_of=answer["run_id"], model=ScriptedModel([]))


def test_follow_up_is_in_the_manifest_after_sealing(svc, answer):
    out = ask(svc, user="eng_p1", workspace="quality", project="follow-ups", question="Was it really R-118 again?",
              scope={}, follow_up_of=answer["run_id"], model=ScriptedModel(script.PLAN_TURNS))
    res = approve(svc, out["run_id"], user="eng_p1", model=ScriptedModel(script.ANALYSIS_TURNS),
                  reviewer=ScriptedReviewer(script.REVIEW))
    m = read_json(as_path(res["path"]) / "manifest.json")
    assert m["question"]["follow_up_of"] == {"run_id": answer["run_id"], "seal_sha256": answer["seal_sha256"]}
