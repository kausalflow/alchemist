"""penguineer's commands as analysis tools.

Alchemist depends on penguineer (https://github.com/kausalflow/penguineer) for the steps that decide what the answer
is: data health, dead times, lot alignment, group
comparisons, hypothesis ranking. Each command below becomes one analysis tool whose input schema is
built from the command's Params model. A call becomes one notebook cell,

    pg_p1 = alc.penguineer("p1", "data health", {"data": "q2"})

so penguineer runs in the sandbox kernel on the run's own snapshots (the orchestrator never reads lake rows), its
outputs are sealed with the run, and a replay re-runs it. The agent then records findings from `pg_p1` like from
any other computed value.
"""

from __future__ import annotations

from penguineer.agent.tools import command_tools

from .nb import PENGUINEER_HIDDEN

# Tool name -> penguineer command. Left out: `init` and `graph draft` (they set up a project), `bench homeostat`.
COMMANDS = {
    "penguineer_data_health": "data health",
    "penguineer_lag": "lag",
    "penguineer_align": "align",
    "penguineer_compare": "compare",
    "penguineer_investigate": "investigate",
    "penguineer_graph_check": "graph check",
    "penguineer_graph_lags": "graph lags",
}

FILE_INPUT = ("A query id of this run (e.g. q2), whose snapshot penguineer reads as a CSV/Parquet export, an earlier "
              "call id (e.g. p3) for the table that call wrote, or knowledge:<name> for structured knowledge such "
              "as the plant graph.")


def tools() -> dict[str, dict]:
    """Tool definitions, keyed by tool name."""
    return command_tools(COMMANDS, hidden=PENGUINEER_HIDDEN, file_input=FILE_INPUT,
                         note="Runs in the kernel on this run's query snapshots; the full result is kept as a dict "
                              "pg_<call_id> for record_finding.")


def cell(call_id: str, tool: str, inp: dict) -> str:
    """The notebook cell that runs one tool call."""
    return f"pg_{call_id} = alc.penguineer({call_id!r}, {COMMANDS[tool]!r}, {inp!r})"
