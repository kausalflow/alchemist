"""Run lifecycle: plan, approve, analyse, review, report, seal; and replay.

    ask()      creates the question and run, runs the planning step, and stops for approval
    approve()  runs the analysis in a fresh sandbox, then reports and seals the run
    replay()   re-executes a sealed run's notebook on its snapshots (or on current data)
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import platform
import re
from pathlib import Path
from typing import Callable

from . import penguineer_tools, prompts, steering
from .access import AccessDenied, User
from .agent import Budget, Model, ToolOutput, run_loop
from .llm import role_model
from .catalog import Catalog, now
from .credentials import CredentialBroker, RefreshingCredentials
from .knowledge import Knowledge
from .model_roles import with_choices
from .paths import as_path, copy_tree, read_json, read_yaml, write_json
from .plugins import get_registry
from .report import ReportError, render
from .sandbox import RemoteKernel, Sandbox, finding_cell, query_cell
from .workspace import (create_question, create_run, ensure_project, seal, update_question, verify)

ACTIVE = ("planning", "running")  # a step is (or should be) working on the run
OPEN = ACTIVE + ("awaiting_approval",)  # not finished yet


class RunLimit(Exception):
    """Too many runs planning or analysing at once (ALC_MAX_ACTIVE_RUNS_PER_USER, ALC_MAX_ACTIVE_RUNS)."""


PACKAGES = ("duckdb", "pandas", "numpy", "scipy", "pyarrow", "matplotlib", "cloudpathlib", "anthropic", "alchemist")


def environment() -> dict:
    versions = {}
    for p in PACKAGES:
        try:
            versions[p] = importlib.metadata.version(p)
        except importlib.metadata.PackageNotFoundError:
            versions[p] = None
    lock = Path(__file__).resolve().parents[2] / "uv.lock"
    import hashlib
    import os
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": versions,
            "uv_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None,
            "image": os.environ.get("ALC_IMAGE_DIGEST")}


class Services:
    """The backends a run uses. Access comes from the registered access provider (ALC_ACCESS_PROVIDER); any
    backend can also be passed in directly. `broker` needs issue() (credentials.Broker)."""

    def __init__(self, settings, *, catalog=None, access=None, knowledge=None, broker=None):
        reg = get_registry()
        self.catalog = catalog or Catalog(settings.db_path)
        # The role models, with any an admin picked on the "Models and limits" page (model_roles.py).
        self.settings = settings = with_choices(settings, self.catalog.db)
        self.access = access or reg.access_providers.get(settings.access_provider).load(settings)
        self.knowledge = knowledge or Knowledge(settings.knowledge_root, packs=list(reg.knowledge_packs))
        self.broker = broker or CredentialBroker.from_settings(settings)

    def sandbox_env(self, ctx: "RunContext", views: dict) -> tuple[dict, RefreshingCredentials | None]:
        """Kernel environment: the pseudonym key, and the run's own S3 credentials when a run role is set.

        The credentials come with the refresher that re-issues them before they expire; the caller starts it and
        stops it with the kernel.
        """
        env = {"ALC_PSEUDONYM_KEY": self.settings.pseudonym_key}

        def issue(policy_out=None):
            return self.broker.issue(run_id=ctx.row["run_id"], user_id=ctx.user_id, workspace=ctx.row["workspace"],
                                     run_dir=str(ctx.run), read_globs=[v["glob"] for v in views.values()],
                                     policy_out=policy_out)

        creds = issue(ctx.run / "logs" / "session_policy.json")  # later sessions carry the same policy
        if not creds:
            ctx.credentials = {"mode": "ambient"}
            return env, None
        lifetime = (creds.expires_at() - dt.datetime.now(dt.timezone.utc)).total_seconds()
        refresher = RefreshingCredentials(issue, first=creds, refresh_before=min(1800, max(lifetime / 2, 60)))
        ctx.credentials = refresher.record()
        env.update(refresher.env())
        return env, refresher

    def sandbox(self, ctx: "RunContext", views: dict, *, run_mode: str, sandbox_url: str | None = None) -> Sandbox:
        """The run's kernel: a local child process, or the separate sandbox task at sandbox_url."""
        if sandbox_url:
            kernel = RemoteKernel(sandbox_url, run_dir=str(ctx.run), run_mode=run_mode, seed=self.settings.seed)
            return Sandbox(str(ctx.run), run_mode=run_mode, seed=self.settings.seed, kernel=kernel)
        if self.settings.sandbox_mode == "separate":
            raise ValueError("ALC_SANDBOX_MODE=separate: give the sandbox's address (--sandbox-task-arn or --sandbox-url)")
        env, refresher = self.sandbox_env(ctx, views)
        sb = Sandbox(str(ctx.run), run_mode=run_mode, seed=self.settings.seed, extra_env=env)
        if refresher:
            sb.on_stop.append(refresher.start().stop)
        return sb

    @property
    def seals_in_worker(self) -> bool:
        """In-process the worker seals the run; with a separate sandbox the workflow's Seal step does."""
        return self.settings.sandbox_mode != "separate"


def plugin_tools(phase: str, ctx: "RunContext") -> tuple[list, dict]:
    """Plugin tools for a phase, and handlers bound to the run context."""
    tools = get_registry().tools_for(phase)
    clash = sorted({t.name for t in tools} & set(prompts.TOOLS))
    if clash:
        raise ValueError(f"plugin tools may not reuse built-in tool names: {clash}")

    def bind(tool):
        def handler(inp: dict) -> ToolOutput:
            out = tool.handler(ctx, inp)
            return out if isinstance(out, ToolOutput) else ToolOutput(str(out))
        return handler

    return tools, {t.name: bind(t) for t in tools}


