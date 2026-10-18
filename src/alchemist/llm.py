"""Model access through Pydantic AI, so the provider is a setting.

A model is named `provider:model-id` in Pydantic AI's notation, for example

    bedrock:anthropic.claude-opus-5-5     (default for a bare id; uses AWS_REGION and the AWS credential chain)
    anthropic:claude-opus-5-5             (ANTHROPIC_API_KEY)
    google:gemini-3-pro                   (GOOGLE_API_KEY; Vertex AI is google-cloud:)
    openai:gpt-5                          (OPENAI_API_KEY)

The tool-calling adapter (message conversion, retries on throttling) is penguineer's
(`penguineer.agent.models`); this module decides which model a role gets. Plant data
is private: providers outside `ALC_ALLOWED_PROVIDERS` (default `bedrock`) are refused.
Plugins can add providers (`alchemist.plugins.ModelProvider`); the allow-list applies to them too.
"""

from __future__ import annotations

from penguineer.agent import models as agent_models

DEFAULT_PROVIDER = "bedrock"
# Older Pydantic AI prefixes, still accepted in settings and rewritten to the current ones.
LEGACY_PREFIXES = {"google-gla": "google", "gemini": "google", "google-vertex": "google-cloud"}
# Prefixes that reach the same provider, so one allow-list entry covers them.
PROVIDER_GROUPS = {"openai-chat": "openai", "openai-responses": "openai", "azure-responses": "azure"}


class ProviderNotAllowed(PermissionError):
    pass


def _known_provider(name: str) -> bool:
    from pydantic_ai.providers import infer_provider_class

    from .plugins import get_registry

    if name in get_registry().model_providers:
        return True
    try:
        infer_provider_class(name)
        return True
    except ValueError:
        return False


def split_spec(spec: str) -> tuple[str, str]:
    """`bedrock:anthropic.x` -> ("bedrock", "anthropic.x"); a bare id means Bedrock.

    Old prefixes are rewritten (`google-gla:` -> `google:`, `google-vertex:` -> `google-cloud:`).
    """
    head, sep, rest = spec.partition(":")
    head = LEGACY_PREFIXES.get(head, head)
    if sep and _known_provider(head):
        return head, rest
    return DEFAULT_PROVIDER, spec  # Bedrock ids and ARNs contain ':' themselves


def check_allowed(spec: str, allowed: list[str]) -> str:
    provider, model_id = split_spec(spec)
    names = {LEGACY_PREFIXES.get(a, a) for a in allowed}
    if "*" not in names and provider not in names and PROVIDER_GROUPS.get(provider) not in names:
        raise ProviderNotAllowed(
            f"provider '{provider}' is not in ALC_ALLOWED_PROVIDERS ({', '.join(allowed)}). Plant data may only go to "
            f"approved providers; add it deliberately if your data policy allows it.")
    return f"{provider}:{model_id}"


def build_model(spec: str, settings):
    """Resolve a spec to a Pydantic AI Model, applying the provider allow-list and the Bedrock region."""
    from .plugins import get_registry

    full = check_allowed(spec, settings.allowed_providers)
    provider, model_id = full.split(":", 1)
    providers = get_registry().model_providers
    if provider in providers:  # bedrock, and any provider a plugin adds
        return providers.get(provider).build(model_id, settings)
    from pydantic_ai.models import infer_model

    return infer_model(full)


class PydanticAIModel(agent_models.PydanticAIModel):
    """penguineer's adapter, for a spec checked against the allow-list (and the Bedrock region and plugin
    providers applied)."""

    def __init__(self, settings, spec: str | None = None, effort: str | None = None, max_tokens: int = 16000):
        self.spec = check_allowed(spec or settings.analysis_model, settings.allowed_providers)
        super().__init__(build_model(self.spec, settings), name=self.spec, effort=effort or settings.effort,
                         max_tokens=max_tokens, retry_seconds=settings.model_retry_seconds)


def role_model(settings, role: str) -> PydanticAIModel:
    """The model for a role (planning, analysis, reviewer, small; see model_roles.py) at that role's effort."""
    from .model_roles import ROLES

    r = ROLES[role]
    return PydanticAIModel(settings, getattr(settings, r.field), effort=r.effort)


def complete(settings, spec: str, *, system: str, user: str, effort: str = "low", max_tokens: int = 8000) -> str:
    """One-shot text completion on any allowed provider (catalog drafting)."""
    return agent_models.complete(PydanticAIModel(settings, spec, effort=effort, max_tokens=max_tokens),
                                 system=system, user=user)
