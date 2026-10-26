"""Data sources: keep a plant's historian, LIMS and lot data in the lake Alchemist queries.

Alchemist answers questions over registered lake datasets. Where a plant's data is not in a lake yet, or not in a
shape DuckDB reads well, a source pulls it there with penguineer's connectors:

    pi     AVEVA PI through the PI Web API (penguineer.connectors.pi): recorded or interpolated values, point
           descriptors and units, and AF event frames as lots. Read-only.
    lake   a Parquet, CSV or Delta table on S3, GCS, Azure or disk (penguineer.connectors.lake), with its columns
           mapped to the standard names. Use it for tables in another layout or store; a Parquet table that is
           already on S3 can be registered directly with `alchemist catalog register`.

Sources are listed in sources.yaml (ALC_SOURCES). `alchemist sources sync` reads each source from where its last
sync ended (or `backfill` back on the first sync) up to now and writes standard tables into the lake (ALC_LAKE):

    <lake>/<plant>/<source>/historian/<YYYY-MM-DD>.parquet   timestamp (UTC), tag, value, quality
    <lake>/<plant>/<source>/lims/<YYYY-MM-DD>.parquet        sampled_at (UTC), property, value, lot, ...
    <lake>/<plant>/<source>/lots/lots.parquet                lot, start, end, ...
    <lake>/<plant>/<source>/<table>/_metadata.yaml           what the table holds, for its catalog card
    <lake>/<plant>/<source>/_sync.json                       how far the source is synced

A sync re-reads whole UTC days, so a day file is always complete and a repeated sync gives the same files. Each
table is registered as dataset `<source>-<table>` and its catalog card drafted from `_metadata.yaml`. Drafts are
not queryable: a data admin approves each card once, as for any other dataset.

Credentials are never in sources.yaml. PI basic sign-in reads its password from PI_WEBAPI_PASSWORD (or the
variable named by `password_env`), bearer sign-in its token from PI_WEBAPI_TOKEN (or `token_env`), and Kerberos
uses the process's ticket. Lake sources use the cloud SDKs' usual environment or the workload's role.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .paths import as_path, read_yaml, write_text, write_yaml

log = logging.getLogger("alchemist.sources")

KINDS = ("pi", "lake")
TABLES = ("historian", "lims", "lots")
# The column each time-series table is partitioned by.
TIME_COLUMN = {"historian": "timestamp", "lims": "sampled_at"}
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|m|h|d|w)\s*$")
_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
# Keys a source may not carry, because sources.yaml is not a secret store.
_SECRET_KEYS = ("password", "token", "secret", "api_key", "access_key", "sas")


class SourceError(ValueError):
    pass


def parse_duration(text: str | int | float) -> timedelta:
    """`15m`, `6h`, `30d`, `2w` (or a number of seconds) as a timedelta."""
    if isinstance(text, (int, float)):
        return timedelta(seconds=text)
    m = _DURATION.match(str(text))
    if not m:
        raise SourceError(f"duration {text!r}: use a number and a unit, such as 15m, 6h or 30d")
    return timedelta(seconds=float(m.group(1)) * _SECONDS[m.group(2)])


def parse_time(text: str) -> datetime:
    """An ISO 8601 time; without a zone it is UTC."""
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


@dataclass
class Source:
    id: str
    plant: str
    kind: str
    spec: dict[str, Any]  # the kind's own settings
    backfill: timedelta = timedelta(days=7)
    owner: str = ""
    sensitivity: str = "internal"

    @property
    def tables(self) -> tuple[str, ...]:
        if self.kind == "pi":
            return ("historian", "lots") if self.spec.get("event_frames") else ("historian",)
        return (self.spec.get("table", "historian"),)


@dataclass
class SourcesConfig:
    lake: str
    sources: dict[str, Source] = field(default_factory=dict)

    def get(self, source_id: str) -> Source:
        if source_id not in self.sources:
            raise SourceError(f"no source {source_id!r} in sources.yaml (have: {', '.join(self.sources) or 'none'})")
        return self.sources[source_id]

    def source_root(self, src: Source):
        return as_path(self.lake.rstrip("/")) / src.plant / src.id

    def table_root(self, src: Source, table: str):
        return self.source_root(src) / table


def load(path: str, default_lake: str) -> SourcesConfig:
    """sources.yaml, checked. A missing file is an empty configuration."""
    p = as_path(path)
    raw = read_yaml(p) if p.exists() else {}
    return parse(raw or {}, default_lake)


def parse(raw: dict, default_lake: str) -> SourcesConfig:
    if not isinstance(raw, dict):
        raise SourceError("sources.yaml: expected a mapping with `sources:`")
    cfg = SourcesConfig(lake=str(raw.get("lake") or default_lake))
    for i, item in enumerate(raw.get("sources") or []):
        if not isinstance(item, dict):
            raise SourceError(f"sources.yaml: source #{i + 1} is not a mapping")
        item = dict(item)
        sid, plant, kind = item.pop("id", None), item.pop("plant", None), item.pop("kind", None)
        where = f"source {sid or '#' + str(i + 1)}"
        if not sid or not _ID.match(str(sid)):
            raise SourceError(f"{where}: `id` must be lower-case letters, digits, - or _ (it names datasets)")
        if sid in cfg.sources:
            raise SourceError(f"{where}: duplicate id")
        if not plant:
            raise SourceError(f"{where}: `plant` is required (it decides who may query the data)")
        if kind not in KINDS:
            raise SourceError(f"{where}: `kind` must be one of {', '.join(KINDS)}")
        bad = [k for k in _walk_keys(item) if any(w in k.lower() for w in _SECRET_KEYS) and not k.endswith("_env")]
        if bad:
            raise SourceError(f"{where}: {', '.join(bad)} looks like a credential; sources.yaml is not a secret "
                              "store, so set it in the environment (see `password_env` / `token_env`)")
        src = Source(sid, str(plant), kind, {}, parse_duration(item.pop("backfill", "7d")),
                     str(item.pop("owner", "")), str(item.pop("sensitivity", "internal")))
        src.spec = item
        if kind == "pi":
            if not item.get("url"):
                raise SourceError(f"{where}: `url` (the PI Web API, ending in /piwebapi) is required")
            if not item.get("tags") and not item.get("tag_filter"):
                raise SourceError(f"{where}: give `tags`, `tag_filter` or both")
            if item.get("tag_filter") and not item.get("data_server"):
                raise SourceError(f"{where}: `tag_filter` searches one data server: give `data_server`")
        else:
            if not item.get("source"):
                raise SourceError(f"{where}: `source` (a path or URL, globs allowed) is required")
            if item.get("table", "historian") not in TABLES:
                raise SourceError(f"{where}: `table` must be one of {', '.join(TABLES)}")
        cfg.sources[sid] = src
    return cfg


def _walk_keys(d: Any):
    if isinstance(d, dict):
        for k, v in d.items():
            yield str(k)
            yield from _walk_keys(v)
    elif isinstance(d, list):
        for v in d:
            yield from _walk_keys(v)


# ---------------------------------------------------------------- reading a window


@dataclass
class Window:
    tables: dict[str, Any]  # table -> polars DataFrame in the standard layout
    metadata: dict[str, dict]  # table -> _metadata.yaml content
    notes: list[str] = field(default_factory=list)


@contextlib.contextmanager
def _env(overrides: dict[str, str | None]):
    """Point penguineer's fixed credential variables at a source's own (password_env, token_env)."""
    old = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is not None:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def pi_client(src: Source):
    from penguineer.connectors import pi

    s = src.spec
    return pi.PIClient(s["url"], auth=pi.PIAuth(s.get("auth", "kerberos")), username=s.get("username"),
                       ca_bundle=Path(s["ca_bundle"]) if s.get("ca_bundle") else None, timeout=float(s.get("timeout", 60)))


def _pi_env(src: Source) -> dict[str, str | None]:
    from penguineer.connectors import pi

    out: dict[str, str | None] = {}
    for key, target in (("password_env", pi.PASSWORD_ENV), ("token_env", pi.TOKEN_ENV)):
        name = src.spec.get(key)
        if name:
            if not os.environ.get(name):
                raise SourceError(f"source {src.id}: set {name} (named by {key})")
            out[target] = os.environ[name]
    return out


def read_pi(src: Source, start: datetime, end: datetime) -> Window:
    from penguineer.connectors import pi

    s = src.spec
    ef = s.get("event_frames") or {}
    with _env(_pi_env(src)):
        got = pi.fetch(pi_client(src), start, end, tags=list(s.get("tags") or []), tag_filter=s.get("tag_filter"),
                       data_server=s.get("data_server"), mode=pi.PIMode(s.get("mode", "recorded")),
                       interval=str(s.get("interval", "1m")), event_frame_database=ef.get("database"),
                       event_frame_template=ef.get("template"))
    tags = got.tags.to_dicts()
    mode = s.get("mode", "recorded")
    grain = ("one row per archived value per tag (PI recorded values, not resampled)" if mode == "recorded"
             else f"one row per tag every {s.get('interval', '1m')} (PI interpolated values)")
    meta = {"historian": {
        "description": f"Historian values of plant {src.plant} from AVEVA PI ({s['url']}), synced by Alchemist "
                       f"source {src.id}. Long layout: one row per tag and time; the tag dictionary below gives "
                       f"each tag's description, unit and range.",
        "grain": grain,
        "time_zone": "UTC",
        "time_meaning": "the time PI archived the value (event time at the data source)",
        "join_keys": ["tag", "timestamp"],
        "refresh_lag": "up to one sync interval behind PI",
        "quirks": ["value is null where PI marked the value bad or returned a system state such as I/O Timeout; "
                   "quality names the state",
                   "questionable and substituted values keep their number; quality says so"],
        "columns": {
            "timestamp": {"description": "time of the value, UTC"},
            "tag": {"description": "PI point name, or AF attribute path"},
            "value": {"description": "the value, in the tag's unit (see tag_dictionary)", "unit": "per tag"},
            "quality": {"description": "Good, or the PI state or flag (Bad, Questionable, Substituted, I/O Timeout ...)"},
        },
        "tag_dictionary": {t["tag"]: {k: t[k] for k in ("description", "unit", "range_low", "range_high", "path")
                                      if t.get(k) is not None} for t in tags},
    }}
    tables = {"historian": got.frame}
    if got.lots is not None:
        tables["lots"] = got.lots
        meta["lots"] = {
            "description": f"Lots (AF event frames) of plant {src.plant} from AVEVA PI, synced by source {src.id}.",
            "grain": "one row per event frame",
            "time_zone": "UTC",
            "time_meaning": "start and end of the event frame; an open frame has no end yet",
            "join_keys": ["lot"],
            "columns": {"lot": {"description": "event frame name"},
                        "start": {"description": "start, UTC"}, "end": {"description": "end, UTC"},
                        "template": {"description": "event frame template"}},
        }
    notes = []
    if got.unresolved:
        notes.append(f"not found in PI: {', '.join(got.unresolved)}")
    if got.open_event_frames:
        notes.append(f"{got.open_event_frames} event frame(s) still open")
    return Window(tables, meta, notes)


def read_lake(src: Source, start: datetime, end: datetime) -> Window:
    import polars as pl
    from penguineer.connectors import lake

    s = src.spec
    table = s.get("table", "historian")
    opts = lake.parse_storage_options([f"{k}={v}" for k, v in (s.get("storage_options") or {}).items()])
    tz = s.get("timezone", "UTC")
    got = lake.fetch(s["source"], lake.TableKind(table), start=start, end=end, tags=s.get("tags") or None,
                     columns=s.get("columns") or None, fmt=lake.TableFormat(s.get("format", "auto")),
                     storage_options=opts, hive=bool(s.get("hive", False)), timezone=tz)
    frame = got.frame
    if table == "historian" and got.layout == "wide":
        values = [c for c in frame.columns if c != "timestamp"]
        frame = frame.unpivot(on=values, index="timestamp", variable_name="tag", value_name="value")
        frame = frame.with_columns(pl.col("value").cast(pl.Float64, strict=False))
    for col in (TIME_COLUMN.get(table), *(("start", "end") if table == "lots" else ())):
        if col and col in frame.columns:
            frame = frame.with_columns(_to_utc(frame[col], tz).alias(col))
    if table == "historian" and "quality" not in frame.columns:
        frame = frame.with_columns(pl.lit(None, pl.String).alias("quality"))
    describe = {
        "historian": ("one row per tag and time", ["tag", "timestamp"],
                      {"timestamp": "time of the value, UTC", "tag": "tag name", "value": "the value",
                       "quality": "quality flag from the source, if it has one"}),
        "lims": ("one row per lab result", ["lot", "property"],
                 {"sampled_at": "when the sample was taken, UTC", "property": "what was measured",
                  "value": "the result", "lot": "lot or batch the sample belongs to"}),
        "lots": ("one row per lot", ["lot"],
                 {"lot": "lot or batch id", "start": "start, UTC", "end": "end, UTC"}),
    }[table]
    cols = {c: {"description": d} for c, d in describe[2].items() if c in frame.columns}
    if "value" in cols:
        cols["value"]["unit"] = s.get("unit", "per tag" if table == "historian" else "per property")
    meta = {table: {
        "description": s.get("description") or f"{table.capitalize()} data of plant {src.plant} from "
                                                f"{s['source']}, synced by Alchemist source {src.id}.",
        "grain": describe[0],
        "time_zone": "UTC",
        "time_meaning": s.get("time_meaning", ""),
        "join_keys": describe[1],
        "refresh_lag": "up to one sync interval behind the source",
        "columns": cols,
        "source_columns": {k: v for k, v in got.columns.items() if v},
    }}
    return Window({table: frame}, meta)


def _to_utc(series, tz: str):
    import polars as pl

    if series.dtype == pl.String:
        series = series.str.to_datetime(strict=False)
    if isinstance(series.dtype, pl.Datetime):
        if series.dtype.time_zone is None:
            series = series.dt.replace_time_zone(tz, ambiguous="earliest", non_existent="null")
        return series.dt.convert_time_zone("UTC")
    return series


def read_window(src: Source, start: datetime, end: datetime) -> Window:
    return (read_pi if src.kind == "pi" else read_lake)(src, start, end)


# ---------------------------------------------------------------- writing to the lake


def _write_parquet(frame, path) -> None:
    buf = io.BytesIO()
    frame.write_parquet(buf, compression="zstd", statistics=True)
    p = as_path(path)
    if "://" not in str(p):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(buf.getvalue())


def _days(start: datetime, end: datetime) -> list[datetime]:
    d, out = start.replace(hour=0, minute=0, second=0, microsecond=0), []
    while d < end:
        out.append(d)
        d += timedelta(days=1)
    return out


def write_table(root, table: str, frame, start: datetime, end: datetime) -> int:
    """Write one table's window; returns the files written. Time-series tables get one file per UTC day in
    [start, end) (start is a day boundary), replacing what was there; lots are merged into lots.parquet by lot."""
    import polars as pl

    if table == "lots":
        path = root / "lots.parquet"
        if path.exists() and frame.height:
            old = pl.read_parquet(io.BytesIO(path.read_bytes()))
            key = "lot" if "lot" in frame.columns and "lot" in old.columns else None
            if key:
                old = old.filter(~pl.col(key).is_in(frame[key].implode()))
            frame = pl.concat([old, frame], how="diagonal_relaxed")
        if not frame.height:
            return 0
        _write_parquet(frame.sort("start") if "start" in frame.columns else frame, path)
        return 1
    tcol = TIME_COLUMN[table]
    written = 0
    for day in _days(start, end):
        part = frame.filter((pl.col(tcol) >= day) & (pl.col(tcol) < day + timedelta(days=1)))
        path = root / f"{day:%Y-%m-%d}.parquet"
        if part.height:
            sort = [c for c in ("tag", "property", tcol) if c in part.columns]
            _write_parquet(part.sort(sort), path)
            written += 1
        elif path.exists():
            path.unlink()  # the source has nothing for this day any more
    return written


def dataset_id(src: Source, table: str) -> str:
    return f"{src.id}-{table}"


def state(cfg: SourcesConfig, src: Source) -> dict:
    p = cfg.source_root(src) / "_sync.json"
    return json.loads(p.read_text()) if p.exists() else {}


@dataclass
class SyncResult:
    source: str
    start: str
    end: str
    rows: dict[str, int]
    files: dict[str, int]
    datasets: list[dict]
    notes: list[str]
    seconds: float


def sync(cfg: SourcesConfig, src: Source, catalog, settings, *, start: datetime | None = None,
         end: datetime | None = None, register: bool = True, actor: str = "sync") -> SyncResult:
    """Read the source's next window, write it to the lake, register its datasets and draft their cards."""
    from .catalog import index_dataset

    t0 = time.monotonic()
    end = (end or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
    if start is None:
        synced = state(cfg, src).get("synced_to")
        start = parse_time(synced) if synced else end - src.backfill
    start = start.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)  # whole days
    if start >= end:
        raise SourceError(f"source {src.id}: start {start:%Y-%m-%d %H:%M} is not before end {end:%Y-%m-%d %H:%M}")
    log.info("source %s: reading %s .. %s", src.id, start.isoformat(), end.isoformat())
    win = read_window(src, start, end)
    rows, files, datasets = {}, {}, []
    for table, frame in win.tables.items():
        root = cfg.table_root(src, table)
        files[table] = write_table(root, table, frame, start, end)
        rows[table] = frame.height
        meta_path = root / "_metadata.yaml"
        # Rewritten only when it changes, so the card is re-drafted only when the tags or the setup change.
        text = _yaml_text(win.metadata[table])
        if not meta_path.exists() or meta_path.read_text() != text:
            write_text(meta_path, text)
        if register:
            datasets.append(_register(catalog, settings, src, table, root, meta_path, actor, index_dataset))
    prev = state(cfg, src)
    history = ([{"at": datetime.now(UTC).isoformat(timespec="seconds"), "start": start.isoformat(),
                 "end": end.isoformat(), "rows": rows, "notes": win.notes}] + prev.get("history", []))[:20]
    write_text(cfg.source_root(src) / "_sync.json",
               json.dumps({"source": src.id, "kind": src.kind, "plant": src.plant,
                           "synced_to": end.isoformat(), "history": history}, indent=2))
    if catalog is not None:
        catalog.audit(actor, "source.sync", src.id, {"start": start.isoformat(), "end": end.isoformat(),
                                                    "rows": rows, "notes": win.notes})
    return SyncResult(src.id, start.isoformat(), end.isoformat(), rows, files, datasets, win.notes,
                      round(time.monotonic() - t0, 2))