class RunContext:
    """Everything one run's tools need, and the provenance they record."""

    def __init__(self, svc: Services, run_row: dict, *, user: str):
        self.svc, self.row, self.user_id = svc, run_row, user
        self.credentials: dict = {}
        self.run = as_path(run_row["path"])
        self.qdir = self.run.parent.parent
        self.question = read_yaml(self.qdir / "question.yaml")
        self.plants = frozenset(json.loads(run_row["plants"]))
        self.citations: list[dict] = []
        self.plan: dict | None = self.question.get("plan")
        self.revising: dict | None = None  # the plan a person sent back, while a new one is drafted
        self.steering: steering.Steering | None = None
        self.sandbox: Sandbox | None = None
        self.query_n = self.finding_n = self.penguineer_n = 0
        self.findings_done = self.review = self.report_out = None
        self.review_requested = False
        self.models: dict = {"prompts": prompts.prompt_version()}
        logs = self.run / "logs" / "citations.json"
        if logs.exists():
            self.citations = read_json(logs)

    # ---------- shared tools ----------
    def search_catalog(self, inp: dict) -> ToolOutput:
        hits = self.svc.catalog.search(inp["query"], self.plants)
        if not hits:
            hits = [{"dataset": d["dataset"]["id"], "view": f"ds.{d['dataset']['id']}", "description": d["card"].get("description")}
                    for d in self.svc.catalog.queryable(self.plants)]
            return ToolOutput("No close match. Datasets available to this run:\n" + json.dumps(hits, indent=1))
        return ToolOutput(json.dumps(hits, indent=1, default=str))

    def cite(self, doc: str, version: str, title: str, section: str, stale: bool = False) -> None:
        """Add a source to the run's citations (shown in the report); plugin tools use this too."""
        key = (doc, version, section)
        if key not in {(c["doc"], c["version"], c["section"]) for c in self.citations}:
            self.citations.append({"doc": doc, "version": version, "title": title, "section": section, "stale": stale})
        write_json(self.run / "logs" / "citations.json", self.citations)

    def search_knowledge(self, inp: dict) -> ToolOutput:
        hits = self.svc.knowledge.search(inp["query"], self.plants)
        for h in hits:
            self.cite(h["doc"], h["version"], h["title"], h["section"], h["stale"])
        return ToolOutput(json.dumps(hits, indent=1) if hits else "No matching passages.")

    # ---------- planning ----------
    def submit_plan(self, inp: dict) -> ToolOutput:
        datasets = {d["dataset"]["id"] for d in self.svc.catalog.queryable(self.plants)}
        unknown = sorted({ds.removeprefix("ds.") for h in inp["hypotheses"] for ds in h.get("datasets", [])} - datasets)
        if unknown:
            return ToolOutput(f"These datasets are not available to this run: {unknown}. Use search_catalog.", is_error=True)
        self.plan = {"version": (self.revising or {}).get("version", 0) + 1, **inp, "submitted_at": now()}
        if self.revising:
            self.plan["revises"] = self.revising.get("version", 1)
        update_question(self.qdir, plan=self.plan, status="awaiting_approval")
        return ToolOutput("Plan saved and sent to the user for approval.")

    # ---------- analysis ----------
    def run_query(self, inp: dict) -> ToolOutput:
        self.query_n += 1
        qid = f"q{self.query_n}"
        res = self.sandbox.execute(query_cell(qid, inp["sql"]), kind="query", meta={"query_id": qid, "purpose": inp.get("purpose", "")})
        if res.error:
            self.query_n -= 1
            return ToolOutput(f"Query failed: {res.error}", is_error=True)
        summary = next((r for r in res.results if r.get("query") == qid), {})
        preview = {"query": qid, "cell": res.cell_id, **{k: summary.get(k) for k in ("rows", "columns", "stats", "spark")}}
        return ToolOutput(f"Cell {res.cell_id}: query {qid} saved as {summary.get('snapshot')}; frame df_{qid}.\n"
                          + json.dumps({k: summary.get(k) for k in ("rows", "columns", "stats", "head")}, default=str)[:6000],
                          meta={"preview": preview})

    def run_python(self, inp: dict) -> ToolOutput:
        res = self.sandbox.execute(inp["code"], kind="python", meta={"purpose": inp.get("purpose", "")})
        figs = f"\nFigures: {res.figure_paths}" if res.figure_paths else ""
        return ToolOutput(f"Cell {res.cell_id}:\n{res.model_text()}{figs}", images=res.images, is_error=bool(res.error),
                          meta={"cell": res.cell_id, "figures": res.figure_paths} if res.figure_paths else {})

    def run_penguineer(self, tool: str, inp: dict) -> ToolOutput:
        self.penguineer_n += 1
        pid = f"p{self.penguineer_n}"
        command = penguineer_tools.COMMANDS[tool]
        res = self.sandbox.execute(penguineer_tools.cell(pid, tool, inp), kind="penguineer",
                                   meta={"call_id": pid, "command": command})
        if res.error:
            self.penguineer_n -= 1
            return ToolOutput(f"penguineer {command} failed: {res.error}", is_error=True)
        rec = next((r for r in res.results if r.get("penguineer") == pid), {})
        return ToolOutput(f"Cell {res.cell_id}: penguineer {command} as {pid}, outputs in {rec.get('folder')}; "
                          f"result dict pg_{pid}.\n{rec.get('summary', '')}\n"
                          + json.dumps(rec.get("result"), default=str)[:6000],
                          meta={"cell": res.cell_id, "penguineer": pid})

    def record_finding(self, inp: dict) -> ToolOutput:
        self.finding_n += 1
        fid = f"F{self.finding_n}"
        code = finding_cell(fid, statement=inp["statement"], value_expr=inp["value_expression"], unit=inp.get("unit", ""),
                            verdict=inp["verdict"], confidence=inp["confidence"], hypothesis=inp.get("hypothesis", ""),
                            evidence=inp.get("evidence", []))
        res = self.sandbox.execute(code, kind="finding", meta={"finding_id": fid})
        if res.error:
            self.finding_n -= 1
            return ToolOutput(f"Finding not recorded: {res.error}", is_error=True)
        rec = next((r for r in res.results if r.get("id") == fid), {})
        return ToolOutput(f"Recorded {fid} in cell {res.cell_id}: value {rec.get('value')!r} {rec.get('unit', '')}. "
                          f"Use {{{{{fid}}}}} in the conclusion.")

    def suggest_catalog_fix(self, inp: dict) -> ToolOutput:
        sid = self.svc.catalog.suggest(inp["dataset"].removeprefix("ds."), f"run:{self.row['run_id']}",
                                       inp.get("kind", "other"), inp["text"])
        return ToolOutput(f"Suggestion {sid} filed for catalog admins.")

    def finish_analysis(self, inp: dict, reviewer: Model) -> ToolOutput:
        findings = self._findings()
        if not findings:
            return ToolOutput("No findings recorded yet; record findings before finishing.", is_error=True)
        self.review = run_review(reviewer, self.plan, self.sandbox, findings)
        self.review_requested = True
        write_json(self.run / "logs" / "review.json", self.review)
        return ToolOutput("Independent review:\n" + json.dumps(self.review, indent=1)
                          + "\nAddress any issues, then call build_report.")

    def build_report(self, inp: dict) -> ToolOutput:
        if not self.review_requested:
            return ToolOutput("Call finish_analysis first to get the independent review.", is_error=True)
        if self.sandbox is not None:  # the Report step of a separate sandbox has no kernel
            self.sandbox.execute("alc.check_results()", kind="check")
        try:
            out = render(self.run, ctx=self.report_ctx(), report_input=inp, findings=self._findings(),
                         queries=self._queries())
        except ReportError as e:
            return ToolOutput(f"Report rejected: {e}", is_error=True)
        write_json(self.run / "logs" / "report_input.json", inp)
        extra = []
        for r in get_registry().report_renderers:  # plugin outputs, written before the run is sealed
            extra += r.render(self.run, {"ctx": self.report_ctx(), "report_input": inp, "findings": self._findings(),
                                         "queries": self._queries(), "conclusion_text": out["conclusion_text"]})
        if extra:
            out["extra"] = extra
        self.report_out = out
        return ToolOutput(f"Report built.\n\n{out['conclusion_text']}")

    # ---------- provenance ----------
    def _findings(self) -> list[dict]:
        f = self.run / "findings.json"
        return read_json(f)["findings"] if f.exists() else []

    def _queries(self) -> dict:
        f = self.run / "inputs" / "queries.json"
        return read_json(f) if f.exists() else {}

    def report_ctx(self) -> dict:
        return {"run_id": self.row["run_id"], "created": self.row.get("created_at", ""), "asker": self.question["asker"],
                "workspace": self.row["workspace"], "plants": sorted(self.plants), "question": self.question["text"],
                "plan": self.plan or {"scope": {}, "hypotheses": []}, "review": self.review or {},
                "citations": self.citations, "run_path": str(self.run), "env": environment(), "models": self.models,
                "steering": steering.record(self.svc, self.row["run_id"], self.run)}

    def write_manifest(self, status: str, usage: dict | None = None, extra: dict | None = None) -> dict:
        views_file = self.run / "inputs" / "views.json"
        views = read_json(views_file) if views_file.exists() else {}
        nb_file = self.run / "notebook.ipynb"
        cells = []
        if nb_file.exists():
            import hashlib
            for c in json.loads(nb_file.read_text())["cells"]:
                if c["cell_type"] == "code":
                    meta = c.get("metadata", {}).get("alchemist", {})
                    src = "".join(c["source"]) if isinstance(c["source"], list) else c["source"]
                    cells.append({"id": meta.get("cell_id"), "kind": meta.get("kind"),
                                  "sha256": hashlib.sha256(src.encode()).hexdigest()})
        manifest = {
            "schema": "alchemist.manifest/v1",
            "run_id": self.row["run_id"], "mode": self.row.get("mode", "live"), "parent_run": self.row.get("parent_run"),
            "status": status, "updated_at": now(),
            "question": {"id": self.question["id"], "text": self.question["text"], "scope": self.question.get("scope")},
            "plan": self.plan,
            "access": {"user": self.user_id, "workspace": self.row["workspace"], "project": self.row["project"],
                       "plants": sorted(self.plants), "credentials": self.credentials},
            "environment": environment(),
            "model": self.models,
            "plugins": get_registry().summary(),
            "catalog": [{"dataset": k, "card_version": v["card_version"]} for k, v in sorted(views.items())],
            "knowledge": {"citations": self.citations,
                          "structured": read_json(self.run / "inputs" / "knowledge_index.json")
                          if (self.run / "inputs" / "knowledge_index.json").exists() else []},
            "queries": self._queries(),
            "cells": cells,
            "findings": "findings.json",
            "result_hashes": {f["id"]: f["value_sha256"] for f in self._findings()},
            "steering": steering.record(self.svc, self.row["run_id"], self.run),
            "usage": usage or {},
            **(extra or {}),
        }
        write_json(self.run / "manifest.json", manifest)
        return manifest


