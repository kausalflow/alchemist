"""Questions and runs: ask, approve, follow, stop, read, replay, judge.

Every read goes through the run folder (the source of truth); the database only says which runs exist and who may
see them. A workspace member who is not entitled to every plant a run used sees its title, owner and status only.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import zipfile
from typing import Any, Literal

import markdown
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ..access import AccessDenied
from ..catalog import now
from ..paths import as_path, is_cloud, read_json, read_yaml
from ..workspace import verify
from .deps import Me, Svc, access_error, entitled, entitled_run, in_workspace, run_or_404

log = logging.getLogger("alchemist.api")

router = APIRouter(tags=["runs"])

TERMINAL = {"completed", "partial", "failed", "mismatch", "cancelled", "expired"}


# ------------------------------------------------------------------ helpers

def _question(row: dict) -> dict:
    q = read_yaml(as_path(row["path"]).parent.parent / "question.yaml")
    return {k: q.get(k) for k in ("id", "text", "asker", "scope", "status", "created_at", "updated_at")} | {
        "plan": q.get("plan")}


def _json(path, default=None):
    p = as_path(path)
    try:
        return read_json(p) if p.exists() else default
    except (json.JSONDecodeError, FileNotFoundError):  # being rewritten by a running step
        return default


def _summary(svc, me, row: dict) -> dict:
    ok = entitled(svc, me, row)
    out = {"run_id": row["run_id"], "question_id": row["question_id"], "workspace": row["workspace"],
           "project": row["project"], "user_id": row["user_id"], "mode": row["mode"], "parent_run": row["parent_run"],
           "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"],
           "sealed": bool(row["seal_sha256"]), "plants": json.loads(row["plants"] or "[]"), "entitled": ok}
    try:
        out["question"] = read_yaml(as_path(row["path"]).parent.parent / "question.yaml")["text"]
    except (FileNotFoundError, OSError, KeyError, TypeError):
        out["question"] = None
    return out


def _transcript_steps(path) -> list[dict]:
    """Model turns and tool calls from a transcript, in the shape the live view shows."""
    p = as_path(path)
    if not p.exists():
        return []
    steps = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "steering" in e:
            steps.append({"at": e["at"], "type": "note", "notes": e["steering"]})
        elif "tool" in e:
            steps.append({"at": e["at"], "type": "tool", "tool": e["tool"], "input": e.get("input"),
                          "is_error": e.get("is_error", False), "seconds": e.get("seconds"),
                          "output": (e.get("output") or "")[:2000], "images": e.get("images", 0),
                          **({"meta": e["meta"]} if e.get("meta") else {})})
        elif "model" in e:
            texts = [b.get("text", "") for b in e["message"]["content"] if b.get("type") == "text"]
            steps.append({"at": e["at"], "type": "model", "model": e["model"], "stop_reason": e.get("stop_reason"),
                          "usage": e.get("usage"), "text": "\n".join(t for t in texts if t)[:2000]})
    return steps


def _launcher(request: Request):
    return request.app.state.launcher


def _time_limit(svc, run, plan: dict | None, status: str) -> dict | None:
    """The approved run's time limit and the analysis's working time so far (from its last checkpoint, plus the time
    since while it is running)."""
    from ..run import checkpoint_of, paused_at_time_limit, time_limit

    if not (plan or {}).get("approved_at"):
        return None
    used = None
    saved = checkpoint_of(run) if status in ("running", "paused") else None
    if saved:
        used = float(saved.get("budget", {}).get("seconds", 0))
        if status == "running":
            try:
                at = dt.datetime.fromisoformat(saved["at"].replace("Z", "+00:00"))
                used += max(0.0, (dt.datetime.now(dt.timezone.utc) - at).total_seconds())
            except (KeyError, ValueError):
                pass
    else:
        used = (_json(run / "manifest.json") or {}).get("usage", {}).get("seconds")
    return {"minutes": time_limit(svc, plan), "used_seconds": None if used is None else round(used),
            "reached": status == "paused" and paused_at_time_limit(run)}


def _queue_position(request: Request, run_id: str) -> dict | None:
    position = getattr(_launcher(request), "position", None)
    try:
        return position(run_id) if position else None
    except Exception:
        log.exception("reading the queue position of run %s failed", run_id)
        return None


def _launch(svc, request: Request, run_id: str, step: str, *args) -> None:
    """Hand a step to the launcher; if that fails, close the run (or pause it again, if it can resume) instead of
    leaving it looking busy."""
    from ..run import interrupted

    try:
        getattr(_launcher(request), step)(run_id, *args)
    except Exception as e:
        interrupted(svc, run_id, f"could not start the {step} step: {type(e).__name__}: {e}")
        raise HTTPException(503, f"could not start the run's {step} step; try again") from e


def _limit_error(e) -> HTTPException:
    return HTTPException(429, str(e))


# ------------------------------------------------------------------ ask

class AskBody(BaseModel):
    workspace: str
    project: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
    question: str = Field(min_length=5, max_length=4000)
    scope: dict[str, Any] = Field(default_factory=dict)


@router.post("/questions", status_code=201)
def ask(body: AskBody, svc: Svc, me: Me, request: Request) -> dict:
    """Create a question and its first run, and start planning. The plan then waits for approval."""
    from ..run import RunLimit, create

    try:
        row = create(svc, user=me.user, workspace=body.workspace, project=body.project, question=body.question,
                     scope=body.scope)
    except AccessDenied as e:
        raise access_error(e) from e
    except RunLimit as e:
        raise _limit_error(e) from e
    _launch(svc, request, row["run_id"], "plan")
    return _summary(svc, me, row)


@router.get("/questions/similar")
def similar(q: str, svc: Svc, me: Me, limit: int = 5) -> list[dict]:
    """Past questions the user may open whose words overlap the new one, so they can reuse an answer."""
    words = {w for w in q.lower().split() if len(w) > 3}
    hits = []
    for row in svc.catalog.runs():
        if row["mode"] != "live" or not entitled(svc, me, row):
            continue
        s = _summary(svc, me, row)
        text = (s.get("question") or "").lower()
        score = sum(1 for w in words if w in text)
        if score:
            hits.append((score, s))
    hits.sort(key=lambda h: -h[0])
    return [h[1] for h in hits[:limit]]


# ------------------------------------------------------------------ list and read

@router.get("/runs")
def list_runs(svc: Svc, me: Me, workspace: str | None = None, project: str | None = None,
              question_id: str | None = None) -> list[dict]:
    out = []
    for row in svc.catalog.runs():
        if not in_workspace(me, row["workspace"]):
            continue
        if (workspace and row["workspace"] != workspace) or (project and row["project"] != project) or (
                question_id and row["question_id"] != question_id):
            continue
        s = _summary(svc, me, row)
        if not s["entitled"]:
            s = {k: s[k] for k in ("run_id", "workspace", "project", "user_id", "status", "created_at", "entitled",
                                   "mode", "question")}
        out.append(s)
    return sorted(out, key=lambda r: r["created_at"] or "", reverse=True)


@router.get("/runs/{run_id}")
def get_run(run_id: str, svc: Svc, me: Me, request: Request) -> dict:
    """Everything the run page shows: question, plan, progress, conclusion, findings, data used, methods, provenance."""
    row = run_or_404(svc, run_id)
    if not in_workspace(me, row["workspace"]):
        raise HTTPException(404, f"no run {run_id}")
    s = _summary(svc, me, row)
    if not s["entitled"]:
        return {"run": {k: s[k] for k in ("run_id", "workspace", "project", "user_id", "status", "question")},
                "entitled": False}
    run = as_path(row["path"])
    q = _question(row)
    conclusion = (run / "report" / "conclusion.md")
    conclusion_md = conclusion.read_text() if conclusion.exists() else None
    findings = (_json(run / "findings.json") or {}).get("findings", [])
    manifest = _json(run / "manifest.json")
    queries = _json(run / "inputs" / "queries.json", {})
    purposes = _query_purposes(run)
    children = [_summary(svc, me, r) for r in svc.catalog.runs() if r["parent_run"] == run_id]
    is_owner = me.user.id == q["asker"] or me.user.is_admin
    owns = me.user.id == row["user_id"] or me.user.is_admin
    from ..steering import NOTE_STATUSES, record
    return {
        "run": s,
        "entitled": True,
        "question": q,
        "plan": q["plan"],
        "permissions": {
            "approve": row["status"] == "awaiting_approval" and is_owner,
            "send_back": row["status"] == "awaiting_approval" and is_owner,
            "note": row["status"] in NOTE_STATUSES and row["mode"] == "live" and owns,
            "stop": row["status"] in ("planning", "awaiting_approval", "running", "paused") and owns,
            "pause": row["status"] == "running" and row["mode"] == "live" and owns,
            "resume": row["status"] == "paused" and owns,
            "replay": bool(row["seal_sha256"]),
            "verdict": bool(row["seal_sha256"]) and in_workspace(me, row["workspace"]),
        },
        # With the run queue: whether its current step waits for a worker (and behind how many) or which one runs it.
        "queue": _queue_position(request, run_id) if row["status"] in ("planning", "running") else None,
        "time_limit": _time_limit(svc, run, q["plan"], row["status"]),
        "conclusion_md": conclusion_md,
        "conclusion_html": markdown.markdown(conclusion_md, extensions=["tables"]) if conclusion_md else None,
        "findings": findings,
        "notes": record(svc, run_id, run),
        "plan_history": _json(run / "logs" / "plan_history.json", []),
        "queries": [{"id": k, **v, "purpose": purposes.get(k, "")} for k, v in queries.items()],
        "datasets": [{"dataset": k, "card_version": v["card_version"], "plant": v["plant"], "format": v["format"],
                      "columns": v["columns"]} for k, v in (_json(run / "inputs" / "views.json", {}) or {}).items()],
        "knowledge": {"citations": _json(run / "logs" / "citations.json", []),
                      "structured": _json(run / "inputs" / "knowledge_index.json", [])},
        "review": _json(run / "logs" / "review.json"),
        "result_check": _json(run / "work" / "result_check.json"),
        "manifest": manifest,
        "has_report": (run / "report" / "report.html").exists(),
        "has_notebook": (run / "notebook.ipynb").exists(),
        "figures": sorted(f.name for f in (run / "figures").glob("*.png")) if (run / "figures").exists() else [],
        "children": children,
        "verdicts": _verdicts(svc, run_id),
        "failure": _failure(svc, run_id),
        "closed": _closed(svc, run_id),
    }


def _query_purposes(run) -> dict:
    nb = run / "notebook.ipynb"
    if not nb.exists():
        return {}
    out = {}
    for c in json.loads(nb.read_text())["cells"]:
        meta = c.get("metadata", {}).get("alchemist", {})
        if meta.get("kind") == "query" and meta.get("query_id"):
            out[meta["query_id"]] = meta.get("purpose", "")
    return out


def _audit(svc, run_id: str, action: str) -> list[dict]:
    rows = svc.catalog.db.execute("select at, actor, action, detail from audit_events where target = ? and action = ?"
                                  " order by id", (run_id, action)).fetchall()
    return [{"at": r["at"], "actor": r["actor"], **(json.loads(r["detail"]) if r["detail"] else {})} for r in rows]


def _verdicts(svc, run_id: str) -> list[dict]:
    return _audit(svc, run_id, "run.verdict")


def _failure(svc, run_id: str) -> dict | None:
    rows = _audit(svc, run_id, "run.fail")
    return rows[-1] if rows else None


def _closed(svc, run_id: str) -> dict | None:
    """Why a run was cancelled, expired or paused (who, when, reason)."""
    rows = _audit(svc, run_id, "run.cancel") + _audit(svc, run_id, "run.expire") + _audit(svc, run_id, "run.paused")
    return sorted(rows, key=lambda r: r["at"])[-1] if rows else None


@router.get("/runs/{run_id}/steps")
def steps(run_id: str, svc: Svc, me: Me) -> dict:
    """The planning and analysis transcripts as steps: what the model said and which tools it called with what."""
    run = as_path(entitled_run(svc, me, run_id)["path"])
    return {"planning": _transcript_steps(run / "logs" / "plan_transcript.jsonl"),
            "analysis": _transcript_steps(run / "logs" / "transcript.jsonl")}


@router.get("/runs/{run_id}/notebook")
def notebook(run_id: str, svc: Svc, me: Me) -> dict:
    """The executed notebook as cells with text and image outputs, for the notebook viewer."""
    run = as_path(entitled_run(svc, me, run_id)["path"])
    nb = run / "notebook.ipynb"
    if not nb.exists():
        return {"cells": []}
    cells = []
    for c in json.loads(nb.read_text())["cells"]:
        src = "".join(c["source"]) if isinstance(c["source"], list) else c["source"]
        cell = {"type": c["cell_type"], "source": src, "meta": c.get("metadata", {}).get("alchemist", {}),
                "outputs": []}
        for o in c.get("outputs", []):
            if o["output_type"] == "stream":
                cell["outputs"].append({"type": "text", "text": "".join(o["text"]) if isinstance(o["text"], list) else o["text"]})
            elif o["output_type"] in ("execute_result", "display_data"):
                data = o.get("data", {})
                if "image/png" in data:
                    cell["outputs"].append({"type": "image", "png": data["image/png"].strip()})
                elif "text/plain" in data:
                    t = data["text/plain"]
                    cell["outputs"].append({"type": "text", "text": "".join(t) if isinstance(t, list) else t})
            elif o["output_type"] == "error":
                cell["outputs"].append({"type": "error", "text": f"{o.get('ename')}: {o.get('evalue')}"})
        cells.append(cell)
    return {"cells": cells}


@router.get("/runs/{run_id}/snapshots/{query_id}")
def snapshot(run_id: str, query_id: str, svc: Svc, me: Me, limit: int = Query(50, le=500)) -> dict:
    """The first rows of the data a query read, exactly as stored in the run's inputs."""
    import pandas as pd

    run = as_path(entitled_run(svc, me, run_id)["path"])
    q = (_json(run / "inputs" / "queries.json", {}) or {}).get(query_id)
    if not q:
        raise HTTPException(404, f"no query {query_id}")
    f = run / q["snapshot"]
    df = pd.read_parquet(f if not is_cloud(f) else io.BytesIO(f.read_bytes()))
    head = df.head(limit)
    return {"columns": [{"name": c, "type": str(t)} for c, t in df.dtypes.items()], "rows": len(df),
            "head": json.loads(head.to_json(orient="values", date_format="iso"))}


