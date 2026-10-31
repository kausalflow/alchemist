"""Starting the long steps of a run (planning, analysis, replay) away from the request.

The API only records what was asked and hands the step to a launcher, then returns at once; the browser follows
progress through the run's event stream. `LocalLauncher` runs steps on a thread pool in the API process, which is
what `alchemist serve` uses on a laptop. `alchemist.runqueue.QueueLauncher` puts them in the run queue for
`alchemist worker` processes (docker compose, Kubernetes, or AWS with workers). In AWS the launcher starts the run's Step Functions execution (planning)
and sends the task token on approval instead; everything a step needs is in the database and the run folder, so a
step can run in any process.

Stopping is a flag in the database (an audit event), so it works whichever process runs the step. A run that
is planning or waiting for approval is cancelled at once, and `cancel()` also ends its workflow execution.

`reconcile()` runs every ALC_RECONCILE_SECONDS in the API. It closes runs whose step died without saying so (the
API restarted under a thread, an ECS task was killed or timed out, approval timed out), so no run looks busy
forever.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Protocol

from ..config import Settings

log = logging.getLogger("alchemist.api")


class Launcher(Protocol):
    def plan(self, run_id: str) -> None: ...

    def analyse(self, run_id: str, user: str) -> None: ...

    def replay(self, run_id: str) -> None: ...

    def resume(self, run_id: str) -> None: ...

    def replan(self, run_id: str) -> None: ...

    def cancel(self, run_id: str) -> None: ...

    def reconcile(self) -> None: ...


def models(settings: Settings, phase: str):
    """Lead and reviewer models for API runs. ALC_API_MODEL=scripted plays the offline demo script instead."""
    if os.environ.get("ALC_API_MODEL", "default") == "scripted":
        from ..agent import ScriptedModel
        from ..demo import script
        from ..run import ScriptedReviewer

        turns = script.PLAN_TURNS if phase == "plan" else script.ANALYSIS_TURNS
        return ScriptedModel(turns), ScriptedReviewer(script.REVIEW)
    return None, None  # run.py uses the role models (model_roles.py)


def stop_requested(svc, run_id: str) -> str | None:
    """"stop" or "pause" if a user asked for it since the run last (re)started, else None. Stop wins."""
    rows = svc.catalog.db.execute("select action, max(id) as id from audit_events where target = ? and action in"
                                  " ('run.stop', 'run.pause', 'run.resume') group by action", (run_id,)).fetchall()
    last = {r["action"]: r["id"] for r in rows}
    if "run.stop" in last:
        return "stop"  # a stop is never undone; a stopped paused run is resumed only to seal it
    if last.get("run.pause", -1) > last.get("run.resume", -1):
        return "pause"
    return None


def mark_failed(svc, run_id: str, error: "Exception | str") -> None:
    """A step that crashed outside its own error handling must not leave the run looking busy."""
    from ..run import ACTIVE, end_run

    reason = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    end_run(svc, run_id, "failed", actor="system", reason=reason, from_status=ACTIVE)


def approved_by(services_factory, run_id: str) -> str:
    from ..paths import as_path, read_yaml

    svc = services_factory()
    try:
        row = svc.catalog.run(run_id)
    finally:
        svc.catalog.db.close()
    q = read_yaml(as_path(row["path"]).parent.parent / "question.yaml")
    return (q.get("plan") or {}).get("approved_by") or row["user_id"]


def last_launch(svc, run_id: str) -> dict | None:
    row = svc.catalog.db.execute("select detail from audit_events where action = 'run.launch' and target = ?"
                                 " order by id desc limit 1", (run_id,)).fetchone()
    return json.loads(row["detail"]) if row and row["detail"] else None


def run_step(services_factory: Callable[[], object], run_id: str, step: str, user: str | None = None) -> str | None:
    """Run one step of a run ("plan", "analyse" or "replay") in this thread, with its own database connection.

    A step that finds the run has moved on (cancelled while queued) does nothing. A step that crashes outside its own
    error handling pauses the run if it can resume from a checkpoint, and fails it otherwise. Returns the error, if
    any."""
    from ..run import analyse, execute_replay, interrupted, plan

    try:
        svc = services_factory()  # one connection per thread: SQLite connections are not shared across threads
        try:
            status = svc.catalog.run(run_id)["status"]
            if step == "plan":
                if status == "planning":
                    model, _ = models(svc.settings, "plan")
                    plan(svc, run_id, model=model, should_stop=lambda: stop_requested(svc, run_id))
            elif step == "analyse":
                if status == "running":
                    model, reviewer = models(svc.settings, "analysis")
                    analyse(svc, run_id, user=user or approved_by(services_factory, run_id), model=model,
                            reviewer=reviewer, should_stop=lambda: stop_requested(svc, run_id))
            elif step == "replay":
                execute_replay(svc, run_id)
            else:
                raise ValueError(f"unknown step {step!r}")
        finally:
            svc.catalog.db.close()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        log.error("run %s step %s failed:\n%s", run_id, step, traceback.format_exc())
        svc = services_factory()
        try:
            interrupted(svc, run_id, error)
        finally:
            svc.catalog.db.close()
        return error
    return None


class LocalLauncher:
    """Steps on a thread pool in this process. One `alchemist serve` per database: a run this process did not
    launch but another LocalLauncher did is taken to have died with that process."""

    def __init__(self, services_factory: Callable[[], object], workers: int | None = None):
        self.services = services_factory
        workers = workers or int(os.environ.get("ALC_LOCAL_WORKERS", "2"))
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="alc-run")
        self.instance = secrets.token_hex(8)

    def _submit(self, run_id: str, step: str, user: str | None = None):
        svc = self.services()
        try:
            svc.catalog.audit("system", "run.launch", run_id, {"launcher": "local", "instance": self.instance})
        finally:
            svc.catalog.db.close()
        return self.pool.submit(run_step, self.services, run_id, step, user)

    def cancel(self, run_id: str) -> None:
        pass  # the step sees the stop flag; a queued step finds the run cancelled and does nothing

    def reconcile(self) -> None:
        from ..run import ACTIVE, interrupted

        svc = self.services()
        try:
            for row in svc.catalog.active_runs(ACTIVE):
                launch = last_launch(svc, row["run_id"])
                if launch and launch.get("launcher") == "local" and launch.get("instance") != self.instance:
                    interrupted(svc, row["run_id"], "the server restarted while this step was running")
        finally:
            svc.catalog.db.close()

    def plan(self, run_id: str) -> None:
        self._submit(run_id, "plan")

    def resume(self, run_id: str) -> None:
        self.analyse(run_id, approved_by(self.services, run_id))

    def replan(self, run_id: str) -> None:
        self.plan(run_id)  # the planning step continues its saved conversation with the note

    def analyse(self, run_id: str, user: str) -> None:
        self._submit(run_id, "analyse", user)

    def replay(self, run_id: str) -> None:
        self._submit(run_id, "replay")

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True)