# ------------------------------------------------------------------ review

def notebook_digest(sandbox: Sandbox, limit: int = 60000) -> str:
    parts = []
    for c in sandbox.nb.cells:
        if c.cell_type != "code":
            continue
        meta = c.metadata.get("alchemist", {})
        outs = []
        for o in c.outputs:
            if o.output_type == "stream":
                outs.append(o.text)
            elif o.output_type in ("execute_result", "display_data"):
                outs.append(o.data.get("text/plain", "[figure]") if "image/png" not in o.data else "[figure]")
            elif o.output_type == "error":
                outs.append(f"{o.ename}: {o.evalue}")
        parts.append(f"### cell {meta.get('cell_id')} ({meta.get('kind')})\n{c.source}\n--- output ---\n{''.join(outs)[:3000]}")
    return "\n\n".join(parts)[-limit:]


def run_review(reviewer: Model, plan: dict, sandbox: Sandbox, findings: list[dict]) -> dict:
    msg = (f"Plan:\n{json.dumps(plan, indent=1)}\n\nNotebook:\n{notebook_digest(sandbox)}\n\n"
           f"Findings:\n{json.dumps(findings, indent=1, default=str)}")
    resp = reviewer.create(system=prompts.REVIEW_SYSTEM, tools=[], messages=[{"role": "user", "content": msg}])
    text = "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        review = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        review = {}
    return {"ok": bool(review.get("ok", False)), "issues": review.get("issues", []),
            "notes": review.get("notes", text[:2000]), "model": reviewer.name}


class ScriptedReviewer:
    def __init__(self, review: dict, name: str = "scripted-reviewer"):
        self.review, self.name = review, name

    def create(self, *, system, tools, messages):
        return {"content": [{"type": "text", "text": json.dumps(self.review)}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0}}


# ------------------------------------------------------------------ lifecycle

def check_capacity(svc: Services, user: str) -> None:
    """Refuse a new planning or analysis step while the user (or everyone together) is at the limit."""
    s = svc.settings
    if s.max_active_runs_per_user and len(svc.catalog.active_runs(user_id=user)) >= s.max_active_runs_per_user:
        raise RunLimit(f"you already have {s.max_active_runs_per_user} runs planning or running; wait for one to "
                       "finish or stop one")
    if s.max_active_runs and len(svc.catalog.active_runs()) >= s.max_active_runs:
        raise RunLimit(f"{s.max_active_runs} runs are planning or running already; try again when one finishes")


