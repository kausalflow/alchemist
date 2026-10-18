"""The agent loop, from penguineer (`penguineer.agent`), re-exported for plugins and older imports.

Alchemist supplies the tools (catalog, SQL, the notebook, knowledge) and the run around the loop: budgets
from settings, the transcript in the run folder, checkpoints, pauses and steering notes.
"""

from penguineer.agent.loop import Budget, Model, ScriptedModel, ScriptedText, ToolOutput, run_loop, strip_images

__all__ = ["Budget", "Model", "ScriptedModel", "ScriptedText", "ToolOutput", "run_loop", "strip_images"]
