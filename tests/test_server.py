import json
import os

import pytest
from fastapi.testclient import TestClient

from alchemist.api.app import create_app
from alchemist.api.identity import user_from_groups
from alchemist.api.jobs import LocalLauncher
from alchemist.demo import script


class InlineLauncher(LocalLauncher):
    """Runs each step before the request returns, so tests need no polling."""

    def _submit(self, run_id, fn):
        try:
            fn()
        except Exception as e:  # same handling as the thread pool
            from alchemist.api.jobs import mark_failed
            mark_failed(self.services(), run_id, e)


@pytest.fixture(scope="module")
def client(svc):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")
    os.environ["ALC_API_MODEL"] = "scripted"
    from alchemist.api.app import services_factory_for

    factory = services_factory_for(svc.settings)
    app = create_app(svc.settings, launcher=InlineLauncher(factory), services_factory=factory, web_dist="/nonexistent")
    with TestClient(app) as c:
        yield c
    os.environ.pop("ALC_API_MODEL", None)


def as_user(user):
    return {"X-Alchemist-User": user}


@pytest.fixture(scope="module")
def finished(client):
    r = client.post("/api/questions", headers=as_user("eng_p1"),
                    json={"workspace": "quality", "project": "line2-api", "question": script.QUESTION, "scope": script.SCOPE})
    assert r.status_code == 201, r.text
    rid = r.json()["run_id"]
    run = client.get(f"/api/runs/{rid}", headers=as_user("eng_p1")).json()
    assert run["run"]["status"] == "awaiting_approval"
    assert run["permissions"]["approve"] and run["plan"]["hypotheses"]
    r = client.post(f"/api/runs/{rid}/plan:approve", headers=as_user("eng_p1"), json={})
    assert r.status_code == 200, r.text
    return rid


def test_needs_a_user(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=as_user("nobody")).status_code == 401
    me = client.get("/api/me", headers=as_user("eng_p1")).json()
    assert me["readable_plants"] == ["P01"] and me["workspaces"] == ["quality"]


def test_run_page_shows_conclusion_data_and_methods(client, finished):
    run = client.get(f"/api/runs/{finished}", headers=as_user("eng_p1")).json()
    assert run["run"]["status"] == "completed" and run["run"]["sealed"]
    assert "R-118" in run["conclusion_html"]
    assert {f["id"] for f in run["findings"]} == {"F1", "F2", "F3", "F4", "F5"}
    assert run["queries"][0]["sql"].strip().startswith("select") and run["queries"][0]["purpose"]
    assert {d["dataset"] for d in run["datasets"]} >= {"quality_lots", "historian_line2"}
    assert run["knowledge"]["citations"] and run["review"]["ok"]
    assert run["has_report"] and run["has_notebook"] and run["figures"]


def test_transparency_endpoints(client, finished):
    h = as_user("eng_p1")
    nb = client.get(f"/api/runs/{finished}/notebook", headers=h).json()
    kinds = {c["meta"].get("kind") for c in nb["cells"]}
    assert {"query", "python", "finding", "check"} <= kinds
    assert any(o["type"] == "image" for c in nb["cells"] for o in c["outputs"])
    snap = client.get(f"/api/runs/{finished}/snapshots/q1", headers=h).json()
    assert snap["rows"] > 0 and "operator_name" not in [c["name"] for c in snap["columns"]]
    steps = client.get(f"/api/runs/{finished}/steps", headers=h).json()
    assert steps["planning"] and any(s.get("tool") == "build_report" for s in steps["analysis"])
    report = client.get(f"/api/runs/{finished}/files/report/report.html", headers=h)
    assert report.status_code == 200 and "default-src 'none'" in report.headers["content-security-policy"]
    assert client.get(f"/api/runs/{finished}/files/../../question.yaml", headers=h).status_code in (400, 404)
    assert client.get(f"/api/runs/{finished}/verify", headers=h).json()["ok"]
    z = client.get(f"/api/runs/{finished}/bundle", headers=h)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"


def test_events_stream_ends_with_final_status(client, finished):
    with client.stream("GET", f"/api/runs/{finished}/events", headers=as_user("eng_p1")) as r:
        body = "".join(r.iter_text())
    assert "event: status" in body and "event: step" in body
    assert body.strip().endswith('data: {"status": "completed"}')