def end_run(svc: Services, run_id: str, status: str, *, actor: str, reason: str,
            from_status: tuple[str, ...] = OPEN) -> bool:
    """Close a run that will not be sealed (cancelled, failed, expired), if it is still open. False if it was not."""
    row = svc.catalog.run(run_id)
    if not svc.catalog.transition(run_id, from_status, status):
        return False
    action = {"cancelled": "run.cancel", "expired": "run.expire"}.get(status, "run.fail")
    svc.catalog.audit(actor, action, run_id, {"error" if action == "run.fail" else "reason": reason[:1000],
                                              "from": row["status"]})
    if row["mode"] == "live":
        try:
            update_question(as_path(row["path"]).parent.parent, status=status)
        except (FileNotFoundError, OSError):
            pass
    return True


def _owner(svc: Services, row: dict, user: "str | User", what: str) -> "User":
    u = svc.access.resolve(user)
    if u.id != row["user_id"] and not u.is_admin:
        raise AccessDenied(f"only the run's owner or an admin can {what} it")
    return u


def cancel(svc: Services, run_id: str, *, user: "str | User") -> str:
    """Stop a run as its owner or an admin. Returns what happened:

    - "cancelled": it was planning or waiting for approval, and is closed at once (nothing was read from the lake);
    - "stopping": it is analysing, and stops after the current step, then seals a partial report;
    - "finishing": it was paused; it must be resumed once to build and seal that partial report (the caller then
      hands it to the launcher's resume)."""
    row = svc.catalog.run(run_id)
    u = _owner(svc, row, user, "stop")
    if row["status"] not in OPEN + ("paused",):
        raise ValueError(f"run {run_id} is {row['status']}")
    svc.catalog.audit(u.id, "run.stop", run_id)  # the running step sees this between model turns
    if row["status"] == "paused":
        if svc.catalog.transition(run_id, "paused", "running"):
            return "finishing"
        raise ValueError(f"run {run_id} is {svc.catalog.run(run_id)['status']}")
    if row["status"] != "running" and end_run(svc, run_id, "cancelled", actor=u.id, reason=f"cancelled by {u.id}",
                                             from_status=("planning", "awaiting_approval")):
        return "cancelled"
    return "stopping"


def pause(svc: Services, run_id: str, *, user: "str | User") -> None:
    """Ask an analysing run to pause after its current step. Its conversation and notebook are already saved after
    every step; the step then ends (in AWS its tasks stop) and the run waits, at no cost, for resume()."""
    row = svc.catalog.run(run_id)
    u = _owner(svc, row, user, "pause")
    if row["status"] != "running" or row["mode"] != "live":
        raise ValueError(f"only a running analysis can be paused; run {run_id} is {row['status']}")
    svc.catalog.audit(u.id, "run.pause", run_id)


def resume(svc: Services, run_id: str, *, user: "str | User") -> None:
    """Mark a paused run running again; the caller hands it to the launcher's resume(), whose analysis step picks
    up from the checkpoint."""
    row = svc.catalog.run(run_id)
    u = _owner(svc, row, user, "resume")
    if row["status"] != "paused":
        raise ValueError(f"run {run_id} is {row['status']}, not paused")
    # Without a checkpoint (paused before its first step finished) the analysis simply starts again.
    check_capacity(svc, row["user_id"])
    if not svc.catalog.transition(run_id, "paused", "running"):
        raise ValueError(f"run {run_id} is {svc.catalog.run(run_id)['status']}, not paused")
    svc.catalog.audit(u.id, "run.resume", run_id)
    update_question(as_path(row["path"]).parent.parent, status="running")


def note(svc: Services, run_id: str, *, user: "str | User", text: str) -> None:
    """Leave a note for the agent as the run's owner or an admin. A planning or analysing run receives it before its
    next model call; a run waiting for approval or paused receives it when its analysis starts or resumes."""
    row = svc.catalog.run(run_id)
    u = _owner(svc, row, user, "steer")
    if row["status"] not in steering.NOTE_STATUSES or row["mode"] != "live":
        raise ValueError(f"run {run_id} is {row['status']}; notes reach only a run that is still working")
    steering.add(svc, run_id, author=u.id, text=text)


def send_back(svc: Services, run_id: str, *, user: "str | User", text: str) -> None:
    """Return a proposed plan to planning with a note saying what to change; the caller hands the run to the
    launcher's replan(), whose planning step continues its conversation with the note and submits a new version."""
    row = svc.catalog.run(run_id)
    if row["status"] != "awaiting_approval":
        raise ValueError(f"run {run_id} is {row['status']}, not awaiting approval")
    question = read_yaml(as_path(row["path"]).parent.parent / "question.yaml")
    u = svc.access.resolve(user)
    if u.id != question["asker"] and not u.is_admin:
        raise AccessDenied("only the person who asked (or an admin) can send the plan back")
    if not text.strip():
        raise ValueError("say what should change in the plan")
    check_capacity(svc, row["user_id"])
    if not svc.catalog.transition(run_id, "awaiting_approval", "planning"):
        raise ValueError(f"run {run_id} is {svc.catalog.run(run_id)['status']}, not awaiting approval")
    steering.add(svc, run_id, author=u.id, text=text, kind="send_back", phase="awaiting_approval")
    svc.catalog.audit(u.id, "plan.send_back", run_id, {"version": (question.get("plan") or {}).get("version")})
    update_question(as_path(row["path"]).parent.parent, status="planning")


def interrupted(svc: Services, run_id: str, reason: str) -> str | None:
    """A step died without saying so. An analysis with a checkpoint is paused, so it can be resumed from its last
    step; anything else fails. Returns the new status, or None if the run had already moved on."""
    row = svc.catalog.run(run_id)
    if row["status"] == "running" and not row.get("seal_sha256") and checkpoint_of(row["path"]):
        if svc.catalog.transition(run_id, "running", "paused"):
            svc.catalog.audit("system", "run.paused", run_id, {"reason": f"interrupted: {reason}"[:1000]})
            update_question(as_path(row["path"]).parent.parent, status="paused")
            return "paused"
        return None
    return "failed" if end_run(svc, run_id, "failed", actor="system", reason=reason, from_status=ACTIVE) else None


