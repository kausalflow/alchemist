"""Run lifecycle: plan, approve, analyse, review, report, seal; and replay.

    ask()      creates the question and run, runs the planning step, and stops for approval
    approve()  runs the analysis in a fresh sandbox, then reports and seals the run
    replay()   re-executes a sealed run's notebook on its snapshots (or on current data)
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import re
from pathlib import Path
from typing import Callable

from . import prompts
from .access import AccessDenied, User
from .agent import Budget, Model, ToolOutput, run_loop
from .llm import PydanticAIModel
from .catalog import Catalog, now
from .credentials import AMBIENT_AWS_VARS, CredentialBroker
from .knowledge import Knowledge
from .paths import as_path, copy_tree, read_json, read_yaml, write_json
from .plugins import get_registry
from .report import ReportError, render
from .sandbox import Sandbox, finding_cell, query_cell
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
        self.settings = settings
        self.catalog = catalog or Catalog(settings.db_path)
        self.access = access or reg.access_providers.get(settings.access_provider).load(settings)
        self.knowledge = knowledge or Knowledge(settings.knowledge_root, packs=list(reg.knowledge_packs))
        self.broker = broker or CredentialBroker.from_settings(settings)

    def sandbox_env(self, ctx: "RunContext", views: dict) -> dict:
        """Kernel environment: the pseudonym key, and the run's own S3 credentials when a run role is set."""
        env = {"ALC_PSEUDONYM_KEY": self.settings.pseudonym_key}
        creds = self.broker.issue(run_id=ctx.row["run_id"], user_id=ctx.user_id, workspace=ctx.row["workspace"],
                                  run_dir=str(ctx.run), read_globs=[v["glob"] for v in views.values()],
                                  policy_out=ctx.run / "logs" / "session_policy.json")
        ctx.credentials = creds.record() if creds else {"mode": "ambient"}
        if creds:
            env.update(creds.env())
            env.update({k: None for k in AMBIENT_AWS_VARS})
        return env


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
        self.sandbox: Sandbox | None = None
        self.query_n = self.finding_n = 0
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
        self.plan = {"version": 1, **inp, "submitted_at": now()}
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
        return ToolOutput(f"Cell {res.cell_id}: query {qid} saved as {summary.get('snapshot')}; frame df_{qid}.\n"
                          + json.dumps({k: summary.get(k) for k in ("rows", "columns", "stats", "head")}, default=str)[:6000])

    def run_python(self, inp: dict) -> ToolOutput:
        res = self.sandbox.execute(inp["code"], kind="python", meta={"purpose": inp.get("purpose", "")})
        figs = f"\nFigures: {res.figure_paths}" if res.figure_paths else ""
        return ToolOutput(f"Cell {res.cell_id}:\n{res.model_text()}{figs}", images=res.images, is_error=bool(res.error))

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
                "citations": self.citations, "run_path": str(self.run), "env": environment(), "models": self.models}

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


def cancel(svc: Services, run_id: str, *, user: "str | User") -> str:
    """Stop a run as its owner or an admin. Planning or waiting for approval: the run is cancelled at once (nothing
    was read from the lake yet). Analysing: it stops after the current step and seals a partial report.
    Returns what happened: "cancelled" or "stopping"."""
    u = svc.access.resolve(user)
    row = svc.catalog.run(run_id)
    if u.id != row["user_id"] and not u.is_admin:
        raise AccessDenied("only the run's owner or an admin can stop it")
    if row["status"] not in OPEN:
        raise ValueError(f"run {run_id} is {row['status']}")
    svc.catalog.audit(u.id, "run.stop", run_id)  # the running step sees this between model turns
    if row["status"] != "running" and end_run(svc, run_id, "cancelled", actor=u.id, reason=f"cancelled by {u.id}",
                                             from_status=("planning", "awaiting_approval")):
        return "cancelled"
    return "stopping"


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
    """The planning step of a created run: search, then submit a plan for approval."""
    s = svc.settings
    row = svc.catalog.run(run_id)
    if row["status"] != "planning":  # cancelled before the step started
        return {"run_id": run_id, "status": row["status"], "plan": None, "ended": "not planning", "path": row["path"]}
    user = row["user_id"]
    ctx = RunContext(svc, row, user=user)
    rdir = ctx.run
    model = model or PydanticAIModel(s)
    ctx.models["lead"] = model.name
    budget = Budget(s.max_steps, s.max_output_tokens_total)
    handlers = {"search_catalog": ctx.search_catalog, "search_knowledge": ctx.search_knowledge,
                "submit_plan": ctx.submit_plan}
    extra_tools, extra_handlers = plugin_tools("plan", ctx)
    handlers.update(extra_handlers)
    first = (f"Question from {user}: {ctx.question['text']}\nScope given by the user: {json.dumps(ctx.question.get('scope') or {})}\n"
             f"Plants this run may read: {sorted(ctx.plants)}")
    try:
        ended = run_loop(model, system=prompts.PLAN_SYSTEM, tools=prompts.tool_list(prompts.PLAN_TOOLS, extra_tools),
                         handlers=handlers, first_message=first, done=lambda: ctx.plan is not None, budget=budget,
                         transcript=rdir / "logs" / "plan_transcript.jsonl", should_stop=should_stop)
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


