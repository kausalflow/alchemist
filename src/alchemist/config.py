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
    # in_process with a sandbox service: each run's kernel runs in `alchemist sandbox serve` at this address (docker
    # compose and the Helm chart), which holds no database, model or PI credentials and has no network but storage;
    # the worker still reports and seals. Unset: the kernel is a child of the worker.
    sandbox_url: str | None = field(default_factory=lambda: os.environ.get("ALC_SANDBOX_URL") or None)
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
    aws_region: str = field(default_factory=lambda: os.environ.get("ALC_AWS_REGION") or os.environ.get("AWS_REGION", "eu-central-1"))
    # Bedrock may use its own AWS profile and region, so model calls and storage (S3) can use different identities.
    # Unset: the AWS profile and region of everything else (ALC_AWS_PROFILE / ALC_AWS_REGION, else AWS_*).
    bedrock_profile: str | None = field(default_factory=lambda: os.environ.get("ALC_BEDROCK_PROFILE") or None)
    bedrock_region: str = field(default_factory=lambda: os.environ.get("ALC_BEDROCK_REGION") or os.environ.get("ALC_AWS_REGION") or os.environ.get("AWS_REGION", "eu-central-1"))
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
    # How long a run's analysis may work, in minutes (time paused or waiting for a worker does not count), and the
    # longest a person may choose when approving the plan. Admins can change both on the "Models and limits" page.
    max_run_minutes: int = field(default_factory=lambda: int(os.environ.get("ALC_MAX_RUN_MINUTES", "120")))
    max_run_minutes_cap: int = field(default_factory=lambda: int(os.environ.get("ALC_MAX_RUN_MINUTES_CAP", "480")))
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


CONFIG_FILE_NAME = "alchemist.yaml"
_applied_config: dict = {"path": None, "keys": []}


def find_config_file(explicit: str | None = None) -> str | None:
    """The settings file: --config, else ALC_CONFIG, else ./alchemist.yaml if it exists."""
    if explicit:
        return explicit
    if os.environ.get("ALC_CONFIG"):
        return os.environ["ALC_CONFIG"]
    return CONFIG_FILE_NAME if Path(CONFIG_FILE_NAME).is_file() else None


def _env_value(key: str, value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ValueError(f"{key}: a setting is a string, number, true/false or a list, not {type(value).__name__}")


def apply_config_file(path: str | None) -> list[str]:
    """Read a YAML file of settings, named like the environment variables, into this process's environment.

    A variable already set in the environment wins over the file, so one file can be reused and overridden per
    shell. Values only reach this process and what it starts (workers, kernels), never the user's other programs, so
    AWS_PROFILE here picks the profile for Alchemist alone. Returns the keys taken from the file.
    """
    if not path:
        return []
    import yaml

    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"settings file not found: {p}")
    text = p.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        hint = ""
        if "\\" in text and "escape" in str(e):
            hint = ("\nIn double quotes YAML reads a backslash as an escape (\\U, \\a, ...). Write Windows paths in single "
                    "quotes or without quotes: ALC_HOME: 'C:\\alc'")
        raise ValueError(f"{p}: not valid YAML: {e}{hint}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{p}: expected a mapping of setting names to values, e.g. ALC_HOME: C:\\alc")
    applied = []
    for key, value in data.items():
        key = str(key)
        if not key.replace("_", "").isalnum() or key.upper() != key:
            raise ValueError(f"{p}: {key!r} is not a setting name; use the variable names, e.g. ALC_BEDROCK_PROFILE")
        if value is None or key in os.environ:
            continue
        os.environ[key] = _env_value(key, value)
        applied.append(key)
    _applied_config.update(path=str(p), keys=applied)
    return applied


def applied_config() -> dict:
    """The settings file this process read, and which keys it set (the others were already in the environment)."""
    return dict(_applied_config)


# Alchemist's own names for the AWS profile and region. They are copied onto the standard AWS variables inside this
# process (and what it starts: workers, kernels), so every AWS client Alchemist uses (S3 through cloudpathlib and
# DuckDB, STS, Step Functions, Bedrock) picks them up, while the user's other AWS tools keep their own settings.
ALC_AWS_ALIASES = {"ALC_AWS_PROFILE": "AWS_PROFILE", "ALC_AWS_REGION": "AWS_REGION"}


def apply_alchemist_aws_env() -> dict[str, str]:
    """Set AWS_PROFILE / AWS_REGION from ALC_AWS_PROFILE / ALC_AWS_REGION, which win. Returns what it set."""
    out = {}
    for ours, theirs in ALC_AWS_ALIASES.items():
        value = os.environ.get(ours)
        if value and os.environ.get(theirs) != value:
            os.environ[theirs] = value
            out[theirs] = value
    return out


def load_settings() -> Settings:
    apply_alchemist_aws_env()
    s = Settings()
    s.ensure_local_dirs()
    return s
