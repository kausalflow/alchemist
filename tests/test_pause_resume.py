"""Pausing and resuming an analysis, and recovering one whose step died, on both kinds of sandbox."""

import copy
import dataclasses
import json
import threading
from pathlib import Path

import nbformat
import pytest

from alchemist.agent import ScriptedModel
from alchemist.api.jobs import stop_requested
from alchemist.demo import script
from alchemist.paths import read_json
from alchemist.run import (ScriptedReviewer, analyse, ask, cancel, checkpoint_of, interrupted, pause, report_step,
                           resume, seal_step, start_approval)
from alchemist.sandbox_server import KernelHost, make_server
from alchemist.workspace import verify


@pytest.fixture(scope="module", autouse=True)
def resin_approved(svc):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")


def approved(svc, project="pause"):
    out = ask(svc, user="eng_p1", workspace="quality", project=project, question="Pause and resume test",
              scope=script.SCOPE, model=ScriptedModel(script.PLAN_TURNS))
    start_approval(svc, out["run_id"], user="eng_p1")
    return out["run_id"]


def step(svc, rid, *, pause_after=None, model=None, **kw):
    """One analysis step; `pause_after=n` asks it to pause before its (n+1)th model call."""
    calls = []

    def should_stop():
        calls.append(1)
        if pause_after is not None and len(calls) > pause_after:
            return "pause"
        return stop_requested(svc, rid)

    return analyse(svc, rid, user="eng_p1", model=model or ScriptedModel(script.ANALYSIS_TURNS),
                   reviewer=ScriptedReviewer(script.REVIEW), should_stop=should_stop, **kw)


def hashes(path):
    return {f["id"]: f["value_sha256"] for f in read_json(Path(path) / "findings.json")["findings"]}


@pytest.fixture(scope="module")
def reference(svc):
    """The same analysis without any pause."""
    return hashes(step(svc, approved(svc, "pause-ref"))["path"])


def test_pause_and_resume_gives_the_same_findings(svc, reference):
    rid = approved(svc)
    out = step(svc, rid, pause_after=3)
    run = Path(out["path"])
    assert out["status"] == "paused" and svc.catalog.run(rid)["status"] == "paused"
    assert not (run / "SEALED").exists() and not (run / "report" / "report.html").exists()
    saved = checkpoint_of(run)
    assert saved["budget"]["model_calls"] == 3 and saved["messages"][-1]["role"] == "user"
    assert read_json(run / "manifest.json")["status"] == "paused"

    resume(svc, rid, user="eng_p1")
    assert stop_requested(svc, rid) is None  # the pause request is spent
    done = step(svc, rid)
    assert done["status"] == "completed" and verify(run, done["seal_sha256"])["ok"]
    assert hashes(run) == reference
    # one notebook, with a note where it resumed; the transcript carries on in the same file
    nb = nbformat.reads((run / "notebook.ipynb").read_text(), as_version=4)
    assert sum("Resumed" in c.source for c in nb.cells if c.cell_type == "markdown") == 1
    assert sum(1 for c in nb.cells if c.metadata.get("alchemist", {}).get("kind") == "setup") == 1
    assert any(json.loads(line).get("resumed") for line in (run / "logs" / "transcript.jsonl").read_text().splitlines())
    m = read_json(run / "manifest.json")
    assert m["usage"]["model_calls"] == len(script.ANALYSIS_TURNS)  # counted across the pause, none repeated


def test_only_a_running_analysis_pauses_and_only_a_paused_one_resumes(svc):
    from alchemist.access import AccessDenied

    rid = approved(svc)
    with pytest.raises(ValueError, match="not paused"):
        resume(svc, rid, user="eng_p1")
    with pytest.raises(AccessDenied):
        pause(svc, rid, user="eng_p2")
    pause(svc, rid, user="eng_p1")
    assert stop_requested(svc, rid) == "pause"
    out = step(svc, rid)
    assert out["status"] == "paused" and checkpoint_of(out["path"]) is None  # paused before its first step
    resume(svc, rid, user="eng_p1")
    assert step(svc, rid)["status"] == "completed"  # so it simply starts again
    nb = nbformat.reads((Path(out["path"]) / "notebook.ipynb").read_text(), as_version=4)
    assert sum(1 for c in nb.cells if c.metadata.get("alchemist", {}).get("kind") == "setup") == 1


class TaskKilled(BaseException):
    """Stands in for the process dying: nothing in the step can catch it."""


