"""A project's home: its name, the background its members write for Alchemist, and (through other routes) its files
and questions.

The background is what an engineer would tell a colleague before asking: the process, what normal looks like, what
the team already suspects or ruled out. It lives in project.yaml; every new question copies it (question.yaml
`project_background`), so the planning and analysis agents read it and the run records the version it used.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..access import AccessDenied
from ..catalog import now
from ..paths import read_yaml, write_yaml
from ..workspace import ensure_project
from .deps import Me, Svc
from .uploads import _project

router = APIRouter(tags=["projects"])

MAX_BACKGROUND = 20_000


def _meta(pdir) -> dict:
    f = pdir / "project.yaml"
    return read_yaml(f) if f.exists() else {}


def _out(workspace: str, project: str, meta: dict) -> dict:
    return {"workspace": workspace, "name": project, "exists": bool(meta), "owner": meta.get("owner"),
            "created_at": meta.get("created_at"), "background": meta.get("background") or "",
            "background_updated_by": meta.get("background_updated_by"),
            "background_updated_at": meta.get("background_updated_at")}


@router.get("/projects/{workspace}/{project}")
def get_project(workspace: str, project: str, svc: Svc, me: Me) -> dict:
    """The project, or an empty one when nobody has used the name yet (saving its background creates it)."""
    return _out(workspace, project, _meta(_project(svc, me, workspace, project)))


class BackgroundBody(BaseModel):
    text: str = Field(max_length=MAX_BACKGROUND)


@router.put("/projects/{workspace}/{project}/background")
def set_background(workspace: str, project: str, body: BackgroundBody, svc: Svc, me: Me) -> dict:
    """Any workspace member may write the background. Runs already asked keep the version they started with."""
    pdir = _project(svc, me, workspace, project)
    if not _meta(pdir):
        try:
            plants = svc.access.workspace_plants(workspace)
        except AccessDenied as e:
            raise HTTPException(404, str(e)) from e
        u = me.user
        readable = svc.access.all_plants if (u.is_admin or u.is_global) else u.plants
        ensure_project(svc.settings.workspace_root, workspace, project, owner=u.id, plants=sorted(plants & readable))
    meta = _meta(pdir)
    meta.update(background=body.text.strip(), background_updated_by=me.user.id, background_updated_at=now())
    write_yaml(pdir / "project.yaml", meta)
    svc.catalog.audit(me.user.id, "project.background", f"{workspace}/{project}", {"chars": len(meta["background"])})
    return _out(workspace, project, meta)
