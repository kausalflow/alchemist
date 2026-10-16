"""The admin area: per-plant catalogs of datasets, documents, reference facts and lines."""

import dataclasses
import shutil

import pytest
from fastapi.testclient import TestClient

from alchemist.api.app import create_app, services_factory_for
from alchemist.api.jobs import LocalLauncher
from alchemist.knowledge import Knowledge

ADMIN = {"X-Alchemist-User": "data_admin"}
ENGINEER = {"X-Alchemist-User": "eng_p1"}


@pytest.fixture(scope="module")
def admin(svc, tmp_path_factory):
    # Its own copy of the knowledge folder, so edits here do not change what other tests' runs cite.
    root = tmp_path_factory.mktemp("knowledge")
    shutil.copytree(svc.settings.knowledge_root, root, dirs_exist_ok=True)
    settings = dataclasses.replace(svc.settings, knowledge_root=str(root))
    factory = services_factory_for(settings)
    app = create_app(settings, launcher=LocalLauncher(factory), services_factory=factory, web_dist="/nonexistent")
    with TestClient(app) as c:
        c.knowledge_root = root
        yield c


def test_admins_only(admin):
    assert admin.get("/api/admin/plants", headers=ENGINEER).status_code == 403
    assert admin.post("/api/admin/plants/P01/preview", headers=ENGINEER, json={"question": "yield"}).status_code == 403


def test_plants_list_global_first_with_counts(admin):
    plants = admin.get("/api/admin/plants", headers=ADMIN).json()
    assert [p["id"] for p in plants][:3] == ["global", "P01", "P02"]
    p01 = next(p for p in plants if p["id"] == "P01")
    assert p01["in_access_model"] and p01["readers_group"] == "alc-plant-P01"
    assert p01["datasets"] == 3 and p01["facts"] >= 1


