"""Data files people upload (Excel and CSV), converted into tables a run can query.

An upload belongs to one project of a workspace and is written once, never changed (apart from which columns the
model may see):

    <project>/uploads/<upload-id>/
        upload.json                         what was uploaded, by whom, and how each sheet was converted
        original/<file name>                the file exactly as uploaded (its SHA-256 is in upload.json)
        tables/<table>/data.parquet         one table per sheet (one for a CSV file)

Each table is queried as the view `ds.<table>` (table names start with `up_`), through the same SQL guard, run
credentials and snapshots as lake datasets, so a run over uploaded data replays like any other. A question lists the
uploads it uses; the run pins their tables in inputs/views.json when its plan is approved.

Conversion is deliberately plain and recorded: the header row is found (and a units row under it), column names are
cleaned (the original header is kept), and a text column becomes numbers or dates only when nearly all of its values
are. Values that do not convert (`<0.1`, `n/a`) become null and are kept as text in `<column>__raw`, and every such
choice is listed in the table's notes, which the agent reads as the table's quirks.
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .catalog import EXPOSURES, empty_card, missing_facts, now, validate_card
from .paths import as_path, create_exclusive, is_cloud, new_id, put_file, read_json, sha256_file, write_json

SCHEMA = "alchemist.upload/v1"
CONVERTER_VERSION = "1"
FORMATS = {".xlsx": "xlsx", ".xlsm": "xlsx", ".xls": "xls", ".csv": "csv", ".tsv": "csv", ".txt": "csv"}
PREFIX = "up_"
MAX_UNZIPPED_BYTES = 2 << 30  # an .xlsx is a zip: refuse one that unpacks to more than 2 GiB
MAX_CELLS = 50_000_000  # per sheet
HEADER_SCAN_ROWS = 30
NUMERIC_SHARE = 0.8  # a text column becomes numeric when at least this share of its values are numbers
DATE_SHARE = 0.9
_UNIT = re.compile(r"^(?P<name>.*?)\s*[\(\[]\s*(?P<unit>[^\)\]]{1,24})\s*[\)\]]\s*$")
_NUM_DOT = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_NUM_COMMA = re.compile(r"^[+-]?\d+,\d+$")
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")


class UploadError(ValueError):
    """The file cannot be taken: wrong type, too large, unreadable or empty."""


def uploads_dir(project_dir) -> object:
    return as_path(project_dir) / "uploads"


def file_format(filename: str) -> str:
    fmt = FORMATS.get(Path(filename).suffix.lower())
    if not fmt:
        raise UploadError(f"{filename}: upload an Excel (.xlsx, .xlsm, .xls) or CSV (.csv, .tsv) file")
    return fmt


def safe_name(filename: str) -> str:
    """The file's own name, without any folder, and with only characters safe in a path."""
    name = re.split(r"[\\/]", filename)[-1].strip() or "upload"
    name = re.sub(r"[^\w.\- ]", "_", name)[:120]
    return name.lstrip(".") or "upload"


def slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return s[:limit].rstrip("_")


# ---------------------------------------------------------------- reading sheets


def read_sheets(path: Path, fmt: str) -> dict[str, pd.DataFrame]:
    """Every sheet as a frame of raw cells (no header), in workbook order."""
    if fmt == "xlsx":
        try:
            with zipfile.ZipFile(path) as z:
                if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
                    raise UploadError("the workbook unpacks to more than 2 GiB; split it or save it as CSV")
        except zipfile.BadZipFile as e:
            raise UploadError("this is not a readable .xlsx file (save it again from Excel, or as CSV)") from e
        # Formulas are read as the values Excel last calculated and saved.
        return pd.read_excel(path, sheet_name=None, header=None, engine="openpyxl", dtype=object)
    if fmt == "xls":
        try:
            return pd.read_excel(path, sheet_name=None, header=None, engine="xlrd", dtype=object)
        except Exception as e:  # xlrd raises its own error types for damaged or encrypted files
            raise UploadError(f"this .xls file could not be read ({type(e).__name__}); save it as .xlsx or CSV") from e
    return {"": _read_csv(path)}


