"""Plant catalogs: what admins set up per plant so agents find the right data and knowledge.

A plant's catalog is its datasets (the `datasets` table, `plant` column), its documents and reference facts
(knowledge files whose `plant` is the plant), and its lines. "global" is the shared catalog every plant also sees.

Documents and facts edited here are written as the same files `Knowledge` reads (see docs/concepts/knowledge.md):

    <ALC_KNOWLEDGE>/plants/<plant>/<DOC-ID>.md       a new document
    <ALC_KNOWLEDGE>/structured/<name>.yaml           a new reference fact table
    <ALC_KNOWLEDGE>/.history/...                     the previous version of anything saved over

so runs, citations and replays work exactly as for hand-placed files. A document or fact that already exists keeps its
path and gets the next version number. Plugin knowledge packs are read-only.

Plants and lines live in the app database. Saving a plant's lines also writes a generated document (LINES-<plant>) so
`search_knowledge` finds line names and what they make.
"""

from __future__ import annotations

import datetime as dt
import re

import yaml

from .catalog import now
from .paths import as_path, write_text

DOC_TYPES = ["SOP", "FMEA", "Incident report", "Specification", "Design document", "Procedure", "Other"]
LINES_TYPE = "Plant lines"
RESERVED_FACT_KEYS = ("plant", "version", "description", "updated_at", "updated_by")


class ReadOnly(Exception):
    """The document or fact comes from a plugin knowledge pack."""


def doc_id_from_title(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").upper()[:60]
    return slug or "DOC"


def _front_matter(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n" + body.strip() + "\n"


def _archive(root, kind: str, name: str, version: str, path) -> None:
    src = as_path(path)
    write_text(as_path(root) / ".history" / kind / name / f"v{version}{src.suffix}", src.read_text())


def save_document(knowledge, root, *, plant: str, title: str, type: str, lines: list[str], review_date: str,
                  content: str, actor: str, id: str | None = None) -> str:
    """Write a document (a new one, or the next version of an existing one) and return its id."""
    doc_id = id or doc_id_from_title(title)
    existing = knowledge.documents.get(doc_id)
    if existing and existing.get("pack"):
        raise ReadOnly(f"{doc_id} is provided by the plugin {existing['pack']!r}")
    if existing:
        _archive(root, "documents", doc_id, existing["version"], existing["path"])
        version, path = int(existing["version"]) + 1 if existing["version"].isdigit() else 2, existing["path"]
    else:
        version, path = 1, as_path(root) / "plants" / plant / f"{doc_id}.md"
    meta = {"id": doc_id, "title": title, "version": version, "plant": plant, "type": type, "lines": list(lines),
            "review_date": review_date or "", "updated_at": now(), "updated_by": actor}
    write_text(path, _front_matter(meta, content))
    return doc_id


def read_document(knowledge, doc_id: str) -> str:
    """A document's body without its front matter."""
    text = as_path(knowledge.documents[doc_id]["path"]).read_text()
    return text.split("---", 2)[2].lstrip("\n") if text.startswith("---") else text


def save_fact(knowledge, root, *, name: str, plant: str, description: str, content: str, actor: str) -> None:
    """Write a reference fact table from YAML text (a mapping); its plant, version and description are set here."""
    try:
        values = yaml.safe_load(content) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"not valid YAML: {e}") from e
    if not isinstance(values, dict):
        raise ValueError("a fact table must be a YAML mapping (key: value lines)")
    existing = next((f for f in knowledge.facts() if f["name"] == name), None)
    if existing and existing.get("pack"):
        raise ReadOnly(f"{name} is provided by the plugin {existing['pack']!r}")
    if existing:
        _archive(root, "facts", name, existing["version"], existing["path"])
        version = int(existing["version"]) + 1 if existing["version"].isdigit() else 2
    else:
        version = 1
    data = {"plant": plant, "version": version, "description": description, "updated_at": now(), "updated_by": actor}
    data.update({k: v for k, v in values.items() if k not in RESERVED_FACT_KEYS})
    write_text(as_path(root) / "structured" / f"{name}.yaml", yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def read_fact(knowledge, name: str) -> str:
    f = next(f for f in knowledge.facts() if f["name"] == name)
    data = yaml.safe_load(as_path(f["path"]).read_text()) or {}
    values = {k: v for k, v in data.items() if k not in RESERVED_FACT_KEYS}
    return yaml.safe_dump(values, sort_keys=False, allow_unicode=True) if values else ""


def overdue(review_date: str, today: dt.date | None = None) -> bool:
    return bool(review_date) and review_date < (today or dt.date.today()).isoformat()


# ---------------------------------------------------------------- plants and lines (app database)

def plants(db) -> list[dict]:
    return [dict(r) for r in db.execute("select * from plants order by id")]


def upsert_plant(db, *, id: str, name: str, description: str, actor: str) -> None:
    db.execute("insert into plants(id, name, description, created_by, created_at, updated_at) values (?,?,?,?,?,?)"
               " on conflict (id) do update set name = excluded.name, description = excluded.description,"
               " updated_at = excluded.updated_at", (id, name, description, actor, now(), now()))
    db.commit()


def lines(db, plant: str) -> list[dict]:
    return [{"id": r["id"], "name": r["name"] or "", "description": r["description"] or ""}
            for r in db.execute("select * from plant_lines where plant = ? order by id", (plant,))]


def set_lines(db, knowledge, root, plant: str, new: list[dict], actor: str) -> list[dict]:
    with db.transaction():
        db.execute("delete from plant_lines where plant = ?", (plant,))
        for ln in new:
            db.execute("insert into plant_lines(plant, id, name, description) values (?,?,?,?)",
                       (plant, ln["id"], ln.get("name", ""), ln.get("description", "")))
    saved = lines(db, plant)
    body = "\n\n".join(f"# Line {ln['id']}: {ln['name']}\n{ln['description'] or ln['name']}".rstrip() for ln in saved)
    save_document(knowledge, root, id=f"LINES-{plant}", plant=plant, title=f"Lines at plant {plant}", type=LINES_TYPE,
                  lines=[ln["id"] for ln in saved], review_date="", content=body or "No lines recorded.", actor=actor)
    return saved
