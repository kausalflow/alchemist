"""Domain knowledge: searchable documents and structured facts.

Documents are markdown files with YAML front matter (id, title, version, plant,
review_date). Splitting them into passages and ranking passages is penguineer's
(`penguineer.agent.knowledge`); this module finds the files, applies the plant
access rule and handles knowledge packs. The target design uses hybrid search
(pgvector + full text) over the same passages.

Structured knowledge (spec limits, residence times, tag maps) lives in
`structured/*.yaml` and is copied into each run's inputs, so code uses it
directly and replays see the same values.

Plugins can add knowledge packs (`alchemist.plugins.KnowledgePack`): folders with
the same layout, searched and copied alongside ALC_KNOWLEDGE.
"""

from __future__ import annotations

import datetime as dt

import yaml
from penguineer.agent.knowledge import KeywordIndex, document

from .paths import as_path, write_json


class Knowledge:
    def __init__(self, root, packs=()):
        self.root = as_path(root)
        self.roots = [(None, self.root)] + [(p.name, as_path(p.root)) for p in packs]
        self.passages: list[dict] = []
        self.documents: dict[str, dict] = {}  # doc id -> its front matter, path and pack (admin pages list these)
        for pack, r in self.roots:
            if r.exists():
                for f in sorted(r.rglob("*.md"), key=str):
                    # Folders starting with "." hold earlier versions of edited documents (see plant_catalog.py).
                    if any(part.startswith(".") for part in f.relative_to(r).parts[:-1]):
                        continue
                    self._load(f, pack)
        self.index = KeywordIndex(self.passages)

    def _load(self, path, pack: str | None = None) -> None:
        meta, sections = document(path.read_text(), path.stem)
        doc = {k: meta[k] for k in ("doc", "title", "version", "plant", "review_date")} | {"path": str(path)}
        if pack:
            doc["pack"] = pack
        lines = meta.get("lines") or []
        self.documents[doc["doc"]] = {**doc, "type": str(meta.get("type", "Other")),
                                      "lines": [str(x) for x in (lines if isinstance(lines, list) else [lines])],
                                      "updated_at": str(meta.get("updated_at", "")),
                                      "updated_by": str(meta.get("updated_by", "")), "sections": len(sections)}
        self.passages += [{**doc, "heading": heading, "text": text} for heading, text in sections]

    def search(self, query: str, plants, limit: int = 5, today: dt.date | None = None) -> list[dict]:
        """Passages of global documents and of the plants this run may read."""
        return self.index.search(query, keep=lambda p: p["plant"] == "global" or p["plant"] in plants,
                                 limit=limit, today=today)

    def facts(self) -> list[dict]:
        """Every structured knowledge table: name, plant, version, description, pack and path (not its values)."""
        out = []
        for pack, root in self.roots:
            folder = root / "structured"
            if not folder.exists():
                continue
            for f in sorted(folder.glob("*.yaml"), key=str):
                data = yaml.safe_load(f.read_text()) or {}
                entry = {"name": f.stem, "plant": str(data.get("plant", "global")), "version": str(data.get("version", "1")),
                         "description": str(data.get("description", "")), "updated_at": str(data.get("updated_at", "")),
                         "updated_by": str(data.get("updated_by", "")), "path": str(f)}
                if pack:
                    entry["pack"] = pack
                out.append(entry)
        return out

    def copy_structured(self, run_dir, plants) -> list[dict]:
        """Copy structured knowledge for the run's plants into inputs/knowledge/."""
        copied, seen = [], set()
        for pack, root in self.roots:
            folder = root / "structured"
            if not folder.exists():
                continue
            for f in sorted(folder.glob("*.yaml"), key=str):
                if f.stem in seen:
                    raise ValueError(f"structured knowledge {f.stem!r} is defined twice (in {pack or 'ALC_KNOWLEDGE'})")
                seen.add(f.stem)
                data = yaml.safe_load(f.read_text()) or {}
                plant = str(data.get("plant", "global"))
                if plant != "global" and plant not in plants:
                    continue
                write_json(as_path(run_dir) / "inputs" / "knowledge" / f"{f.stem}.json", data)
                entry = {"name": f.stem, "version": str(data.get("version", "1")), "plant": plant}
                if pack:
                    entry["pack"] = pack
                copied.append(entry)
        return copied