def create(svc: Services, *, user: "str | User", workspace: str, project: str, question: str, scope: dict) -> dict:
    """Check access, create the question and run folders and index the run. Returns the run row."""
    s, acc = svc.settings, svc.access
    u = svc.access.resolve(user)
    user = u.id
    plants = acc.run_plants(u, workspace, scope.get("plants"))
    check_capacity(svc, user)
    ensure_project(s.workspace_root, workspace, project, owner=user, plants=sorted(plants))
    qid, qdir = create_question(s.workspace_root, workspace, project, text=question, asker=user, scope=scope)
    rid, rdir = create_run(qdir)
    row = {"run_id": rid, "question_id": qid, "project": project, "workspace": workspace, "user_id": user,
           "path": str(rdir), "mode": "live", "status": "planning", "plants": json.dumps(sorted(plants))}
    svc.catalog.upsert_run(**row)
    svc.catalog.audit(user, "run.create", rid, {"plants": sorted(plants), "workspace": workspace})
    return svc.catalog.run(rid)


def plan(svc: Services, run_id: str, *, model: Model | None = None,
         should_stop: Callable[[], bool] | None = None) -> dict:
    """The planning step of a created run: search, then submit a plan for approval.

    The step saves its conversation after every turn. When a person sends the plan back, the next planning step
    continues that conversation with their note and submits the next version of the plan."""
    s = svc.settings
    row = svc.catalog.run(run_id)
    if row["status"] != "planning":  # cancelled before the step started
        return {"run_id": run_id, "status": row["status"], "plan": None, "ended": "not planning", "path": row["path"]}
    user = row["user_id"]
    ctx = RunContext(svc, row, user=user)
    rdir = ctx.run
    model = model or role_model(s, "planning")
    ctx.models["planning"] = model.name
    budget = Budget(s.max_steps, s.max_output_tokens_total)
    saved = _read_checkpoint(rdir / PLAN_CHECKPOINT)
    if ctx.plan:  # sent back: keep the earlier version, draft the next one
        ctx.revising, ctx.plan = ctx.plan, None
        history = rdir / "logs" / "plan_history.json"
        write_json(history, (read_json(history) if history.exists() else []) + [ctx.revising])
    steer = ctx.steering = steering.Steering(svc, run_id, rdir, phase="planning", budget=budget,
                                             delivered=saved.get("steering") if saved else None)
    handlers = {"search_catalog": ctx.search_catalog, "search_knowledge": ctx.search_knowledge,
                "submit_plan": ctx.submit_plan}
    extra_tools, extra_handlers = plugin_tools("plan", ctx)
    handlers.update(extra_handlers)
    first = (f"Question from {user}: {ctx.question['text']}\nScope given by the user: {json.dumps(ctx.question.get('scope') or {})}\n"
             f"Plants this run may read: {sorted(ctx.plants)}")
    try:
        ended = run_loop(model, system=prompts.PLAN_SYSTEM, tools=prompts.tool_list(prompts.PLAN_TOOLS, extra_tools),
                         handlers=handlers, first_message=first, done=lambda: ctx.plan is not None, budget=budget,
                         transcript=rdir / "logs" / "plan_transcript.jsonl", should_stop=should_stop, steer=steer,
                         checkpoint=lambda state: write_json(rdir / PLAN_CHECKPOINT, {
                             "schema": "alchemist.checkpoint/v1", "phase": "planning", "at": now(), **state,
                             "steering": steer.delivered}),
                         resume=saved)
    except Exception as e:  # a model or network failure leaves a failed run, not a run stuck in planning
        ended = f"error: {type(e).__name__}: {e}"
    if ctx.plan:
        status = "awaiting_approval"
        if not svc.catalog.transition(run_id, "planning", "awaiting_approval"):
            status = svc.catalog.run(run_id)["status"]  # cancelled (or failed) while planning: keep that
            update_question(ctx.qdir, status=status)
    elif ended.startswith("stopped: stop requested"):
        end_run(svc, run_id, "cancelled", actor=user, reason="stopped while planning", from_status=("planning",))
        status = svc.catalog.run(run_id)["status"]
    else:
        end_run(svc, run_id, "failed", actor="system", reason=f"planning ended without a plan: {ended}",
                from_status=("planning",))
        status = svc.catalog.run(run_id)["status"]
    ctx.write_manifest(status, usage={"planning": budget.as_dict()}, extra={"planning_ended": ended})
    return {"run_id": run_id, "status": status, "plan": ctx.plan, "ended": ended, "path": str(rdir)}


def ask(svc: Services, *, user: "str | User", workspace: str, project: str, question: str, scope: dict,
        model: Model | None = None) -> dict:
    """Create a run and plan it; it then waits for approval."""
    row = create(svc, user=user, workspace=workspace, project=project, question=question, scope=scope)
    return plan(svc, row["run_id"], model=model)


def approve(svc: Services, run_id: str, *, user: "str | User", plan: dict | None = None, model: Model | None = None,
            reviewer: Model | None = None, should_stop: Callable[[], bool] | None = None) -> dict:
    start_approval(svc, run_id, user=user, plan=plan)
    return analyse(svc, run_id, user=getattr(user, "id", user), model=model, reviewer=reviewer, should_stop=should_stop)


def start_approval(svc: Services, run_id: str, *, user: "str | User", plan: dict | None = None) -> dict:
    """Record the approval (with the user's edits, if any) and mark the run running. Returns the approved plan."""
    row = svc.catalog.run(run_id)
    if row["status"] != "awaiting_approval":
        raise ValueError(f"run {run_id} is {row['status']}, not awaiting approval")
    question = read_yaml(as_path(row["path"]).parent.parent / "question.yaml")
    u = svc.access.resolve(user)
    user = u.id
    if user != question["asker"] and not u.is_admin:
        raise AccessDenied("only the person who asked (or an admin) can approve the plan")
    check_capacity(svc, row["user_id"])
    if not svc.catalog.transition(run_id, "awaiting_approval", "running"):  # approved twice at once, or cancelled
        raise ValueError(f"run {run_id} is {svc.catalog.run(run_id)['status']}, not awaiting approval")
    try:
        return _record_approval(svc, row, user, plan)
    except Exception as e:  # the run must not look busy when nothing will run it
        end_run(svc, run_id, "failed", actor="system", reason=f"approval failed: {type(e).__name__}: {e}",
                from_status=("running",))
        raise


