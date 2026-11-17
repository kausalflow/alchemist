"""Alchemist as an MCP server, so Claude, GitHub Copilot, Cursor and other AI assistants can use it.

The Model Context Protocol (https://modelcontextprotocol.io) is the open standard these assistants use to call
outside tools. Two ways in, one set of tools:

    alchemist mcp [--url http://127.0.0.1:8000] [--user eng_p1 | --token <id token>] [--read-only]
        stdio, for an assistant on the same computer (Claude Desktop, Claude Code, VS Code). It talks to a running
        Alchemist server (`alchemist serve`) over its REST API.

    POST /api/mcp  on the Alchemist server itself (streamable HTTP, JSON responses)
        for assistants that connect by URL. The caller signs in exactly as for the API (dev user header, OIDC bearer
        token, or the load balancer's sign-in).

Every tool is a call to the REST API as the signed-in user, so the API's rules apply unchanged: plant groups,
workspaces, run entitlement, never_to_model columns, pseudonyms, the human plan-approval gate's permissions. The
server adds no rights of its own. What a tool returns goes to the assistant's model, which may be outside the
company: see docs/guides/ai-assistants.md.

The protocol layer is small and has no dependencies (tools only: initialize, ping, tools/list, tools/call).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import __version__

log = logging.getLogger("alchemist.mcp")

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_TEXT = 60_000  # characters of tool output per call; assistants have limited context

INSTRUCTIONS = """\
Alchemist answers process and chemical engineering questions from plant data: an AI planner proposes a plan, a person
approves it, then an analyst agent queries the data, runs Python and writes a conclusion with findings and a report.

