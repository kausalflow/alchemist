"""The agent loop, from penguineer (`penguineer.agent`), re-exported for plugins and older imports.

Alchemist supplies the tools (catalog, SQL, the notebook, knowledge) and the run around the loop: budgets
from settings, the transcript in the run folder, checkpoints, pauses and steering notes.
"""

from penguineer.agent.loop import Budget, Model, ScriptedText, ToolOutput, run_loop, strip_images
from penguineer.agent.loop import ScriptedModel as _ScriptedModel

__all__ = ["Budget", "Model", "ScriptedModel", "ScriptedText", "ToolOutput", "run_loop", "strip_images"]


class ScriptedModel(_ScriptedModel):
    """penguineer's scripted model, where a turn may also hold `("text", markdown)`: what the model says before its tool
    calls, as a real model does, so the offline demo shows the agent's notes too."""

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> dict:
        if self.i >= len(self.turns):
            return super().create(system=system, tools=tools, messages=messages)
        turn = self.turns[self.i]
        self.i += 1
        content = [{"type": "text", "text": inp} if name == "text" else
                   {"type": "tool_use", "id": f"toolu_s{self.i}_{k}", "name": name, "input": inp}
                   for k, (name, inp) in enumerate(turn)]
        calls = any(c["type"] == "tool_use" for c in content)
        return {"content": content, "stop_reason": "tool_use" if calls else "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0}}
