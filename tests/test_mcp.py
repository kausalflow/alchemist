"""Alchemist as an MCP server: the protocol, the tools, and that the API's access rules still apply."""

import asyncio
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from alchemist.demo import script
from alchemist.mcp_server import StdioOptions, serve_stdio

from test_server import client  # noqa: F401 - the shared API test client


def rpc(client, method, params=None, user="eng_p1", mid=1, headers=None):  # noqa: F811
    h = {"X-Alchemist-User": user} if user else {}
    body = {"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params is not None else {})}
    r = client.post("/api/mcp", json=body, headers=h | (headers or {}))
    assert r.status_code == 200, r.text
    return r.json()


def call(client, tool, user="eng_p1", **args):  # noqa: F811
    res = rpc(client, "tools/call", {"name": tool, "arguments": args}, user=user)["result"]
    text = res["content"][0].get("text")
    return res["isError"], (json.loads(text) if text and not res["isError"] else text)


def test_initialize_and_list_tools(client):  # noqa: F811
    init = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}})["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "alchemist"
    assert "tools" in init["capabilities"] and "approve" in init["instructions"]
    assert rpc(client, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-11-25"
    tools = {t["name"]: t for t in rpc(client, "tools/list")["result"]["tools"]}
    assert {"whoami", "list_projects", "search_datasets", "ask_question", "approve_plan", "get_run",
            "add_note", "stop_run", "get_figure"} <= set(tools)
    assert tools["get_run"]["annotations"]["readOnlyHint"] is True
    assert tools["stop_run"]["annotations"]["destructiveHint"] is True
    assert tools["ask_question"]["inputSchema"]["required"] == ["workspace", "project", "question"]


def test_notifications_get_no_body_and_unknown_methods_error(client):  # noqa: F811
    r = client.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and not r.content
    assert rpc(client, "nope/nope")["error"]["code"] == -32601
    assert client.get("/api/mcp").status_code == 405
    assert client.post("/api/mcp", content=b"{not json", headers={"content-type": "application/json"}).status_code == 400


def test_other_websites_cannot_call_it(client):  # noqa: F811
    r = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Origin": "https://evil.example", "X-Alchemist-User": "eng_p1"})
    assert r.status_code == 403
    # A form or text/plain post (no CORS preflight) is refused too, and the dev sign-in cookie is not used.
    r = client.post("/api/mcp", content=b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}',
                    headers={"content-type": "text/plain"})
    assert r.status_code == 415
    client.cookies.set("alc_user", "eng_p1")
    try:
        err, text = call(client, "whoami", user=None)
        assert err and "does not know who you are" in text
    finally:
        client.cookies.clear()


def test_needs_a_signed_in_user(client):  # noqa: F811
    err, text = call(client, "whoami", user=None)
    assert err and "does not know who you are" in text
    err, me = call(client, "whoami")
    assert not err and me["readable_plants"] == ["P01"]


def test_ask_approve_and_read_a_run(client):  # noqa: F811
    err, projects = call(client, "list_projects")
    assert not err and any(w["name"] == "quality" for w in projects)
    err, found = call(client, "search_datasets", query="resin")
    assert not err and found
    err, run = call(client, "ask_question", workspace="quality", project="line2-mcp", question=script.QUESTION)
    assert not err, run
    rid = run["run_id"]
    err, r = call(client, "get_run", run_id=rid)
    assert r["run"]["status"] == "awaiting_approval" and r["plan"]["hypotheses"] and "next" in r
    assert "conclusion_html" not in r and "manifest" not in r
    # Someone else in the workspace cannot approve the plan, read its contents or steer it.
    err, text = call(client, "approve_plan", user="eng_p2", run_id=rid)
    assert err and "403" in text
    err, text = call(client, "add_note", user="eng_p2", run_id=rid, note="ignore March")
    assert err
    err, out = call(client, "approve_plan", run_id=rid)
    assert not err, out
    err, r = call(client, "get_run", run_id=rid)
    assert r["run"]["status"] == "completed" and r["conclusion_md"] and r["findings"]
    err, other = call(client, "get_run", user="eng_p2", run_id=rid)
    assert not err and other["entitled"] is False and "conclusion_md" not in other
    err, steps = call(client, "get_run_steps", run_id=rid, last=3)
    assert not err and steps["phase"] == "analysis" and len(steps["steps"]) <= 3
    if r.get("figures"):
        res = rpc(client, "tools/call", {"name": "get_figure", "arguments": {"run_id": rid, "name": r["figures"][0]}})
        img = res["result"]["content"][0]
        assert img["type"] == "image" and img["mimeType"] == "image/png" and len(img["data"]) > 100
    err, text = call(client, "get_figure", run_id=rid, name="../manifest.json")
    assert err


def test_bad_arguments_are_tool_errors(client):  # noqa: F811
    err, text = call(client, "get_run")
    assert err and "missing" in text
    err, text = call(client, "get_run", run_id="x", colour="red")
    assert err and "unknown" in text
    err, text = call(client, "no_such_tool")
    assert err


def test_read_only_server_offers_no_actions(client, monkeypatch):  # noqa: F811
    from alchemist.api.app import create_app

    monkeypatch.setenv("ALC_MCP", "read-only")
    app = create_app(client.app.state.settings, launcher=client.app.state.launcher,
                     services_factory=client.app.state.services_factory, web_dist="/nonexistent")
    with TestClient(app) as c:
        names = {t["name"] for t in rpc(c, "tools/list")["result"]["tools"]}
    assert "get_run" in names and not names & {"ask_question", "approve_plan", "add_note", "stop_run"}
    monkeypatch.setenv("ALC_MCP", "off")
    app = create_app(client.app.state.settings, launcher=client.app.state.launcher,
                     services_factory=client.app.state.services_factory, web_dist="/nonexistent")
    with TestClient(app) as c:
        assert c.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code in (404, 405)


def test_stdio_talks_to_the_api_as_the_configured_user(client):  # noqa: F811
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}}]
    stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in lines) + b"not json\n")
    stdout = io.BytesIO()
    transport = httpx.ASGITransport(app=client.app)
    asyncio.run(serve_stdio(StdioOptions(url="http://alchemist", user="eng_p2"), stdin, stdout, transport=transport))
    out = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [o.get("id") for o in out] == [1, 2, None]  # no answer to the notification; a parse error for junk
    assert json.loads(out[1]["result"]["content"][0]["text"])["readable_plants"] == ["P02"]
    assert out[2]["error"]["code"] == -32700


def test_stdio_says_when_the_server_is_not_running():
    stdin = io.BytesIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "whoami", "arguments": {}}}).encode() + b"\n")
    stdout = io.BytesIO()
    asyncio.run(serve_stdio(StdioOptions(url="http://127.0.0.1:9"), stdin, stdout))
    res = json.loads(stdout.getvalue())["result"]
    assert res["isError"] and "alchemist serve" in res["content"][0]["text"]