Typical use: list_projects -> search_datasets or get_project -> ask_question (returns a run in planning) ->
get_run until status is awaiting_approval -> show the plan to the person -> approve_plan ONLY when the person says so
-> get_run until completed -> read conclusion and findings. Use add_note to steer a running analysis, and
ask_question with follow_up_of to ask more about a finished run. Runs take minutes; do not poll more than every 30
seconds. Results contain company plant data: do not send them anywhere else."""


class ToolError(Exception):
    """A tool failed in a way the assistant should read (shown as isError content, not a protocol error)."""


@dataclass
class Tool:
    name: str
    title: str
    description: str
    params: dict[str, dict]
    required: list[str]
    call: Callable[..., Awaitable[Any]]
    read_only: bool = True
    destructive: bool = False

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": {"type": "object", "properties": self.params, "required": self.required,
                                "additionalProperties": False},
                "annotations": {"title": self.title, "readOnlyHint": self.read_only,
                                "destructiveHint": self.destructive, "idempotentHint": self.read_only,
                                "openWorldHint": False}}


def _s(description: str, **extra) -> dict:
    return {"type": "string", "description": description, **extra}


def _i(description: str, **extra) -> dict:
    return {"type": "integer", "description": description, **extra}


def _q(part: str) -> str:
    return quote(str(part), safe="")


class Alchemist:
    """The tools, over an httpx client pointed at an Alchemist server (or at the app in-process)."""

    def __init__(self, client: httpx.AsyncClient, *, read_only: bool = False, where: str = "the Alchemist server"):
        self.client = client
        self.where = where
        self.tools: dict[str, Tool] = {}
        self._define(read_only)

    # ------------------------------------------------------------------ HTTP

    async def _api(self, method: str, path: str, **kw) -> Any:
        try:
            r = await self.client.request(method, "/api" + path, **kw)
        except httpx.HTTPError as e:
            raise ToolError(f"Could not reach {self.where} ({type(e).__name__}). Is `alchemist serve` running, "
                            "and is the address right?") from e
        if r.status_code == 401:
            raise ToolError("Alchemist does not know who you are: " + _detail(r) + ". Set the user or token in the "
                            "assistant's MCP settings (see the 'AI assistants' guide).")
        if r.status_code >= 400:
            raise ToolError(f"Alchemist said no ({r.status_code}): {_detail(r)}")
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r

    # ------------------------------------------------------------------ tools

    def _define(self, read_only: bool) -> None:
        def tool(name, title, description, params=None, required=(), *, writes=False, destructive=False):
            def deco(fn):
                if writes and read_only:
                    return fn
                self.tools[name] = Tool(name, title, description, params or {}, list(required), fn,
                                        read_only=not writes, destructive=destructive)
                return fn
            return deco

        @tool("whoami", "Who am I in Alchemist",
              "The signed-in user: plants whose data they may read, workspaces, and roles.")
        async def whoami():
            me = await self._api("GET", "/me")
            return {k: me.get(k) for k in ("id", "name", "plants", "readable_plants", "workspaces", "global", "admin")}

        @tool("list_projects", "List workspaces and projects",
              "Workspaces the user belongs to, the plants each covers, and their projects. Questions are asked "
              "inside a workspace and project.")
        async def list_projects():
            return await self._api("GET", "/workspaces")

        @tool("get_project", "Read a project",
              "A project's background (what the team told Alchemist about the process), its uploaded data files "
              "(their ids can be passed to ask_question) and its background documents.",
              {"workspace": _s("Workspace name"), "project": _s("Project name")}, ["workspace", "project"])
        async def get_project(workspace, project):
            base = f"/projects/{_q(workspace)}/{_q(project)}"
            out = await self._api("GET", base)
            out["data_files"] = [{k: u.get(k) for k in ("id", "file", "uploaded_by", "uploaded_at", "plant",
                                                        "description", "tables") if k in u}
                                 for u in await self._api("GET", base + "/uploads")]
            out["documents"] = await self._api("GET", base + "/documents")
            return out

        @tool("search_datasets", "Search the data catalog",
              "Approved datasets the user's plants may read (process, quality, line setup data), with descriptions "
              "and columns. Without a query, lists them all.",
              {"query": _s("Words to search for, e.g. 'extruder melt temperature'")})
        async def search_datasets(query=None):
            return await self._api("GET", "/datasets", params={"q": query} if query else None)

        @tool("get_dataset", "Read a dataset's catalog card",
              "One dataset's catalog card: description, columns with units, quirks, time range.",
              {"dataset_id": _s("Dataset id from search_datasets")}, ["dataset_id"])
        async def get_dataset(dataset_id):
            d = await self._api("GET", f"/datasets/{_q(dataset_id)}")
            return {k: d.get(k) for k in ("dataset", "approved", "summary")}

        @tool("find_similar_questions", "Find past questions",
              "Earlier questions with similar words that the user may open, so an existing answer can be reused.",
              {"question": _s("The question or its key words")}, ["question"])
        async def find_similar_questions(question):
            return await self._api("GET", "/questions/similar", params={"q": question})

        @tool("list_runs", "List runs",
              "Runs (one per question, plus replays) the user can see, newest first, with status.",
              {"workspace": _s("Only this workspace"), "project": _s("Only this project"),
               "limit": _i("At most this many (default 20)", minimum=1, maximum=200)})
        async def list_runs(workspace=None, project=None, limit=20):
            params = {k: v for k, v in (("workspace", workspace), ("project", project)) if v}
            return (await self._api("GET", "/runs", params=params))[:limit]

        @tool("get_run", "Read a run",
              "A run's status, plan, conclusion, findings, notes and what the user may do next (permissions). "
              "Statuses: planning -> awaiting_approval -> running (or paused) -> completed / partial / failed / "
              "cancelled.",
              {"run_id": _s("Run id")}, ["run_id"])
        async def get_run(run_id):
            return _compact_run(await self._api("GET", f"/runs/{_q(run_id)}"))

        @tool("get_run_steps", "Follow a run's steps",
              "The latest steps of a run's analysis (or planning): what the agent said and which tools it ran, "
              "trimmed. Use it to tell the person what the analysis is doing.",
              {"run_id": _s("Run id"), "last": _i("How many of the latest steps (default 10)", minimum=1, maximum=50)},
              ["run_id"])
        async def get_run_steps(run_id, last=10):
            s = await self._api("GET", f"/runs/{_q(run_id)}/steps")
            phase = "analysis" if s.get("analysis") else "planning"
            steps = s.get(phase, [])
            for st in steps:
                for k in ("output", "text"):
                    if isinstance(st.get(k), str) and len(st[k]) > 800:
                        st[k] = st[k][:800] + " ..."
            return {"phase": phase, "total_steps": len(steps), "steps": steps[-last:]}

        @tool("get_figure", "Show a figure from a run",
              "One chart from a run, as an image. get_run lists the figure names.",
              {"run_id": _s("Run id"), "name": _s("Figure file name, e.g. fig_01.png")}, ["run_id", "name"])
        async def get_figure(run_id, name):
            if "/" in name or "\\" in name or not name.lower().endswith(".png"):
                raise ToolError("name must be a .png file name from get_run's figures")
            r = await self._api("GET", f"/runs/{_q(run_id)}/files/figures/{_q(name)}")
            if isinstance(r, httpx.Response) and r.status_code in (301, 302, 303, 307, 308):  # S3 presigned URL
                async with httpx.AsyncClient(timeout=30) as plain:
                    r = await plain.get(r.headers["location"])
                    r.raise_for_status()
            return Image(r.content, "image/png")

        @tool("ask_question", "Ask Alchemist a question",
              "Ask a process or quality question. Alchemist makes a plan, which waits for the person's approval "
              "(the run's status becomes awaiting_approval); nothing reads plant data before that. Returns the run.",
              {"workspace": _s("Workspace name (from list_projects)"),
               "project": _s("Project name; a new name starts a new project"),
               "question": _s("The question in plain words, with the line, product and period if known"),
               "data_files": {"type": "array", "items": {"type": "string"},
                              "description": "Ids of the project's uploaded data files to use (from get_project)"},
               "follow_up_of": _s("Run id of a finished run this question follows up on")},
              ["workspace", "project", "question"], writes=True)
        async def ask_question(workspace, project, question, data_files=None, follow_up_of=None):
            body = {"workspace": workspace, "project": project, "question": question,
                    "uploads": data_files or [], "follow_up_of": follow_up_of}
            return await self._api("POST", "/questions", json=body)

        @tool("approve_plan", "Approve a run's plan",
              "Approve the proposed plan so the analysis starts and reads plant data. Only call this when the "
              "person has seen the plan and said to approve it.",
              {"run_id": _s("Run id"),
               "time_limit_minutes": _i("How long the analysis may work (default: the admins' default)", minimum=1)},
              ["run_id"], writes=True)
        async def approve_plan(run_id, time_limit_minutes=None):
            body = {"time_limit_minutes": time_limit_minutes} if time_limit_minutes else {}
            return await self._api("POST", f"/runs/{_q(run_id)}/plan:approve", json=body)

        @tool("send_back_plan", "Send a plan back",
              "Send the proposed plan back with what to change; a revised plan comes back for approval.",
              {"run_id": _s("Run id"), "note": _s("What to change in the plan")}, ["run_id", "note"], writes=True)
        async def send_back_plan(run_id, note):
            return await self._api("POST", f"/runs/{_q(run_id)}/plan:send-back", json={"text": note})

        @tool("add_note", "Steer a run with a note",
              "Leave a note for the agent, e.g. 'the March sensor data drifted, exclude it'. A working run reads it "
              "before its next step; the report records it.",
              {"run_id": _s("Run id"), "note": _s("The note")}, ["run_id", "note"], writes=True)
        async def add_note(run_id, note):
            r = await self._api("POST", f"/runs/{_q(run_id)}/notes", json={"text": note})
            return {"run_id": run_id, "notes": len(r.get("notes", [])), "saved": True}

        @tool("pause_run", "Pause a run", "Pause a running analysis after its current step. Nothing is lost.",
              {"run_id": _s("Run id")}, ["run_id"], writes=True)
        async def pause_run(run_id):
            return await self._api("POST", f"/runs/{_q(run_id)}:pause")

        @tool("resume_run", "Resume a run",
              "Continue a paused run. A run paused for review needs a note saying what to do next.",
              {"run_id": _s("Run id"), "note": _s("What to do next"),
               "more_minutes": _i("Extra working time", minimum=1)}, ["run_id"], writes=True)
        async def resume_run(run_id, note=None, more_minutes=None):
            body = {k: v for k, v in (("note", note), ("more_minutes", more_minutes)) if v}
            return await self._api("POST", f"/runs/{_q(run_id)}:resume", json=body)

        @tool("stop_run", "Stop a run",
              "Stop a run. Before approval it is cancelled; while analysing it stops after the current step and "
              "seals a partial report. Only when the person asks.",
              {"run_id": _s("Run id")}, ["run_id"], writes=True, destructive=True)
        async def stop_run(run_id):
            return await self._api("POST", f"/runs/{_q(run_id)}:stop")

    # ------------------------------------------------------------------ calling

    async def call(self, name: str, arguments: dict | None) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return _error_result(f"no tool {name!r}")
        args = dict(arguments or {})
        unknown = set(args) - set(tool.params)
        missing = [p for p in tool.required if args.get(p) in (None, "")]
        if unknown or missing:
            return _error_result(f"{name}: " + "; ".join(
                ([f"unknown argument(s) {sorted(unknown)}"] if unknown else []) +
                ([f"missing {missing}"] if missing else [])))
        try:
            out = await tool.call(**args)
        except ToolError as e:
            return _error_result(str(e))
        except Exception as e:  # noqa: BLE001 - the assistant sees a plain message, the log has the trace
            log.exception("tool %s failed", name)
            return _error_result(f"{name} failed: {type(e).__name__}")
        if isinstance(out, Image):
            return {"content": [{"type": "image", "data": base64.b64encode(out.data).decode(), "mimeType": out.mime}],
                    "isError": False}
        text = json.dumps(out, indent=1, default=str, ensure_ascii=False)
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "\n... (cut; ask for less, e.g. a smaller limit)"
        result = {"content": [{"type": "text", "text": text}], "isError": False}
        if isinstance(out, dict):
            result["structuredContent"] = out
        return result


@dataclass
class Image:
    data: bytes
    mime: str


def _error_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except (ValueError, AttributeError):
        d = None
    if isinstance(d, list):  # FastAPI validation errors
        d = "; ".join(f"{'.'.join(map(str, e.get('loc', [])[1:]))}: {e.get('msg')}" for e in d)
    return str(d or r.text[:300] or r.reason_phrase)


RUN_KEYS = ("run", "entitled", "question", "plan", "permissions", "time_limit", "review_pause", "conclusion_md",
            "findings", "notes", "datasets", "figures", "failure", "closed", "verdicts", "children", "uploads")


def _compact_run(r: dict) -> dict:
    """The parts of GET /runs/{id} an assistant needs (not the HTML, manifest, provenance or snapshot lists)."""
    out = {k: r[k] for k in RUN_KEYS if k in r and r[k] not in (None, [], {})}
    if isinstance(out.get("question"), dict):
        out["question"] = {k: v for k, v in out["question"].items() if k != "plan"}
    for d in out.get("datasets", []):
        cols = d.get("columns")
        if isinstance(cols, list) and len(cols) > 40:
            d["columns"] = cols[:40] + [f"... {len(cols) - 40} more"]
    if out.get("run", {}).get("status") == "awaiting_approval":
        out["next"] = "Show the plan to the person; approve_plan only if they say so, or send_back_plan with changes."
    return out


# ------------------------------------------------------------------ JSON-RPC

class Server:
    """MCP over JSON-RPC 2.0: tools only."""

    def __init__(self, alchemist: Alchemist):
        self.alchemist = alchemist

    async def handle(self, message: Any) -> Any:
        """One incoming message (or a batch) -> the response, or None for notifications."""
        if isinstance(message, list):
            out = [r for r in [await self._one(m) for m in message] if r is not None]
            return out or None
        return await self._one(message)

    async def _one(self, m: Any) -> dict | None:
        if not isinstance(m, dict) or m.get("jsonrpc") != "2.0":
            return _rpc_error(None, -32600, "invalid request")
        if "method" not in m:  # a response to something we never send
            return None
        mid, method, params = m.get("id"), m["method"], m.get("params") or {}
        notification = "id" not in m
        try:
            result = await self._dispatch(method, params)
        except _RpcError as e:
            return None if notification else _rpc_error(mid, e.code, e.message)
        return None if notification else {"jsonrpc": "2.0", "id": mid, "result": result}

    async def _dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":
            asked = params.get("protocolVersion")
            return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "alchemist", "title": "Alchemist", "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method == "ping" or method.startswith("notifications/"):
            return {}
        if method == "tools/list":
            return {"tools": [t.describe() for t in self.alchemist.tools.values()]}
        if method == "tools/call":
            if not isinstance(params.get("name"), str):
                raise _RpcError(-32602, "tools/call needs a tool name")
            return await self.alchemist.call(params["name"], params.get("arguments"))
        if method in ("resources/list", "prompts/list"):
            return {method.split("/")[0]: []}
        raise _RpcError(-32601, f"method not found: {method}")


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _rpc_error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


# ------------------------------------------------------------------ stdio (alchemist mcp)

@dataclass
class StdioOptions:
    url: str = "http://127.0.0.1:8000"
    user: str | None = None
    token: str | None = None
    read_only: bool = False
    headers: dict[str, str] = field(default_factory=dict)


def client_headers(o: StdioOptions) -> dict[str, str]:
    h = dict(o.headers)
    if o.user:
        h["X-Alchemist-User"] = o.user
    if o.token:
        name = os.environ.get("ALC_OIDC_TOKEN_HEADER", "authorization")
        h[name] = o.token if name.lower() != "authorization" else f"Bearer {o.token}"
    return h


async def serve_stdio(o: StdioOptions, stdin=None, stdout=None, transport=None) -> None:
    """Newline-delimited JSON-RPC on stdin/stdout (logs go to stderr), until stdin closes. `transport` is for tests."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    loop = asyncio.get_running_loop()
    async with httpx.AsyncClient(base_url=o.url.rstrip("/"), headers=client_headers(o), timeout=60,
                                 follow_redirects=False, transport=transport) as client:
        server = Server(Alchemist(client, read_only=o.read_only, where=f"Alchemist at {o.url}"))
        while True:
            line = await loop.run_in_executor(None, stdin.readline)
            if not line:
                return
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response = _rpc_error(None, -32700, "parse error")
            else:
                response = await server.handle(message)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
                stdout.flush()


