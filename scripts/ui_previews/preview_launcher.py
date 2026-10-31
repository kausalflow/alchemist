"""Launcher for taking UI screenshots: the offline demo script, held part-way through the analysis.

The scripted demo finishes in a second or two, too fast to photograph the Running screen. With
ALC_LAUNCHER=preview_launcher:PreviewLauncher (this folder on PYTHONPATH) the analysis waits before model turn
ALC_PREVIEW_HOLD_TURN (default 6, after the first findings) for as long as the file ALC_PREVIEW_HOLD exists.
Only scripts/ui_previews/run.sh uses it; it is not part of the app.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from alchemist.agent import ScriptedModel
from alchemist.api.jobs import LocalLauncher, stop_requested


class HeldModel(ScriptedModel):
    def __init__(self, turns, hold: Path | None, at: int):
        super().__init__(turns)
        self.hold, self.at = hold, at

    def create(self, **kw) -> dict:
        deadline = time.monotonic() + 300
        while self.i == self.at and self.hold and self.hold.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        return super().create(**kw)


class PreviewLauncher(LocalLauncher):
    def analyse(self, run_id: str, user: str) -> None:
        def go():
            from alchemist.demo import script
            from alchemist.run import ScriptedReviewer, analyse

            hold = os.environ.get("ALC_PREVIEW_HOLD")
            model = HeldModel(script.ANALYSIS_TURNS, Path(hold) if hold else None,
                              int(os.environ.get("ALC_PREVIEW_HOLD_TURN", "6")))
            svc = self.services()
            try:
                if svc.catalog.run(run_id)["status"] != "running":
                    return
                analyse(svc, run_id, user=user, model=model, reviewer=ScriptedReviewer(script.REVIEW),
                        should_stop=lambda: stop_requested(svc, run_id))
            finally:
                svc.catalog.db.close()
        svc = self.services()
        try:
            svc.catalog.audit("system", "run.launch", run_id, {"launcher": "local", "instance": self.instance})
        finally:
            svc.catalog.db.close()
        self.pool.submit(go)