SERVABLE = {".html": "text/html", ".png": "image/png", ".json": "application/json", ".md": "text/markdown",
            ".ipynb": "application/x-ipynb+json", ".jsonl": "application/jsonl", ".parquet": "application/vnd.apache.parquet",
            ".csv": "text/csv", ".yaml": "text/yaml", "": "text/plain"}


@router.get("/runs/{run_id}/files/{path:path}")
def run_file(run_id: str, path: str, svc: Svc, me: Me, download: bool = False):
    """One file from the run folder (report, figures, notebook, snapshots, logs, manifest)."""
    run = as_path(entitled_run(svc, me, run_id)["path"])
    if ".." in path.split("/") or path.startswith("/"):
        raise HTTPException(400, "bad path")
    f = run / path
    if not f.exists() or not f.is_file():
        raise HTTPException(404, "no such file")
    suffix = "" if f.name == "SEALED" else f.suffix
    media = SERVABLE.get(suffix)
    if media is None:
        raise HTTPException(415, "file type not served")
    headers = {"Content-Disposition": f'attachment; filename="{f.name}"'} if download else {}
    if suffix == ".html":
        # Reports are generated from model text: no scripts, and only same-origin images.
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
    if is_cloud(f):
        # S3: a short-lived presigned GET, issued only after the entitlement check above.
        params = {"Bucket": f.bucket, "Key": f.key, "ResponseContentType": media}
        if download:
            params["ResponseContentDisposition"] = headers["Content-Disposition"]
        url = f.client.client.generate_presigned_url("get_object", Params=params, ExpiresIn=300)
        return RedirectResponse(url, status_code=307)
    return FileResponse(str(f), media_type=media, headers=headers)