def _record_approval(svc: Services, row: dict, user: str, plan: dict | None) -> dict:
    run_id = row["run_id"]
    ctx = RunContext(svc, row, user=user)
    if plan is not None:  # the user edited the plan
        ctx.plan = {**plan, "version": (ctx.plan or {}).get("version", 1) + 1}
    ctx.plan = {**ctx.plan, "approved_by": user, "approved_at": now()}
    update_question(ctx.qdir, plan=ctx.plan, status="running")
    svc.catalog.audit(user, "plan.approve", run_id)

    # Pin what this run may read: approved card versions for its plants, and structured knowledge.
    views = {}
    for d in svc.catalog.queryable(ctx.plants):
        ds = d["dataset"]
        views[ds["id"]] = {"glob": svc.catalog.data_glob(ds), "format": ds["file_format"], "plant": ds["plant"],
                           "card_version": d["version"],
                           "columns": [{"name": c["name"], "exposure": c.get("exposure", "model")} for c in d["card"]["columns"]]}
    write_json(ctx.run / "inputs" / "views.json", views)
    write_json(ctx.run / "inputs" / "knowledge_index.json", svc.knowledge.copy_structured(ctx.run, ctx.plants))
    return ctx.plan


CHECKPOINT = "logs/checkpoint.json"
PLAN_CHECKPOINT = "logs/plan_checkpoint.json"


def checkpoint_of(run) -> dict | None:
    """The analysis checkpoint of a run folder: the conversation, budget and notebook position after its last
    completed turn. A run with one can be paused and resumed, and recovers from a crash or a timeout."""
    return _read_checkpoint(as_path(run) / CHECKPOINT)


def planned_models(run) -> dict:
    """The planning model the run's planning step recorded, so the analysis step's manifest keeps it."""
    f = as_path(run) / "manifest.json"
    try:
        recorded = (read_json(f).get("model") or {}) if f.exists() else {}
    except (json.JSONDecodeError, FileNotFoundError):
        return {}
    planning = recorded.get("planning") or recorded.get("lead")  # "lead": manifests from before the four roles
    return {"planning": planning} if planning else {}


def _read_checkpoint(f) -> dict | None:
    try:
        return read_json(f) if f.exists() else None
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def analyse(svc: Services, run_id: str, *, user: str, model: Model | None = None, reviewer: Model | None = None,
            should_stop: "Callable[[], bool | str | None] | None" = None, sandbox_url: str | None = None) -> dict:
    """The analysis step of an approved run: query, compute, review, report, seal (in-process; with a separate
    sandbox the workflow's Seal step seals).

    After every completed turn the step writes a checkpoint. If the run has one when the step starts (it was paused,
    or an earlier attempt crashed or timed out), the step resumes: a fresh kernel re-runs the notebook's cells on
    their stored snapshots, and the conversation continues where it stopped. A pause request, or a model provider
    that stays unavailable, ends the step with the run `paused` instead of sealing it."""
    from .llm import is_retryable

    s = svc.settings
    row = svc.catalog.run(run_id)
    if row["status"] != "running":
        raise ValueError(f"run {run_id} is {row['status']}, not approved and running")
    ctx = RunContext(svc, row, user=user)
    question = ctx.question
    views = read_json(ctx.run / "inputs" / "views.json")
    model = model or role_model(s, "analysis")
    reviewer = reviewer or role_model(s, "reviewer")
    ctx.models.update(planned_models(ctx.run), analysis=model.name, reviewer=reviewer.name)
    budget = Budget(s.max_steps, s.max_output_tokens_total)
    saved = checkpoint_of(ctx.run)
    # Notes are delivered once: a resumed step starts from its checkpoint's list (a note given in a turn that never
    # completed is given again), a new one from the notes the planning step delivered.
    delivered = saved.get("steering", steering.delivered_in(ctx.run)) if saved else [
        d for d in steering.delivered_in(ctx.run) if d["phase"] == "planning"]
    ctx.steering = steering.Steering(svc, run_id, ctx.run, phase="analysis", delivered=delivered, budget=budget)
    ended = "error"
    with svc.sandbox(ctx, views, run_mode="live", sandbox_url=sandbox_url) as sb:
        ctx.sandbox = sb
        if sb.kernel.credentials is not None:
            ctx.credentials = sb.kernel.credentials
        if saved:
            replay_errors = _restore(ctx, sb, budget, model, saved)
            svc.catalog.audit("system", "run.resumed", run_id, {"cells": saved["notebook"]["cells"],
                                                                 "steps": budget.steps, "cell_errors": replay_errors})
        else:
            sb.add_markdown(f"# {question['text']}\n\nRun `{run_id}` · approved plan v{ctx.plan.get('version')} · "
                            f"plants {', '.join(sorted(ctx.plants))}\n\nSet `run_mode = 'replay'` in the next cell to "
                            "re-execute on the stored snapshots.")
            sb.setup()
        handlers = {"search_catalog": ctx.search_catalog, "search_knowledge": ctx.search_knowledge,
                    "run_query": ctx.run_query, "run_python": ctx.run_python, "record_finding": ctx.record_finding,
                    "suggest_catalog_fix": ctx.suggest_catalog_fix,
                    "finish_analysis": lambda inp: ctx.finish_analysis(inp, reviewer),
                    "build_report": ctx.build_report,
                    **{t: (lambda inp, t=t: ctx.run_penguineer(t, inp)) for t in penguineer_tools.COMMANDS}}
        extra_tools, extra_handlers = plugin_tools("analysis", ctx)
        handlers.update(extra_handlers)
        first = (f"Question: {question['text']}\n\nApproved plan:\n{json.dumps(ctx.plan, indent=1)}\n\n"
                 f"Datasets you may query: {sorted(views)}. Structured knowledge available via alc.knowledge(name): "
                 f"{[k['name'] for k in read_json(ctx.run / 'inputs' / 'knowledge_index.json')]}.")
        methods = get_registry().method_docs()
        if methods:
            first += f"\nAnalysis methods available in the kernel as alc.methods.<name>(...): {json.dumps(methods)}"
        try:
            ended = run_loop(model, system=prompts.ANALYSIS_SYSTEM, tools=prompts.tool_list(prompts.ANALYSIS_TOOLS, extra_tools),
                             handlers=handlers, first_message=first, done=lambda: ctx.report_out is not None,
                             budget=budget, transcript=ctx.run / "logs" / "transcript.jsonl", should_stop=should_stop,
                             checkpoint=lambda state: _save_checkpoint(ctx, sb, budget, state), resume=saved,
                             steer=ctx.steering)
        except Exception as e:  # a model or network failure still ends in a sealed, partial run
            ended = f"error: {type(e).__name__}: {e}"
            if is_retryable(e) and checkpoint_of(ctx.run):  # the provider is down: keep the work, resume later
                ended = f"paused: the model provider is unavailable ({type(e).__name__}: {str(e)[:300]})"
        finally:
            if ctx.report_out is None and ctx._findings() and not ended.startswith("paused"):
                # Budget stop or failure: still leave a partial report of what was found.
                partial = {"title": f"Partial result: {question['text']}",
                           "conclusion": "The run stopped before it finished; findings so far are listed below.",
                           "top_causes": [], "limitations": [f"Run ended early: {ended}"]}
                ctx.review_requested = True
                ctx.build_report(partial)
    if ended.startswith("paused"):
        return _pause(svc, ctx, budget, ended)
    status = "completed" if ended == "done" else "partial"
    return _finish(svc, ctx, status, usage=budget.as_dict(), extra={"analysis_ended": ended})


