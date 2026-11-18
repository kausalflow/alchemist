"""Comments on notebook cells: what a person pointed at when they wrote "About cell c12 ...".

People comment on an output cell (a table, a query preview, a figure, printed output) in the notebook view; the
note or follow-up question then names the cell. When it is saved, the cell is looked up in the run's notebook and a
short excerpt (what the cell was for, its code and its text output) is kept with it, so the agent sees exactly what
the person was looking at, also in a follow-up run whose own notebook does not have that cell.
"""

from __future__ import annotations

import json
import re

from .paths import as_path

REF = re.compile(r"\bcell\s+(c\d+)\b", re.IGNORECASE)
MAX_CELLS = 5
CODE_CHARS = 1500
OUTPUT_CHARS = 1500
MARKER = "ALC_RESULT "


def mentioned(text: str) -> list[str]:
    """The cell ids a text names, in order, once each."""
    return list(dict.fromkeys(m.lower() for m in REF.findall(text or "")))


def _text(v) -> str:
    return "".join(v) if isinstance(v, list) else (v or "")


def excerpts(run, text: str) -> list[dict]:
    """The cells of the run's notebook that `text` names, as short excerpts (unknown ids are left out)."""
    ids = mentioned(text)[:MAX_CELLS]
    nb = as_path(run) / "notebook.ipynb"
    if not ids or not nb.exists():
        return []
    try:
        cells = json.loads(nb.read_text(encoding="utf-8")).get("cells", [])
    except (json.JSONDecodeError, OSError):
        return []
    by_id = {}
    for c in cells:
        meta = (c.get("metadata") or {}).get("alchemist") or {}
        if c.get("cell_type") == "code" and meta.get("cell_id"):
            by_id[meta["cell_id"]] = (c, meta)
    out = []
    for cid in ids:
        if cid not in by_id:
            continue
        c, meta = by_id[cid]
        texts, figures = [], 0
        for o in c.get("outputs", []):
            kind = o.get("output_type")
            if kind == "stream":
                texts.append(_text(o.get("text")))
            elif kind in ("execute_result", "display_data"):
                data = o.get("data") or {}
                if "image/png" in data:
                    figures += 1
                elif "text/plain" in data:
                    texts.append(_text(data["text/plain"]))
            elif kind == "error":
                texts.append(f"{o.get('ename')}: {o.get('evalue')}")
        output = "\n".join(l for l in "".join(texts).splitlines() if not l.startswith(MARKER)).strip()
        out.append({"cell": cid, "kind": meta.get("kind"), "purpose": meta.get("purpose") or "",
                    "query": meta.get("query_id"), "finding": meta.get("finding_id"),
                    "code": _text(c.get("source"))[:CODE_CHARS], "output": output[:OUTPUT_CHARS], "figures": figures})
    return out


def for_model(cells: list[dict], *, with_code: bool = False) -> str:
    """The excerpts as the agent reads them. `with_code` for a follow-up, whose agent has not seen the cell."""
    parts = []
    for c in cells:
        head = f'<cell id="{c["cell"]}" kind="{c.get("kind") or "code"}"'
        purpose = (c.get("purpose") or "").replace('"', "'")
        head += f' purpose="{purpose}">' if purpose else ">"
        body = []
        if with_code and c.get("code"):
            body.append(f"code:\n{c['code']}")
        if c.get("output"):
            body.append(f"output:\n{c['output']}")
        if c.get("figures"):
            body.append(f"({c['figures']} figure{'s' if c['figures'] != 1 else ''} shown under this cell)")
        parts.append(head + "\n" + "\n".join(body) + "\n</cell>")
    return "\n".join(parts)
