"""The admin area: one catalog per plant (datasets, documents, reference facts, lines), plus "global", the shared
catalog. Catalog admins and admins only. See plant_catalog.py for where documents and facts are stored.

It also holds the model for each role (model_roles.py): catalog admins see them, admins change them."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import model_roles
from .. import plant_catalog as pc
from ..catalog import now
from ..knowledge import Knowledge
from ..llm import ProviderNotAllowed
from ..paths import as_path
from ..plugins import get_registry
from .deps import Svc, require_catalog_admin
from .identity import Principal

router = APIRouter(prefix="/admin", tags=["admin"])
Admin = Annotated[Principal, Depends(require_catalog_admin)]
GLOBAL = "global"
PLANT_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"


def _fresh_knowledge(svc) -> Knowledge:
    """Knowledge read now, not the copy services share for a minute: edits must see the latest versions."""
    return Knowledge(svc.settings.knowledge_root, packs=list(get_registry().knowledge_packs))


def _changed(request: Request) -> None:
    """Drop the shared knowledge index so the next request (and run) sees the edit."""
    invalidate = getattr(request.app.state.services_factory, "invalidate", None)
    if invalidate:
        invalidate()


def _plant_ids(svc) -> list[str]:
    return sorted(set(svc.access.all_plants) | {p["id"] for p in pc.plants(svc.catalog.db)})


def _known(svc, plant: str) -> None:
    if plant != GLOBAL and plant not in _plant_ids(svc):
        raise HTTPException(404, f"no plant {plant}")


def dataset_row(svc, d: dict) -> dict:
    approved, latest = svc.catalog.latest_card(d["id"]), svc.catalog.latest_card(d["id"], status=None)
    return {"dataset": d["id"], "name": d["name"], "plant": d["plant"], "s3_prefix": d["s3_prefix"],
            "file_glob": d["file_glob"], "file_format": d["file_format"], "metadata_uri": d["metadata_uri"],
            "owner": d["owner"] or "", "approved_version": approved and approved["version"],
            "latest_version": latest and latest["version"],
            "waiting_for_review": bool(latest and latest["status"] == "draft"),
            "missing_facts": (latest or {}).get("card", {}).get("missing", []),
            "checks": (latest or {}).get("checks", []),
            "no_metadata_file": not (d["metadata_uri"] and as_path(d["metadata_uri"]).exists()),
            "runs_using": None}


def _doc(d: dict) -> dict:
    return {"id": d["doc"], "title": d["title"], "type": d["type"], "plant": d["plant"], "lines": d["lines"],
            "version": int(d["version"]) if str(d["version"]).isdigit() else d["version"],
            "review_date": d["review_date"], "overdue": pc.overdue(d["review_date"]), "updated_at": d["updated_at"],
            "updated_by": d["updated_by"], "sections": d["sections"], "read_only": bool(d.get("pack"))}


def _fact(f: dict) -> dict:
    return {"name": f["name"], "plant": f["plant"], "description": f["description"],
            "version": int(f["version"]) if str(f["version"]).isdigit() else f["version"],
            "updated_at": f["updated_at"], "updated_by": f["updated_by"], "read_only": bool(f.get("pack"))}


def _documents(knowledge, plant: str | None = None) -> list[dict]:
    docs = [d for d in knowledge.documents.values() if d["type"] != pc.LINES_TYPE]
    return [_doc(d) for d in sorted(docs, key=lambda d: d["doc"]) if plant is None or d["plant"] == plant]


def _summary(svc, knowledge, plant: str, names: dict) -> dict:
    datasets = [dataset_row(svc, d) for d in svc.catalog.datasets() if d["plant"] == plant]
    docs = _documents(knowledge, plant)
    rec = names.get(plant, {})
    return {"id": plant, "name": rec.get("name") or ("All plants (shared)" if plant == GLOBAL else plant),
            "description": rec.get("description") or "",
            "in_access_model": plant == GLOBAL or plant in svc.access.all_plants,
            "readers_group": "" if plant == GLOBAL else
            getattr(svc.access, "identity", {}).get("plant_group", "alc-plant-{plant}").replace("{plant}", plant),
            "datasets": len(datasets), "datasets_waiting": sum(r["waiting_for_review"] for r in datasets),
            "datasets_unusable": sum(r["approved_version"] is None for r in datasets),
            "documents": len(docs), "documents_overdue": sum(d["overdue"] for d in docs),
            "facts": sum(f["plant"] == plant for f in knowledge.facts()),
            "lines": len(pc.lines(svc.catalog.db, plant))}


@router.get("/plants")
def list_plants(svc: Svc, me: Admin) -> list[dict]:
    names, knowledge = {p["id"]: p for p in pc.plants(svc.catalog.db)}, _fresh_knowledge(svc)
    return [_summary(svc, knowledge, p, names) for p in [GLOBAL, *_plant_ids(svc)]]


class PlantBody(BaseModel):
    id: str = Field(pattern=PLANT_ID)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


@router.post("/plants")
def save_plant(body: PlantBody, svc: Svc, me: Admin) -> dict:
    if body.id == GLOBAL:
        raise HTTPException(422, "global is the shared catalog, not a plant")
    pc.upsert_plant(svc.catalog.db, id=body.id, name=body.name, description=body.description, actor=me.user.id)
    svc.catalog.audit(me.user.id, "plant.save", body.id, body.model_dump())
    names = {p["id"]: p for p in pc.plants(svc.catalog.db)}
    return _summary(svc, _fresh_knowledge(svc), body.id, names)


@router.get("/plants/{plant}")
def plant_detail(plant: str, svc: Svc, me: Admin) -> dict:
    _known(svc, plant)
    names = {p["id"]: p for p in pc.plants(svc.catalog.db)}
    datasets = [dataset_row(svc, d) for d in svc.catalog.datasets() if d["plant"] == plant]
    ids, knowledge = {d["dataset"] for d in datasets}, _fresh_knowledge(svc)
    return {"plant": _summary(svc, knowledge, plant, names), "datasets": datasets,
            "documents": _documents(knowledge, plant),
            "facts": [_fact(f) for f in knowledge.facts() if f["plant"] == plant],
            "lines": pc.lines(svc.catalog.db, plant),
            "suggestions": [s for s in svc.catalog.suggestions() if s["dataset_id"] in ids]}


class Line(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=2000)


class LinesBody(BaseModel):
    lines: list[Line]


@router.put("/plants/{plant}/lines")
def save_lines(plant: str, body: LinesBody, request: Request, svc: Svc, me: Admin) -> list[dict]:
    _known(svc, plant)
    if len({ln.id for ln in body.lines}) != len(body.lines):
        raise HTTPException(422, "line ids must be unique")
    saved = pc.set_lines(svc.catalog.db, _fresh_knowledge(svc), svc.settings.knowledge_root, plant,
                         [ln.model_dump() for ln in body.lines], me.user.id)
    svc.catalog.audit(me.user.id, "plant.lines", plant, [ln["id"] for ln in saved])
    _changed(request)
    return saved


class PreviewBody(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


@router.post("/plants/{plant}/preview")
def preview(plant: str, body: PreviewBody, svc: Svc, me: Admin) -> dict:
    """What an agent working on a question scoped to this plant would find: the same searches its tools run."""
    _known(svc, plant)
    plants = set() if plant == GLOBAL else {plant}
    knowledge = _fresh_knowledge(svc)
    return {"datasets": [{"dataset": d["dataset"], "plant": d["plant"], "description": d["description"],
                          "version": d["card_version"]} for d in svc.catalog.search(body.question, plants, limit=8)],
            "documents": [{**h, "plant": knowledge.documents[h["doc"]]["plant"]}
                          for h in knowledge.search(body.question, plants, limit=8)],
            "facts": [{"name": f["name"], "plant": f["plant"], "version": f["version"]}
                      for f in knowledge.facts() if f["plant"] in plants | {GLOBAL}],
            "lines": pc.lines(svc.catalog.db, plant)}


# ---------------------------------------------------------------- documents and facts

class DocumentBody(BaseModel):
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    plant: str
    title: str = Field(min_length=1, max_length=200)
    type: str = "Other"
    lines: list[str] = []
    review_date: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    content: str = Field(min_length=1, max_length=2_000_000)


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, svc: Svc, me: Admin) -> dict:
    knowledge = _fresh_knowledge(svc)
    if doc_id not in knowledge.documents:
        raise HTTPException(404, f"no document {doc_id}")
    return {**_doc(knowledge.documents[doc_id]), "content": pc.read_document(knowledge, doc_id)}


@router.post("/documents")
def save_document(body: DocumentBody, request: Request, svc: Svc, me: Admin) -> dict:
    _known(svc, body.plant)
    if body.type not in pc.DOC_TYPES:
        raise HTTPException(422, f"type must be one of {', '.join(pc.DOC_TYPES)}")
    try:
        doc_id = pc.save_document(_fresh_knowledge(svc), svc.settings.knowledge_root, actor=me.user.id,
                                  **body.model_dump())
    except pc.ReadOnly as e:
        raise HTTPException(409, str(e)) from e
    svc.catalog.audit(me.user.id, "document.save", doc_id, {"plant": body.plant, "title": body.title})
    _changed(request)
    return _doc(_fresh_knowledge(svc).documents[doc_id])


class FactBody(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    plant: str
    description: str = Field(default="", max_length=2000)
    content: str = Field(max_length=1_000_000)


@router.get("/facts/{name}")
def get_fact(name: str, svc: Svc, me: Admin) -> dict:
    knowledge = _fresh_knowledge(svc)
    f = next((f for f in knowledge.facts() if f["name"] == name), None)
    if f is None:
        raise HTTPException(404, f"no fact table {name}")
    return {**_fact(f), "content": pc.read_fact(knowledge, name)}


@router.post("/facts")
def save_fact(body: FactBody, request: Request, svc: Svc, me: Admin) -> dict:
    _known(svc, body.plant)
    try:
        pc.save_fact(_fresh_knowledge(svc), svc.settings.knowledge_root, actor=me.user.id, **body.model_dump())
    except pc.ReadOnly as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    svc.catalog.audit(me.user.id, "fact.save", body.name, {"plant": body.plant})
    _changed(request)
    return _fact(next(f for f in _fresh_knowledge(svc).facts() if f["name"] == body.name))


def _models(request: Request, svc) -> dict:
    base = request.app.state.settings  # the deployment's configuration, before admins' choices
    return {"roles": [{**r, "default": model_roles.ROLES[r["role"]].default}
                      for r in model_roles.describe(base, svc.catalog.db)],
            "allowed_providers": base.allowed_providers}


@router.get("/models")
def get_models(request: Request, svc: Svc, me: Admin) -> dict:
    """The model each role uses now, and where it comes from: an admin's choice or the configuration."""
    return _models(request, svc)


class ModelsBody(BaseModel):
    # role -> `provider:model-id`, or null to go back to the deployment's configured model
    models: dict[str, str | None]


@router.put("/models")
def save_models(body: ModelsBody, request: Request, svc: Svc, me: Admin) -> dict:
    """Admins only. Runs started (or steps that start) after the change use the new models; each run's manifest
    records the models it used, so earlier runs and their replays are unaffected."""
    if not me.user.is_admin:
        raise HTTPException(403, "only admins can change the models")
    try:
        model_roles.save_choices(svc.catalog, request.app.state.settings, body.models, actor=me.user.id, now=now())
    except ProviderNotAllowed as e:
        raise HTTPException(422, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _models(request, svc)
