"""The agent loop: Claude on Amazon Bedrock, calling Alchemist's tools.

The loop is a plain Messages API tool loop so every model call and tool call
passes through code Alchemist owns (budgets, logging, the provenance record).
`ScriptedModel` replays a fixed list of tool calls through the same loop, for
offline demos and tests.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Callable, Protocol

from .paths import as_path


def bedrock_client(settings):
    from anthropic import AnthropicBedrockMantle

    return AnthropicBedrockMantle(aws_region=settings.aws_region)


class Model(Protocol):
    name: str

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> dict: ...


class BedrockModel:
    """Claude through the company's Bedrock account. Model ids may be inference profile ARNs."""

    def __init__(self, settings, model: str | None = None, effort: str | None = None, max_tokens: int = 16000):
        self.client = bedrock_client(settings)
        self.name = model or settings.lead_model
        self.effort = effort or settings.effort
        self.max_tokens = max_tokens

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> dict:
        resp = self.client.messages.create(
            model=self.name,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        # Keep every block (thinking included) exactly as returned, so the history stays append-only.
        content = [b.to_dict() if hasattr(b, "to_dict") else b.model_dump(exclude_none=True) for b in resp.content]
        return {"content": content, "stop_reason": resp.stop_reason,
                "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}}


class ScriptedModel:
    """Plays back a fixed sequence of turns. Each turn is a list of (tool_name, input) calls."""

    def __init__(self, turns: list[list[tuple[str, dict]]], name: str = "scripted"):
        self.turns = list(turns)
        self.name = name
        self.i = 0

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> dict:
        if self.i >= len(self.turns):
            return {"content": [{"type": "text", "text": "Done."}], "stop_reason": "end_turn",
                    "usage": {"input_tokens": 0, "output_tokens": 0}}
        calls = self.turns[self.i]
        self.i += 1
        content = [{"type": "tool_use", "id": f"toolu_s{self.i}_{k}", "name": name, "input": inp}
                   for k, (name, inp) in enumerate(calls)]
        return {"content": content, "stop_reason": "tool_use", "usage": {"input_tokens": 0, "output_tokens": 0}}


class ToolOutput:
    def __init__(self, text: str, images: list[bytes] | None = None, is_error: bool = False):
        self.text, self.images, self.is_error = text, images or [], is_error


def _result_block(tool_use_id: str, out: ToolOutput) -> dict:
    content: list[dict] = [{"type": "text", "text": out.text or "(no output)"}]
    for png in out.images[:2]:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.b64encode(png).decode()}})
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if out.is_error:
        block["is_error"] = True
    return block


def _loggable(message: dict) -> dict:
    """Transcript copy without base64 image payloads."""
    def strip(x):
        if isinstance(x, dict):
            if x.get("type") == "image":
                return {"type": "image", "omitted": True}
            return {k: strip(v) for k, v in x.items()}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return strip(message)


class Budget:
    def __init__(self, max_steps: int, max_output_tokens: int):
        self.max_steps, self.max_output_tokens = max_steps, max_output_tokens
        self.steps = self.input_tokens = self.output_tokens = self.model_calls = 0

    def exceeded(self) -> str | None:
        if self.steps >= self.max_steps:
            return f"step budget of {self.max_steps} tool calls used"
        if self.output_tokens >= self.max_output_tokens:
            return f"output token budget of {self.max_output_tokens} used"
        return None

    def as_dict(self) -> dict:
        return {"steps": self.steps, "model_calls": self.model_calls,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


def run_loop(model: Model, *, system: str, tools: list[dict], handlers: dict[str, Callable[[dict], ToolOutput]],
             first_message: str, done: Callable[[], bool], budget: Budget, transcript,
             max_nudges: int = 2) -> str:
    """Run until `done()` is true, the budget runs out, or the model stops. Returns the reason it ended."""
    messages: list[dict] = [{"role": "user", "content": first_message}]
    log = as_path(transcript)
    lines: list[str] = [json.dumps({"at": time.time(), "message": messages[0]})]
    nudges = 0

    def flush():
        log.write_text("\n".join(lines) + "\n")

    while True:
        reason = budget.exceeded()
        if reason:
            flush()
            return f"budget: {reason}"
        resp = model.create(system=system, tools=tools, messages=messages)
        budget.model_calls += 1
        budget.input_tokens += resp["usage"]["input_tokens"]
        budget.output_tokens += resp["usage"]["output_tokens"]
        assistant = {"role": "assistant", "content": resp["content"]}
        messages.append(assistant)
        lines.append(json.dumps({"at": time.time(), "model": model.name, "stop_reason": resp["stop_reason"],
                                 "usage": resp["usage"], "message": _loggable(assistant)}))

        if resp["stop_reason"] == "refusal":
            flush()
            return "refusal: the model declined to continue"
        tool_uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
        if not tool_uses:
            if done():
                flush()
                return "done"
            if nudges >= max_nudges or resp["stop_reason"] == "max_tokens":
                flush()
                return f"stopped: model ended its turn ({resp['stop_reason']}) before finishing"
            nudges += 1
            nudge = {"role": "user", "content": "Continue with the task using the tools. It is finished only when the required final tool has succeeded."}
            messages.append(nudge)
            lines.append(json.dumps({"at": time.time(), "message": nudge}))
            continue

        results = []
        for tu in tool_uses:
            budget.steps += 1
            handler = handlers.get(tu["name"])
            started = time.time()
            if handler is None:
                out = ToolOutput(f"unknown tool {tu['name']}", is_error=True)
            else:
                try:
                    out = handler(tu.get("input") or {})
                except Exception as e:  # a tool failure goes back to the model, not up the stack
                    out = ToolOutput(f"{type(e).__name__}: {e}", is_error=True)
            results.append(_result_block(tu["id"], out))
            lines.append(json.dumps({"at": time.time(), "tool": tu["name"], "input": tu.get("input"),
                                     "is_error": out.is_error, "seconds": round(time.time() - started, 3),
                                     "output": out.text[:4000], "images": len(out.images)}))
        user = {"role": "user", "content": results}
        messages.append(user)
        flush()
        if done():
            return "done"
