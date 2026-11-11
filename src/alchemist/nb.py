"""Helpers imported inside the sandbox kernel as `alc`.

Every notebook a run produces calls these functions, and the same notebook runs
in three modes:

- live:    alc.query loads the content-hashed snapshot the query step stored
- replay:  alc.query reads the stored snapshot, so results must match exactly
- rerun:   alc.query loads the result the query step got on current data, and reports how it differs

The kernel runs code the model wrote, so it never reads datasets itself: each query runs in the query step outside
the kernel (querystep.py), which applies the dataset card (hidden and pseudonymised columns) and writes the result.

alc.finding records each claim with the number that supports it; in replay the
number is checked against the original run.
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from penguineer.agent import findings as agent_findings
from penguineer.agent.tools import HIDDEN as PENGUINEER_HIDDEN

from .paths import as_path, copy_tree, read_json, write_json
from .plugins import get_registry

RESULT_MARKER = "ALC_RESULT "

_state: dict = {}


def init(run_dir: str, run_mode: str = "live", seed: int = 1729) -> None:
    """Prepare the kernel: seeds and the run folder.

    The kernel never reads datasets: queries run in the query step outside it (querystep.py), which writes each
    result to the run folder; alc.query loads it from there."""
    random.seed(seed)
    np.random.seed(seed)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    run = as_path(run_dir)
    tmp = Path(tempfile.mkdtemp(prefix="alc-"))
    _state.update(run=run, mode=run_mode, seed=seed, tmp=tmp, findings={}, rng=np.random.default_rng(seed))

    parent = run / "inputs" / "parent_findings.json"
    _state["expected"] = {f["id"]: f for f in read_json(parent)["findings"]} if parent.exists() else {}
    findings_file = run / "findings.json"
    if findings_file.exists() and run_mode == "live":
        _state["findings"] = {f["id"]: f for f in read_json(findings_file)["findings"]}


def _queries() -> dict:
    """The run's queries as the query step recorded them (inputs/queries.json)."""
    if "run" not in _state:
        raise RuntimeError("call alc.init(...) first")
    f = _state["run"] / "inputs" / "queries.json"
    return read_json(f) if f.exists() else {}


def _summary(df: pd.DataFrame, max_rows: int = 20) -> dict:
    desc = {}
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_bool_dtype(s):
            desc[col] = {"distinct": int(s.nunique()), "nulls": int(s.isna().sum())}
        elif pd.api.types.is_numeric_dtype(s):
            desc[col] = {"min": _num(s.min()), "mean": _num(s.mean()), "max": _num(s.max()), "nulls": int(s.isna().sum())}
        elif pd.api.types.is_datetime64_any_dtype(s):
            desc[col] = {"min": _iso(s.min()), "max": _iso(s.max()), "nulls": int(s.isna().sum())}
        else:
            desc[col] = {"distinct": int(s.nunique()), "nulls": int(s.isna().sum())}
    return {"rows": int(len(df)), "columns": {c: str(t) for c, t in df.dtypes.items()}, "stats": desc,
            "head": json.loads(df.head(max_rows).to_json(orient="records", date_format="iso")),
            "spark": _sparks(df)}


def _iso(x):
    return None if pd.isna(x) else pd.Timestamp(x).isoformat()


def _sparks(df: pd.DataFrame, points: int = 48, columns: int = 6) -> dict:
    """Numeric columns in row order, averaged into `points` buckets: a preview for people following the run (the
    model is not shown it). NaN buckets stay None, so gaps show."""
    import numpy as np

    out = {}
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    for col in num[:columns]:
        v = df[col].to_numpy(dtype=float)
        if not len(v):
            continue
        edges = np.linspace(0, len(v), min(points, len(v)) + 1).astype(int)
        out[col] = [None if np.isnan(m) else round(float(m), 4)
                    for m in (np.nanmean(v[a:b]) if np.isfinite(v[a:b]).any() else np.nan
                              for a, b in zip(edges[:-1], edges[1:]))]
    return out


def _num(x):
    try:
        return None if pd.isna(x) else round(float(x), 6)
    except (TypeError, ValueError):
        return str(x)


