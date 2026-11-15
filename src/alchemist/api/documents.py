"""Documents (PDF, Word, PowerPoint, text) people add to a project as background for its questions (documents.py).

Like data files, the file is the request body. Any workspace member may add, read and take out a project's
documents; taking one out keeps it on record for the questions that already read it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from .. import documents
from ..access import AccessDenied
from ..uploads import UploadError, safe_name
from ..workspace import ensure_project
from .deps import Me, Svc, access_error
from .uploads import _project

router = APIRouter(tags=["documents"])

PREVIEW_CHARS = 4000


def _out(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("id", "added_by", "added_at", "description", "file", "chars", "pages",
                                     "empty_pages", "sections", "removed_at", "removed_by")}


def _get(pdir, document_id: str) -> dict:
    try:
        return documents.load(pdir, document_id)
    except KeyError as e:
        raise HTTPException(404, f"no document {document_id}") from e


@router.get("/projects/{workspace}/{project}/documents")
def list_documents(workspace: str, project: str, svc: Svc, me: Me) -> list[dict]:
    return [_out(r) for r in documents.list_all(_project(svc, me, workspace, project))]


@router.post("/projects/{workspace}/{project}/documents", status_code=201)
async def add_document(workspace: str, project: str, request: Request, svc: Svc, me: Me, filename: str,
                       description: str = "") -> dict:
    """Add one PDF, Word, PowerPoint or text file as the request body; its text becomes searchable background."""
    pdir = _project(svc, me, workspace, project)
    try:
        documents.file_format(filename)
    except UploadError as e:
        raise HTTPException(415, str(e)) from e
    limit = svc.settings.upload_max_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(413, f"the file is larger than {svc.settings.upload_max_mb} MB")
    try:
        plants = svc.access.run_plants(me.user, workspace)
    except AccessDenied as e:
        raise access_error(e) from e
    with tempfile.TemporaryDirectory(prefix="alc-doc-") as tmp:
        local = Path(tmp) / safe_name(filename)
        size = 0
        with open(local, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"the file is larger than {svc.settings.upload_max_mb} MB")
                f.write(chunk)
        if size == 0:
            raise HTTPException(422, "the file is empty")

        def convert() -> dict:
            ensure_project(svc.settings.workspace_root, workspace, project, owner=me.user.id, plants=sorted(plants))
            return documents.save(pdir, filename=filename, source=local, user=me.user.id,
                                  description=description[:2000])
        try:
            rec = await run_in_threadpool(convert)
        except UploadError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:  # a damaged file the readers choke on
            raise HTTPException(422, f"the file could not be read ({type(e).__name__}: {str(e)[:200]}); "
                                     "open it and save it again, or save it as PDF") from e
    svc.catalog.audit(me.user.id, "document.add", f"{workspace}/{project}/{rec['id']}",
                      {"file": rec["file"]["name"], "sha256": rec["file"]["sha256"], "chars": rec["chars"]})
    return _out(rec)


@router.get("/projects/{workspace}/{project}/documents/{document_id}")
def get_document(workspace: str, project: str, document_id: str, svc: Svc, me: Me) -> dict:
    """The document with the start of the text Alchemist reads, so people can check it came out right."""
    pdir = _project(svc, me, workspace, project)
    rec = _get(pdir, document_id)
    text = documents.text(pdir, document_id)
    return {**_out(rec), "text": text[:PREVIEW_CHARS], "truncated": len(text) > PREVIEW_CHARS}


@router.get("/projects/{workspace}/{project}/documents/{document_id}/original")
def original(workspace: str, project: str, document_id: str, svc: Svc, me: Me):
    pdir = _project(svc, me, workspace, project)
    rec = _get(pdir, document_id)
    f = documents.documents_dir(pdir) / document_id / rec["file"]["path"]
    return Response(f.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{rec["file"]["name"]}"'})


@router.delete("/projects/{workspace}/{project}/documents/{document_id}")
def remove_document(workspace: str, project: str, document_id: str, svc: Svc, me: Me) -> dict:
    """Take the document out of the project. Questions already asked keep it in their record."""
    pdir = _project(svc, me, workspace, project)
    _get(pdir, document_id)
    rec = documents.remove(pdir, document_id, me.user.id)
    svc.catalog.audit(me.user.id, "document.remove", f"{workspace}/{project}/{document_id}",
                      {"file": rec["file"]["name"]})
    return _out(rec)