def test_other_plant_sees_only_the_title(client, finished):
    h = as_user("eng_p2")
    run = client.get(f"/api/runs/{finished}", headers=h).json()
    assert run["entitled"] is False and "findings" not in run
    assert client.get(f"/api/runs/{finished}/notebook", headers=h).status_code == 403
    assert client.get(f"/api/runs/{finished}/files/findings.json", headers=h).status_code == 403
    assert client.post(f"/api/runs/{finished}:replay", headers=h, json={}).status_code == 403
    listed = {r["run_id"]: r for r in client.get("/api/runs", headers=h).json()}
    assert finished in listed and "plants" not in listed[finished]


def test_replay_and_verdict(client, finished):
    h = as_user("eng_p1")
    r = client.post(f"/api/runs/{finished}:replay", headers=h, json={"mode": "replay"})
    assert r.status_code == 201, r.text
    rep = client.get(f"/api/runs/{r.json()['run_id']}", headers=h).json()
    assert client.get(f"/api/runs/{finished}", headers=h).json()["question"]["status"] == "completed"  # not the replay's
    assert rep["run"]["status"] == "completed" and rep["result_check"]["status"]["mismatches"] == 0
    assert any(c["run_id"] == rep["run"]["run_id"] for c in client.get(f"/api/runs/{finished}", headers=h).json()["children"])
    v = client.post(f"/api/runs/{finished}/verdict", headers=h, json={"verdict": "confirmed", "note": "matches the lab"})
    assert v.status_code == 201 and v.json()["verdicts"][-1]["verdict"] == "confirmed"


def test_only_the_asker_approves(client):
    r = client.post("/api/questions", headers=as_user("eng_p1"),
                    json={"workspace": "quality", "project": "line2-api", "question": script.QUESTION, "scope": script.SCOPE})
    rid = r.json()["run_id"]
    assert client.post(f"/api/runs/{rid}/plan:approve", headers=as_user("eng_p2"), json={}).status_code == 403
    bad = dict(script.PLAN, hypotheses=[dict(script.PLAN["hypotheses"][0], datasets=["historian_line5"])])
    assert client.post(f"/api/runs/{rid}/plan:approve", headers=as_user("eng_p1"), json={"plan": bad}).status_code == 422
    assert client.post(f"/api/runs/{rid}:stop", headers=as_user("eng_p2")).status_code == 403
    r = client.post(f"/api/runs/{rid}:stop", headers=as_user("eng_p1"))  # waiting for approval: cancelled at once
    assert r.status_code == 200 and r.json()["result"] == "cancelled"
    run = client.get(f"/api/runs/{rid}", headers=as_user("eng_p1")).json()
    assert run["run"]["status"] == "cancelled" and run["closed"]["actor"] == "eng_p1"
    assert not run["permissions"]["stop"] and not run["permissions"]["approve"]
    assert client.post(f"/api/runs/{rid}/plan:approve", headers=as_user("eng_p1"), json={}).status_code == 409


def test_too_many_active_runs_is_429(client, svc):
    from alchemist.run import cancel, create

    held = create(svc, user="eng_p2", workspace="quality", project="line2-api", question="Holds a run slot",
                  scope={})["run_id"]
    before = svc.settings.max_active_runs
    svc.settings.max_active_runs = len(svc.catalog.active_runs())  # full
    try:
        r = client.post("/api/questions", headers=as_user("eng_p1"),
                        json={"workspace": "quality", "project": "line2-api", "question": script.QUESTION,
                              "scope": script.SCOPE})
        assert r.status_code == 429
    finally:
        svc.settings.max_active_runs = before
        cancel(svc, held, user="eng_p2")


def test_asking_outside_your_plants_is_refused(client):
    r = client.post("/api/questions", headers=as_user("eng_p1"),
                    json={"workspace": "quality", "project": "x", "question": "why so slow?", "scope": {"plants": ["P02"]}})
    assert r.status_code == 403


def test_catalog_is_filtered_by_plant_and_admin_only_for_changes(client):
    p1 = {d["dataset"] for d in client.get("/api/datasets", headers=as_user("eng_p1")).json()}
    assert "historian_line5" not in p1 and "quality_lots" in p1
    assert client.get("/api/datasets/historian_line5", headers=as_user("eng_p1")).status_code == 404
    hits = client.get("/api/datasets", params={"q": "resin melt flow"}, headers=as_user("eng_p1")).json()
    assert "resin_lots" in {h["dataset"] for h in hits}
    assert client.get("/api/catalog/health", headers=as_user("eng_p1")).status_code == 403
    health = client.get("/api/catalog/health", headers=as_user("data_admin")).json()
    assert {h["dataset"] for h in health} >= {"historian_line5", "quality_lots"}
    detail = client.get("/api/datasets/quality_lots", headers=as_user("data_admin")).json()
    assert detail["versions"] and detail["approved"]["status"] == "approved"
    s = client.post("/api/datasets/quality_lots/suggestions", headers=as_user("eng_p1"),
                    json={"kind": "unit", "text": "first_pass_yield_pct is a percentage of pieces, not of mass"})
    assert s.status_code == 201


