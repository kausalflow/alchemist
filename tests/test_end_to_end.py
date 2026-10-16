import json
import os

import pandas as pd
import pytest

from alchemist.access import AccessDenied
from alchemist.agent import ScriptedModel
from alchemist.demo import script
from alchemist.paths import read_json
from alchemist.run import ScriptedReviewer, approve, ask, replay
from alchemist.workspace import verify


@pytest.fixture(scope="module")
def sealed_run(svc):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")
    out = ask(svc, user="eng_p1", workspace="quality", project="line2", question=script.QUESTION, scope=script.SCOPE,
              model=ScriptedModel(script.PLAN_TURNS))
    assert out["status"] == "awaiting_approval"
    res = approve(svc, out["run_id"], user="eng_p1", model=ScriptedModel(script.ANALYSIS_TURNS),
                  reviewer=ScriptedReviewer(script.REVIEW))
    return res


def test_run_completes_with_planted_cause(sealed_run):
    assert sealed_run["status"] == "completed"
    findings = {f["id"]: f for f in read_json(os.path.join(sealed_run["path"], "findings.json"))["findings"]}
    assert 4.0 < findings["F2"]["value"] < 5.5      # resin effect, planted at 4.9 points
    assert 1.5 < findings["F3"]["value"] < 2.5      # melt temperature, planted at 2 degC
    assert findings["F5"]["value"] == 0
    assert "R-118" in sealed_run["conclusion"]


def test_hidden_columns_never_reach_snapshots(sealed_run):
    queries = read_json(os.path.join(sealed_run["path"], "inputs", "queries.json"))
    lots = pd.read_parquet(os.path.join(sealed_run["path"], queries["q1"]["snapshot"]))
    assert "operator_name" not in lots.columns
    assert set(lots.crew.unique()).isdisjoint({"A", "B", "C"})   # pseudonymised
    assert all(o["etag"] for o in queries["q2"]["objects"])         # each source object recorded


def test_manifest_and_seal(sealed_run):
    m = read_json(os.path.join(sealed_run["path"], "manifest.json"))
    assert m["schema"] == "alchemist.manifest/v1" and set(m["result_hashes"]) == {"F1", "F2", "F3", "F4", "F5"}
    assert {c["dataset"] for c in m["catalog"]} >= {"quality_lots", "historian_line2"}
    # each role's model, the planning one carried over from the planning step
    assert {k: m["model"][k] for k in ("planning", "analysis", "reviewer")} == {
        "planning": "scripted", "analysis": "scripted", "reviewer": "scripted-reviewer"}
    assert verify(sealed_run["path"], sealed_run["seal_sha256"])["ok"]


def test_replay_matches(svc, sealed_run):
    rep = replay(svc, sealed_run["run_id"], user="eng_p1")
    assert rep["status"] == "completed", rep
    assert rep["result_check"]["status"] == {"mode": "replay", "compared": 5, "mismatches": 0}


def test_rerun_on_unchanged_data_reports_no_change(svc, sealed_run):
    rep = replay(svc, sealed_run["run_id"], user="eng_p1", mode="rerun")
    assert rep["status"] == "completed"
    assert rep["result_check"]["status"]["mismatches"] == 0


def test_other_plant_user_cannot_replay(svc, sealed_run):
    with pytest.raises(AccessDenied):
        replay(svc, sealed_run["run_id"], user="eng_p2")


def test_tampering_is_detected(svc, sealed_run, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(sealed_run["path"], copy)
    f = copy / "findings.json"
    os.chmod(f, 0o644)
    data = json.loads(f.read_text())
    data["findings"][0]["value"] = 99
    f.write_text(json.dumps(data))
    res = verify(copy, sealed_run["seal_sha256"])
    assert not res["ok"] and "changed: findings.json" in res["problems"]


def test_p1_user_cannot_query_p2_data(svc):
    from alchemist.run import RunContext  # noqa: F401  (plan step validates datasets against the run's plants)
    bad_plan = dict(script.PLAN, hypotheses=[dict(script.PLAN["hypotheses"][0], datasets=["historian_line5"])])
    out = ask(svc, user="eng_p1", workspace="quality", project="line2", question="probe", scope={"plants": ["P01"]},
              model=ScriptedModel([[("submit_plan", bad_plan)]]))
    assert out["status"] == "failed"
    with pytest.raises(AccessDenied):
        ask(svc, user="eng_p1", workspace="quality", project="line2", question="probe", scope={"plants": ["P02"]},
            model=ScriptedModel([]))