def query(qid: str, sql: str | None = None) -> pd.DataFrame:
    """Load the result of one recorded query: the snapshot the query step wrote (live, replay), or the one it wrote
    on current data (rerun), with how it differs from the original.

    The SQL in the call documents the query; it must be the SQL that was run. Code in the kernel cannot run new SQL
    on datasets: the run_query tool does that, outside the kernel."""
    run, mode = _state["run"], _state["mode"]
    rec = _queries().get(qid)
    if rec is None:
        raise KeyError(f"query {qid} has no result in this run; datasets are queried with the run_query tool, "
                       "and code in the kernel can only load those results (alc.query('q1'))")
    if sql is not None and " ".join(sql.split()) != " ".join(rec["sql"].split()):
        raise ValueError(f"query {qid} was run with other SQL; call alc.query({qid!r}) to load its result")
    if mode == "replay":
        df = pd.read_parquet(_local(run / rec["snapshot"]))
        print(RESULT_MARKER + json.dumps({"query": qid, "mode": "replay", "snapshot": rec["snapshot"], "rows": len(df)}))
        return df
    if mode == "rerun":
        again = rec.get("rerun")
        if again is None:
            raise KeyError(f"query {qid} has not been re-run on current data")
        df = pd.read_parquet(_local(run / again["snapshot"]))
        print(RESULT_MARKER + json.dumps(again["report"]))
        return df
    df = pd.read_parquet(_local(run / rec["snapshot"]))
    print(RESULT_MARKER + json.dumps({"query": qid, "snapshot": rec["snapshot"], **_summary(df)}, default=str))
    return df


def _local(path) -> Path:
    if isinstance(path, Path):
        return path
    return Path(path.fspath)  # cloudpathlib downloads to its local cache


def finding(fid: str, *, statement: str, value=None, unit: str = "", verdict: str = "supported",
            confidence: str = "medium", hypothesis: str = "", evidence: list | None = None) -> dict:
    """Record one claim and the number computed for it in this cell."""
    rec = agent_findings.finding(fid, statement=statement, value=value, unit=unit, verdict=verdict,
                                 confidence=confidence, hypothesis=hypothesis, evidence=evidence)
    digest = rec["value_sha256"]
    exp = _state["expected"].get(fid)
    if exp is not None:
        rec["matches_original"] = exp["value_sha256"] == digest
    _state["findings"][fid] = rec
    write_json(_state["run"] / "findings.json", {"schema": "alchemist.findings/v1", "findings": list(_state["findings"].values())})
    print(RESULT_MARKER + json.dumps(rec, default=str))
    return rec


def resume(run_mode: str) -> None:
    """After a paused run's cells were re-run in replay mode: continue in `run_mode`, and make findings.json hold
    exactly the findings of the cells that were re-run (one recorded after the last checkpoint is dropped)."""
    _state["mode"] = run_mode
    write_json(_state["run"] / "findings.json", {"schema": "alchemist.findings/v1", "findings": list(_state["findings"].values())})


def check_results() -> pd.DataFrame:
    """Last cell of every notebook: compare finding values with the original run."""
    rows = []
    for fid, rec in _state["findings"].items():
        exp = _state["expected"].get(fid)
        rows.append({"finding": fid, "value": rec["value"],
                     "original": None if exp is None else exp["value"],
                     "match": None if exp is None else exp["value_sha256"] == rec["value_sha256"]})
    missing = [fid for fid in _state["expected"] if fid not in _state["findings"]]
    for fid in missing:
        rows.append({"finding": fid, "value": None, "original": _state["expected"][fid]["value"], "match": False})
    df = pd.DataFrame(rows)
    status = {"mode": _state["mode"], "compared": int(df["match"].notna().sum()) if len(df) else 0,
              "mismatches": int((df["match"] == False).sum()) if len(df) else 0}  # noqa: E712
    write_json(_state["run"] / "work" / "result_check.json", {"status": status, "rows": rows})
    print(RESULT_MARKER + json.dumps({"result_check": {"status": status, "rows": rows}}, default=str))
    return df


