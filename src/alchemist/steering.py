"""Steering notes: what people tell a run while it works, and when the agent received each note.

A note is an audit event (`run.note`) in the database, so the API can add one whichever process runs the step.
Before every model call the step checks for new notes (as it checks for pause and stop) and appends them to the
conversation; the agent takes them into account from its next turn. Sending a plan back for revision is a note of
kind "send_back" that also returns the run to planning.

Each delivery is written to the run folder (logs/steering.json) and kept in the step's checkpoint, so a note is
delivered once even across pauses and crashes, and the sealed run records every note, who wrote it, and the step
at which the agent received it (or that the run ended before it could).
"""

from __future__ import annotations

import json

from .catalog import now
from .paths import as_path, read_json, write_json

NOTE_STATUSES = ("planning", "awaiting_approval", "running", "paused")  # where a note can still reach the agent
FILE = "logs/steering.json"


def add(svc, run_id: str, *, author: str, text: str, kind: str = "note", phase: str | None = None) -> None:
    text = text.strip()
    if not text:
        raise ValueError("a note needs some text")
    svc.catalog.audit(author, "run.note", run_id, {"text": text[:4000], "kind": kind,
                                                   "status": phase or svc.catalog.run(run_id)["status"]})


def notes(svc, run_id: str) -> list[dict]:
    """Every note on the run, oldest first, numbered from 1."""
    rows = svc.catalog.db.execute("select id, at, actor, detail from audit_events where target = ? and action = ?"
                                  " order by id", (run_id, "run.note")).fetchall()
    out = []
    for n, r in enumerate(rows, 1):
        d = json.loads(r["detail"]) if r["detail"] else {}
        out.append({"id": r["id"], "n": n, "at": r["at"], "author": r["actor"], "text": d.get("text", ""),
                    "kind": d.get("kind", "note"), "status": d.get("status")})
    return out


def delivered_in(run) -> list[dict]:
    f = as_path(run) / FILE
    try:
        return read_json(f).get("delivered", []) if f.exists() else []
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def record(svc, run_id: str, run) -> list[dict]:
    """The notes with their delivery (None if the agent has not received it, or the run ended first)."""
    got = {d["note"]: d for d in delivered_in(run)}
    return [{**n, "delivered": {k: v for k, v in got[n["id"]].items() if k in ("phase", "step", "model_call", "at")}
             if n["id"] in got else None} for n in notes(svc, run_id)]


def for_model(notes_: list[dict]) -> str:
    parts = []
    for n in notes_:
        if n["kind"] == "send_back":
            parts.append(f"{n['author']} sent the plan back for revision ({n['at']}):\n{n['text']}\n"
                         "Revise the plan accordingly and call submit_plan again.")
        elif n["kind"] == "more_time":
            parts.append(f"{n['author']} gave this run {n['text']}. Continue the investigation from where you stopped; "
                         "when you have finished, call finish_analysis and build_report again, and that report "
                         "replaces the preliminary one.")
        else:
            parts.append(f"Note from {n['author']} ({n['at']}), who is following this run:\n{n['text']}\n"
                         "Take it into account from your next step.")
    return "\n\n".join(parts)


class Steering:
    """The `steer` callback of one step: new notes for the agent, each delivered once.

    `delivered` is the list this step starts from: the step's checkpoint when it resumes, else what earlier steps
    recorded in the run folder."""

    def __init__(self, svc, run_id: str, run, *, phase: str, delivered: list[dict] | None = None, budget=None):
        self.svc, self.run_id, self.run, self.phase, self.budget = svc, run_id, as_path(run), phase, budget
        self.delivered = list(delivered if delivered is not None else delivered_in(self.run))
        if delivered is not None and delivered != delivered_in(self.run):
            self.save()  # a note given in a turn that never completed is not delivered: it is given again

    def __call__(self) -> dict | None:
        seen = {d["note"] for d in self.delivered}
        new = [n for n in notes(self.svc, self.run_id) if n["id"] not in seen]
        if not new:
            return None
        b = self.budget
        for n in new:
            self.delivered.append({"note": n["id"], "n": n["n"], "author": n["author"], "kind": n["kind"],
                                   "text": n["text"], "written_at": n["at"], "phase": self.phase, "at": now(),
                                   "step": b.steps if b else None, "model_call": b.model_calls + 1 if b else None})
        self.save()
        return {"text": for_model(new), "notes": [{k: n[k] for k in ("n", "author", "kind", "text", "at")} for n in new]}

    def save(self) -> None:
        write_json(self.run / FILE, {"schema": "alchemist.steering/v1", "delivered": self.delivered})