def _read_csv(path: Path) -> pd.DataFrame:
    """A CSV file as raw cells. The delimiter is the one that splits most lines into the same number of fields, so
    a title line above the table (or a comma inside it) does not decide it; short lines are padded."""
    import csv

    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):  # latin-1 decodes any bytes
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:200_000].splitlines()[:500]

    def score(sep: str) -> tuple[int, int]:
        counts = [len(r) for r in csv.reader(sample, delimiter=sep) if len(r) > 1]
        if not counts:
            return (0, 0)
        modal = max(set(counts), key=counts.count)
        return (counts.count(modal), modal)

    sep = max(("\t", ";", ",", "|"), key=score)
    if score(sep) == (0, 0):
        sep = ","
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=sep))
    except csv.Error as e:
        raise UploadError(f"the CSV file could not be read: {e}") from e
    width = max((len(r) for r in rows), default=0)
    if width == 0:
        raise UploadError("the CSV file is empty")
    return pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=object)


# ---------------------------------------------------------------- converting one sheet


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or (isinstance(v, str) and not v.strip()) or v is pd.NaT


def _is_text(v) -> bool:
    return isinstance(v, str) and not _NUM_DOT.match(v.strip()) and not _NUM_COMMA.match(v.strip())


def find_header(cells: pd.DataFrame) -> int | None:
    """Index of the header row: the first of the top rows that is mostly filled and mostly text, with data below."""
    ncols = cells.shape[1]
    for i in range(min(HEADER_SCAN_ROWS, len(cells) - 1)):
        row = [v for v in cells.iloc[i] if not _blank(v)]
        if len(row) >= max(1, int(0.5 * ncols + 0.5)) and sum(_is_text(v) for v in row) >= 0.8 * len(row):
            return i
    return None


def _units_row(cells: pd.DataFrame, header: int) -> bool:
    """Whether the row under the header holds units (short text cells above numeric data)."""
    if header + 2 >= len(cells):
        return False
    row, below = cells.iloc[header + 1], cells.iloc[header + 2]
    filled = [(v, b) for v, b in zip(row, below) if not _blank(v)]
    if not filled:
        return False
    texty = [v for v, _ in filled if _is_text(v) and len(str(v).strip()) <= 16]
    numeric_below = [b for _, b in filled if not _blank(b) and not _is_text(b)]
    return len(texty) == len(filled) and len(numeric_below) >= 0.5 * len(filled)


def _clean_names(headers: list, units: list) -> list[dict]:
    cols, seen = [], set()
    for i, (h, u) in enumerate(zip(headers, units)):
        original = "" if _blank(h) else str(h).strip()
        unit = "" if _blank(u) else str(u).strip().strip("[]()")
        base = original
        m = _UNIT.match(original)
        if m and not unit:
            base, unit = m.group("name"), m.group("unit").strip()
        name = slug(base, 60) or f"col_{i + 1}"
        if name[0].isdigit():
            name = f"c_{name}"
        n, k = name, 2
        while n in seen:
            n, k = f"{name}_{k}", k + 1
        seen.add(n)
        cols.append({"name": n, "original": original or f"(column {i + 1}, no header)", **({"unit": unit} if unit else {})})
    return cols


def _to_number(s: pd.Series) -> tuple[pd.Series, str | None]:
    """Text to numbers; returns the numbers (NaN where a value is not one) and the decimal convention used."""
    text = s.astype(str).str.strip().str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)
    mode = None
    if text.str.contains(r"\d,\d{3}\.", regex=True).any() or (text.str.match(_THOUSANDS).any()
                                                                and text.str.contains(".", regex=False).any()):
        text, mode = text.str.replace(",", "", regex=False), "',' read as a thousands separator"
    elif text.str.match(_NUM_COMMA).any() and not text.str.match(r"^[+-]?\d*\.\d+$").any():
        text, mode = text.str.replace(",", ".", regex=False), "',' read as the decimal separator"
    return pd.to_numeric(text, errors="coerce"), mode


def _examples(values: pd.Series, n: int = 3) -> str:
    return ", ".join(repr(str(v)) for v in values.drop_duplicates().head(n))


_DMY = re.compile(r"^\s*(\d{1,2})([./-])(\d{1,2})\2\d{2,4}\b")


def day_first(text: pd.Series) -> tuple[bool, str]:
    """Whether dates written as a/b/yyyy put the day first, decided once for the whole column."""
    parts = text.str.extract(_DMY)
    if parts.dropna().empty:
        return False, "year first"
    a, sep, b = parts[0].dropna().astype(int), parts[1].dropna(), parts[2].dropna().astype(int)
    if (a > 12).any():
        return True, "day before month"
    if (b > 12).any():
        return False, "month before day"
    if (sep == ".").all():
        return True, "day before month (dates with dots)"
    return False, "month before day: ambiguous in this column, check it"


