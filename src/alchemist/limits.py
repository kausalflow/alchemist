"""Run limits an admin can change: how long a run may work, and its tool-call and output-token budgets.

Each limit is, in order: an admin's choice saved in the database (the admin "Models and limits" page), then its
variable (ALC_MAX_RUN_MINUTES, ...), then the default in config.py. A person approving a plan may choose a shorter or
longer time limit for that run, up to `max_run_minutes_cap`; the run records the limit it got, so changing a limit
later does not change runs already approved.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass

log = logging.getLogger("alchemist.limits")


@dataclass(frozen=True)
class Limit:
    name: str  # the Settings field
    label: str
    does: str
    env: str
    minimum: int
    maximum: int


LIMITS: dict[str, Limit] = {x.name: x for x in [
    Limit("max_run_minutes", "Time per run (minutes)",
          "default for a new run: the analysis stops after this much working time and reports what it found",
          "ALC_MAX_RUN_MINUTES", 1, 10_080),
    Limit("max_run_minutes_cap", "Longest time a person may choose (minutes)",
          "the most anyone may set when approving a plan", "ALC_MAX_RUN_MINUTES_CAP", 1, 10_080),
    Limit("max_steps", "Tool calls per run step", "queries, Python cells and other tool calls before the step stops",
          "ALC_MAX_STEPS", 1, 10_000),
    Limit("max_output_tokens_total", "Output tokens per run step", "model output before the step stops",
          "ALC_MAX_OUTPUT_TOKENS", 1_000, 100_000_000),
]}


def choices(db) -> dict[str, dict]:
    try:
        rows = db.execute("select name, value, updated_by, updated_at from limit_choices").fetchall()
    except Exception:  # a database from before migration 7 (or a stub in tests)
        return {}
    return {r["name"]: dict(r) for r in rows if r["name"] in LIMITS}


def _consistent(values: dict[str, int]) -> None:
    if values["max_run_minutes"] > values["max_run_minutes_cap"]:
        raise ValueError(f"the time per run ({values['max_run_minutes']} minutes) is longer than the longest a person "
                         f"may choose ({values['max_run_minutes_cap']} minutes)")


def with_choices(settings, db):
    """A copy of settings with the admins' limits."""
    saved = choices(db)
    if not saved or not dataclasses.is_dataclass(settings):
        return settings
    values = {k: getattr(settings, k) for k in LIMITS} | {k: int(c["value"]) for k, c in saved.items()}
    try:
        _consistent(values)
    except ValueError as e:  # the configuration changed under a saved choice: keep the configured time limits
        log.warning("ignoring the saved time limits: %s", e)
        values["max_run_minutes"] = min(values["max_run_minutes"], values["max_run_minutes_cap"])
    return dataclasses.replace(settings, **values)


def describe(settings, db) -> list[dict]:
    """Each limit now, where it comes from, and the deployment's own value (used when a choice is cleared)."""
    saved = choices(db)
    out = []
    for x in LIMITS.values():
        configured = getattr(settings, x.name)
        c = saved.get(x.name)
        out.append({"name": x.name, "label": x.label, "does": x.does, "env": x.env, "minimum": x.minimum,
                    "maximum": x.maximum, "configured": configured,
                    "value": int(c["value"]) if c else configured, "source": "admin" if c else "configuration",
                    "updated_by": c["updated_by"] if c else None, "updated_at": c["updated_at"] if c else None})
    return out


def save_choices(catalog, settings, values: dict[str, int | None], *, actor: str, now: str) -> dict[str, dict]:
    """Save admin limits: {name: n} sets one, {name: None} goes back to the configured value. Audited."""
    unknown = sorted(set(values) - set(LIMITS))
    if unknown:
        raise ValueError(f"unknown limits: {', '.join(unknown)} (known: {', '.join(LIMITS)})")
    for name, v in values.items():
        x = LIMITS[name]
        if v is not None and not (x.minimum <= int(v) <= x.maximum):
            raise ValueError(f"{x.label} must be between {x.minimum:,} and {x.maximum:,}")
    saved = choices(catalog.db)
    current = {k: int(saved[k]["value"]) if k in saved else getattr(settings, k) for k in LIMITS}
    after = current | {k: (int(v) if v is not None else getattr(settings, k)) for k, v in values.items()}
    _consistent(after)
    db = catalog.db
    with db.transaction():
        for name, v in values.items():
            db.execute("delete from limit_choices where name = ?", (name,))
            if v is not None:
                db.execute("insert into limit_choices(name, value, updated_by, updated_at) values (?, ?, ?, ?)",
                           (name, int(v), actor, now))
    changed = {k: {"from": current[k], "to": after[k]} for k in values if current[k] != after[k]}
    if changed:
        catalog.audit(actor, "limits.update", "limits", changed)
    return changed
