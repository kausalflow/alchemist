"""Settings, read from environment variables with local-friendly defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .db import database_url, is_postgres


def _home(*parts: str) -> str:
    return str(Path(os.environ.get("ALC_HOME", "./alchemist-home")).joinpath(*parts))


def _model(role: str) -> str:
    from .model_roles import from_env

    return from_env(role)


@dataclass
class Settings:
    # Root of the workspace store: s3://alchemist-workspace in production, a folder locally.
    workspace_root: str = field(default_factory=lambda: os.environ.get("ALC_WORKSPACE") or _home("workspace"))
    # App database (catalog, run index, audit): a SQLite file locally, or a postgresql:// URL (Aurora on AWS, built
    # from ALC_DB_HOST/PORT/NAME/USER when ALC_DB is unset; the password comes from ALC_DB_PASSWORD).
    db_path: str = field(default_factory=lambda: database_url() or _home("alchemist.db"))
    # Plants, workspaces and how identity-provider groups map to them; may also list local users (fallback).
    access_file: str = field(default_factory=lambda: os.environ.get("ALC_ACCESS") or _home("access.yaml"))
    # Role the credential broker assumes once per run, narrowed by a session policy to the run's data and folder.
    # Unset: the sandbox uses the process's own AWS credentials (laptops, tests).
    run_role_arn: str | None = field(default_factory=lambda: os.environ.get("ALC_RUN_ROLE_ARN") or None)
    run_credential_seconds: int = field(default_factory=lambda: int(os.environ.get("ALC_RUN_CREDENTIAL_SECONDS", "3600")))
    # in_process: the analysis kernel is a child of the worker, which also reports and seals the run. separate: the
    # kernel runs in its own sandbox task (`alchemist sandbox serve`) that gets data credentials from the broker
    # Lambda; workers drive it (--sandbox-task-arn / --sandbox-url) and separate `worker report` / `worker seal`
    # steps finish the run. The API's launcher also writes each run's broker grant in this mode.
    sandbox_mode: str = field(default_factory=lambda: os.environ.get("ALC_SANDBOX_MODE", "in_process"))
    # How long a run's broker grant stays valid after approval (separate mode): the analysis timeout plus slack.
    grant_hours: int = field(default_factory=lambda: int(os.environ.get("ALC_GRANT_HOURS", "8")))
    # Which registered access provider supplies users, plant groups and workspaces ("yaml" reads access_file).
    access_provider: str = field(default_factory=lambda: os.environ.get("ALC_ACCESS_PROVIDER", "yaml"))
    # Data sources synced into the lake (sources.py): which PI servers and lake tables, and where they land.
    sources_file: str = field(default_factory=lambda: os.environ.get("ALC_SOURCES") or _home("sources.yaml"))
    lake_root: str = field(default_factory=lambda: os.environ.get("ALC_LAKE") or _home("lake"))
    # Searchable domain documents (markdown with front matter).
    knowledge_root: str = field(default_factory=lambda: os.environ.get("ALC_KNOWLEDGE") or _home("knowledge"))
    # Models are Pydantic AI specs `provider:model-id`; a bare id means Bedrock (and may be an inference profile ARN).
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "eu-central-1"))
    # One model per job (see model_roles.py). Each falls back to the older ALC_LEAD_MODEL (planning, analysis) or
    # ALC_SUB_MODEL (reviewer, small jobs), then to the default. An admin's choice in the database overrides these.
    planning_model: str = field(default_factory=lambda: _model("planning"))
    analysis_model: str = field(default_factory=lambda: _model("analysis"))
    reviewer_model: str = field(default_factory=lambda: _model("reviewer"))
    small_model: str = field(default_factory=lambda: _model("small"))
    # The OpenAI-compatible address of a model server in your own network, for `local:<model>` specs (vLLM, Ollama,
    # llama.cpp, LM Studio, TGI), e.g. http://ollama:11434/v1. A key, if the server wants one: ALC_LOCAL_LLM_API_KEY.
    local_llm_url: str | None = field(default_factory=lambda: os.environ.get("ALC_LOCAL_LLM_URL") or None)
    # Providers plant data may be sent to. Anything else is refused unless listed (or '*').
    allowed_providers: list[str] = field(default_factory=lambda: [p.strip() for p in os.environ.get("ALC_ALLOWED_PROVIDERS", "bedrock").split(",") if p.strip()])
    effort: str = field(default_factory=lambda: os.environ.get("ALC_EFFORT", "high"))
    # Budgets per run.
    max_steps: int = field(default_factory=lambda: int(os.environ.get("ALC_MAX_STEPS", "60")))
    max_output_tokens_total: int = field(default_factory=lambda: int(os.environ.get("ALC_MAX_OUTPUT_TOKENS", "400000")))
    # Runs planning or analysing at once (waiting for approval does not count): per user, and in total (0: no limit).
    # They share the model quota and the cost, so one person cannot starve everyone else.
    max_active_runs_per_user: int = field(default_factory=lambda: int(os.environ.get("ALC_MAX_ACTIVE_RUNS_PER_USER", "3")))
    max_active_runs: int = field(default_factory=lambda: int(os.environ.get("ALC_MAX_ACTIVE_RUNS", "0")))
    # How long a model call keeps retrying when the provider throttles (many runs share one quota).
    model_retry_seconds: float = field(default_factory=lambda: float(os.environ.get("ALC_MODEL_RETRY_SECONDS", "300")))
    # Key for pseudonymised columns (keyed hash). Set from a secret store in production.
    pseudonym_key: str = field(default_factory=lambda: os.environ.get("ALC_PSEUDONYM_KEY", "dev-only-key"))
    seed: int = 1729

    def ensure_local_dirs(self) -> None:
        if not is_postgres(self.db_path):
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        if "://" not in self.workspace_root:
            Path(self.workspace_root).mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    s = Settings()
    s.ensure_local_dirs()
    return s