def convert_column(s: pd.Series, name: str) -> tuple[pd.Series, pd.Series | None, list[str]]:
    """One column's typed values, its raw text when some values did not convert, and notes on what was done."""
    filled = s[~s.map(_blank)]
    s = s.where(~s.map(_blank), None)
    if filled.empty:
        return s.astype("string"), None, []
    kinds = filled.map(lambda v: "bool" if isinstance(v, (bool, np.bool_)) else
                       "num" if isinstance(v, (int, float, np.integer, np.floating)) else
                       "date" if isinstance(v, (pd.Timestamp, np.datetime64)) or hasattr(v, "isoformat") else "text")
    if (kinds == "bool").all():
        return s.astype("boolean"), None, []
    if (kinds == "date").all():
        return pd.to_datetime(s, errors="coerce"), None, []
    notes: list[str] = []
    num, mode = _to_number(filled.astype(str)) if (kinds != "num").any() else (pd.to_numeric(filled), None)
    ok = num.notna()
    if ok.mean() >= NUMERIC_SHARE:
        out = pd.Series(np.nan, index=s.index, dtype="float64")
        out[filled.index] = num
        if mode:
            notes.append(f"{name}: {mode}")
        raw = None
        if not ok.all():
            bad = filled[~ok]
            raw = s.astype("string")
            notes.append(f"{name}: {len(bad)} of {len(filled)} values are not numbers (e.g. {_examples(bad)}); they "
                         f"are null in {name} and kept as text in {name}__raw")
        if out.dropna().apply(float.is_integer).all() and out.notna().all():
            out = out.astype("int64")
        return out, raw, notes
    if (kinds == "text").all():
        dayfirst, order = day_first(filled.astype(str))
        dates = pd.to_datetime(filled.astype(str), errors="coerce", format="mixed", dayfirst=dayfirst)
        if dates.notna().mean() >= DATE_SHARE:
            out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
            out[filled.index] = dates
            raw = None
            if not dates.notna().all():
                bad = filled[dates.isna()]
                raw = s.astype("string")
                notes.append(f"{name}: {len(bad)} of {len(filled)} values are not dates (e.g. {_examples(bad)}); "
                             f"they are null in {name} and kept as text in {name}__raw")
            notes.append(f"{name}: read as date/time from text, {order} (e.g. {_examples(filled, 2)})")
            return out, raw, notes
    if (kinds != "text").any() and (kinds == "text").any():
        notes.append(f"{name}: mixes numbers and text, so it is kept as text (e.g. {_examples(filled[kinds == 'text'])})")
    return s.map(lambda v: None if v is None else str(v)).astype("string"), None, notes


@dataclass
class Table:
    frame: pd.DataFrame
    columns: list[dict]
    header_row: int | None  # 1-based row in the sheet, as Excel shows it
    units_row: int | None
    first_data_row: int
    notes: list[str]


def convert_sheet(cells: pd.DataFrame) -> Table | None:
    """A sheet of raw cells as a typed table, or None when it holds no data."""
    cells = cells.copy()
    cells.index = range(len(cells))  # sheet row = index + 1
    blank = cells.map(_blank)
    cells = cells.loc[:, ~blank.all(axis=0)]
    if cells.empty or cells.shape[0] < 1:
        return None
    if cells.shape[0] * cells.shape[1] > MAX_CELLS:
        raise UploadError(f"a sheet has more than {MAX_CELLS:,} cells; split it or save it as CSV")
    nonblank_rows = cells.index[~cells.map(_blank).all(axis=1)]
    cells = cells.loc[nonblank_rows.min():nonblank_rows.max()]
    notes: list[str] = []
    rel = find_header(cells.reset_index(drop=True))
    if rel is None:
        header_idx, units = None, False
        headers, unit_cells = [None] * cells.shape[1], [None] * cells.shape[1]
        data = cells
        notes.append("no header row found; columns are named col_1, col_2, ...")
    else:
        header_idx = cells.index[rel]
        units = _units_row(cells.reset_index(drop=True), rel)
        headers = list(cells.loc[header_idx])
        unit_cells = list(cells.iloc[rel + 1]) if units else [None] * cells.shape[1]
        data = cells.iloc[rel + (2 if units else 1):]
        if rel > 0:
            notes.append(f"rows 1-{header_idx} above the header were skipped")
    data = data.loc[~data.map(_blank).all(axis=1)]
    if data.empty:
        return None
    cols = _clean_names(headers, unit_cells)
    out: dict[str, pd.Series] = {}
    out_cols: list[dict] = []
    for col, (_, series) in zip(cols, data.items()):
        typed, raw, col_notes = convert_column(series.reset_index(drop=True), col["name"])
        out[col["name"]] = typed
        out_cols.append({**col, "type": _duck_type(typed)})
        notes += col_notes
        if raw is not None:
            out[f"{col['name']}__raw"] = raw
            out_cols.append({"name": f"{col['name']}__raw", "original": f"{col['original']} (as written)",
                             "type": "VARCHAR"})
    first = int(data.index[0]) + 1
    return Table(pd.DataFrame(out), out_cols, None if header_idx is None else int(header_idx) + 1,
                 int(header_idx) + 2 if units else None, first, notes)


