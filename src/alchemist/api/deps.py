"""Per-request services and the signed-in principal."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from ..access import AccessDenied
from ..run import Services
from .identity import NotSignedIn, Principal, resolve


def services(request: Request):
    # A fresh Services per request; on PostgreSQL its connection comes from a shared pool and goes back on close.
    svc = request.app.state.services_factory()
    try:
        yield svc
    finally:
        svc.catalog.db.close()


Svc = Annotated[Services, Depends(services)]


def principal(request: Request, svc: Svc) -> Principal:
    try:
        return resolve(svc, request.headers, request.cookies)
    except NotSignedIn as e:
        raise HTTPException(401, str(e)) from e


Me = Annotated[Principal, Depends(principal)]


def require_catalog_admin(me: Me) -> Principal:
    if not me.is_catalog_admin:
        raise HTTPException(403, "catalog admins only")
    return me


def run_or_404(svc, run_id: str) -> dict:
    try:
        return svc.catalog.run(run_id)
    except KeyError as e:
        raise HTTPException(404, f"no run {run_id}") from e


def entitled(svc, me: Principal, row: dict) -> bool:
    """A workspace member entitled to every plant the run used (or an admin) sees the run's contents."""
    return svc.access.can_view_run(me.user, row["workspace"], json.loads(row["plants"] or "[]"))


def in_workspace(me: Principal, workspace: str) -> bool:
    return me.user.is_admin or workspace in me.user.workspaces


def entitled_run(svc, me: Principal, run_id: str) -> dict:
    row = run_or_404(svc, run_id)
    if not entitled(svc, me, row):
        raise HTTPException(403, "you may not open this run: it used plants you are not entitled to")
    return row


def access_error(e: AccessDenied) -> HTTPException:
    return HTTPException(403, str(e))