def analyse(svc: Services, run_id: str, *, user: str, model: Model | None = None, reviewer: Model | None = None,
            should_stop: Callable[[], bool] | None = None) -> dict:
    """The analysis step of an approved run: query, compute, review, report, seal."""
    s = svc.settings
    row = svc.catalog.run(run_id)
    if row["status"] != "running":
        raise ValueError(f"run {run_id} is {row['status']}, not approved and running")
    ctx = RunContext(svc, row, user=user)
    question = ctx.question
    views = read_json(ctx.run / "inputs" / "views.json")
    model = model or PydanticAIModel(s)
    reviewer = reviewer or PydanticAIModel(s, s.sub_model, effort="medium")
    ctx.models.update(lead=model.name, reviewer=reviewer.name)
    budget = Budget(s.max_steps, s.max_output_tokens_total)
    ended = "error"
    with Sandbox(str(ctx.run), run_mode="live", seed=s.seed, extra_env=svc.sandbox_env(ctx, views)) as sb:
        ctx.sandbox = sb
        sb.add_markdown(f"# {question['text']}\n\nRun `{run_id}` · approved plan v{ctx.plan.get('version')} · "
                        f"plants {', '.join(sorted(ctx.plants))}\n\nSet `run_mode = 'replay'` in the next cell to "
                        "re-execute on the stored snapshots.")
        sb.setup()
        handlers = {"search_catalog": ctx.search_catalog, "search_knowledge": ctx.search_knowledge,
                    "run_query": ctx.run_query, "run_python": ctx.run_python, "record_finding": ctx.record_finding,
                    "suggest_catalog_fix": ctx.suggest_catalog_fix,
                    "finish_analysis": lambda inp: ctx.finish_analysis(inp, reviewer),
                    "build_report": ctx.build_report}
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
                             budget=budget, transcript=ctx.run / "logs" / "transcript.jsonl", should_stop=should_stop)
        except Exception as e:  # a model or network failure still ends in a sealed, partial run
            ended = f"error: {type(e).__name__}: {e}"
        finally:
            if ctx.report_out is None and ctx._findings():
                # Budget stop or failure: still leave a partial report of what was found.
                partial = {"title": f"Partial result: {question['text']}",
                           "conclusion": "The run stopped before it finished; findings so far are listed below.",
                           "top_causes": [], "limitations": [f"Run ended early: {ended}"]}
                ctx.review_requested = True
                ctx.build_report(partial)
    status = "completed" if ended == "done" else "partial"
    return _finish(svc, ctx, status, usage=budget.as_dict(), extra={"analysis_ended": ended})


def _finish(svc: Services, ctx: RunContext, status: str, usage: dict, extra: dict | None = None) -> dict:
    ctx.write_manifest(status, usage=usage, extra=extra)
    if ctx.row.get("mode", "live") == "live":  # replays share the question; its status is the live run's
        update_question(ctx.qdir, status=status)
    seal_sha = seal(ctx.run, ctx.row["run_id"])
    svc.catalog.upsert_run(run_id=ctx.row["run_id"], status=status, seal_sha256=seal_sha)
    svc.catalog.audit(ctx.user_id, "run.seal", ctx.row["run_id"], {"status": status, "seal_sha256": seal_sha})
    return {"run_id": ctx.row["run_id"], "status": status, "path": str(ctx.run), "seal_sha256": seal_sha,
            **(extra or {}), "conclusion": (ctx.report_out or {}).get("conclusion_text")}


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


def execute_replay(svc: Services, run_id: str) -> dict:
    """Run the parent's notebook cells in a fresh kernel, compare findings, then seal."""
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
    with Sandbox(str(rdir), run_mode=mode, seed=svc.settings.seed, extra_env=svc.sandbox_env(ctx, views)) as sb:
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
        if not any(c.get("metadata", {}).get("alchemist", {}).get("kind") == "check" for c in cells):
            sb.execute("alc.check_results()", kind="check")
    result_check = read_json(rdir / "work" / "result_check.json")
    drift = plugin_drift(read_json(prun / "manifest.json").get("plugins", []), get_registry().summary())
    status = "completed" if not errors and result_check["status"]["mismatches"] == 0 else "mismatch"
    if mode == "rerun":
        status = "completed" if not errors else "failed"
    out = _finish(svc, ctx, status, usage={}, extra={"parent_run": parent_id, "result_check": result_check,
                                                     "cell_errors": errors, "plugin_drift": drift})
    return out


def plugin_drift(then: list[dict], now_: list[dict]) -> list[str]:
    """Plugins added, removed or at another version since the parent run; replays report them."""
    a = {p["name"]: p.get("version") for p in then if p["name"] != "builtin"}
    b = {p["name"]: p.get("version") for p in now_ if p["name"] != "builtin"}
    out = [f"{n} removed" for n in sorted(set(a) - set(b))] + [f"{n} added" for n in sorted(set(b) - set(a))]
    out += [f"{n} {a[n]} -> {b[n]}" for n in sorted(set(a) & set(b)) if a[n] != b[n]]
    return out
