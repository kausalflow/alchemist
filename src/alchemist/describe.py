"""A plain-words description of an uploaded data file, and a few questions an engineer could start with.

Worked out from the converted tables alone (no model call), so it is instant, free and the same every time. Columns
the uploader hid from the model are left out, because a starter question goes to the model as the question text.
"""

from __future__ import annotations

import io
import re

import pandas as pd

from .paths import is_cloud
from .uploads import _UNIT, uploads_dir

# Names that usually mean "the result we care about"; the first numeric column that matches is the outcome.
_OUTCOME = re.compile(r"yield|fpy|quality|defect|scrap|reject|purity|conversion|selectivity|oee|loss|waste|"
                      r"rework|failure|strength|viscosity|mfi|melt.?flow|moisture|impurit|grade|output|throughput",
                      re.IGNORECASE)
_LOWER_IS_WORSE = re.compile(r"yield|fpy|quality|purity|conversion|selectivity|oee|strength|output|throughput", re.IGNORECASE)
_ID = re.compile(r"(^|_)(id|no|nr|number|batch|lot|order|sample|serial|code)($|_)", re.IGNORECASE)
MAX_CATEGORIES = 30


def _frame(project_dir, upload_id: str, table: str) -> pd.DataFrame:
    f = uploads_dir(project_dir) / upload_id / "tables" / table / "data.parquet"
    return pd.read_parquet(io.BytesIO(f.read_bytes())) if is_cloud(f) else pd.read_parquet(f)


def _num(v: float) -> str:
    if pd.isna(v):
        return "?"
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.3g}" if abs(v) < 1000 else f"{v:,.0f}"


def _label(c: dict) -> str:
    """The column as the person wrote it in the file, which is what they will recognise."""
    o = c.get("original") or ""
    if not o or o.startswith("(column"):
        return c["name"]
    m = _UNIT.match(o)
    return m.group("name").strip() if m and m.group("name").strip() else o


def _when(v) -> str:
    t = pd.Timestamp(v)
    return t.strftime("%d %b %Y") if t == t.normalize() else t.strftime("%d %b %Y %H:%M")


def describe_table(df: pd.DataFrame, columns: list[dict]) -> dict:
    """{sentences, numbers, dates, categories, ids} for one table, columns hidden from the model left out."""
    cols = [c for c in columns if not c["name"].endswith("__raw") and c.get("exposure") != "never_to_model"
            and c["name"] in df.columns]
    rows = len(df)
    out: dict = {"rows": rows, "numbers": [], "dates": [], "categories": [], "ids": [], "sentences": []}
    sentences = out["sentences"]
    sentences.append(f"{rows:,} rows and {len(cols)} columns.")
    gaps = []
    for c in cols:
        s, name, label = df[c["name"]], c["name"], _label(c)
        filled = s.dropna()
        if rows and len(filled) < 0.9 * rows:
            gaps.append(f"{label} ({100 - round(100 * len(filled) / rows)} % empty)")
        if filled.empty:
            continue
        if pd.api.types.is_datetime64_any_dtype(s):
            out["dates"].append({"name": name, "label": label, "from": _when(filled.min()), "to": _when(filled.max())})
        elif pd.api.types.is_bool_dtype(s):
            out["categories"].append({"name": name, "label": label, "values": ["true", "false"]})
        elif pd.api.types.is_numeric_dtype(s):
            unit = f" {c['unit']}" if c.get("unit") else ""
            if _ID.search(name) and filled.nunique() == len(filled):
                out["ids"].append({"name": name, "label": label})
                continue
            out["numbers"].append({"name": name, "label": label, "unit": c.get("unit", ""),
                                   "min": float(filled.min()), "max": float(filled.max()),
                                   "text": f"{label} from {_num(filled.min())} to {_num(filled.max())}{unit}"})
        else:
            distinct = filled.astype(str).nunique()
            if (distinct == len(filled) and len(filled) >= 10) or (distinct >= 0.9 * len(filled) > MAX_CATEGORIES):
                out["ids"].append({"name": name, "label": label})
            elif distinct <= MAX_CATEGORIES:
                values = filled.astype(str).value_counts().index.tolist()
                out["categories"].append({"name": name, "label": label, "values": values})
            else:
                out["ids"].append({"name": name, "label": label})
    if out["dates"]:
        d = out["dates"][0]
        sentences.append(f"It covers {d['from']} to {d['to']} ({d['label']}).")
    if out["categories"]:
        parts = []
        for k in out["categories"][:3]:
            v = k["values"]
            shown = ", ".join(v[:4]) + (f" and {len(v) - 4} more" if len(v) > 4 else "")
            parts.append(f"{len(v)} {k['label']} ({shown})")
        sentences.append("It is split by " + "; ".join(parts) + ".")
    if out["numbers"]:
        shown = [n["text"] for n in out["numbers"][:5]]
        more = len(out["numbers"]) - len(shown)
        sentences.append("Measured values: " + "; ".join(shown) + (f"; and {more} more" if more > 0 else "") + ".")
    if out["ids"]:
        sentences.append("Identifiers: " + ", ".join(i["label"] for i in out["ids"][:4]) + ".")
    if gaps:
        sentences.append("Mostly empty: " + ", ".join(gaps[:4]) + ".")
    return out


def starter_questions(info: dict, limit: int = 4) -> list[str]:
    """Three or four questions a process engineer might ask first about this table."""
    nums, cats, dates = info["numbers"], info["categories"], info["dates"]
    if not nums:
        if cats and dates:
            return [f"How many rows per {cats[0]['label']} are there over time, and does anything stand out?"]
        return []
    outcome = next((n for n in nums if _OUTCOME.search(n["name"]) or _OUTCOME.search(n["label"])), nums[0])
    worse = "lowest" if _LOWER_IS_WORSE.search(outcome["name"] + " " + outcome["label"]) else "highest"
    y = outcome["label"]
    others = [n["label"] for n in nums if n is not outcome]
    qs = []
    group = next((c for c in cats if 2 <= len(c["values"]) <= 12), None)
    if group:
        qs.append(f"Which {group['label']} has the {worse} {y}, and is the difference real or just noise?")
    if dates:
        qs.append(f"How did {y} change between {dates[0]['from']} and {dates[0]['to']}, and when did it shift?")
    if others:
        qs.append(f"What explains the variation in {y}? Check {', '.join(others[:3])}.")
    qs.append(f"Which rows have unusual {y}, and what do they have in common?")
    return qs[:limit]


def about(project_dir, upload: dict) -> dict:
    """What GET .../uploads/{id}/about returns: per table, sentences and starter questions."""
    tables = []
    for t in upload["tables"]:
        info = describe_table(_frame(project_dir, upload["id"], t["name"]), t["columns"])
        where = f"Sheet {t['sheet']}: " if t.get("sheet") and len(upload["tables"]) > 1 else ""
        tables.append({"table": t["name"], "sheet": t.get("sheet"),
                       "description": where + " ".join(info["sentences"]),
                       "questions": starter_questions(info)})
    questions = [q for t in tables for q in t["questions"]]
    return {"id": upload["id"], "tables": tables, "questions": list(dict.fromkeys(questions))[:4]}
