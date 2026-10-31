"""The run queue: the API puts each step of a run in the database, and workers anywhere take them from there.

With `ALC_LAUNCHER=queue` the API does not run steps itself. Starting a run, approving its plan, resuming it or
replaying it adds a row to `run_jobs`, and returns at once. `alchemist worker` processes take queued steps, oldest
first, a few at a time each (`--concurrency`), and run them; start as many workers as the load needs, on one
computer or many, as long as they share the database and the workspace. On PostgreSQL workers claim steps with
`FOR UPDATE SKIP LOCKED`, so no two take the same one; on SQLite (one computer) a claim is one write.

Everything a person does to a running step already goes through the database (stop, pause, notes, sending a plan
back), so it reaches the step whichever worker runs it.

A worker records a heartbeat for each step it runs. A step whose heartbeat is older than ALC_JOB_LEASE_SECONDS lost
its worker (killed, out of memory, the machine went away): it is queued again (ALC_JOB_RETRIES times; an analysis
continues from its checkpoint, a planning step from its saved conversation), and after that the run is paused if it
can resume, or failed. The API and every worker check for lost steps.

`ALC_QUEUE_EMBEDDED_WORKERS=N` also runs N workers inside the API process, so one `alchemist serve` keeps working
on its own; the queue still survives a restart of the API.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
import time
from typing import Callable

from .api.jobs import approved_by, last_launch, run_step

log = logging.getLogger("alchemist.worker")

STEPS = ("plan", "analyse", "replay")
RUN_STATUS_FOR = {"plan": "planning", "analyse": "running"}  # a lost step is retried only while the run still is


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def lease_seconds() -> float:
    return _env_float("ALC_JOB_LEASE_SECONDS", 120)


def retries() -> int:
    return int(os.environ.get("ALC_JOB_RETRIES", "2"))


# ---------------------------------------------------------------- the table

def enqueue(svc, run_id: str, step: str, *, user: str | None = None, attempt: int = 1) -> int:
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}")
    db = svc.catalog.db
    row = db.execute("insert into run_jobs(run_id, step, user_id, status, attempt, created_at)"
                     " values (?, ?, ?, 'queued', ?, ?) returning id", (run_id, step, user, attempt, time.time())).fetchone()
    db.commit()
    job = row["id"]
    svc.catalog.audit("system", "run.launch", run_id, {"launcher": "queue", "step": step, "job": job, "attempt": attempt})
    return job


def claim(svc, worker: str) -> dict | None:
    """Take the oldest queued step whose run has no step running, or None."""
    db = svc.catalog.db
    lock = " for update skip locked" if db.dialect == "postgres" else ""
    t = time.time()
    row = db.execute(
        "update run_jobs set status = 'claimed', worker = ?, claimed_at = ?, heartbeat_at = ?"
        " where status = 'queued' and id = (select j.id from run_jobs j where j.status = 'queued' and not exists"
        " (select 1 from run_jobs c where c.run_id = j.run_id and c.status = 'claimed')"
        f" order by j.id limit 1{lock}) returning id, run_id, step, user_id, attempt", (worker, t, t)).fetchone()
    db.commit()
    return dict(row) if row else None


def heartbeat(svc, job_ids: list[int]) -> None:
    if not job_ids:
        return
    db = svc.catalog.db
    db.execute(f"update run_jobs set heartbeat_at = ? where status = 'claimed' and id in ({', '.join('?' * len(job_ids))})",
               (time.time(), *job_ids))
    db.commit()


def finish(svc, job_id: int, error: str | None = None) -> None:
    db = svc.catalog.db
    db.execute("update run_jobs set status = ?, finished_at = ?, error = ? where id = ? and status = 'claimed'",
               ("failed" if error else "done", time.time(), (error or None) and error[:2000], job_id))
    db.commit()


def cancel(svc, run_id: str) -> int:
    """Drop a run's queued steps (a running step sees the run's stop flag)."""
    db = svc.catalog.db
    n = db.execute("update run_jobs set status = 'cancelled', finished_at = ? where run_id = ? and status = 'queued'",
                   (time.time(), run_id)).rowcount
    db.commit()
    return n


def recover_lost(svc, *, lease: float | None = None, max_retries: int | None = None) -> list[dict]:
    """Steps whose worker stopped sending heartbeats: queue them again, or pause or fail their run. Safe to run in
    many processes at once: each lost step is handled by whoever marks it lost."""
    from .run import interrupted

    lease = lease_seconds() if lease is None else lease
    max_retries = retries() if max_retries is None else max_retries
    db = svc.catalog.db
    cutoff = time.time() - lease
    handled = []
    for job in [dict(r) for r in db.execute("select id, run_id, step, user_id, attempt, worker from run_jobs"
                                            " where status = 'claimed' and heartbeat_at < ?", (cutoff,)).fetchall()]:
        n = db.execute("update run_jobs set status = 'lost', finished_at = ?, error = ? where id = ?"
                       " and status = 'claimed' and heartbeat_at < ?",
                       (time.time(), f"no heartbeat from worker {job['worker']} for {lease:.0f}s", job["id"],
                        cutoff)).rowcount
        db.commit()
        if n != 1:
            continue
        run = svc.catalog.run(job["run_id"])
        if run["status"] == RUN_STATUS_FOR.get(job["step"]) and job["attempt"] <= max_retries:
            enqueue(svc, job["run_id"], job["step"], user=job["user_id"], attempt=job["attempt"] + 1)
            outcome = "requeued"
        else:
            outcome = interrupted(svc, job["run_id"], f"the worker running its {job['step']} step stopped") or "none"
        log.warning("step %s of run %s lost its worker %s: %s", job["step"], job["run_id"], job["worker"], outcome)
        handled.append({**job, "outcome": outcome})
    return handled


def depth(svc) -> dict:
    """How many steps are waiting and running: what a deployment scales workers on."""
    rows = svc.catalog.db.execute("select status, count(*) as n from run_jobs where status in ('queued', 'claimed')"
                                  " group by status").fetchall()
    counts = {r["status"]: int(r["n"]) for r in rows}
    return {"queued": counts.get("queued", 0), "running": counts.get("claimed", 0)}


def position(svc, run_id: str) -> dict | None:
    """Where a run's latest unfinished step is: {"state": "queued", "ahead": n} or {"state": "running", "worker": w}."""
    db = svc.catalog.db
    job = db.execute("select id, step, status, worker, attempt from run_jobs where run_id = ?"
                     " and status in ('queued', 'claimed') order by id desc limit 1", (run_id,)).fetchone()
    if job is None:
        return None
    out = {"step": job["step"], "attempt": job["attempt"]}
    if job["status"] == "claimed":
        return {**out, "state": "running", "worker": job["worker"]}
    ahead = db.execute("select count(*) as n from run_jobs where status = 'queued' and id < ?", (job["id"],)).fetchone()
    return {**out, "state": "queued", "ahead": int(ahead["n"])}


