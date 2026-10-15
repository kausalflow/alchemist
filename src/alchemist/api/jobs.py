"""Starting the long steps of a run (planning, analysis, replay) away from the request.

The API only records what was asked and hands the step to a launcher, then returns at once; the browser follows
progress through the run's event stream. `LocalLauncher` runs steps on a thread pool in the API process, which is
what `alchemist serve` uses on a laptop. In AWS the launcher starts the run's Step Functions execution (planning)
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
    return None, None  # run.py builds PydanticAIModel from settings (ALC_LEAD_MODEL, ALC_SUB_MODEL)


def stop_requested(svc, run_id: str) -> bool:
    row = svc.catalog.db.execute("select 1 from audit_events where action = 'run.stop' and target = ? limit 1",
                                 (run_id,)).fetchone()
    return row is not None


def mark_failed(svc, run_id: str, error: "Exception | str") -> None:
    """A step that crashed outside its own error handling must not leave the run looking busy."""
    from ..run import ACTIVE, end_run

    reason = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    end_run(svc, run_id, "failed", actor="system", reason=reason, from_status=ACTIVE)


def last_launch(svc, run_id: str) -> dict | None:
    row = svc.catalog.db.execute("select detail from audit_events where action = 'run.launch' and target = ?"
                                 " order by id desc limit 1", (run_id,)).fetchone()
    return json.loads(row["detail"]) if row and row["detail"] else None


class LocalLauncher:
    """Steps on a thread pool in this process. One `alchemist serve` per database: a run this process did not
    launch but another LocalLauncher did is taken to have died with that process."""

    def __init__(self, services_factory: Callable[[], object], workers: int | None = None):
        self.services = services_factory
        workers = workers or int(os.environ.get("ALC_LOCAL_WORKERS", "2"))
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="alc-run")
        self.instance = secrets.token_hex(8)

    def _submit(self, run_id: str, fn):
        svc = self.services()
        try:
            svc.catalog.audit("system", "run.launch", run_id, {"launcher": "local", "instance": self.instance})
        finally:
            svc.catalog.db.close()

        def wrapped():
            try:
                fn()
            except Exception as e:
                log.error("run %s step failed:\n%s", run_id, traceback.format_exc())
                mark_failed(self.services(), run_id, e)
        return self.pool.submit(wrapped)

    def cancel(self, run_id: str) -> None:
        pass  # the step sees the stop flag; a queued step finds the run cancelled and does nothing

    def reconcile(self) -> None:
        from ..run import ACTIVE

        svc = self.services()
        try:
            for row in svc.catalog.active_runs(ACTIVE):
                launch = last_launch(svc, row["run_id"])
                if launch and launch.get("launcher") == "local" and launch.get("instance") != self.instance:
                    mark_failed(svc, row["run_id"], "the server restarted while this step was running")
        finally:
            svc.catalog.db.close()

    def plan(self, run_id: str) -> None:
        def go():
            from ..run import plan

            svc = self.services()  # one connection per thread: SQLite connections are not shared across threads
            if svc.catalog.run(run_id)["status"] != "planning":
                return  # cancelled while queued
            model, _ = models(svc.settings, "plan")
            plan(svc, run_id, model=model, should_stop=lambda: stop_requested(svc, run_id))
        self._submit(run_id, go)

    def analyse(self, run_id: str, user: str) -> None:
        def go():
            from ..run import analyse

            svc = self.services()
            if svc.catalog.run(run_id)["status"] != "running":
                return
            model, reviewer = models(svc.settings, "analysis")
            analyse(svc, run_id, user=user, model=model, reviewer=reviewer,
                    should_stop=lambda: stop_requested(svc, run_id))
        self._submit(run_id, go)

    def replay(self, run_id: str) -> None:
        def go():
            from ..run import execute_replay

            execute_replay(self.services(), run_id)
        self._submit(run_id, go)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True)
