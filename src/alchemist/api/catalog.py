"""The data catalog: browse and search approved cards (filtered by plant); register, index and review (admins)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..catalog import index_dataset, summarise_card
from ..paths import as_path
from .deps import Me, Svc, require_catalog_admin
from .identity import Principal

router = APIRouter(tags=["catalog"])
Admin = Annotated[Principal, Depends(require_catalog_admin)]


def _readable(svc, me) -> set[str]:
    """Plants whose data the user may read: theirs, or all for global users and admins."""
    u = me.user
    return set(svc.access.all_plants if (u.is_global or u.is_admin) else u.plants)


@router.get("/datasets")
def datasets(svc: Svc, me: Me, q: str | None = None) -> list[dict]:
    """Approved datasets the user's plants may read; with `q`, ranked by relevance."""
    plants = _readable(svc, me)
    if q:
        return svc.catalog.search(q, plants, limit=50)
    return [summarise_card(d) for d in svc.catalog.queryable(plants)]


@router.get("/datasets/{dataset_id}")
def dataset(dataset_id: str, svc: Svc, me: Me) -> dict:
    """A dataset's registry entry and its approved card. Catalog admins also see every card version and checks."""
    try:
        ds = svc.catalog.dataset(dataset_id)
    except KeyError as e:
        raise HTTPException(404, f"no dataset {dataset_id}") from e
    plants = _readable(svc, me)
    if ds["plant"] != "global" and ds["plant"] not in plants and not me.is_catalog_admin:
        raise HTTPException(404, f"no dataset {dataset_id}")
    approved = svc.catalog.latest_card(dataset_id)
    out = {"dataset": ds, "approved": approved,
           "summary": summarise_card({"dataset": ds, "card": approved["card"], "version": approved["version"]})
           if approved else None}
    if me.is_catalog_admin:
        versions = []
        for c in svc.catalog.cards():
            if c["dataset_id"] == dataset_id:
                full = svc.catalog.db.execute("select card, checks from catalog_cards where dataset_id = ? and version = ?",
                                              (dataset_id, c["version"])).fetchone()
                versions.append({**c, "card": json.loads(full["card"]), "checks": json.loads(full["checks"] or "[]")})
        out["versions"] = versions
        out["suggestions"] = [s for s in svc.catalog.suggestions() if s["dataset_id"] == dataset_id]
    return out


class SuggestionBody(BaseModel):
    kind: Literal["description", "unit", "quirk", "column", "other"] = "other"
    text: str = Field(min_length=3, max_length=4000)


@router.post("/datasets/{dataset_id}/suggestions", status_code=201)
def suggest(dataset_id: str, body: SuggestionBody, svc: Svc, me: Me) -> dict:
    """Anyone who can read the dataset can suggest a fix to its card; admins review it."""
    dataset(dataset_id, svc, me)  # same visibility check
    sid = svc.catalog.suggest(dataset_id, me.user.id, body.kind, body.text)
    return {"id": sid}


# ------------------------------------------------------------------ admin

class RegisterBody(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    s3_prefix: str
    file_glob: str = "*.parquet"
    file_format: str = "parquet"  # parquet, csv, json, or a format a plugin registers
    plant: str
    metadata_uri: str | None = None
    owner: str = ""
    sensitivity: str = "internal"
    name: str | None = None


@router.post("/datasets", status_code=201)
def register(body: RegisterBody, svc: Svc, me: Admin) -> dict:
    try:
        svc.catalog.register(actor=me.user.id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return svc.catalog.dataset(body.id)


class IndexBody(BaseModel):
    use_model: bool = False
    force: bool = False


@router.post("/datasets/{dataset_id}:index")
def index(dataset_id: str, body: IndexBody, svc: Svc, me: Admin) -> dict:
    """Draft a new card version from the dataset's metadata file and a data sample."""
    try:
        return index_dataset(svc.catalog, svc.settings, dataset_id, use_model=body.use_model, force=body.force)
    except KeyError as e:
        raise HTTPException(404, f"no dataset {dataset_id}") from e


class ApproveCardBody(BaseModel):
    card: dict[str, Any] | None = None  # the edited card; omitted approves the draft as is


@router.post("/datasets/{dataset_id}/cards/{version}:approve")
def approve_card(dataset_id: str, version: int, body: ApproveCardBody, svc: Svc, me: Admin) -> dict:
    try:
        svc.catalog.approve(dataset_id, version, reviewer=me.user.id, card=body.card)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return svc.catalog.latest_card(dataset_id)


@router.post("/datasets/{dataset_id}/cards/{version}:reject")
def reject_card(dataset_id: str, version: int, svc: Svc, me: Admin) -> dict:
    svc.catalog.reject(dataset_id, version, reviewer=me.user.id)
    return {"dataset": dataset_id, "version": version, "status": "rejected"}


@router.get("/catalog/health")
def health(svc: Svc, me: Admin) -> list[dict]:
    """Per dataset: approved version, drafts waiting for review, facts the indexer could not find."""
    rows = []
    for d in svc.catalog.datasets():
        approved, latest = svc.catalog.latest_card(d["id"]), svc.catalog.latest_card(d["id"], status=None)
        rows.append({"dataset": d["id"], "plant": d["plant"], "approved_version": approved and approved["version"],
                     "latest_version": latest and latest["version"],
                     "waiting_for_review": bool(latest and latest["status"] == "draft"),
                     "missing_facts": (latest or {}).get("card", {}).get("missing", []),
                     "checks": (latest or {}).get("checks", []),
                     "no_metadata_file": not (d["metadata_uri"] and as_path(d["metadata_uri"]).exists())})
    return rows


@router.get("/catalog/suggestions")
def suggestions(svc: Svc, me: Admin) -> list[dict]:
    return svc.catalog.suggestions()