def _duck_type(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(s):
        return "BIGINT"
    if pd.api.types.is_float_dtype(s):
        return "DOUBLE"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "TIMESTAMP"
    return "VARCHAR"


# ---------------------------------------------------------------- saving an upload


def table_card(table: str, upload: dict, t: dict) -> dict:
    """The catalog card the agent reads for an uploaded table (alchemist.card/v1)."""
    card = empty_card(table)
    where = f"sheet '{t['sheet']}' of " if t.get("sheet") else ""
    card["description"] = (f"Uploaded by {upload['uploaded_by']} on {upload['uploaded_at'][:10]}: {where}"
                           f"{upload['file']['name']}." + (f" {upload['description']}" if upload.get("description") else ""))
    card["grain"] = "one row per row of the uploaded sheet"
    card["time_zone"] = upload.get("time_zone") or ""
    card["quirks"] = list(t.get("notes", []))
    card["columns"] = [{"name": c["name"], "type": c["type"], **({"unit": c["unit"]} if c.get("unit") else {}),
                        "description": f"header in the file: {c['original']}", "exposure": c.get("exposure", "model")}
                       for c in t["columns"]]
    card["missing"] = [m for m in missing_facts(card) if m not in ("grain", "join_keys")]
    validate_card(card)
    return card


def save(project_dir, *, filename: str, source: Path, user: str, plant: str | None = None,
         description: str = "", time_zone: str = "", taken: set[str] | None = None) -> dict:
    """Convert a file and write the upload folder. `taken` holds the project's existing table names."""
    fmt = file_format(filename)
    name = safe_name(filename)
    sheets = read_sheets(source, fmt)
    uid = new_id()
    root = uploads_dir(project_dir) / uid
    stem = slug(Path(name).stem, 30) or "file"
    taken = set(taken or ())
    record = {"schema": SCHEMA, "id": uid, "uploaded_by": user, "uploaded_at": now(), "plant": plant or None,
              "description": description.strip(), "time_zone": time_zone.strip(),
              "file": {"name": name, "format": fmt, "size": source.stat().st_size, "sha256": sha256_file(source),
                       "path": f"original/{name}"},
              "converter": {"version": CONVERTER_VERSION, "pandas": pd.__version__}, "tables": [], "skipped": []}
    with tempfile.TemporaryDirectory(prefix="alc-up-") as tmp:
        for sheet, cells in sheets.items():
            table = convert_sheet(cells)
            if table is None:
                record["skipped"].append({"sheet": sheet, "reason": "no data"})
                continue
            base = PREFIX + stem + (f"_{slug(sheet, 20)}" if len(sheets) > 1 and slug(sheet, 20) else "")
            tname, k = base, 2
            while tname in taken:
                tname, k = f"{base}_{k}", k + 1
            taken.add(tname)
            local = Path(tmp) / f"{tname}.parquet"
            table.frame.to_parquet(local, index=False)
            put_file(local, root / "tables" / tname / "data.parquet")
            record["tables"].append({"name": tname, "sheet": sheet or None, "header_row": table.header_row,
                                     "units_row": table.units_row, "first_data_row": table.first_data_row,
                                     "rows": len(table.frame), "columns": [{**c, "exposure": "model"} for c in table.columns],
                                     "notes": table.notes})
    if not record["tables"]:
        raise UploadError(f"{name}: no sheet holds any data")
    put_file(source, root / "original" / name)
    create_exclusive(root / "upload.json", json.dumps(record, indent=2) + "\n")
    return record


# ---------------------------------------------------------------- reading uploads


def load(project_dir, upload_id: str) -> dict:
    if not re.fullmatch(r"[0-9A-Z]{26}", upload_id or ""):
        raise KeyError(upload_id)
    f = uploads_dir(project_dir) / upload_id / "upload.json"
    if not f.exists():
        raise KeyError(upload_id)
    return read_json(f)


def list_all(project_dir) -> list[dict]:
    d = uploads_dir(project_dir)
    if not d.exists():
        return []
    out = []
    for f in d.glob("*/upload.json"):
        try:
            out.append(read_json(f))
        except (ValueError, OSError):
            continue
    return sorted(out, key=lambda u: u["uploaded_at"], reverse=True)


def table_names(project_dir) -> set[str]:
    return {t["name"] for u in list_all(project_dir) for t in u["tables"]}


def table_glob(project_dir, upload_id: str, table: str) -> str:
    return str(uploads_dir(project_dir) / upload_id / "tables" / table) + "/*.parquet"


def set_exposure(project_dir, upload_id: str, table: str, column: str, exposure: str) -> dict:
    """Change whether the model sees a column (model, pseudonymise, never_to_model). Runs approved earlier keep the
    exposures they pinned."""
    if exposure not in EXPOSURES:
        raise ValueError(f"exposure must be one of {', '.join(EXPOSURES)}")
    record = load(project_dir, upload_id)
    t = next((t for t in record["tables"] if t["name"] == table), None)
    c = next((c for c in (t or {}).get("columns", []) if c["name"] == column), None)
    if c is None:
        raise KeyError(f"{table}.{column}")
    c["exposure"] = exposure
    if not column.endswith("__raw"):  # the raw text of a column follows it
        for r in t["columns"]:
            if r["name"] == f"{column}__raw":
                r["exposure"] = exposure
    write_json(uploads_dir(project_dir) / upload_id / "upload.json", record)
    return record


def preview(project_dir, upload_id: str, table: str, rows: int = 20) -> dict:
    """The first rows of a table as people see it (all columns, including hidden ones: they uploaded them)."""
    f = uploads_dir(project_dir) / upload_id / "tables" / table / "data.parquet"
    if not re.fullmatch(r"[a-z0-9_]+", table) or not f.exists():
        raise KeyError(table)
    if is_cloud(f):
        df = pd.read_parquet(io.BytesIO(f.read_bytes()))
    else:
        df = pd.read_parquet(f)
    head = df.head(rows)
    return {"table": table, "rows": len(df), "columns": list(df.columns),
            "head": json.loads(head.to_json(orient="records", date_format="iso"))}


def views(project_dir, records: list[dict]) -> dict:
    """inputs/views.json entries for the tables of the given uploads."""
    out = {}
    for u in records:
        for t in u["tables"]:
            out[t["name"]] = {
                "glob": table_glob(project_dir, u["id"], t["name"]), "format": "parquet",
                "plant": u.get("plant") or "upload", "card_version": f"upload:{u['id']}",
                "columns": [{"name": c["name"], "exposure": c.get("exposure", "model")} for c in t["columns"]],
                "upload": {"id": u["id"], "file": u["file"]["name"], "sha256": u["file"]["sha256"],
                           "sheet": t.get("sheet"), "header_row": t.get("header_row"),
                           "converter": u["converter"]["version"]},
            }
    return out


def summaries(records: list[dict]) -> list[dict]:
    """What search_catalog returns for uploaded tables (hidden columns left out)."""
    out = []
    for u in records:
        for t in u["tables"]:
            card = table_card(t["name"], u, t)
            out.append({"dataset": t["name"], "view": f"ds.{t['name']}", "uploaded": True,
                        "plant": u.get("plant") or "upload", "rows": t["rows"],
                        "description": card["description"], "time_zone": card["time_zone"], "quirks": card["quirks"],
                        "columns": [{k: c.get(k) for k in ("name", "type", "unit", "description") if c.get(k)}
                                    for c in card["columns"] if c.get("exposure") != "never_to_model"],
                        "missing_facts": card["missing"]})
    return out
