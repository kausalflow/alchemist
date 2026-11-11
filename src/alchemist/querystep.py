"""The query step: a run's SQL runs here, outside its kernel, and only the filtered result reaches the kernel.

A run's kernel executes code the model wrote, so it must never hold what it could use to read past a dataset card:
the raw files (with their `never_to_model` columns), credentials that can read them, or the pseudonym key. Every
live or rerun query therefore runs in this step instead:

1. the SQL is checked (sqlguard: one SELECT over `ds.<dataset>` views the run was approved for);
2. each view selects only the card's visible columns, with `pseudonymise` columns replaced by a keyed hash
   (HMAC-SHA256) computed in a Python function the SQL cannot call;
3. the result is written to the run folder as a content-hashed snapshot (`inputs/<sha256>.parquet`) and recorded in
   `inputs/queries.json`, with the objects it read.

The kernel's `alc.query(qid)` only loads that snapshot (nb.py). The step runs as its own short-lived process
(`python -m alchemist.querystep`, request on stdin, answer on stdout): with the run's query credentials (dataset
read, write to the run's inputs/ only), the pseudonym key passed on stdin rather than in its environment, and, where
Linux Landlock is available, file access limited to the run's local datasets and its run folder. The worker starts
it for an in-process kernel; `alchemist sandbox serve` starts it for its kernels (POST /query).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PSEUDONYM_FUNCTION = "alc_pseudonym"  # registered on the step's connection only; sqlguard rejects it in SQL
PSEUDONYM_CHARS = 32
QUERY_TIMEOUT = 900.0


class QueryFailed(RuntimeError):
    """The query was rejected or failed; the message is safe to show the model."""


def pseudonym(key: str, value: str) -> str:
    """The pseudonym of one value: HMAC-SHA256 with the deployment's key, as hex, first 32 characters. Without the
    key it cannot be recomputed, so a guessed name cannot be matched to its pseudonym."""
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()[:PSEUDONYM_CHARS]


# ---------------------------------------------------------------- inside the step process

def _connect(views: dict, tmp: Path, key: str):
    import duckdb
    import pyarrow as pa

    from .plugins import get_registry

    con = duckdb.connect()
    con.execute("set threads = 1")

    def pseudo(values):  # a pyarrow array in, one out (no annotations: DuckDB would evaluate them)
        return pa.array([None if v is None else pseudonym(key, v) for v in values.to_pylist()], type=pa.string())

    con.create_function(PSEUDONYM_FUNCTION, pseudo, ["VARCHAR"], "VARCHAR", type="arrow", null_handling="special",
                        side_effects=False)
    backends = get_registry().storage_backends
    for scheme in sorted({_scheme(v["glob"]) for v in views.values()}):
        backends.get(scheme).setup(con)
    roots = sorted({_root(v["glob"]) for v in views.values()} | {str(tmp)})
    con.execute("set allowed_directories = [" + ", ".join("'" + r.replace("'", "''") + "'" for r in roots) + "]")
    con.execute("set enable_external_access = false")
    con.execute("set lock_configuration = true")
    con.execute("create schema if not exists ds")
    return con


def _scheme(uri: str) -> str:
    return uri.split("://", 1)[0] if "://" in uri else "file"


def _root(glob: str) -> str:
    parts = glob.split("/")
    for i, p in enumerate(parts):
        if any(c in p for c in "*?["):
            return "/".join(parts[:i])
    return "/".join(parts[:-1])


def view_sql(name: str, spec: dict, scan: str) -> str:
    """The view of one dataset: the card's visible columns only, pseudonymised ones through the keyed hash."""
    cols = []
    for c in spec["columns"]:
        if c["exposure"] == "never_to_model":
            continue
        q = '"' + c["name"].replace('"', '""') + '"'
        cols.append(f"{PSEUDONYM_FUNCTION}({q}::varchar) as {q}" if c["exposure"] == "pseudonymise" else q)
    if not cols:
        raise QueryFailed(f"dataset {name} has no columns the model may see")
    return f"create or replace view ds.{name} as select {', '.join(cols)} from {scan}"


