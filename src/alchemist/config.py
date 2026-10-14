"""Settings, read from environment variables with local-friendly defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _home(*parts: str) -> str:
    return str(Path(os.environ.get("ALC_HOME", "./alchemist-home")).joinpath(*parts))


@dataclass
class Settings:
    # Root of the workspace store: s3://alchemist-workspace in production, a folder locally.
    workspace_root: str = field(default_factory=lambda: os.environ.get("ALC_WORKSPACE") or _home("workspace"))
    # Catalog / index database (SQLite in the MVP; Aurora PostgreSQL in the target design).
    db_path: str = field(default_factory=lambda: os.environ.get("ALC_DB") or _home("alchemist.db"))
    # Who may see which plants and workspaces (stands in for identity-provider groups).
    access_file: str = field(default_factory=lambda: os.environ.get("ALC_ACCESS") or _home("access.yaml"))
    # Searchable domain documents (markdown with front matter).
    knowledge_root: str = field(default_factory=lambda: os.environ.get("ALC_KNOWLEDGE") or _home("knowledge"))
    # Models are Pydantic AI specs `provider:model-id`; a bare id means Bedrock (and may be an inference profile ARN).
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "eu-central-1"))
    lead_model: str = field(default_factory=lambda: os.environ.get("ALC_LEAD_MODEL", "anthropic.claude-opus-5-5"))
    sub_model: str = field(default_factory=lambda: os.environ.get("ALC_SUB_MODEL", "anthropic.claude-sonnet-5-5"))
    # Providers plant data may be sent to. Anything else is refused unless listed (or '*').
    allowed_providers: list[str] = field(default_factory=lambda: [p.strip() for p in os.environ.get("ALC_ALLOWED_PROVIDERS", "bedrock").split(",") if p.strip()])
    effort: str = field(default_factory=lambda: os.environ.get("ALC_EFFORT", "high"))
    # Budgets per run.
    max_steps: int = field(default_factory=lambda: int(os.environ.get("ALC_MAX_STEPS", "60")))
    max_output_tokens_total: int = field(default_factory=lambda: int(os.environ.get("ALC_MAX_OUTPUT_TOKENS", "400000")))
    # Key for pseudonymised columns (keyed hash). Set from a secret store in production.
    pseudonym_key: str = field(default_factory=lambda: os.environ.get("ALC_PSEUDONYM_KEY", "dev-only-key"))
    seed: int = 1729

    def ensure_local_dirs(self) -> None:
        for p in (self.db_path,):
            Path(p).parent.mkdir(parents=True, exist_ok=True)
        if "://" not in self.workspace_root:
            Path(self.workspace_root).mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    s = Settings()
    s.ensure_local_dirs()
    return s
