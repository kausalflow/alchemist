"""Documents people add to a project (PDF, Word, PowerPoint, text) for Alchemist to read as background.

Engineers already have SOPs, specs, FMEAs and past reports. Rather than retyping them into the project's background,
they drop the files into the project; every question asked there afterwards can search them with search_knowledge,
next to the plant's own documents, and cites the passages it used.

    <project>/documents/<document-id>/
        document.json       what was added, by whom, the text it gave and (if taken out) by whom and when
        original/<file>     the file exactly as added (its SHA-256 is in document.json)
        text.md             the text the agents search: one section per page (PDF), slide (PowerPoint) or heading

A document is written once and never changed. Taking one out of the project only marks it (`removed_at`), so the
records of questions that read it stay complete; new questions no longer see it. Each question pins the documents
it was asked with (question.yaml `documents`), like the background, so later changes do not alter what it read.

Only the text is read: pictures, scanned pages and drawings are not. A PDF without a text layer (a scan) is refused
with a note saying so, rather than added and silently ignored.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from penguineer.agent.knowledge import KeywordIndex, sections

from .catalog import now
from .paths import as_path, create_exclusive, is_cloud, new_id, put_file, read_json, sha256_file, write_json
from .uploads import UploadError, safe_name

SCHEMA = "alchemist.document/v1"
CONVERTER_VERSION = "1"
FORMATS = {".pdf": "pdf", ".docx": "docx", ".docm": "docx", ".pptx": "pptx", ".txt": "text", ".md": "text"}
# Formats people will try that cannot be read reliably, with what to do instead.
ADVICE = {
    ".doc": "Old Word files (.doc) cannot be read: in Word choose File > Save As > Word Document (.docx) or PDF.",
    ".ppt": "Old PowerPoint files (.ppt) cannot be read: save it as .pptx or PDF.",
    ".rtf": "Save the file as .docx or PDF first.",
}
MAX_UNZIPPED_BYTES = 512 << 20
MAX_CHARS = 2_000_000  # about 600 pages of text
PASSAGE_CHARS = 1000  # search returns whole passages, so long pages are split into parts of about this size
DOC_PREFIX = "project-doc:"


def documents_dir(project_dir):
    return as_path(project_dir) / "documents"


def file_format(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in ADVICE:
        raise UploadError(f"{filename}: {ADVICE[ext]}")
    fmt = FORMATS.get(ext)
    if not fmt:
        raise UploadError(f"{filename}: add a PDF, Word (.docx), PowerPoint (.pptx) or text file")
    return fmt


def is_document(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in FORMATS or ext in ADVICE


# ---------------------------------------------------------------- reading files


def _zip(path: Path, kind: str) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise UploadError(f"this is not a readable {kind} file (open it and save it again, or save it as PDF)") from e
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
        raise UploadError("the file unpacks to more than 512 MB; save it as PDF")
    return z


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_of(el) -> str:
    """The text of a Word paragraph or table cell: runs, tabs and line breaks."""
    out = []
    for node in el.iter():
        name = _local(node.tag)
        if name == "t" and node.text:
            out.append(node.text)
        elif name == "tab":
            out.append("\t")
        elif name in ("br", "cr"):
            out.append("\n")
    return "".join(out)


def read_docx(path: Path) -> list[tuple[str, str]]:
    """Paragraphs with their heading levels (Heading 1..9 and Title become markdown headings) and tables as rows of
    cells separated by ' | '. Comments, headers and footers are left out."""
    with _zip(path, "Word") as z:
        try:
            root = ET.fromstring(z.read("word/document.xml"))
        except KeyError as e:
            raise UploadError("this is not a Word document (no word/document.xml inside)") from e
    body = next((c for c in root if _local(c.tag) == "body"), None)
    lines: list[str] = []
    for block in body if body is not None else []:
        name = _local(block.tag)
        if name == "p":
            text = _text_of(block).strip()
            if not text:
                continue
            style = next((s.get(next(k for k in s.attrib if _local(k) == "val"), "") for s in block.iter()
                          if _local(s.tag) == "pStyle" and s.attrib), "")
            m = re.match(r"(?i)heading\s*(\d)|^(title)$", style.replace("berschrift", "eading"))
            if m:
                level = 1 if m.group(2) else min(int(m.group(1)), 6)
                lines.append(f"{'#' * level} {text.splitlines()[0]}")
            else:
                lines.append(text)
        elif name == "tbl":
            for row in (r for r in block.iter() if _local(r.tag) == "tr"):
                cells = [_text_of(c).strip().replace("\n", " ") for c in row if _local(c.tag) == "tc"]
                if any(cells):
                    lines.append(" | ".join(cells))
            lines.append("")
    return [("", "\n".join(lines))]


def read_pptx(path: Path) -> list[tuple[str, str]]:
    """One section per slide, with the slide's text boxes and table cells in order (speaker notes are left out)."""
    with _zip(path, "PowerPoint") as z:
        names = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        out = []
        for i, n in enumerate(names, 1):
            root = ET.fromstring(z.read(n))
            paras = []
            for p in (e for e in root.iter() if _local(e.tag) == "p"):
                text = "".join(t.text or "" for t in p.iter() if _local(t.tag) == "t").strip()
                if text:
                    paras.append(text)
            out.append((f"Slide {i}", "\n".join(paras)))
    return out


