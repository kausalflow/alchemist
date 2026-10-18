"""Helpers imported inside the sandbox kernel as `alc`.

Every notebook a run produces calls these functions, and the same notebook runs
in three modes:

- live:    alc.query runs SQL on the lake and stores a content-hashed snapshot
- replay:  alc.query reads the stored snapshot, so results must match exactly
- rerun:   alc.query runs the SQL on current data and reports how it differs

alc.finding records each claim with the number that supports it; in replay the
number is checked against the original run.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import tempfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

from .paths import as_path, copy_tree, expand_glob, object_info, put_file, read_json, write_json
from .plugins import get_registry
from .sqlguard import check_sql

RESULT_MARKER = "ALC_RESULT "

_state: dict = {}


def init(run_dir: str, run_mode: str = "live", seed: int = 1729) -> None:
    """Prepare the kernel: seeds, the run folder and a locked-down DuckDB connection."""
    random.seed(seed)
    np.random.seed(seed)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    run = as_path(run_dir)
    tmp = Path(tempfile.mkdtemp(prefix="alc-"))
    _state.update(run=run, mode=run_mode, seed=seed, tmp=tmp, findings={}, rng=np.random.default_rng(seed))

    views_file = run / "inputs" / "views.json"
    views = read_json(views_file) if views_file.exists() else {}
    _state["views"] = views
    queries_file = run / "inputs" / "queries.json"
    _state["queries"] = read_json(queries_file) if queries_file.exists() else {}
    parent = run / "inputs" / "parent_findings.json"
    _state["expected"] = {f["id"]: f for f in read_json(parent)["findings"]} if parent.exists() else {}
    findings_file = run / "findings.json"
    if findings_file.exists() and run_mode == "live":
        _state["findings"] = {f["id"]: f for f in read_json(findings_file)["findings"]}

    con = duckdb.connect()
    con.execute("set threads = 1")
    backends = get_registry().storage_backends
    for scheme in sorted({_scheme(v["glob"]) for v in views.values()}):
        backends.get(scheme).setup(con)
    roots = sorted({_root(v["glob"]) for v in views.values()} | {str(tmp)})
    con.execute("set allowed_directories = " + "[" + ", ".join(f"'{r}'" for r in roots) + "]")
    con.execute("set enable_external_access = false")
    con.execute("set lock_configuration = true")
    con.execute("create schema if not exists ds")
    _state["con"] = con


def _scheme(uri: str) -> str:
    return uri.split("://", 1)[0] if "://" in uri else "file"


def _root(glob: str) -> str:
    parts = glob.split("/")
    for i, p in enumerate(parts):
        if any(c in p for c in "*?["):
            return "/".join(parts[:i])
    return "/".join(parts[:-1])


def _con() -> duckdb.DuckDBPyConnection:
    if "con" not in _state:
        raise RuntimeError("call alc.init(...) first")
    return _state["con"]


def _pin_views(datasets: list[str]) -> list[dict]:
    """Expand each dataset's glob once and point its view at that explicit file list."""
    con, objects = _con(), []
    key = os.environ.get("ALC_PSEUDONYM_KEY", "dev-only-key").replace("'", "")
    for name in datasets:
        spec = _state["views"][name]
        files = expand_glob(spec["glob"])
        if not files:
            raise FileNotFoundError(f"no files for dataset {name}")
        objects += [dict(object_info(f), dataset=name) for f in files]
        scan = get_registry().data_formats.get(spec["format"]).scan(files)
        cols = []
        for c in spec["columns"]:
            if c["exposure"] == "never_to_model":
                continue
            q = f'"{c["name"]}"'
            cols.append(f"md5('{key}' || {q}::varchar) as {q}" if c["exposure"] == "pseudonymise" else q)
        con.execute(f"create or replace view ds.{name} as select {', '.join(cols)} from {scan}")
    return objects


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
    """Run (live), read back (replay) or re-run and compare (rerun) one recorded query."""
    run, mode = _state["run"], _state["mode"]
    rec = _state["queries"].get(qid)
    if mode == "replay":
        if rec is None:
            raise KeyError(f"query {qid} has no snapshot in this run's inputs")
        df = pd.read_parquet(_local(run / rec["snapshot"]))
        print(RESULT_MARKER + json.dumps({"query": qid, "mode": "replay", "snapshot": rec["snapshot"], "rows": len(df)}))
        return df

    sql = sql if sql is not None else (rec or {}).get("sql")
    if sql is None:
        raise ValueError("live queries need SQL")
    datasets = check_sql(sql, set(_state["views"]))
    objects = _pin_views(datasets)
    tmp_file = _state["tmp"] / f"{qid}.parquet"
    _con().execute(f"copy ({sql}) to '{tmp_file}' (format parquet)")
    df = pd.read_parquet(tmp_file)
    digest = _frame_hash(df)

    if mode == "rerun" and rec is not None:
        changed = digest != rec.get("result_sha256")
        drift = [o for o in objects if o["key"] not in {x["key"]: x for x in rec["objects"]}
                 or {x["key"]: x for x in rec["objects"]}[o["key"]]["etag"] != o["etag"]]
        report = {"query": qid, "mode": "rerun", "rows_then": rec["rows"], "rows_now": len(df),
                  "result_changed": changed, "objects_changed_or_new": len(drift)}
        print(RESULT_MARKER + json.dumps(report))
        return df

    snapshot = f"inputs/{digest}.parquet"
    put_file(tmp_file, run / snapshot)
    _state["queries"][qid] = {"sql": sql, "datasets": datasets, "objects": objects, "rows": int(len(df)),
                              "snapshot": snapshot, "result_sha256": digest,
                              "card_versions": {d: _state["views"][d]["card_version"] for d in datasets}}
    write_json(run / "inputs" / "queries.json", _state["queries"])
    print(RESULT_MARKER + json.dumps({"query": qid, "snapshot": snapshot, **_summary(df)}, default=str))
    return df


