"""A project's background (read by every question in it) and the profile page's view of sign-in groups."""

import json
import os

import pytest

from alchemist.access import AccessModel
from alchemist.agent import ScriptedModel
from alchemist.paths import as_path, read_yaml
from alchemist.run import ask

PLAN = {"metric": "Viscosity", "scope": {"plants": ["P01"]},
        "hypotheses": [{"id": "H1", "cause": "Drift", "method": "Mean", "datasets": ["quality_lots"]}]}


@pytest.fixture(scope="module")
def client(svc):
    from fastapi.testclient import TestClient

    from alchemist.api.app import create_app, services_factory_for
    from alchemist.api.jobs import LocalLauncher

    os.environ["ALC_API_MODEL"] = "scripted"
    factory = services_factory_for(svc.settings)
    app = create_app(svc.settings, launcher=LocalLauncher(factory), services_factory=factory, web_dist="/nonexistent")
    with TestClient(app) as c:
        yield c
    os.environ.pop("ALC_API_MODEL", None)


def H(user):
    return {"X-Alchemist-User": user}


def test_background_is_saved_and_read_by_new_questions(client, svc):
    url = "/api/projects/quality/bg-test"
    got = client.get(url, headers=H("eng_p1")).json()
    assert got["exists"] is False and got["background"] == ""
    r = client.put(f"{url}/background", headers=H("eng_p1"),
                   json={"text": "  Line 2 extrudes PP. Yield below 94 % is abnormal.  "})
    assert r.status_code == 200, r.text
    assert r.json()["background"] == "Line 2 extrudes PP. Yield below 94 % is abnormal."
    assert r.json()["background_updated_by"] == "eng_p1"
    # Another member of the workspace sees it (and the project now exists in the workspace list).
    assert client.get(url, headers=H("eng_p2")).json()["exists"] is True
    projects = {p["name"] for w in client.get("/api/workspaces", headers=H("eng_p1")).json() for p in w["projects"]}
    assert "bg-test" in projects

    out = ask(svc, user="eng_p1", workspace="quality", project="bg-test", question="Why was yield low?",
              scope={"plants": ["P01"]}, model=ScriptedModel([[("submit_plan", PLAN)]]))
    qdir = as_path(out["path"]).parent.parent
    assert read_yaml(qdir / "question.yaml")["project_background"].startswith("Line 2 extrudes PP")
    first = json.loads((as_path(out["path"]) / "logs" / "plan_transcript.jsonl").read_text().splitlines()[0])
    assert "Yield below 94 % is abnormal" in json.dumps(first)

    # A later edit does not change what the asked question saw.
    client.put(f"{url}/background", headers=H("eng_p1"), json={"text": "Changed."})
    assert read_yaml(qdir / "question.yaml")["project_background"].startswith("Line 2 extrudes PP")


def test_background_needs_workspace_membership(client):
    r = client.put("/api/projects/maint/x/background", headers=H("eng_p1"), json={"text": "hi"})
    assert r.status_code in (403, 404)
    assert client.get("/api/projects/quality/bad%20name", headers=H("eng_p1")).status_code == 422


def test_me_has_sign_in_details(client):
    me = client.get("/api/me", headers=H("eng_p1")).json()
    assert me["auth"] == "dev" and me["groups"] == []
    assert me["group_names"]["plant_group"] == "alc-plant-{plant}"


def test_groups_explained_for_the_profile():
    m = AccessModel({"plants": ["P01"], "workspaces": {"quality": {"plants": ["P01"]}},
                     "identity": {"group_aliases": {"guid-1": "alc-plant-P01"}}})
    got = {g["group"]: g for g in m.explain_groups(
        {"groups": ["guid-1", "alc-ws-quality", "alc-plant-P09", "alc-admin", "Everyone"]})}
    assert got["alc-plant-P01"] == {"group": "alc-plant-P01", "id": "guid-1", "means": "Read data from plant P01",
                                    "used": True}
    assert got["alc-ws-quality"]["means"] == "Member of workspace quality"
    assert got["alc-plant-P09"]["used"] is False
    assert got["alc-admin"]["used"] is True
    assert got["Everyone"] == {"group": "Everyone", "means": "Not used by Alchemist", "used": False}


def test_principal_from_alb_claims_carries_groups(svc):
    from alchemist.api.identity import principal_from_claims

    p = principal_from_claims(svc, {"email": "kim@plant.example", "name": "Kim Lee",
                                    "groups": ["alc-plant-P01", "alc-ws-quality", "Everyone"]})
    assert p.name == "Kim Lee" and p.email == "kim@plant.example"
    assert [g["group"] for g in p.groups if g["used"]] == ["alc-plant-P01", "alc-ws-quality"]
