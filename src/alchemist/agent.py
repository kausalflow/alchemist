"""The agent loop: a model (any provider, via llm.py) calling Alchemist's tools.

The loop is a plain tool loop so every model call and tool call
passes through code Alchemist owns (budgets, logging, the provenance record).
`ScriptedModel` replays a fixed list of tool calls through the same loop, for
offline demos and tests.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Callable, Protocol

from .paths import as_path, write_text


class Model(Protocol):
    name: str

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> dict: ...


class ScriptedModel:
    """Plays back a fixed sequence of turns. Each turn is a list of (tool_name, input) calls."""

    def __init__(self, turns: list[list[tuple[str, dict]]], name: str = "scripted"):
        self.turns = list(turns)
        self.name = name
        self.i = 0

    def advance(self, model_calls: int) -> None:
        """Skip the turns already played before a pause, so a resumed run continues the script."""
        self.i = model_calls

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


def strip_images(messages: list[dict]) -> list[dict]:
    """The conversation without image payloads, for a checkpoint (figures stay in the run's figures/ folder)."""
    def strip(x):
        if isinstance(x, dict):
            if x.get("type") == "image":
                return {"type": "text", "text": "[figure shown before the run was paused; see figures/]"}
            return {k: strip(v) for k, v in x.items()}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return strip(messages)


def run_loop(model: Model, *, system: str, tools: list[dict], handlers: dict[str, Callable[[dict], ToolOutput]],
             first_message: str, done: Callable[[], bool], budget: Budget, transcript,
             max_nudges: int = 2, should_stop: "Callable[[], bool | str | None] | None" = None,
             checkpoint: Callable[[dict], None] | None = None, resume: dict | None = None,
             steer: "Callable[[], dict | None] | None" = None) -> str:
    """Run until `done()` is true, the budget runs out, the model stops, or `should_stop()` asks it to.
    Returns the reason it ended.

    `should_stop()` returns "pause" to pause (the run can resume later) or anything else truthy to stop.
    `checkpoint(state)` is called after every completed turn with the conversation so far; passing that state back
    as `resume` continues the conversation where it left off (the transcript file is appended to).
    `steer()` is asked before every model call for notes from people following the run: {"text": ..., "notes": [...]}
    or None. The text is added to the end of the conversation, so the model reads it on that call."""
    log = as_path(transcript)
    if resume:
        messages: list[dict] = list(resume["messages"])
        nudges = resume.get("nudges", 0)
        lines = log.read_text().splitlines() if log.exists() else []
        lines.append(json.dumps({"at": time.time(), "resumed": True, "messages": len(messages)}))
    else:
        messages = [{"role": "user", "content": first_message}]
        lines = [json.dumps({"at": time.time(), "message": messages[0]})]
        nudges = 0

    def flush():
        write_text(log, "\n".join(lines) + "\n")

    while True:
        reason = budget.exceeded()
        if reason:
            flush()
            return f"budget: {reason}"
        request = should_stop() if should_stop else None
        if request:
            flush()
            return "paused: pause requested by a user" if request == "pause" else "stopped: stop requested by a user"
        note = steer() if steer else None
        if note:
            last = messages[-1]  # always the user's turn here: the question, a nudge, or tool results
            blocks = [{"type": "text", "text": last["content"]}] if isinstance(last["content"], str) else list(last["content"])
            messages[-1] = {**last, "content": blocks + [{"type": "text", "text": note["text"]}]}
            lines.append(json.dumps({"at": time.time(), "steering": note["notes"]}))
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
        if checkpoint:
            checkpoint({"messages": strip_images(messages), "nudges": nudges})
        if done():
            return "done"