def _pin_views(con, views: dict, datasets: list[str]) -> list[dict]:
    """Expand each dataset's glob once and point its view at that explicit file list."""
    from .paths import expand_glob, object_info
    from .plugins import get_registry

    objects = []
    for name in datasets:
        spec = views[name]
        files = expand_glob(spec["glob"])
        if not files:
            raise QueryFailed(f"no files for dataset {name}")
        objects += [dict(object_info(f), dataset=name) for f in files]
        scan = get_registry().data_formats.get(spec["format"]).scan(files)
        con.execute(view_sql(name, spec, scan))
    return objects


def frame_hash(df) -> str:
    """Content hash of a result, independent of Parquet writer details."""
    import pandas as pd

    h = hashlib.sha256()
    h.update(json.dumps([str(c) for c in df.columns]).encode())
    h.update(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    return h.hexdigest()


def execute(req: dict) -> dict:
    """Run one query for a run (the body of the step process). `req`: run_dir, views (the run's pinned views),
    qid, sql (None in a rerun: the recorded SQL), mode (live or rerun), key (the pseudonym key)."""
    import pandas as pd

    from .paths import as_path, put_file, read_json, write_json
    from .sqlguard import SQLRejected, check_sql

    run, views, qid, mode = as_path(req["run_dir"]), req["views"], req["qid"], req.get("mode", "live")
    if mode not in ("live", "rerun"):
        raise QueryFailed(f"the query step runs live or rerun queries, not {mode}")
    queries_file = run / "inputs" / "queries.json"
    queries = read_json(queries_file) if queries_file.exists() else {}
    rec = queries.get(qid)
    if mode == "rerun" and rec is None:
        raise QueryFailed(f"query {qid} is not in the run being re-run")
    sql = req.get("sql") or (rec or {}).get("sql")
    if not sql:
        raise QueryFailed("a live query needs SQL")
    try:
        datasets = check_sql(sql, set(views))
    except SQLRejected as e:
        raise QueryFailed(str(e)) from None

    tmp = Path(tempfile.mkdtemp(prefix="alc-query-"))
    con = _connect({d: views[d] for d in datasets}, tmp, req.get("key") or "")
    objects = _pin_views(con, views, datasets)
    tmp_file = tmp / f"{qid}.parquet"
    try:
        con.execute(f"copy ({sql}) to '{tmp_file}' (format parquet)")
    except Exception as e:  # a DuckDB error in the model's SQL: tell it what DuckDB said
        raise QueryFailed(f"{type(e).__name__}: {e}") from None
    df = pd.read_parquet(tmp_file)
    digest = frame_hash(df)
    snapshot = f"inputs/{digest}.parquet"
    put_file(tmp_file, run / snapshot)
    out = {"query": qid, "snapshot": snapshot, "rows": int(len(df)), "datasets": datasets}

    if mode == "rerun":
        then = {x["key"]: x for x in rec["objects"]}
        drift = [o for o in objects if o["key"] not in then or then[o["key"]]["etag"] != o["etag"]]
        report = {"query": qid, "mode": "rerun", "rows_then": rec["rows"], "rows_now": int(len(df)),
                  "result_changed": digest != rec.get("result_sha256"), "objects_changed_or_new": len(drift)}
        rec["rerun"] = {"snapshot": snapshot, "rows": int(len(df)), "result_sha256": digest, "objects": objects,
                        "report": report}
        out["report"] = report
    else:
        queries[qid] = {"sql": sql, "datasets": datasets, "objects": objects, "rows": int(len(df)),
                        "snapshot": snapshot, "result_sha256": digest,
                        "card_versions": {d: views[d].get("card_version") for d in datasets}}
    write_json(queries_file, queries)
    return out


def main() -> None:
    """`python -m alchemist.querystep`: one request (JSON) on stdin, one answer (JSON) on stdout."""
    from . import fsjail

    if os.environ.get("ALC_JAIL_WRITE") is not None:
        read = [*fsjail.SYSTEM_READ, *fsjail.python_paths(), os.path.expanduser("~/.duckdb"),
                *fsjail.split(os.environ.get("ALC_JAIL_READ"))]
        fsjail.drop_capabilities()
        fsjail.restrict(read, [*fsjail.SYSTEM_WRITE, *fsjail.split(os.environ.get("ALC_JAIL_WRITE"))])
    req = json.loads(sys.stdin.read())
    try:
        out = execute(req)
    except QueryFailed as e:
        out = {"error": str(e)}
    except Exception as e:  # noqa: BLE001 - reported to the caller, which shows it to the model
        out = {"error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(out, default=str))


# ---------------------------------------------------------------- the caller: worker or sandbox service

class QueryRunner:
    """Starts the query step for one run's queries.

    `env`: the step's environment (sandbox.kernel_base_env of the caller's, plus the run's query credentials);
    `key`: the pseudonym key, sent on stdin; `read_paths`: local folders of the run's datasets, and `extra_read`
    (e.g. the credentials file) for the Landlock jail; `jail_mode`: ALC_KERNEL_JAIL (auto, required or off)."""

    def __init__(self, env: dict, *, key: str, read_paths: list[str] | None = None,
                 extra_read: list[str] | None = None, jail_mode: str = "auto", timeout: float = QUERY_TIMEOUT):
        self.env = {k: v for k, v in env.items() if v is not None}
        self.key = key
        self.read_paths = [p for p in (read_paths or []) if "://" not in p]
        self.extra_read = list(extra_read or [])
        self.jail_mode = jail_mode
        self.timeout = timeout

    def jail(self, run_dir: str) -> dict | None:
        from . import fsjail

        if self.jail_mode == "off":
            return None
        if not fsjail.available():
            if self.jail_mode == "required":
                raise PermissionError("ALC_KERNEL_JAIL=required, but this machine has no Landlock (Linux 5.13+)")
            return None
        local_run = [] if "://" in str(run_dir) else [str(run_dir)]
        # With ambient credentials (no run role: a laptop) the step may read the AWS config and SSO cache it uses.
        aws = [] if self.extra_read else [os.path.expanduser("~/.aws"), *(self.env[k] for k in (
            "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE") if self.env.get(k))]
        return {"read": [*self.read_paths, *self.extra_read, *aws, *local_run], "write": local_run}

    def run(self, *, run_dir: str, views: dict, qid: str, sql: str | None, mode: str = "live") -> dict:
        from . import fsjail

        fsjail.protect_process()  # the step runs as this user: keep it out of this process's /proc files
        tmp = tempfile.mkdtemp(prefix="alc-qstep-")
        env = {**self.env, "TMPDIR": tmp, "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1"}
        jail = self.jail(run_dir)
        if jail is not None:
            env["ALC_JAIL_READ"] = os.pathsep.join(jail["read"])
            env["ALC_JAIL_WRITE"] = os.pathsep.join([tmp, *jail["write"]])
        req = {"run_dir": str(run_dir), "views": views, "qid": qid, "sql": sql, "mode": mode, "key": self.key}
        try:
            p = subprocess.run([sys.executable, "-m", "alchemist.querystep"], input=json.dumps(req), text=True,
                               capture_output=True, env=env, cwd=tmp, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise QueryFailed(f"query {qid} ran longer than {self.timeout:.0f} s") from None
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        try:
            out = json.loads(p.stdout)
        except ValueError:
            raise QueryFailed(f"the query step failed: {(p.stderr or p.stdout).strip()[-1500:]}") from None
        if "error" in out:
            raise QueryFailed(out["error"])
        return out


if __name__ == "__main__":
    main()