def penguineer(call_id: str, command: str, params: dict | None = None) -> dict:
    """Run a penguineer command on this run's snapshots and return its result as a dict.

    File inputs are a query id (`"q2"`: that query's snapshot), an earlier call id (`"p3"`: the table that call
    wrote, such as align's), or `"knowledge:<name>"` (structured knowledge, such as a plant graph, written out as
    YAML); nothing outside the run folder can be read. The call runs in its own folder with no LLM, and its outputs
    are copied to work/penguineer/<call_id>/ in the run, so they are sealed with it and a replay re-runs the call on
    the same snapshots.
    """
    import shutil

    import penguineer as pg
    from penguineer.core import default_registry
    from penguineer.core.fields import field_shape, is_path_type

    params = dict(params or {})
    hidden = sorted(PENGUINEER_HIDDEN & set(params))
    if hidden:
        raise ValueError(f"penguineer {command}: {hidden} are set by Alchemist, leave them out")
    run, folder = _state["run"], f"work/penguineer/{call_id}"
    work = _state["tmp"] / "penguineer" / call_id
    if work.exists():
        shutil.rmtree(work)
    project, staged = work / "project", work / "inputs"
    staged.mkdir(parents=True)

    fields = default_registry.get(command).params.model_fields
    shown = {str(project): folder}  # local paths -> what the result says instead
    for name, value in params.items():
        if name in fields and value is not None and is_path_type(field_shape(fields[name]).base):
            local, label = _penguineer_input(value, staged)
            params[name], shown[str(local)] = str(local), label

    try:
        out = pg.execute(command, params, project=project, seed=_state["seed"], cache_dir=work / "cache")
    except pg.PenguineerError as e:
        raise RuntimeError(f"penguineer {command}: {e}") from None

    # Each command writes under <project>/<kind>/<penguineer run id>/; drop the run id level, so the call's
    # folder and the paths in its result are the same in every replay.
    run_id = out.record.run_id
    text = out.result.model_dump_json()
    summary = out.result.summary()
    for kind in [d for d in project.iterdir() if (d / run_id).is_dir()]:
        for f in (kind / run_id).iterdir():
            f.rename(kind / f.name)
        (kind / run_id).rmdir()
        text = text.replace(f"{kind}/{run_id}", str(kind))
        summary = summary.replace(f"{kind}/{run_id}", str(kind))
    for local, name in shown.items():
        text, summary = text.replace(local, name), summary.replace(local, name)
    copy_tree(project, run / folder)
    result = json.loads(text)
    _state.setdefault("calls", {})[call_id] = result
    print(summary)
    print(RESULT_MARKER + json.dumps({"penguineer": call_id, "command": command, "folder": folder,
                                      "exit_code": out.result.exit_code(), "summary": summary,
                                      "result": result}, default=str))
    return result


def _penguineer_input(value: str, staged: Path) -> tuple[Path, str]:
    """The local file for a penguineer file input, and how the result should name it."""
    if value.startswith("knowledge:"):
        name = value.removeprefix("knowledge:")
        path = staged / f"{name}.yaml"
        path.write_text(yaml.safe_dump(knowledge(name), sort_keys=False))
        return path, value
    queries = _queries()
    if value in queries:
        snapshot = queries[value]["snapshot"]
        return _local(_state["run"] / snapshot), snapshot
    table = _state.get("calls", {}).get(value, {}).get("table")
    if table:
        return _local(_state["run"] / table), table
    raise ValueError(f"{value!r} is not a query of this run, an earlier call with a table, or knowledge:<name>; "
                     f"penguineer reads only the run's query snapshots ({sorted(queries)}), tables of "
                     f"earlier calls ({sorted(_state.get('calls', {}))}) and its structured knowledge")


class _Methods:
    """Analysis methods from plugins: alc.methods.<name>(...). alc.methods.list() shows what is available."""

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return get_registry().analysis_methods.get(name)

    def __dir__(self):
        return get_registry().analysis_methods.names()

    def list(self) -> dict[str, str]:
        return get_registry().method_docs()


methods = _Methods()


def knowledge(name: str):
    """Structured knowledge (spec limits, residence times) shipped into the run's inputs."""
    path = _state["run"] / "inputs" / "knowledge" / f"{name}.json"
    return read_json(path)


def _debug() -> dict:  # pragma: no cover - handy in an interactive kernel
    return dict(_state)


sys.modules.setdefault("alc", sys.modules[__name__])