def _save_checkpoint(ctx: RunContext, sb: Sandbox, budget: Budget, state: dict) -> None:
    write_json(ctx.run / CHECKPOINT, {
        "schema": "alchemist.checkpoint/v1", "phase": "analysis", "at": now(), **state,
        "budget": budget.as_dict(),
        "context": {"query_n": ctx.query_n, "finding_n": ctx.finding_n, "penguineer_n": ctx.penguineer_n,
                    "review_requested": ctx.review_requested,
                    "review": ctx.review},
        "notebook": {"cells": len(sb.nb.cells), "counter": sb.counter},
        "steering": ctx.steering.delivered if ctx.steering else []})


def _restore(ctx: RunContext, sb: Sandbox, budget: Budget, model: Model, saved: dict) -> list[dict]:
    """Put a resumed step back where the checkpoint left it: kernel state, counters, budget, model position."""
    errors = sb.restore(cells=saved["notebook"]["cells"], counter=saved["notebook"]["counter"])
    c = saved["context"]
    ctx.query_n, ctx.finding_n, ctx.penguineer_n = c["query_n"], c["finding_n"], c.get("penguineer_n", 0)
    ctx.review_requested, ctx.review = c["review_requested"], c["review"]
    b = saved["budget"]
    budget.steps, budget.model_calls = b["steps"], b["model_calls"]
    budget.input_tokens, budget.output_tokens = b["input_tokens"], b["output_tokens"]
    if hasattr(model, "advance"):  # the offline script continues from the turn it reached
        model.advance(budget.model_calls)
    sb.add_markdown(f"*Resumed {now()} from the checkpoint of {saved['at']}: the cells above were re-run on their "
                    "stored snapshots in a fresh kernel.*")
    return errors


def _pause(svc: Services, ctx: RunContext, budget: Budget, ended: str) -> dict:
    run_id = ctx.row["run_id"]
    ctx.write_manifest("paused", usage=budget.as_dict(), extra={"analysis_ended": ended})
    if svc.catalog.transition(run_id, "running", "paused"):
        svc.catalog.audit("system", "run.paused", run_id, {"reason": ended, "steps": budget.steps})
        update_question(ctx.qdir, status="paused")
    return {"run_id": run_id, "status": svc.catalog.run(run_id)["status"], "path": str(ctx.run), "seal_sha256": None,
            "analysis_ended": ended}


def _finish(svc: Services, ctx: RunContext, status: str, usage: dict, extra: dict | None = None) -> dict:
    ctx.write_manifest(status, usage=usage, extra=extra)
    if ctx.row.get("mode", "live") == "live":  # replays share the question; its status is the live run's
        update_question(ctx.qdir, status=status)
    out = {"run_id": ctx.row["run_id"], "status": status, "path": str(ctx.run), "seal_sha256": None,
           **(extra or {}), "conclusion": (ctx.report_out or {}).get("conclusion_text")}
    if not svc.seals_in_worker:
        svc.catalog.upsert_run(run_id=ctx.row["run_id"], status=status)
        return out
    seal_sha = seal(ctx.run, ctx.row["run_id"])
    svc.catalog.upsert_run(run_id=ctx.row["run_id"], status=status, seal_sha256=seal_sha)
    svc.catalog.audit(ctx.user_id, "run.seal", ctx.row["run_id"], {"status": status, "seal_sha256": seal_sha})
    return {**out, "seal_sha256": seal_sha}


def report_step(svc: Services, run_id: str) -> dict:
    """Separate sandbox, after Analyse (which may have crashed or timed out): make sure the run has a manifest and,
    if anything was found, a report. A run Analyse already finished is left as it is."""
    row = svc.catalog.run(run_id)
    if row["status"] != "running":
        return {"run_id": run_id, "status": row["status"], "ended": "already reported"}
    if checkpoint_of(row["path"]) and interrupted(svc, run_id, "the analysis step stopped before it finished") == "paused":
        return {"run_id": run_id, "status": "paused", "ended": "paused; resume continues from the last step"}
    ctx = RunContext(svc, row, user=(read_yaml(as_path(row["path"]).parent.parent / "question.yaml").get("plan") or {})
                     .get("approved_by") or row["user_id"])
    ended = "the analysis step stopped before it finished"
    if ctx._findings():
        ctx.review_requested = True
        ctx.build_report({"title": f"Partial result: {ctx.question['text']}",
                          "conclusion": "The run stopped before it finished; findings so far are listed below.",
                          "top_causes": [], "limitations": [f"Run ended early: {ended}"]})
    status = "partial" if ctx.report_out else "failed"
    ctx.write_manifest(status, extra={"analysis_ended": f"error: {ended}"})
    update_question(ctx.qdir, status=status)
    svc.catalog.upsert_run(run_id=run_id, status=status)
    svc.catalog.audit("system", "run.report", run_id, {"status": status})
    return {"run_id": run_id, "status": status, "ended": ended}