@router.get("/runs/{run_id}/bundle")
def bundle(run_id: str, svc: Svc, me: Me):
    """The whole sealed run folder as a zip, for people or agents to replay elsewhere."""
    row = entitled_run(svc, me, run_id)
    run = as_path(row["path"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(run.rglob("*"), key=str):
            if f.is_file():
                z.writestr(f"{run_id}/{f.relative_to(run)}", f.read_bytes())
    svc.catalog.audit(me.user.id, "run.download", run_id)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="alchemist-run-{run_id}.zip"'})


@router.get("/runs/{run_id}/verify")
def verify_run(run_id: str, svc: Svc, me: Me) -> dict:
    row = entitled_run(svc, me, run_id)
    if not row["seal_sha256"]:
        return {"ok": False, "problems": ["run is not sealed yet"]}
    return verify(row["path"], row["seal_sha256"]) | {"seal_sha256": row["seal_sha256"]}


# ------------------------------------------------------------------ progress

@router.get("/runs/{run_id}/events")
async def events(run_id: str, request: Request, svc: Svc, me: Me, poll: float = 1.0):
    """Live progress as server-sent events: `status` when the run's status changes, `step` for each new model
    turn or tool call, and `end` once the run reaches a final status.

    Progress is read from the run folder's transcripts, so it works whichever process runs the step."""
    row = entitled_run(svc, me, run_id)
    run = as_path(row["path"])
    db_path = request.app.state.settings.db_path

    def snapshot_state():
        from ..catalog import Catalog

        cat = Catalog(db_path)  # only the status: cheaper than full services once a second per viewer
        try:
            r = cat.run(run_id)
        finally:
            cat.db.close()
        st = _transcript_steps(run / "logs" / "plan_transcript.jsonl")
        sa = _transcript_steps(run / "logs" / "transcript.jsonl")
        return r["status"], [("planning", x) for x in st] + [("analysis", x) for x in sa]

    async def stream():
        last_status, sent = None, 0
        while True:
            if await request.is_disconnected():
                return
            status, all_steps = await asyncio.to_thread(snapshot_state)
            if status != last_status:
                yield _sse("status", {"status": status})
                last_status = status
            for phase, step in all_steps[sent:]:
                yield _sse("step", {"phase": phase, **step})
            sent = len(all_steps)
            if status in TERMINAL or status == "paused":
                yield _sse("end", {"status": status})
                return
            await asyncio.sleep(poll)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ------------------------------------------------------------------ actions

class ApproveBody(BaseModel):
    plan: dict[str, Any] | None = None  # the edited plan; omitted means approve as proposed
    # How long the analysis may work, up to the admins' cap; omitted means the default (GET /meta budgets).
    time_limit_minutes: int | None = None


@router.post("/runs/{run_id}/plan:approve")
def approve(run_id: str, body: ApproveBody, svc: Svc, me: Me, request: Request) -> dict:
    """Approve (or edit and approve) the plan; the analysis starts."""
    from ..run import RunLimit, TimeLimitError, start_approval

    row = entitled_run(svc, me, run_id)
    if body.plan is not None and not body.plan.get("hypotheses"):
        raise HTTPException(422, "an edited plan needs at least one hypothesis")
    if body.plan is not None:
        known = {d["dataset"]["id"] for d in svc.catalog.queryable(set(json.loads(row["plants"])))}
        unknown = sorted({ds.removeprefix("ds.") for h in body.plan["hypotheses"] for ds in h.get("datasets", [])} - known)
        if unknown:
            raise HTTPException(422, f"datasets not available to this run: {unknown}")
    try:
        plan = start_approval(svc, run_id, user=me.user, plan=body.plan, time_limit_minutes=body.time_limit_minutes)
    except AccessDenied as e:
        raise access_error(e) from e
    except RunLimit as e:
        raise _limit_error(e) from e
    except TimeLimitError as e:
        raise HTTPException(422, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    _launch(svc, request, run_id, "analyse", me.user.id)
    return {"run_id": run_id, "status": "running", "plan": plan}


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _notes(svc, row) -> list[dict]:
    from ..steering import record

    return record(svc, row["run_id"], as_path(row["path"]))


@router.post("/runs/{run_id}/notes", status_code=201)
def add_note(run_id: str, body: NoteBody, svc: Svc, me: Me) -> dict:
    """Leave a note for the agent ("the March sensor data drifted, exclude it"). A working run reads it before its
    next model call; a paused run, or one waiting for approval, when its analysis resumes or starts. Every note is
    kept in the run's record, and the report shows when the agent received it."""
    from ..run import note

    row = entitled_run(svc, me, run_id)
    try:
        note(svc, run_id, user=me.user, text=body.text)
    except AccessDenied as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"run_id": run_id, "notes": _notes(svc, row)}


@router.get("/runs/{run_id}/notes")
def list_notes(run_id: str, svc: Svc, me: Me) -> list[dict]:
    return _notes(svc, entitled_run(svc, me, run_id))


@router.post("/runs/{run_id}/plan:send-back")
def send_back(run_id: str, body: NoteBody, svc: Svc, me: Me, request: Request) -> dict:
    """Send the proposed plan back with a note saying what to change; planning continues and a new version of the
    plan comes back for approval."""
    from ..run import RunLimit, send_back as send

    row = entitled_run(svc, me, run_id)
    try:
        send(svc, run_id, user=me.user, text=body.text)
    except AccessDenied as e:
        raise access_error(e) from e
    except RunLimit as e:
        raise _limit_error(e) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    _launch(svc, request, run_id, "replan")
    return {"run_id": run_id, "status": "planning", "notes": _notes(svc, row)}


@router.post("/runs/{run_id}:stop")
def stop(run_id: str, svc: Svc, me: Me, request: Request) -> dict:
    """Stop a run. Planning or waiting for approval: it is cancelled now (nothing was read from the lake yet).
    Analysing: it stops after its current step, and a partial report of the findings so far is built and sealed."""
    from ..run import cancel

    entitled_run(svc, me, run_id)
    try:
        result = cancel(svc, run_id, user=me.user)
    except AccessDenied as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    if result == "cancelled":
        try:
            _launcher(request).cancel(run_id)
        except Exception:  # the run is closed either way; reconcile() ends a workflow left behind
            pass
    elif result == "finishing":  # paused: resume it once, so it builds and seals its partial report
        _launch(svc, request, run_id, "resume")
    return {"run_id": run_id, "stop_requested": True, "result": result,
            "status": svc.catalog.run(run_id)["status"]}


@router.post("/runs/{run_id}:pause")
def pause_run(run_id: str, svc: Svc, me: Me) -> dict:
    """Pause a running analysis after its current step. Nothing is lost: the conversation and the notebook are saved
    after every step, and its tasks stop until it is resumed."""
    from ..run import pause

    entitled_run(svc, me, run_id)
    try:
        pause(svc, run_id, user=me.user)
    except AccessDenied as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"run_id": run_id, "pause_requested": True}


class ResumeBody(BaseModel):
    more_minutes: int | None = None


@router.post("/runs/{run_id}:resume")
def resume_run(run_id: str, svc: Svc, me: Me, request: Request, body: ResumeBody | None = None) -> dict:
    """Continue a paused run from its last step: a fresh kernel re-runs the notebook on its stored snapshots, then the
    agent carries on. `more_minutes` gives it more working time (up to the cap); a run paused at its time limit gets
    the default if it is left out."""
    from ..run import RunLimit, TimeLimitError, resume

    entitled_run(svc, me, run_id)
    try:
        resume(svc, run_id, user=me.user, more_minutes=body.more_minutes if body else None)
    except AccessDenied as e:
        raise HTTPException(403, str(e)) from e
    except RunLimit as e:
        raise _limit_error(e) from e
    except TimeLimitError as e:
        raise HTTPException(422, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    _launch(svc, request, run_id, "resume")
    return {"run_id": run_id, "status": "running"}


class ReplayBody(BaseModel):
    mode: Literal["replay", "rerun"] = "replay"


@router.post("/runs/{run_id}:replay", status_code=201)
def replay(run_id: str, body: ReplayBody, svc: Svc, me: Me, request: Request) -> dict:
    """A new run that re-executes this run's notebook on its snapshots (replay) or on current data (rerun)."""
    from ..run import RunLimit, start_replay

    try:
        row = start_replay(svc, run_id, user=me.user, mode=body.mode)
    except AccessDenied as e:
        raise access_error(e) from e
    except RunLimit as e:
        raise _limit_error(e) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    _launch(svc, request, row["run_id"], "replay")
    return _summary(svc, me, row)


class VerdictBody(BaseModel):
    verdict: Literal["confirmed", "rejected", "partly_confirmed"]
    note: str = Field(default="", max_length=4000)
    findings: dict[str, Literal["confirmed", "rejected"]] = Field(default_factory=dict)


@router.post("/runs/{run_id}/verdict", status_code=201)
def verdict(run_id: str, body: VerdictBody, svc: Svc, me: Me) -> dict:
    """Confirm or reject the conclusion (and single findings); kept with the run for past-investigation search."""
    row = entitled_run(svc, me, run_id)
    if not row["seal_sha256"]:
        raise HTTPException(409, "only finished runs can be judged")
    detail = body.model_dump() | {"recorded_at": now()}
    svc.catalog.audit(me.user.id, "run.verdict", run_id, detail)
    return {"run_id": run_id, "verdicts": _verdicts(svc, run_id)}
