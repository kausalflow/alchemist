"""penguineer's commands as analysis tools, run on a run folder's snapshots.

The data in tests/data/penguineer_offspec is penguineer's own examples/offspec: a feed preheater fault that sends
lot L0803-A off spec.
"""

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from alchemist import nb, penguineer_tools, prompts

DATA = Path(__file__).parent / "data" / "penguineer_offspec"


@pytest.fixture()
def run_dir(tmp_path):
    """A run folder with the example's exports as query snapshots and its graph as structured knowledge."""
    run = tmp_path / "run"
    queries = {}
    for qid, name in (("q1", "historian"), ("q2", "lims"), ("q3", "lots")):
        snapshot = f"inputs/{name}.parquet"
        (run / "inputs").mkdir(parents=True, exist_ok=True)
        pd.read_csv(DATA / f"{name}.csv").to_parquet(run / snapshot, index=False)
        queries[qid] = {"sql": f"select * from ds.{name}", "snapshot": snapshot}
    (run / "inputs" / "queries.json").write_text(json.dumps(queries))
    (run / "inputs" / "knowledge").mkdir()
    graph = yaml.safe_load((DATA / "graph.yaml").read_text())
    (run / "inputs" / "knowledge" / "plant_graph.json").write_text(json.dumps(graph))
    nb.init(str(run), run_mode="live", seed=1729)
    return run


INVESTIGATE = {"lot": "L0803-A", "data": "q1", "lims": "q2", "lots": "q3", "graph": "knowledge:plant_graph"}


def test_investigate_on_snapshots(run_dir, capsys):
    result = nb.penguineer("p1", "investigate", INVESTIGATE)
    top = result["hypotheses"][0]
    assert top["cause_tag"] == "TI-101.PV" and top["confidence"] == "high"
    # outputs are in the run folder, and the result points there, without penguineer's own run id
    assert result["report_json"] == "work/penguineer/p1/reports/report.json"
    assert (run_dir / result["report_json"]).exists() and (run_dir / result["report_html"]).exists()
    assert (run_dir / "work" / "penguineer" / "p1" / "runs" / "run.json").exists()
    assert str(run_dir) not in json.dumps(result)  # inputs are named by snapshot, not by local path
    out = capsys.readouterr().out
    marker = json.loads(next(line for line in out.splitlines() if line.startswith(nb.RESULT_MARKER))
                        .removeprefix(nb.RESULT_MARKER))
    assert marker["penguineer"] == "p1" and marker["folder"] == "work/penguineer/p1"
    assert "TI-101.PV" in marker["summary"] and str(run_dir) not in marker["summary"]


def test_same_call_gives_the_same_result(run_dir):
    """A replay re-runs the cell; the values findings are taken from must not change."""
    first = nb.penguineer("p1", "investigate", INVESTIGATE)
    again = nb.penguineer("p1", "investigate", INVESTIGATE)
    assert first == again


def test_data_health_and_lag(run_dir):
    health = nb.penguineer("p1", "data health", {"data": "q1", "graph": "knowledge:plant_graph"})
    assert any(f["tag"] == "PI-103.PV" and f["check"] == "flatline" for f in health["findings"])
    lag = nb.penguineer("p2", "lag", {"data": "q1", "cause": "TI-101.PV", "effect": "AI-201.PV"})
    assert lag["n"] > 0


def test_align_then_compare_on_its_table(run_dir):
    """A later call reads an earlier call's table by its call id."""
    aligned = nb.penguineer("p1", "align", {"data": "q1", "lots": "q3", "graph": "knowledge:plant_graph",
                                            "sample_point": "SP-1", "tags": ["TI-101.PV"]})
    assert aligned["table"] == "work/penguineer/p1/runs/aligned.parquet" and aligned["n_lots"] == 9
    assert (run_dir / aligned["table"]).exists()
    rows = pd.DataFrame(aligned["rows"])
    cmp = nb.penguineer("p2", "compare", {"data": "p1", "value": "TI-101.PV", "group": "lot", "a": "L0803-B"})
    assert cmp["data"] == aligned["table"] and cmp["n_a"] == 1
    assert cmp["mean_a"] == pytest.approx(rows[rows.lot == "L0803-B"]["TI-101.PV"].iloc[0])


def test_only_run_inputs_and_no_llm(run_dir):
    with pytest.raises(ValueError, match="not a query of this run"):
        nb.penguineer("p1", "data health", {"data": "/etc/passwd"})
    with pytest.raises(ValueError, match="earlier call"):
        nb.penguineer("p1", "data health", {"data": "p9"})
    with pytest.raises(ValueError, match="set by Alchemist"):
        nb.penguineer("p1", "investigate", {**INVESTIGATE, "llm": "api"})


def test_tool_definitions():
    tools = penguineer_tools.tools()
    assert set(tools) == set(penguineer_tools.COMMANDS)
    assert set(penguineer_tools.COMMANDS) <= set(prompts.ANALYSIS_TOOLS) and set(tools) <= set(prompts.TOOLS)
    inv = tools["penguineer_investigate"]["input_schema"]
    assert {"llm", "llm_model", "llm_dry_run", "out"}.isdisjoint(inv["properties"])
    assert inv["properties"]["data"]["type"] == "string" and "query id" in inv["properties"]["data"]["description"]
    assert set(inv["required"]) == {"lot", "lims", "data"}
    assert penguineer_tools.cell("p3", "penguineer_lag", {"cause": "a"}) == \
        "pg_p3 = alc.penguineer('p3', 'lag', {'cause': 'a'})"