def test_meta_lists_tools_from_the_registry(client):
    m = client.get("/api/meta", headers=as_user("eng_p1")).json()
    assert "run_query" in {t["name"] for t in m["tools"]["analysis"]} and m["models"]["scripted"]


def test_workspaces_and_similar_questions(client, finished):
    ws = client.get("/api/workspaces", headers=as_user("eng_p1")).json()
    assert ws[0]["name"] == "quality" and any(p["name"] == "line2-api" for p in ws[0]["projects"])
    sim = client.get("/api/questions/similar", params={"q": "quality low on Line 2"}, headers=as_user("eng_p1")).json()
    assert any(s["run_id"] == finished for s in sim)


def test_group_claims_map_to_plants_and_workspaces():
    u = user_from_groups("ana@example.com", ["alc-plant-p01", "alc-ws-quality", "staff"])
    assert u.plants == {"P01"} and u.workspaces == {"quality"} and not u.is_admin and not u.is_global


def test_alb_token_is_verified(monkeypatch):
    import base64
    import time

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    from alchemist.api.identity import NotSignedIn, verify_alb_jwt

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    arn = "arn:aws:elasticloadbalancing:eu-central-1:123:loadbalancer/app/alc/1"
    monkeypatch.setenv("ALC_ALB_ARN", arn)

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    def token(claims, signer=arn):
        h = b64(json.dumps({"alg": "ES256", "kid": "k1", "signer": signer}).encode())
        p = b64(json.dumps(claims).encode())
        r, s = decode_dss_signature(key.sign(f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256())))
        return f"{h}.{p}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"

    claims = {"email": "ana@example.com", "groups": ["alc-plant-p01"], "exp": time.time() + 60}
    assert verify_alb_jwt(token(claims), key_loader=lambda region, kid: pem)["email"] == "ana@example.com"
    with pytest.raises(NotSignedIn):
        verify_alb_jwt(token(claims, signer="arn:other"), key_loader=lambda region, kid: pem)
    with pytest.raises(NotSignedIn):
        verify_alb_jwt(token(claims)[:-4] + "AAAA", key_loader=lambda region, kid: pem)


def test_pause_and_resume_through_the_api(client, svc):
    from alchemist.agent import ScriptedModel
    from alchemist.run import ScriptedReviewer, analyse

    h = as_user("eng_p1")
    r = client.post("/api/questions", headers=h, json={"workspace": "quality", "project": "line2-api",
                                                       "question": "Pause and resume test", "scope": script.SCOPE})
    rid = r.json()["run_id"]
    assert client.post(f"/api/runs/{rid}:pause", headers=h).status_code == 409  # not analysing yet
    from alchemist.run import start_approval

    start_approval(svc, rid, user="eng_p1")
    assert client.get(f"/api/runs/{rid}", headers=h).json()["permissions"]["pause"]
    assert client.post(f"/api/runs/{rid}:pause", headers=as_user("eng_p2")).status_code in (403, 404)
    assert client.post(f"/api/runs/{rid}:pause", headers=h).status_code == 200
    analyse(svc, rid, user="eng_p1", model=ScriptedModel(script.ANALYSIS_TURNS),
            reviewer=ScriptedReviewer(script.REVIEW), should_stop=lambda: "pause" if svc.catalog.run(rid) else None)
    run = client.get(f"/api/runs/{rid}", headers=h).json()
    assert run["run"]["status"] == "paused" and run["permissions"]["resume"] and run["permissions"]["stop"]
    svc.catalog.transition(rid, "paused", "running")  # stands in for a resume
    calls = []
    analyse(svc, rid, user="eng_p1", model=ScriptedModel(script.ANALYSIS_TURNS),
            reviewer=ScriptedReviewer(script.REVIEW),
            should_stop=lambda: calls.append(1) or ("pause" if len(calls) > 4 else None))
    assert client.get(f"/api/runs/{rid}", headers=h).json()["run"]["status"] == "paused"
    with client.stream("GET", f"/api/runs/{rid}/events", headers=h) as s:
        assert '"status": "paused"' in "".join(s.iter_text())  # the live view ends on a pause
    r = client.post(f"/api/runs/{rid}:resume", headers=h)  # the inline launcher finishes it here
    assert r.status_code == 200, r.text
    assert client.get(f"/api/runs/{rid}", headers=h).json()["run"]["status"] == "completed"