def _yaml_text(data: dict) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _register(catalog, settings, src: Source, table: str, root, meta_path, actor: str, index_dataset) -> dict:
    ds_id = dataset_id(src, table)
    glob = "lots.parquet" if table == "lots" else "*.parquet"
    want = {"s3_prefix": str(root).rstrip("/"), "file_glob": glob, "file_format": "parquet", "plant": src.plant,
            "metadata_uri": str(meta_path)}
    try:
        have = catalog.dataset(ds_id)
    except KeyError:
        have = None
    if have is None or any(str(have.get(k)) != str(v) for k, v in want.items()):
        catalog.register(id=ds_id, name=f"{src.id} {table}", owner=src.owner, sensitivity=src.sensitivity,
                         actor=actor, **want)
    res = index_dataset(catalog, settings, ds_id)
    card = catalog.latest_card(ds_id, status=None) or {}
    return {"dataset": ds_id, "card": res["status"], "version": res.get("version"),
            "card_status": card.get("status"), "missing": res.get("missing", [])}


# ---------------------------------------------------------------- checks


def check(src: Source) -> dict:
    """Reach the source without reading a window: the PI Web API and each listed tag, or the lake table's schema."""
    if src.kind == "pi":
        from penguineer.connectors import pi

        with _env(_pi_env(src)):
            client = pi_client(src)
            root = client.get("")
            found, missing = [], []
            for name in src.spec.get("tags") or []:
                try:
                    pi.resolve(client, name, src.spec.get("data_server"))
                    found.append(name)
                except Exception as e:  # noqa: BLE001 - report every tag, not just the first failure
                    missing.append(f"{name}: {e}")
        return {"source": src.id, "ok": not missing, "server": (root or {}).get("Links", {}).get("Self", src.spec["url"]),
                "tags_found": len(found), "problems": missing}
    from penguineer.connectors import lake

    s = src.spec
    opts = lake.parse_storage_options([f"{k}={v}" for k, v in (s.get("storage_options") or {}).items()])
    schema = lake.scan(s["source"], fmt=lake.TableFormat(s.get("format", "auto")), storage_options=opts,
                       hive=bool(s.get("hive", False))).collect_schema()
    return {"source": src.id, "ok": True, "columns": schema.names()[:50], "problems": []}


