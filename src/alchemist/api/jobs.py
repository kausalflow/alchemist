"""Starting the long steps of a run (planning, analysis, replay) away from the request.

The API only records what was asked and hands the step to a launcher, then returns at once; the browser follows
progress through the run's event stream. `LocalLauncher` runs steps on a thread pool in the API process, which is
what `alchemist serve` uses on a laptop. In AWS the launcher starts the run's Step Functions execution (planning)
and sends the task token on approval instead; everything a step needs is in the database and the run folder, so a
step can run in any process.

Stopping is a flag in the database (an audit event), so it works whichever process runs the step.
"""

from __future__ import annotations

import logging
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Protocol

from ..config import Settings

log = logging.getLogger("alchemist.api")


class Launcher(Protocol):
    def plan(self, run_id: str) -> None: ...

    def analyse(self, run_id: str, user: str) -> None: ...

    def replay(self, run_id: str) -> None: ...


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


def mark_failed(svc, run_id: str, error: Exception) -> None:
    """A step that crashed outside its own error handling must not leave the run looking busy."""
    row = svc.catalog.run(run_id)
    if row["status"] in ("planning", "running"):
        svc.catalog.upsert_run(run_id=run_id, status="failed")
        svc.catalog.audit("system", "run.fail", run_id, {"error": f"{type(error).__name__}: {error}"[:1000]})


class LocalLauncher:
    def __init__(self, services_factory: Callable[[], object], workers: int = 2):
        self.services = services_factory
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="alc-run")

    def _submit(self, run_id: str, fn):
        def wrapped():
            try:
                fn()
            except Exception as e:
                log.error("run %s step failed:\n%s", run_id, traceback.format_exc())
                mark_failed(self.services(), run_id, e)
        return self.pool.submit(wrapped)

    def plan(self, run_id: str) -> None:
        def go():
            from ..run import plan

            svc = self.services()  # one connection per thread: SQLite connections are not shared across threads
            model, _ = models(svc.settings, "plan")
            plan(svc, run_id, model=model, should_stop=lambda: stop_requested(svc, run_id))
        self._submit(run_id, go)

    def analyse(self, run_id: str, user: str) -> None:
        def go():
            from ..run import analyse

            svc = self.services()
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
