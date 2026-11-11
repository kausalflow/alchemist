"""PA-25: the query step applies the dataset card outside the kernel and writes only the filtered result."""

import hashlib
import json
import os

import pandas as pd
import pytest

from alchemist.paths import read_json
from alchemist.querystep import QueryFailed, QueryRunner, execute, pseudonym, view_sql

KEY = "test-pseudonym-key"
COLUMNS = [{"name": "lot", "exposure": "model"}, {"name": "operator", "exposure": "pseudonymise"},
           {"name": "price_eur", "exposure": "never_to_model"}, {"name": "yield_pct", "exposure": "model"}]


@pytest.fixture()
def lake(tmp_path):
    (tmp_path / "lake").mkdir()
    pd.DataFrame({"lot": ["L1", "L2", "L3"], "operator": ["Kim", "Ana", None], "price_eur": [10.5, 11.0, 12.5],
                  "yield_pct": [97.0, 91.5, 95.0]}).to_parquet(tmp_path / "lake" / "lots.parquet")
    run = tmp_path / "runs" / "R1"
    (run / "inputs").mkdir(parents=True)
    views = {"lots": {"glob": f"{tmp_path}/lake/*.parquet", "format": "parquet", "card_version": 3, "columns": COLUMNS}}
    return run, views


def test_pseudonyms_need_the_key():
    p = pseudonym(KEY, "Kim")
    assert len(p) == 32 and p != pseudonym("other-key", "Kim") and p == pseudonym(KEY, "Kim")
    # Not the old md5(key || value), which anyone holding the key in the kernel could recompute.
    assert p != hashlib.md5((KEY + "Kim").encode()).hexdigest()


def test_view_selects_only_visible_columns():
    sql = view_sql("lots", {"columns": COLUMNS}, "read_parquet(['x'])")
    assert '"price_eur"' not in sql and 'alc_pseudonym("operator"::varchar) as "operator"' in sql and KEY not in sql
    with pytest.raises(QueryFailed, match="no columns"):
        view_sql("x", {"columns": [{"name": "a", "exposure": "never_to_model"}]}, "t")


def test_live_query_writes_a_filtered_snapshot_and_its_record(lake):
    run, views = lake
    out = execute({"run_dir": str(run), "views": views, "qid": "q1", "sql": "select * from ds.lots order by lot",
                   "mode": "live", "key": KEY})
    assert out["rows"] == 3 and out["datasets"] == ["lots"]
    df = pd.read_parquet(run / out["snapshot"])
    assert list(df.columns) == ["lot", "operator", "yield_pct"]
    assert df.operator.tolist()[:2] == [pseudonym(KEY, "Kim"), pseudonym(KEY, "Ana")] and df.operator.isna()[2]
    rec = read_json(run / "inputs" / "queries.json")["q1"]
    assert rec["snapshot"] == out["snapshot"] and rec["card_versions"] == {"lots": 3} and rec["objects"][0]["etag"]
    # Hidden values never reach the run folder.
    for f in run.rglob("*"):
        if f.is_file():
            assert b"10.5" not in f.read_bytes() and b"Kim" not in f.read_bytes(), f


def test_a_hidden_column_cannot_be_selected_and_the_hash_cannot_be_called(lake):
    run, views = lake
    req = {"run_dir": str(run), "views": views, "mode": "live", "key": KEY}
    with pytest.raises(QueryFailed, match="price_eur"):
        execute({**req, "qid": "q1", "sql": "select price_eur from ds.lots"})
    for sql in ("select alc_pseudonym('Kim') as guess", "select * from read_parquet('/etc/passwd')",
                "select getenv('ALC_PSEUDONYM_KEY')"):
        with pytest.raises(QueryFailed, match="not allowed"):
            execute({**req, "qid": "q1", "sql": sql})


def test_rerun_records_the_new_result_next_to_the_original(lake):
    run, views = lake
    req = {"run_dir": str(run), "views": views, "key": KEY}
    first = execute({**req, "qid": "q1", "sql": "select avg(yield_pct) as y from ds.lots", "mode": "live"})
    again = execute({**req, "qid": "q1", "sql": None, "mode": "rerun"})
    assert again["report"] == {"query": "q1", "mode": "rerun", "rows_then": 1, "rows_now": 1, "result_changed": False,
                               "objects_changed_or_new": 0}
    rec = read_json(run / "inputs" / "queries.json")["q1"]
    assert rec["snapshot"] == first["snapshot"] and rec["rerun"]["snapshot"] == again["snapshot"]


def test_the_step_runs_in_its_own_process_with_the_key_off_its_environment(lake, monkeypatch):
    run, views = lake
    runner = QueryRunner({"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join(__import__("sys").path)},
                         key=KEY, read_paths=[str(run.parent.parent / "lake")])
    out = runner.run(run_dir=str(run), views=views, qid="q1", sql="select count(*) as n from ds.lots")
    assert out["rows"] == 1
    with pytest.raises(QueryFailed, match="not allowed"):
        runner.run(run_dir=str(run), views=views, qid="q2", sql="select * from read_csv('/etc/passwd')")
    assert json.dumps(runner.env).count(KEY) == 0