def describe(cfg: SourcesConfig) -> list[dict]:
    out = []
    for src in cfg.sources.values():
        st = state(cfg, src)
        out.append({"source": src.id, "kind": src.kind, "plant": src.plant, "tables": list(src.tables),
                    "datasets": [dataset_id(src, t) for t in src.tables], "lake": str(cfg.source_root(src)),
                    "synced_to": st.get("synced_to"), "last": (st.get("history") or [None])[0]})
    return out


def write_example(path) -> None:
    """A commented starting point for sources.yaml."""
    write_yaml(path, EXAMPLE)


EXAMPLE = {
    "sources": [
        {"id": "p01-pi", "plant": "P01", "kind": "pi", "url": "https://pi.example.internal/piwebapi",
         "auth": "kerberos", "data_server": "PISRV01", "tag_filter": "P01.*", "mode": "recorded",
         "backfill": "30d", "event_frames": {"database": "\\\\AFSRV01\\Plant", "template": "Batch"}},
        {"id": "p01-lims", "plant": "P01", "kind": "lake", "table": "lims",
         "source": "s3://acme-lims-export/results/*.parquet", "columns": {"sampled_at": "SAMPLE_TS"},
         "timezone": "Europe/Berlin", "storage_options": {"aws_region": "eu-central-1"}},
    ],
}