class DiesAt(ScriptedModel):
    def __init__(self, at, error=TaskKilled):
        super().__init__(script.ANALYSIS_TURNS)
        self.at, self.error = at, error

    def create(self, **kw):
        if self.i == self.at:
            raise self.error("lost")
        return super().create(**kw)


def test_a_step_that_died_is_paused_and_resumes_from_its_last_step(svc, reference):
    rid = approved(svc)
    with pytest.raises(TaskKilled):
        step(svc, rid, model=DiesAt(4))
    run = Path(svc.catalog.run(rid)["path"])
    assert svc.catalog.run(rid)["status"] == "running" and checkpoint_of(run)["budget"]["model_calls"] == 4

    # The task died after a cell ran but before the step was saved: that cell and its finding are dropped.
    saved = checkpoint_of(run)
    nb = nbformat.reads((run / "notebook.ipynb").read_text(), as_version=4)
    nb.cells.append(nbformat.v4.new_code_cell("lost = 1", metadata={"alchemist": {"cell_id": "c99", "kind": "python"}}))
    (run / "notebook.ipynb").write_text(nbformat.writes(nb))
    findings = read_json(run / "findings.json")
    findings["findings"].append({**findings["findings"][0], "id": "F9"})
    (run / "findings.json").write_text(json.dumps(findings))

    assert interrupted(svc, rid, "the analysis task was killed") == "paused"  # what the reconciler does
    resume(svc, rid, user="eng_p1")
    done = step(svc, rid)
    assert done["status"] == "completed" and hashes(run) == reference
    nb = nbformat.reads((run / "notebook.ipynb").read_text(), as_version=4)
    assert "lost = 1" not in [c.source for c in nb.cells] and len(nb.cells) > saved["notebook"]["cells"]


def test_an_unavailable_model_pauses_the_run(svc):
    class Throttled(Exception):
        status_code = 429

    rid = approved(svc)
    out = step(svc, rid, model=DiesAt(2, Throttled))
    assert out["status"] == "paused" and "unavailable" in out["analysis_ended"]
    assert not (Path(out["path"]) / "SEALED").exists()
    resume(svc, rid, user="eng_p1")
    assert step(svc, rid)["status"] == "completed"


def test_stopping_a_paused_run_seals_a_partial_report(svc):
    rid = approved(svc)
    assert step(svc, rid, pause_after=8)["status"] == "paused"
    assert cancel(svc, rid, user="eng_p1") == "finishing"
    out = step(svc, rid)  # what the launcher's resume runs
    assert out["status"] == "partial" and out["seal_sha256"]
    assert (Path(out["path"]) / "report" / "report.html").exists()


# ------------------------------------------------------------------ separate sandbox

@pytest.fixture(scope="module")
def sandbox_url():
    server = make_server("127.0.0.1", 0, KernelHost())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.kernel_host.stop()


@pytest.fixture(scope="module")
def separate_svc(svc):
    s = copy.copy(svc)
    s.settings = dataclasses.replace(svc.settings, sandbox_mode="separate")
    return s


def test_pause_and_resume_on_a_separate_sandbox(separate_svc, sandbox_url, reference):
    rid = approved(separate_svc, "pause-separate")
    assert step(separate_svc, rid, pause_after=5, sandbox_url=sandbox_url)["status"] == "paused"
    # the workflow's Report and Seal steps run after a paused Analyse too; they leave the run alone
    assert report_step(separate_svc, rid)["status"] == "paused"
    assert seal_step(separate_svc, rid) == {"run_id": rid, "status": "paused", "seal_sha256": None}

    resume(separate_svc, rid, user="eng_p1")
    done = step(separate_svc, rid, sandbox_url=sandbox_url)  # a new sandbox kernel, rebuilt from the notebook
    assert done["status"] == "completed" and hashes(done["path"]) == reference
    assert seal_step(separate_svc, rid)["status"] == "completed"


def test_report_step_pauses_a_separate_run_that_died_with_a_checkpoint(separate_svc, sandbox_url):
    rid = approved(separate_svc, "pause-separate")
    with pytest.raises(TaskKilled):
        step(separate_svc, rid, model=DiesAt(3), sandbox_url=sandbox_url)
    assert report_step(separate_svc, rid)["status"] == "paused"  # Analyse's retries ran out
    assert seal_step(separate_svc, rid)["seal_sha256"] is None
    resume(separate_svc, rid, user="eng_p1")
    assert step(separate_svc, rid, sandbox_url=sandbox_url)["status"] == "completed"