def read_pdf(path: Path) -> list[tuple[str, str]]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise UploadError("the PDF is password-protected; save a copy without a password")
        return [(f"Page {i}", (page.extract_text() or "").strip()) for i, page in enumerate(reader.pages, 1)]
    except PdfReadError as e:
        raise UploadError(f"the PDF could not be read ({e}); open it and save or print it to PDF again") from e


def read_text_file(path: Path) -> list[tuple[str, str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return [("", raw.decode(enc))]
        except UnicodeDecodeError:
            continue
    return [("", raw.decode("latin-1"))]


READERS = {"pdf": read_pdf, "docx": read_docx, "pptx": read_pptx, "text": read_text_file}


def to_markdown(parts: list[tuple[str, str]]) -> str:
    """Pages or slides as `## Page N` sections; a Word or text file keeps its own headings."""
    out = []
    for heading, text in parts:
        text = text.strip()
        if not text:
            continue
        if heading:
            # A page's own lines starting with '#' must not start new sections.
            text = re.sub(r"(?m)^#", r"\\#", text)
            out.append(f"## {heading}\n\n{text}")
        else:
            out.append(text)
    return "\n\n".join(out).strip() + "\n"


# ---------------------------------------------------------------- saving and listing


def save(project_dir, *, filename: str, source: Path, user: str, description: str = "") -> dict:
    fmt = file_format(filename)
    name = safe_name(filename)
    parts = READERS[fmt](source)
    text = to_markdown(parts)
    chars = len(text.strip())
    if fmt == "pdf" and chars < 30 * max(len(parts), 1):
        raise UploadError(f"{name}: no text found in the PDF. It looks like a scan (pictures of pages), which "
                          "Alchemist cannot read. Add a version with text, or write the key points in the background.")
    if chars == 0:
        raise UploadError(f"{name}: the file holds no text")
    if chars > MAX_CHARS:
        raise UploadError(f"{name}: the file holds more than {MAX_CHARS // 1000:,}k characters of text; split it")
    did = new_id()
    root = documents_dir(project_dir) / did
    headings = [h for h, _ in sections(text, Path(name).stem)]
    record = {"schema": SCHEMA, "id": did, "added_by": user, "added_at": now(), "description": description.strip(),
              "file": {"name": name, "format": fmt, "size": source.stat().st_size, "sha256": sha256_file(source),
                       "path": f"original/{name}"},
              "converter": {"version": CONVERTER_VERSION}, "chars": chars,
              **({"pages": len(parts)} if fmt in ("pdf", "pptx") else {}),
              "empty_pages": [i for i, (_, t) in enumerate(parts, 1) if not t.strip()] if fmt == "pdf" else [],
              "sections": headings[:200]}
    put_file(source, root / "original" / name)
    md = root / "text.md"
    if not is_cloud(md):
        md.parent.mkdir(parents=True, exist_ok=True)
    md.write_bytes(text.encode("utf-8"))  # UTF-8 on every system (Windows would default to its code page)
    create_exclusive(root / "document.json", json.dumps(record, indent=2) + "\n")
    return record


def load(project_dir, document_id: str) -> dict:
    if not re.fullmatch(r"[0-9A-Z]{26}", document_id or ""):
        raise KeyError(document_id)
    f = documents_dir(project_dir) / document_id / "document.json"
    if not f.exists():
        raise KeyError(document_id)
    return read_json(f)


def text(project_dir, document_id: str) -> str:
    return (documents_dir(project_dir) / document_id / "text.md").read_bytes().decode("utf-8")


def list_all(project_dir, include_removed: bool = False) -> list[dict]:
    d = documents_dir(project_dir)
    if not d.exists():
        return []
    out = []
    for f in d.glob("*/document.json"):
        try:
            rec = read_json(f)
        except (ValueError, OSError):
            continue
        if include_removed or not rec.get("removed_at"):
            out.append(rec)
    return sorted(out, key=lambda r: r["added_at"], reverse=True)


def remove(project_dir, document_id: str, user: str) -> dict:
    """Take a document out of the project: new questions no longer read it; earlier questions keep their record."""
    rec = load(project_dir, document_id)
    if not rec.get("removed_at"):
        rec.update(removed_at=now(), removed_by=user)
        write_json(documents_dir(project_dir) / document_id / "document.json", rec)
    return rec


def pins(project_dir) -> list[dict]:
    """What a new question records about the project's documents (question.yaml `documents`)."""
    return [{"id": r["id"], "name": r["file"]["name"], "sha256": r["file"]["sha256"]} for r in list_all(project_dir)]


# ---------------------------------------------------------------- what a run reads


def _passages(doc: dict, body: str) -> list[dict]:
    """Sections of a document, long ones split into parts at paragraph breaks so search returns them whole."""
    base = {"doc": DOC_PREFIX + doc["name"], "version": doc["sha256"][:12], "title": doc["name"], "plant": "project",
            "review_date": ""}
    out = []
    for heading, txt in sections(body, Path(doc["name"]).stem):
        chunks, buf = [], ""
        for para in re.split(r"\n\s*\n", txt):
            if buf and len(buf) + len(para) > PASSAGE_CHARS:
                chunks.append(buf)
                buf = ""
            while len(para) > PASSAGE_CHARS * 1.5:  # one very long paragraph: cut at a line break or space
                cut = max(para.rfind("\n", 0, PASSAGE_CHARS), para.rfind(" ", 0, PASSAGE_CHARS), PASSAGE_CHARS // 2)
                chunks.append((buf + "\n\n" + para[:cut]).strip())
                buf, para = "", para[cut:].lstrip()
            buf = (buf + "\n\n" + para).strip()
        if buf:
            chunks.append(buf)
        for i, c in enumerate(chunks, 1):
            out.append({**base, "heading": heading + (f" (part {i})" if len(chunks) > 1 else ""), "text": c})
    return out


class ProjectDocuments:
    """The documents a question was asked with, searchable like plant knowledge."""

    def __init__(self, project_dir, pinned: list[dict] | None):
        self.docs: list[dict] = []
        passages = []
        for pin in pinned or []:
            try:
                rec = load(project_dir, pin["id"])
            except KeyError:
                continue
            if rec["file"]["sha256"] != pin["sha256"]:  # never happens: documents are written once
                continue
            body = text(project_dir, pin["id"])
            self.docs.append({**pin, "sections": rec.get("sections", []), "pages": rec.get("pages")})
            passages += _passages(pin, body)
        self.index = KeywordIndex(passages) if passages else None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        if not self.index:
            return []
        hits = self.index.search(query, limit=limit)
        for h in hits:
            h["source"] = "project document"
        return hits

    def note(self) -> str:
        """How the agents learn which documents the project holds (they read them through search_knowledge)."""
        if not self.docs:
            return ""
        lines = []
        for d in self.docs:
            secs = [s for s in d["sections"] if not re.fullmatch(r"(Page|Slide) \d+", s)][:12]
            extra = f"{d['pages']} pages" if d.get("pages") else ""
            extra += ("; headings: " + "; ".join(secs)) if secs else ""
            lines.append(f"- {d['name']}" + (f" ({extra})" if extra else ""))
        return ("Documents the project's engineers added as background (SOPs, specs, reports). search_knowledge "
                "searches them along with the plant documents; results from them have doc ids starting with "
                f"'{DOC_PREFIX}'. Treat them like the background: context to plan with and to test against the data, "
                "and cite the passages you rely on.\n" + "\n".join(lines))
