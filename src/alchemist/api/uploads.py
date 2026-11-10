"""Data files (Excel, CSV) people upload to a project, for its questions to analyse (see uploads.py).

The file is sent as the request body (not a form), so a large file streams to a temporary file and no form parser is
needed. Anyone in the workspace may upload to and use a project's files; a file tagged with a plant is shown only
to people entitled to that plant.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel

from .. import uploads
from ..access import AccessDenied
from ..workspace import ensure_project, project_dir
from .deps import Me, Svc, access_error, in_workspace

router = APIRouter(tags=["uploads"])

_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


def _project(svc, me, workspace: str, project: str):
    if not _PROJECT.match(project) or not _PROJECT.match(workspace):
        raise HTTPException(422, "workspace and project names are letters, digits, '.', '_' and '-'")
    if not in_workspace(me, workspace):
        raise HTTPException(403, f"you are not a member of workspace {workspace!r}")
    return project_dir(svc.settings.workspace_root, workspace, project)


def _readable_plants(svc, me) -> frozenset[str]:
    u = me.user
    return svc.access.all_plants if (u.is_admin or u.is_global) else u.plants


def _visible(svc, me, rec: dict) -> bool:
    return not rec.get("plant") or rec["plant"] in _readable_plants(svc, me)


def _out(rec: dict) -> dict:
    return {k: rec[k] for k in ("id", "uploaded_by", "uploaded_at", "plant", "description", "time_zone", "file",
                                "converter", "tables", "skipped")}


def _get(svc, me, pdir, upload_id: str) -> dict:
    try:
        rec = uploads.load(pdir, upload_id)
    except KeyError as e:
        raise HTTPException(404, f"no upload {upload_id}") from e
    if not _visible(svc, me, rec):
        raise HTTPException(404, f"no upload {upload_id}")
    return rec


@router.get("/projects/{workspace}/{project}/uploads")
def list_uploads(workspace: str, project: str, svc: Svc, me: Me) -> list[dict]:
    pdir = _project(svc, me, workspace, project)
    return [_out(r) for r in uploads.list_all(pdir) if _visible(svc, me, r)]


@router.post("/projects/{workspace}/{project}/uploads", status_code=201)
async def upload(workspace: str, project: str, request: Request, svc: Svc, me: Me, filename: str,
                 plant: str | None = None, description: str = "", time_zone: str = "") -> dict:
    """Upload one Excel or CSV file as the request body; each sheet becomes a table runs can query."""
    pdir = _project(svc, me, workspace, project)
    try:
        uploads.file_format(filename)
    except uploads.UploadError as e:
        raise HTTPException(415, str(e)) from e
    limit = svc.settings.upload_max_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(413, f"the file is larger than {svc.settings.upload_max_mb} MB")
    if plant and plant not in _readable_plants(svc, me):
        raise HTTPException(403, f"you may not add data of plant {plant!r}")
    try:
        plants = svc.access.run_plants(me.user, workspace)
    except AccessDenied as e:
        raise access_error(e) from e
    if plant and plant not in plants:
        raise HTTPException(403, f"plant {plant!r} is not part of workspace {workspace!r}")
    with tempfile.TemporaryDirectory(prefix="alc-upload-") as tmp:
        local = Path(tmp) / uploads.safe_name(filename)
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
            return uploads.save(pdir, filename=filename, source=local, user=me.user.id, plant=plant,
                                description=description[:2000], time_zone=time_zone[:64],
                                taken=uploads.table_names(pdir))
        try:
            rec = await run_in_threadpool(convert)
        except uploads.UploadError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:  # a damaged file the readers choke on
            raise HTTPException(422, f"the file could not be read as {uploads.file_format(filename)} "
                                     f"({type(e).__name__}: {str(e)[:200]})") from e
    svc.catalog.audit(me.user.id, "upload.create", f"{workspace}/{project}/{rec['id']}",
                      {"file": rec["file"]["name"], "sha256": rec["file"]["sha256"], "plant": plant,
                       "tables": [t["name"] for t in rec["tables"]]})
    return _out(rec)


@router.get("/projects/{workspace}/{project}/uploads/{upload_id}")
def get_upload(workspace: str, project: str, upload_id: str, svc: Svc, me: Me, rows: int = 20) -> dict:
    """The upload with the first rows of each of its tables."""
    pdir = _project(svc, me, workspace, project)
    rec = _get(svc, me, pdir, upload_id)
    return {**_out(rec), "previews": {t["name"]: uploads.preview(pdir, upload_id, t["name"], min(rows, 200))
                                      for t in rec["tables"]}}


@router.get("/projects/{workspace}/{project}/uploads/{upload_id}/original")
def original(workspace: str, project: str, upload_id: str, svc: Svc, me: Me):
    pdir = _project(svc, me, workspace, project)
    rec = _get(svc, me, pdir, upload_id)
    f = uploads.uploads_dir(pdir) / upload_id / rec["file"]["path"]
    return Response(f.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{rec["file"]["name"]}"'})


class ExposureBody(BaseModel):
    table: str
    column: str
    exposure: Literal["model", "pseudonymise", "never_to_model"]


@router.put("/projects/{workspace}/{project}/uploads/{upload_id}/exposure")
def set_exposure(workspace: str, project: str, upload_id: str, body: ExposureBody, svc: Svc, me: Me) -> dict:
    """Hide a column from the model (or show it again). Only the person who uploaded the file, or an admin."""
    pdir = _project(svc, me, workspace, project)
    rec = _get(svc, me, pdir, upload_id)
    if rec["uploaded_by"] != me.user.id and not me.user.is_admin:
        raise HTTPException(403, "only the person who uploaded the file (or an admin) can change this")
    try:
        rec = uploads.set_exposure(pdir, upload_id, body.table, body.column, body.exposure)
    except KeyError as e:
        raise HTTPException(404, f"no column {body.table}.{body.column}") from e
    svc.catalog.audit(me.user.id, "upload.exposure", f"{workspace}/{project}/{upload_id}", body.model_dump())
    return _out(rec)