# ---------------------------------------------------------------- workers

class Worker:
    """Takes queued steps and runs them, `concurrency` at a time, until `stop` is set. A step that is running when
    `stop` is set runs to its end (or until the process is killed, after which its lease expires and the step is
    queued again)."""

    def __init__(self, services_factory: Callable[[], object], *, concurrency: int = 1, poll: float | None = None,
                 name: str | None = None):
        self.services = services_factory
        self.concurrency = max(1, concurrency)
        self.poll = _env_float("ALC_WORKER_POLL_SECONDS", 2) if poll is None else poll
        self.name = name or f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(3)}"
        self.running: dict[int, str] = {}  # job id -> run id
        self._lock = threading.Lock()

    def run_once(self) -> dict | None:
        """Claim one step and run it here. Returns the step, or None if nothing was waiting."""
        svc = self.services()
        try:
            job = claim(svc, self.name)
        finally:
            svc.catalog.db.close()
        if job is None:
            return None
        with self._lock:
            self.running[job["id"]] = job["run_id"]
        log.info("worker %s: %s step of run %s (attempt %s)", self.name, job["step"], job["run_id"], job["attempt"])
        error = None
        try:
            error = run_step(self.services, job["run_id"], job["step"], job["user_id"])
        except BaseException as e:  # run_step handles step errors; this is the database itself, or an interrupt
            error = f"{type(e).__name__}: {e}"
            raise
        finally:
            with self._lock:
                self.running.pop(job["id"], None)
            svc = self.services()
            try:
                finish(svc, job["id"], error)
            finally:
                svc.catalog.db.close()
        return job

    def _loop(self, stop: threading.Event, drain: bool) -> None:
        while not stop.is_set():
            try:
                job = self.run_once()
            except Exception:
                log.exception("worker %s: taking a step failed", self.name)
                job = None
            if job is None:
                if drain:
                    return
                stop.wait(self.poll)

    def _housekeeping(self, stop: threading.Event, done: threading.Event) -> None:
        every = max(1.0, lease_seconds() / 4)
        while not done.wait(every):
            svc = None
            try:
                svc = self.services()
                with self._lock:
                    ids = list(self.running)
                heartbeat(svc, ids)
                if not stop.is_set():
                    recover_lost(svc)
            except Exception:
                log.exception("worker %s: heartbeat failed", self.name)
            finally:
                if svc is not None:
                    svc.catalog.db.close()

    def run(self, stop: threading.Event | None = None, *, drain: bool = False) -> None:
        """Work until `stop` is set (or, with `drain`, until the queue is empty)."""
        stop = stop or threading.Event()
        done = threading.Event()
        keeper = threading.Thread(target=self._housekeeping, args=(stop, done), name="alc-heartbeat", daemon=True)
        keeper.start()
        loops = [threading.Thread(target=self._loop, args=(stop, drain), name=f"alc-worker-{i}")
                 for i in range(self.concurrency)]
        for t in loops:
            t.start()
        try:
            for t in loops:
                while t.is_alive():
                    t.join(0.5)
        finally:
            done.set()
            keeper.join(5)