def test_add_a_plant(admin):
    r = admin.post("/api/admin/plants", headers=ADMIN, json={"id": "P09", "name": "Batch plant", "description": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Batch plant" and not r.json()["in_access_model"]
    assert admin.post("/api/admin/plants", headers=ADMIN, json={"id": "global", "name": "x"}).status_code == 422
    assert admin.get("/api/admin/plants/P77", headers=ADMIN).status_code == 404


def test_documents_are_versioned_and_searchable(admin):
    body = {"plant": "P01", "title": "SOP-200 Die cleaning", "type": "SOP", "lines": ["L2"], "review_date": "2020-01-01",
            "content": "# Die cleaning\nClean the die lips every 8 hours with brass tools to prevent die lines."}
    r = admin.post("/api/admin/documents", headers=ADMIN, json=body)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["id"] == "SOP-200-DIE-CLEANING" and doc["version"] == 1 and doc["overdue"] and doc["sections"] == 1

    r = admin.post("/api/admin/documents", headers=ADMIN, json={**body, "id": doc["id"], "content": "# Die cleaning\nEvery 6 hours."})
    assert r.json()["version"] == 2
    got = admin.get(f"/api/admin/documents/{doc['id']}", headers=ADMIN).json()
    assert "Every 6 hours" in got["content"] and got["lines"] == ["L2"]
    assert (admin.knowledge_root / ".history" / "documents" / doc["id"] / "v1.md").exists()
    # the earlier version is kept out of search
    assert not any("brass" in d["text"] for d in Knowledge(admin.knowledge_root).search("die lips brass", ["P01"]))

    detail = admin.get("/api/admin/plants/P01", headers=ADMIN).json()
    assert doc["id"] in {d["id"] for d in detail["documents"]}
    found = admin.post("/api/admin/plants/P01/preview", headers=ADMIN, json={"question": "die cleaning hours"}).json()
    assert found["documents"][0]["doc"] == doc["id"]
    # a P02 question does not see P01 documents
    other = admin.post("/api/admin/plants/P02/preview", headers=ADMIN, json={"question": "die cleaning hours"}).json()
    assert doc["id"] not in {d["doc"] for d in other["documents"]}


def test_facts(admin):
    r = admin.post("/api/admin/facts", headers=ADMIN,
                   json={"name": "die_limits", "plant": "P01", "description": "Die gap limits",
                         "content": "gap_mm: [1.2, 1.6]\nplant: P02\n"})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.json()["plant"] == "P01"
    got = admin.get("/api/admin/facts/die_limits", headers=ADMIN).json()
    assert "gap_mm" in got["content"] and "plant" not in got["content"]
    assert admin.post("/api/admin/facts", headers=ADMIN,
                      json={"name": "bad", "plant": "P01", "content": "- a list"}).status_code == 422
    r = admin.post("/api/admin/facts", headers=ADMIN, json={"name": "die_limits", "plant": "P01", "content": "gap_mm: 1"})
    assert r.json()["version"] == 2


def test_lines_become_searchable(admin):
    r = admin.put("/api/admin/plants/P01/lines", headers=ADMIN,
                  json={"lines": [{"id": "L2", "name": "Extrusion line 2", "description": "Makes pipe from PE resin."}]})
    assert r.status_code == 200, r.text
    found = admin.post("/api/admin/plants/P01/preview", headers=ADMIN, json={"question": "which line makes pipe"}).json()
    assert found["lines"] == r.json()
    assert any(d["doc"] == "LINES-P01" for d in found["documents"])
    # the generated lines document is not listed among the plant's documents
    assert "LINES-P01" not in {d["id"] for d in admin.get("/api/admin/plants/P01", headers=ADMIN).json()["documents"]}


def test_models_per_role_and_admin_choices(admin):
    from alchemist.run import Services

    assert admin.get("/api/admin/models", headers=ENGINEER).status_code == 403
    got = admin.get("/api/admin/models", headers=ADMIN).json()
    assert [r["role"] for r in got["roles"]] == ["planning", "analysis", "reviewer", "small"]
    assert all(r["source"] == "configuration" for r in got["roles"])

    pick = "bedrock:anthropic.claude-haiku-5-5"
    r = admin.put("/api/admin/models", headers=ADMIN, json={"models": {"analysis": pick}})
    assert r.status_code == 200, r.text
    analysis = next(x for x in r.json()["roles"] if x["role"] == "analysis")
    assert analysis["spec"] == pick and analysis["source"] == "admin" and analysis["updated_by"] == "data_admin"
    assert admin.get("/api/meta", headers=ADMIN).json()["models"]["roles"]["analysis"] == pick

    # runs (and workers) pick the choice up from the database
    settings = admin.app.state.settings
    svc = Services(settings)
    try:
        assert svc.settings.analysis_model == pick and svc.settings.planning_model == settings.planning_model
        audit = svc.catalog.db.execute("select actor, detail from audit_events where action = 'models.update'"
                                       " order by id desc limit 1").fetchone()
        assert audit["actor"] == "data_admin" and pick in audit["detail"]
    finally:
        svc.catalog.db.close()

    # only allowed providers, known roles, and admins
    bad = admin.put("/api/admin/models", headers=ADMIN, json={"models": {"reviewer": "openai:gpt-5"}})
    assert bad.status_code == 422 and "ALC_ALLOWED_PROVIDERS" in bad.text
    assert admin.put("/api/admin/models", headers=ADMIN, json={"models": {"writer": pick}}).status_code == 422
    assert admin.put("/api/admin/models", headers=ENGINEER, json={"models": {"analysis": None}}).status_code == 403

    r = admin.put("/api/admin/models", headers=ADMIN, json={"models": {"analysis": None}})
    analysis = next(x for x in r.json()["roles"] if x["role"] == "analysis")
    assert analysis["source"] == "configuration" and analysis["spec"] == settings.analysis_model


def test_a_saved_choice_for_a_provider_no_longer_allowed_is_ignored(admin):
    from alchemist import model_roles

    settings = admin.app.state.settings
    svc = admin.app.state.services_factory()
    db = svc.catalog.db
    try:
        db.execute("insert into model_choices(role, spec, updated_by, updated_at) values (?, ?, ?, ?)",
                   ("small", "openai:gpt-5", "someone", "2026-10-16T00:00:00Z"))
        db.commit()
        assert model_roles.with_choices(settings, db).small_model == settings.small_model
        small = next(r for r in model_roles.describe(settings, db) if r["role"] == "small")
        assert small["source"] == "configuration" and small["ignored"]["spec"] == "openai:gpt-5"
    finally:
        db.execute("delete from model_choices")
        db.commit()
        db.close()
