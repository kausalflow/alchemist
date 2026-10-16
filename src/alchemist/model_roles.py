"""Which model does which job.

A run uses four models, each a Pydantic AI spec `provider:model-id`:

    planning   searches the catalog and knowledge and writes the plan            ALC_PLANNING_MODEL
    analysis   writes the queries and Python and decides what to check next     ALC_ANALYSIS_MODEL
    reviewer   independently checks the conclusion before the report           ALC_REVIEWER_MODEL
    small      short jobs such as drafting catalog cards from metadata files    ALC_SMALL_MODEL

A role's model is, in order: an admin's choice saved in the database (the admin "Models and limits" page), its own
variable, the older shared variable (ALC_LEAD_MODEL for planning and analysis, ALC_SUB_MODEL for the reviewer and
small jobs), then the default below. An admin's choice whose provider is not in ALC_ALLOWED_PROVIDERS is ignored, so
narrowing the allow-list always wins. Each run records the models it used in its manifest.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass

log = logging.getLogger("alchemist.models")


@dataclass(frozen=True)
class Role:
    name: str
    label: str
    does: str
    env: str
    legacy_env: str
    default: str
    effort: str | None  # None: ALC_EFFORT

    @property
    def field(self) -> str:
        return f"{self.name}_model"


ROLES: dict[str, Role] = {r.name: r for r in [
    Role("planning", "Planning", "searches the catalog and knowledge and writes the plan",
         "ALC_PLANNING_MODEL", "ALC_LEAD_MODEL", "bedrock:anthropic.claude-opus-5-5", None),
    Role("analysis", "Analysis", "writes the queries and Python and decides what to check next",
         "ALC_ANALYSIS_MODEL", "ALC_LEAD_MODEL", "bedrock:anthropic.claude-sonnet-5-5", None),
    Role("reviewer", "Reviewer", "independently checks the conclusion against the evidence before the report",
         "ALC_REVIEWER_MODEL", "ALC_SUB_MODEL", "bedrock:anthropic.claude-opus-5-5", "medium"),
    Role("small", "Small jobs", "short jobs such as drafting catalog cards from metadata files",
         "ALC_SMALL_MODEL", "ALC_SUB_MODEL", "bedrock:anthropic.claude-haiku-5-5", "low"),
]}


def from_env(role: str) -> str:
    r = ROLES[role]
    return os.environ.get(r.env) or os.environ.get(r.legacy_env) or r.default


def env_source(role: str) -> str:
    r = ROLES[role]
    if os.environ.get(r.env):
        return r.env
    if os.environ.get(r.legacy_env):
        return r.legacy_env
    return "default"


def check(spec: str, allowed: list[str]) -> str:
    """The normalised spec, or ProviderNotAllowed / ValueError for a spec that cannot be used."""
    from .llm import check_allowed

    spec = (spec or "").strip()
    if not spec or any(c.isspace() for c in spec) or len(spec) > 300:
        raise ValueError("a model is a single `provider:model-id`, for example bedrock:anthropic.claude-sonnet-5-5")
    return check_allowed(spec, allowed)


# ---------- admin choices (the model_choices table) ----------

def choices(db) -> dict[str, dict]:
    try:
        rows = db.execute("select role, spec, updated_by, updated_at from model_choices").fetchall()
    except Exception:  # a database from before migration 5 (or a stub in tests)
        return {}
    return {r["role"]: dict(r) for r in rows if r["role"] in ROLES}


def with_choices(settings, db):
    """A copy of settings whose role models include the admins' choices. Choices for providers that are not allowed
    are left out (and logged)."""
    saved = choices(db)
    if not saved or not dataclasses.is_dataclass(settings):
        return settings
    changes = {}
    for role, c in saved.items():
        try:
            changes[ROLES[role].field] = check(c["spec"], settings.allowed_providers)
        except (PermissionError, ValueError) as e:
            log.warning("ignoring the saved %s model %r: %s", role, c["spec"], e)
    return dataclasses.replace(settings, **changes) if changes else settings


def describe(settings, db) -> list[dict]:
    """Each role's model now, where it comes from, and the deployment's own value (used when a choice is cleared)."""
    saved = choices(db)
    out = []
    for r in ROLES.values():
        configured = getattr(settings, r.field, None) or from_env(r.name)
        row = {"role": r.name, "label": r.label, "does": r.does, "env": r.env, "legacy_env": r.legacy_env,
               "configured": configured, "configured_from": env_source(r.name), "spec": configured,
               "source": "configuration", "updated_by": None, "updated_at": None, "ignored": None}
        c = saved.get(r.name)
        if c:
            try:
                row.update(spec=check(c["spec"], settings.allowed_providers), source="admin",
                           updated_by=c["updated_by"], updated_at=c["updated_at"])
            except (PermissionError, ValueError) as e:
                row["ignored"] = {"spec": c["spec"], "reason": str(e)}
        out.append(row)
    return out


def save_choices(catalog, settings, models: dict[str, str | None], *, actor: str, now: str) -> dict[str, dict]:
    """Save admin choices: {role: spec} sets a role's model, {role: None} goes back to the configured one.

    Every spec is checked against the allow-list before anything is written; the change is audited."""
    unknown = sorted(set(models) - set(ROLES))
    if unknown:
        raise ValueError(f"unknown model roles: {', '.join(unknown)} (known: {', '.join(ROLES)})")
    checked = {role: (check(spec, settings.allowed_providers) if spec else None) for role, spec in models.items()}
    before = {k: v["spec"] for k, v in choices(catalog.db).items()}
    db = catalog.db
    with db.transaction():
        for role, spec in checked.items():
            db.execute("delete from model_choices where role = ?", (role,))
            if spec:
                db.execute("insert into model_choices(role, spec, updated_by, updated_at) values (?, ?, ?, ?)",
                           (role, spec, actor, now))
    changed = {role: {"from": before.get(role), "to": spec} for role, spec in checked.items()
               if before.get(role) != spec}
    if changed:
        catalog.audit(actor, "models.update", "models", changed)
    return changed