# ------------------------------------------------------------------ HTTP (/api/mcp on the API)

# Headers that carry who the caller is; forwarded from the MCP request to the in-process API calls.
# Not cookies: an assistant sends headers, and a browser that sends cookies on its own must not drive the tools.
IDENTITY_HEADERS = ("x-alchemist-user", "authorization", "x-amzn-oidc-data", "x-amzn-oidc-identity",
                    "x-amzn-oidc-accesstoken")


def mount(app) -> None:
    """Add POST /api/mcp (MCP streamable HTTP, answering with plain JSON) to the Alchemist FastAPI app.
    ALC_MCP=off leaves it out; ALC_MCP=read-only offers only the reading tools."""
    setting = os.environ.get("ALC_MCP", "on").lower()
    if setting in ("off", "0", "false", "no"):
        return
    read_only = setting == "read-only"

    @app.post("/api/mcp", include_in_schema=False)
    async def mcp_post(request: Request):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1].rstrip("/") != request.headers.get("host"):
            # A web page in someone's browser must not drive the user's assistant session (DNS rebinding).
            return JSONResponse(_rpc_error(None, -32600, "cross-origin requests are not allowed"), status_code=403)
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            return JSONResponse(_rpc_error(None, -32600, "send JSON (Content-Type: application/json)"), status_code=415)
        try:
            message = await request.json()
        except ValueError:
            return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)
        names = set(IDENTITY_HEADERS) | {os.environ.get("ALC_OIDC_TOKEN_HEADER", "authorization").lower()}
        headers = {k: v for k, v in request.headers.items() if k.lower() in names}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://alchemist.internal", headers=headers,
                                     timeout=120) as client:
            response = await Server(Alchemist(client, read_only=read_only, where="Alchemist")).handle(message)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)

    @app.get("/api/mcp", include_in_schema=False)
    @app.delete("/api/mcp", include_in_schema=False)
    async def mcp_other():
        # No server-initiated stream and no sessions: the spec's answer is 405.
        return Response(status_code=405, headers={"Allow": "POST"})