def _frame_hash(df: pd.DataFrame) -> str:
    """Content hash of a result, independent of Parquet writer details."""
    h = hashlib.sha256()
    h.update(json.dumps([str(c) for c in df.columns]).encode())
    h.update(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    return h.hexdigest()


def _local(path) -> Path:
    if isinstance(path, Path):
        return path
    return Path(path.fspath)  # cloudpathlib downloads to its local cache


def _canon(value) -> str:
    if isinstance(value, (np.floating, float)):
        return repr(float(f"{float(value):.12g}"))
    if isinstance(value, (np.integer, int)):
        return str(int(value))
    if isinstance(value, dict):
        return json.dumps({k: _canon(v) for k, v in sorted(value.items())})
    if isinstance(value, (list, tuple)):
        return json.dumps([_canon(v) for v in value])
    return json.dumps(value, default=str)


def finding(fid: str, *, statement: str, value=None, unit: str = "", verdict: str = "supported",
            confidence: str = "medium", hypothesis: str = "", evidence: list | None = None) -> dict:
    """Record one claim and the number computed for it in this cell."""
    if verdict not in ("supported", "refuted", "inconclusive"):
        raise ValueError("verdict must be supported, refuted or inconclusive")
    if confidence not in ("high", "medium", "low"):
        raise ValueError("confidence must be high, medium or low")
    if isinstance(value, (np.floating, np.integer)):
        value = value.item()
    if isinstance(value, float):
        value = float(f"{value:.12g}") + 0.0  # + 0.0 turns -0.0 into 0.0
    digest = hashlib.sha256(_canon(value).encode()).hexdigest()
    rec = {"id": fid, "hypothesis": hypothesis, "statement": statement, "value": value, "unit": unit,
           "verdict": verdict, "confidence": confidence, "evidence": evidence or [], "value_sha256": digest}
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


def align(lots: pd.DataFrame, process: pd.DataFrame, *, start: str, end: str, ts: str,
          residence_min: float, values: list[str], by: list[str] | None = None) -> pd.DataFrame:
    """Average process values over the window that produced each lot.

    Material leaving the line at time t was processed residence_min earlier, so each
    lot's [start, end] window is shifted back by the residence time before joining.
    """
    con = duckdb.connect()
    con.register("lots", lots)
    con.register("proc", process)
    on = " and ".join([f"l.{b} = p.{b}" for b in (by or [])] + [
        f"p.{ts} >= l.{start} - interval '{residence_min} minutes'",
        f"p.{ts} < l.{end} - interval '{residence_min} minutes'"])
    aggs = ", ".join(f"avg(p.{v}) as {v}" for v in values)
    keys = ", ".join(f"l.{c}" for c in lots.columns)
    return con.sql(f"select {keys}, {aggs}, count(p.{ts}) as process_rows from lots l left join proc p on {on} "
                   f"group by all order by l.{start}").df()


def effect(a, b) -> dict:
    """Difference in means with Welch's t-test and Cohen's d."""
    from scipy import stats

    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    t = stats.ttest_ind(a, b, equal_var=False)
    pooled = np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
    return {"mean_a": float(a.mean()), "mean_b": float(b.mean()), "diff": float(a.mean() - b.mean()),
            "p_value": float(t.pvalue), "cohens_d": float((a.mean() - b.mean()) / pooled) if pooled else None,
            "n_a": int(len(a)), "n_b": int(len(b))}


PENGUINEER_HIDDEN = frozenset({"llm", "llm_model", "llm_dry_run", "out", "in_place"})


def penguineer(call_id: str, command: str, params: dict | None = None) -> dict:
    """Run a penguineer command on this run's snapshots and return its result as a dict.

    File inputs are a query id (`"q2"`: that query's snapshot) or `"knowledge:<name>"` (structured knowledge, such
    as a plant graph, written out as YAML); nothing outside the run folder can be read. The call runs in its own
    folder with no LLM, and its outputs are copied to work/penguineer/<call_id>/ in the run, so they are sealed
    with it and a replay re-runs the call on the same snapshots.
    """
    import shutil

    import matplotlib

    backend = matplotlib.get_backend()
    import penguineer as pg  # its report modules select Agg on import; keep the kernel's inline figures
    from penguineer.core import default_registry
    from penguineer.core.fields import field_shape, is_path_type

    matplotlib.use(backend)

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
            params[name] = str(_penguineer_input(value, staged, csv=name == "lots"))
            shown[params[name]] = _state["queries"][value]["snapshot"] if value in _state["queries"] else value

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
    print(summary)
    print(RESULT_MARKER + json.dumps({"penguineer": call_id, "command": command, "folder": folder,
                                      "exit_code": out.result.exit_code(), "summary": summary,
                                      "result": result}, default=str))
    return result


def _penguineer_input(value: str, staged: Path, csv: bool = False) -> Path:
    if value.startswith("knowledge:"):
        name = value.removeprefix("knowledge:")
        path = staged / f"{name}.yaml"
        path.write_text(yaml.safe_dump(knowledge(name), sort_keys=False))
        return path
    rec = _state["queries"].get(value)
    if rec is None:
        raise ValueError(f"{value!r} is not a query of this run or knowledge:<name>; penguineer reads only the run's "
                         f"query snapshots ({sorted(_state['queries'])}) and its structured knowledge")
    snapshot = _local(_state["run"] / rec["snapshot"])
    if not csv:
        return snapshot
    path = staged / f"{value}.csv"  # penguineer 0.2 reads lot windows from CSV only
    pd.read_parquet(snapshot).to_csv(path, index=False)
    return path


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
    return {k: v for k, v in _state.items() if k not in ("con",)}


sys.modules.setdefault("alc", sys.modules[__name__])