def seal_step(svc: Services, run_id: str) -> dict:
    """Separate sandbox, last step: seal the run folder (the sealer is the only role that may write SEALED)."""
    row = svc.catalog.run(run_id)
    if row.get("seal_sha256"):
        return {"run_id": run_id, "status": row["status"], "seal_sha256": row["seal_sha256"]}
    if row["status"] == "paused":  # sealed only once it finishes, after resume
        return {"run_id": run_id, "status": "paused", "seal_sha256": None}
    status = "failed" if row["status"] in ("planning", "running") else row["status"]  # a step died without a word
    run = as_path(row["path"])
    if (run / "SEALED").exists():  # sealed by an earlier attempt that failed before indexing it
        import hashlib

        seal_sha = hashlib.sha256((run / "SEALED").read_bytes()).hexdigest()
    else:
        seal_sha = seal(run, run_id)
    svc.catalog.upsert_run(run_id=run_id, status=status, seal_sha256=seal_sha)
    svc.catalog.audit("system", "run.seal", run_id, {"status": status, "seal_sha256": seal_sha})
    return {"run_id": run_id, "status": status, "seal_sha256": seal_sha}


def replay(svc: Services, run_id: str, *, user: "str | User", mode: str = "replay") -> dict:
    """Re-execute a sealed run's notebook as a new run: on its snapshots (replay) or on current data (rerun)."""
    row = start_replay(svc, run_id, user=user, mode=mode)
    return execute_replay(svc, row["run_id"])


def start_replay(svc: Services, run_id: str, *, user: "str | User", mode: str = "replay") -> dict:
    """Check access and the parent's seal, and create the replay run with the parent's inputs. Returns its row."""
    if mode not in ("replay", "rerun"):
        raise ValueError("mode must be replay or rerun")
    parent = svc.catalog.run(run_id)
    if not parent.get("seal_sha256"):
        raise ValueError("only sealed runs can be replayed")
    u = svc.access.resolve(user)
    user = u.id
    plants = json.loads(parent["plants"])
    if not svc.access.can_view_run(u, parent["workspace"], plants):
        raise AccessDenied(f"{user} may not open run {run_id}")
    check_capacity(svc, user)
    check = verify(parent["path"], parent["seal_sha256"])
    if not check["ok"]:
        raise ValueError(f"parent run failed verification: {check['problems'][:3]}")

    prun = as_path(parent["path"])
    qdir = prun.parent.parent
    rid, rdir = create_run(qdir)
    copy_tree(prun / "inputs", rdir / "inputs")
    write_json(rdir / "inputs" / "parent_findings.json", read_json(prun / "findings.json"))
    row = {"run_id": rid, "question_id": parent["question_id"], "project": parent["project"],
           "workspace": parent["workspace"], "user_id": user, "path": str(rdir), "mode": mode,
           "parent_run": run_id, "status": "running", "plants": parent["plants"]}
    svc.catalog.upsert_run(**row)
    svc.catalog.audit(user, f"run.{mode}", rid, {"parent": run_id})
    return svc.catalog.run(rid)


def execute_replay(svc: Services, run_id: str, *, sandbox_url: str | None = None) -> dict:
    """Run the parent's notebook cells in a fresh kernel, compare findings, then seal (in-process)."""
    row = svc.catalog.run(run_id)
    mode, parent_id = row["mode"], row["parent_run"]
    prun = as_path(svc.catalog.run(parent_id)["path"])
    rdir = as_path(row["path"])
    ctx = RunContext(svc, row, user=row["user_id"])
    ctx.models.update({k: v for k, v in read_json(prun / "manifest.json")["model"].items()})

    cells = [c for c in json.loads((prun / "notebook.ipynb").read_text())["cells"] if c["cell_type"] == "code"]
    errors = []
    views = read_json(rdir / "inputs" / "views.json") if (rdir / "inputs" / "views.json").exists() else {}
    if mode == "replay":
        views = {}  # a replay reads only the snapshots in its own folder, never the lake
    result_check = None
    with svc.sandbox(ctx, views, run_mode=mode, sandbox_url=sandbox_url) as sb:
        if sb.kernel.credentials is not None:
            ctx.credentials = sb.kernel.credentials
        sb.add_markdown(f"# {mode.capitalize()} of run `{parent_id}`\n\n{ctx.question['text']}")
        sb.setup()
        for c in cells:
            meta = c.get("metadata", {}).get("alchemist", {})
            if meta.get("kind") in ("parameters", "setup"):
                continue
            src = "".join(c["source"]) if isinstance(c["source"], list) else c["source"]
            res = sb.execute(src, kind=meta.get("kind", "python"), meta={k: v for k, v in meta.items() if k not in ("cell_id", "kind")})
            if res.error:
                errors.append({"cell": meta.get("cell_id"), "error": res.error[:500]})
            result_check = _result_check(res) or result_check
        if not any(c.get("metadata", {}).get("alchemist", {}).get("kind") == "check" for c in cells):
            result_check = _result_check(sb.execute("alc.check_results()", kind="check"))
    if result_check is None:
        raise RuntimeError("the replay's check cell reported no result")
    drift = plugin_drift(read_json(prun / "manifest.json").get("plugins", []), get_registry().summary())
    status = "completed" if not errors and result_check["status"]["mismatches"] == 0 else "mismatch"
    if mode == "rerun":
        status = "completed" if not errors else "failed"
    out = _finish(svc, ctx, status, usage={}, extra={"parent_run": parent_id, "result_check": result_check,
                                                     "cell_errors": errors, "plugin_drift": drift})
    return out


def _result_check(res) -> dict | None:
    """The comparison alc.check_results() prints; read from the cell output so the worker never reads work/."""
    return next((r["result_check"] for r in res.results if "result_check" in r), None)


def plugin_drift(then: list[dict], now_: list[dict]) -> list[str]:
    """Plugins added, removed or at another version since the parent run; replays report them."""
    a = {p["name"]: p.get("version") for p in then if p["name"] != "builtin"}
    b = {p["name"]: p.get("version") for p in now_ if p["name"] != "builtin"}
    out = [f"{n} removed" for n in sorted(set(a) - set(b))] + [f"{n} added" for n in sorted(set(b) - set(a))]
    out += [f"{n} {a[n]} -> {b[n]}" for n in sorted(set(a) & set(b)) if a[n] != b[n]]
    return out
