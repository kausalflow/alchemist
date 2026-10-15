"""Model access through Pydantic AI, so the provider is a setting.

A model is named `provider:model-id` in Pydantic AI's notation, for example

    bedrock:anthropic.claude-opus-5-5     (default for a bare id; uses AWS_REGION and the AWS credential chain)
    anthropic:claude-opus-5-5             (ANTHROPIC_API_KEY)
    google:gemini-3-pro                   (GOOGLE_API_KEY; Vertex AI is google-cloud:)
    openai:gpt-5                          (OPENAI_API_KEY)

Alchemist keeps its own tool loop (budgets, transcript, provenance), so only the
single "send messages, get a response" step goes through Pydantic AI. Plant data
is private: providers outside `ALC_ALLOWED_PROVIDERS` (default `bedrock`) are refused.
Plugins can add providers (`alchemist.plugins.ModelProvider`); the allow-list applies to them too.
"""

from __future__ import annotations

import base64
import logging
import random
import time
from typing import Any, Callable

log = logging.getLogger("alchemist.llm")

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


RETRY_STATUS = {408, 429, 500, 502, 503, 504, 529}
RETRY_WORDS = ("throttl", "toomanyrequests", "too many requests", "rate limit", "serviceunavailable",
               "service unavailable", "overloaded", "modelnotready")


def is_retryable(e: Exception) -> bool:
    """Throttling and transient provider errors. Many runs share one model quota, so these are normal under load."""
    if getattr(e, "status_code", None) in RETRY_STATUS:
        return True
    resp = getattr(e, "response", None)  # botocore ClientError
    code = resp.get("Error", {}).get("Code", "") if isinstance(resp, dict) else ""
    text = f"{type(e).__name__} {code} {e}".lower()
    return any(w in text for w in RETRY_WORDS)


def with_retries(call: Callable[[], Any], *, budget_seconds: float, sleep=time.sleep, first: float = 2.0,
                 cap: float = 60.0) -> Any:
    """Call, retrying throttling with exponential backoff and jitter until `budget_seconds` have passed."""
    deadline = time.monotonic() + budget_seconds
    delay = first
    while True:
        try:
            return call()
        except Exception as e:
            if not is_retryable(e) or time.monotonic() + delay > deadline:
                raise
            wait = random.uniform(delay / 2, delay)
            log.warning("model call throttled (%s); retrying in %.0fs", type(e).__name__, wait)
            sleep(wait)
            delay = min(delay * 2, cap)


class PydanticAIModel:
    """Adapts a Pydantic AI model to the loop's Model protocol (Anthropic-style content blocks)."""

    retry_seconds: float = 300.0  # ALC_MODEL_RETRY_SECONDS

    def __init__(self, settings, spec: str | None = None, effort: str | None = None, max_tokens: int = 16000):
        self.spec = check_allowed(spec or settings.lead_model, settings.allowed_providers)
        self.name = self.spec
        self.model = build_model(self.spec, settings)
        effort = effort or settings.effort
        self.thinking = {"max": "xhigh"}.get(effort, effort) if effort and effort != "off" else None
        self.max_tokens = max_tokens
        self.retry_seconds = settings.model_retry_seconds

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> dict:
        from pydantic_ai.direct import model_request_sync
        from pydantic_ai.models import ModelRequestParameters
        from pydantic_ai.tools import ToolDefinition

        defs = [ToolDefinition(name=t["name"], description=t.get("description"),
                               parameters_json_schema=t["input_schema"]) for t in tools]
        settings: dict[str, Any] = {"max_tokens": self.max_tokens}
        if self.thinking:
            settings["thinking"] = self.thinking
        params = ModelRequestParameters(function_tools=defs, allow_text_output=True)
        resp = with_retries(lambda: model_request_sync(self.model, to_pydantic_messages(system, messages),
                                                       model_settings=settings, model_request_parameters=params),
                            budget_seconds=self.retry_seconds)
        return from_pydantic_response(resp)


def to_pydantic_messages(system: str, messages: list[dict]) -> list:
    from pydantic_ai.messages import (BinaryContent, ModelRequest, ModelResponse, SystemPromptPart, TextPart,
                                      ThinkingPart, ToolCallPart, ToolReturnPart, UserPromptPart)

    out: list = []
    tool_names: dict[str, str] = {}
    for i, m in enumerate(messages):
        head = [SystemPromptPart(system)] if i == 0 and system else []
        if m["role"] == "user":
            if isinstance(m["content"], str):
                out.append(ModelRequest(parts=head + [UserPromptPart(m["content"])]))
                continue
            parts: list = list(head)
            for b in m["content"]:
                if b["type"] == "text":
                    parts.append(UserPromptPart(b["text"]))
                elif b["type"] == "tool_result":
                    content: list = []
                    for c in b["content"]:
                        if c["type"] == "text":
                            content.append(c["text"])
                        elif c["type"] == "image":
                            content.append(BinaryContent(base64.b64decode(c["source"]["data"]),
                                                         media_type=c["source"]["media_type"]))
                    parts.append(ToolReturnPart(tool_name=tool_names.get(b["tool_use_id"], "tool"),
                                                content=content if len(content) > 1 else (content[0] if content else ""),
                                                tool_call_id=b["tool_use_id"],
                                                outcome="failed" if b.get("is_error") else "success"))
            out.append(ModelRequest(parts=parts))
        else:
            parts = []
            for b in m["content"]:
                if b["type"] == "text":
                    parts.append(TextPart(b["text"]))
                elif b["type"] == "thinking":
                    parts.append(ThinkingPart(b["content"], id=b.get("id"), signature=b.get("signature"),
                                              provider_name=b.get("provider_name"),
                                              provider_details=b.get("provider_details")))
                elif b["type"] == "tool_use":
                    tool_names[b["id"]] = b["name"]
                    parts.append(ToolCallPart(b["name"], b.get("input") or {}, tool_call_id=b["id"]))
            out.append(ModelResponse(parts=parts))
    return out


def from_pydantic_response(resp) -> dict:
    from pydantic_ai.messages import TextPart, ThinkingPart, ToolCallPart

    content: list[dict] = []
    for p in resp.parts:
        if isinstance(p, TextPart):
            content.append({"type": "text", "text": p.content})
        elif isinstance(p, ThinkingPart):
            content.append({k: v for k, v in {"type": "thinking", "content": p.content, "id": p.id,
                                              "signature": p.signature, "provider_name": p.provider_name,
                                              "provider_details": p.provider_details}.items() if v is not None})
        elif isinstance(p, ToolCallPart):
            content.append({"type": "tool_use", "id": p.tool_call_id, "name": p.tool_name, "input": p.args_as_dict()})
    reason = resp.finish_reason
    if any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"
    else:
        stop = {"length": "max_tokens", "content_filter": "refusal"}.get(reason, "end_turn")
    return {"content": content, "stop_reason": stop,
            "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}}


def complete(settings, spec: str, *, system: str, user: str, effort: str = "low", max_tokens: int = 8000) -> str:
    """One-shot text completion on any allowed provider (catalog drafting)."""
    resp = PydanticAIModel(settings, spec, effort=effort, max_tokens=max_tokens).create(
        system=system, tools=[], messages=[{"role": "user", "content": user}])
    return "".join(b["text"] for b in resp["content"] if b["type"] == "text")
