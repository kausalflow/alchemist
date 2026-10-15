"""Domain knowledge: searchable documents and structured facts.

Documents are markdown files with YAML front matter (id, title, version, plant,
review_date). The MVP ranks passages by keyword overlap; the target design uses
hybrid search (pgvector + full text) over the same passages.

Structured knowledge (spec limits, residence times, tag maps) lives in
`structured/*.yaml` and is copied into each run's inputs, so code uses it
directly and replays see the same values.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter

import yaml

from .paths import as_path, write_json

WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if len(w) > 1]


class Knowledge:
    def __init__(self, root):
        self.root = as_path(root)
        self.passages: list[dict] = []
        if self.root.exists():
            for f in sorted(self.root.rglob("*.md"), key=str):
                self._load(f)
        df = Counter()
        for p in self.passages:
            df.update(set(p["_words"]))
        n = max(len(self.passages), 1)
        self.idf = {w: math.log(1 + n / c) for w, c in df.items()}

    def _load(self, path) -> None:
        text = path.read_text()
        meta, body = {}, text
        if text.startswith("---"):
            _, front, body = text.split("---", 2)
            meta = yaml.safe_load(front) or {}
        doc = {"doc": str(meta.get("id", path.stem)), "title": str(meta.get("title", path.stem)),
               "version": str(meta.get("version", "1")), "plant": str(meta.get("plant", "global")),
               "review_date": str(meta.get("review_date", "")), "path": str(path)}
        heading, buf = doc["title"], []

        def flush():
            content = "\n".join(buf).strip()
            if content:
                self.passages.append({**doc, "heading": heading, "text": content,
                                      "_words": _words(heading + " " + content)})

        for line in body.splitlines():
            if line.startswith("#"):
                flush()
                heading, buf = line.lstrip("#").strip(), []
            else:
                buf.append(line)
        flush()

    def search(self, query: str, plants, limit: int = 5, today: dt.date | None = None) -> list[dict]:
        today = today or dt.date.today()
        q = set(_words(query))
        scored = []
        for p in self.passages:
            if p["plant"] != "global" and p["plant"] not in plants:
                continue
            tf = Counter(p["_words"])
            s = sum(self.idf.get(w, 0) * (1 + math.log(tf[w])) for w in q if tf[w])
            if s > 0:
                scored.append((s, p))
        scored.sort(key=lambda x: -x[0])
        out = []
        for s, p in scored[:limit]:
            stale = bool(p["review_date"]) and p["review_date"] < today.isoformat()
            out.append({"doc": p["doc"], "version": p["version"], "title": p["title"], "section": p["heading"],
                        "text": p["text"][:1200], "review_date": p["review_date"], "stale": stale})
        return out

    def copy_structured(self, run_dir, plants) -> list[dict]:
        """Copy structured knowledge for the run's plants into inputs/knowledge/."""
        copied = []
        folder = self.root / "structured"
        if not folder.exists():
            return copied
        for f in sorted(folder.glob("*.yaml"), key=str):
            data = yaml.safe_load(f.read_text()) or {}
            plant = str(data.get("plant", "global"))
            if plant != "global" and plant not in plants:
                continue
            write_json(as_path(run_dir) / "inputs" / "knowledge" / f"{f.stem}.json", data)
            copied.append({"name": f.stem, "version": str(data.get("version", "1")), "plant": plant})
        return copied