# ---------------------------------------------------------------- the API side

class QueueLauncher:
    """The API's launcher with `ALC_LAUNCHER=queue`: each step goes into the queue for a worker."""

    def __init__(self, services_factory: Callable[[], object], embedded: int | None = None):
        self.services = services_factory
        embedded = int(os.environ.get("ALC_QUEUE_EMBEDDED_WORKERS", "0")) if embedded is None else embedded
        self._stop = threading.Event()
        self._thread = None
        if embedded > 0:
            self.worker = Worker(services_factory, concurrency=embedded, name=f"api-{socket.gethostname()}-{os.getpid()}")
            self._thread = threading.Thread(target=self.worker.run, args=(self._stop,), name="alc-embedded-worker",
                                            daemon=True)
            self._thread.start()

    def _enqueue(self, run_id: str, step: str, user: str | None = None) -> None:
        svc = self.services()
        try:
            enqueue(svc, run_id, step, user=user)
        finally:
            svc.catalog.db.close()

    def plan(self, run_id: str) -> None:
        self._enqueue(run_id, "plan")

    def replan(self, run_id: str) -> None:
        self._enqueue(run_id, "plan")  # the planning step continues its saved conversation with the note

    def analyse(self, run_id: str, user: str) -> None:
        self._enqueue(run_id, "analyse", user)

    def resume(self, run_id: str) -> None:
        self._enqueue(run_id, "analyse", approved_by(self.services, run_id))

    def replay(self, run_id: str) -> None:
        self._enqueue(run_id, "replay")

    def cancel(self, run_id: str) -> None:
        svc = self.services()
        try:
            cancel(svc, run_id)
        finally:
            svc.catalog.db.close()

    def position(self, run_id: str) -> dict | None:
        svc = self.services()
        try:
            return position(svc, run_id)
        finally:
            svc.catalog.db.close()

    def reconcile(self) -> None:
        from .run import ACTIVE, interrupted

        svc = self.services()
        try:
            recover_lost(svc)
            # Runs a thread in an earlier `alchemist serve` (before ALC_LAUNCHER=queue) was running died with it.
            for row in svc.catalog.active_runs(ACTIVE):
                launch = last_launch(svc, row["run_id"])
                if launch and launch.get("launcher") == "local":
                    interrupted(svc, row["run_id"], "the server restarted while this step was running")
            publish_depth(depth(svc))
        finally:
            svc.catalog.db.close()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(30)


def publish_depth(d: dict, cloudwatch=None) -> None:
    """With ALC_QUEUE_METRICS_NAMESPACE set, send the queue depth to CloudWatch, where ECS scales workers on it."""
    namespace = os.environ.get("ALC_QUEUE_METRICS_NAMESPACE")
    if not namespace:
        return
    if cloudwatch is None:
        import boto3

        cloudwatch = boto3.client("cloudwatch")
    dims = [{"Name": "Environment", "Value": os.environ.get("ALC_ENV", "default")}]
    cloudwatch.put_metric_data(Namespace=namespace, MetricData=[
        {"MetricName": "QueuedSteps", "Dimensions": dims, "Value": d["queued"], "Unit": "Count"},
        {"MetricName": "RunningSteps", "Dimensions": dims, "Value": d["running"], "Unit": "Count"},
        {"MetricName": "Backlog", "Dimensions": dims, "Value": d["queued"] + d["running"], "Unit": "Count"},
    ])
