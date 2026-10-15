"""Model access through Pydantic AI, so the provider is a setting.

A model is named `provider:model-id` in Pydantic AI's notation, for example

    bedrock:anthropic.claude-opus-5-5     (default for a bare id; uses AWS_REGION and the AWS credential chain)
    anthropic:claude-opus-5-5             (ANTHROPIC_API_KEY)
    google-gla:gemini-3-pro               (GOOGLE_API_KEY)
    openai:gpt-5                          (OPENAI_API_KEY)

Alchemist keeps its own tool loop (budgets, transcript, provenance), so only the
single "send messages, get a response" step goes through Pydantic AI. Plant data
is private: providers outside `ALC_ALLOWED_PROVIDERS` (default `bedrock`) are refused.
Plugins can add providers (`alchemist.plugins.ModelProvider`); the allow-list applies to them too.
"""

from __future__ import annotations

import base64
from typing import Any

DEFAULT_PROVIDER = "bedrock"
# Pydantic AI prefixes that mean the same provider (google-gla = Gemini API, google-vertex = Vertex AI).
PROVIDER_ALIASES = {"bedrock": "bedrock", "anthropic": "anthropic", "openai": "openai", "google-gla": "google",
                    "google-vertex": "google-vertex", "gemini": "google", "google": "google"}


class ProviderNotAllowed(PermissionError):
    pass


def split_spec(spec: str) -> tuple[str, str]:
    """`bedrock:anthropic.x` -> ("bedrock", "anthropic.x"); a bare id means Bedrock."""
    from .plugins import get_registry

    head, sep, rest = spec.partition(":")
    known = (head in PROVIDER_ALIASES or head in ("openai-chat", "openai-responses", "groq", "mistral", "cohere")
             or head in get_registry().model_providers)
    if sep and known:
        return head, rest
    return DEFAULT_PROVIDER, spec  # Bedrock ids and ARNs contain ':' themselves


def check_allowed(spec: str, allowed: list[str]) -> str:
    provider, model_id = split_spec(spec)
    group = PROVIDER_ALIASES.get(provider, provider)
    if "*" not in allowed and provider not in allowed and group not in allowed:
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


class PydanticAIModel:
    """Adapts a Pydantic AI model to the loop's Model protocol (Anthropic-style content blocks)."""

    def __init__(self, settings, spec: str | None = None, effort: str | None = None, max_tokens: int = 16000):
        self.spec = check_allowed(spec or settings.lead_model, settings.allowed_providers)
        self.name = self.spec
        self.model = build_model(self.spec, settings)
        effort = effort or settings.effort
        self.thinking = {"max": "xhigh"}.get(effort, effort) if effort and effort != "off" else None
        self.max_tokens = max_tokens

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> dict:
        from pydantic_ai.direct import model_request_sync
        from pydantic_ai.models import ModelRequestParameters
        from pydantic_ai.tools import ToolDefinition

        defs = [ToolDefinition(name=t["name"], description=t.get("description"),
                               parameters_json_schema=t["input_schema"]) for t in tools]
        settings: dict[str, Any] = {"max_tokens": self.max_tokens}
        if self.thinking:
            settings["thinking"] = self.thinking
        resp = model_request_sync(self.model, to_pydantic_messages(system, messages), model_settings=settings,
                                  model_request_parameters=ModelRequestParameters(function_tools=defs,
                                                                                  allow_text_output=True))
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
