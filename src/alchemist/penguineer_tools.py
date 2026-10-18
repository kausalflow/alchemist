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

import copy

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


def _is_path(schema: dict) -> bool:
    options = schema.get("anyOf", [schema])
    return any(o.get("format") in ("file-path", "directory-path", "path") for o in options)


def _input_schema(params: dict) -> dict:
    """penguineer's Params schema, without the fields Alchemist sets, and with file inputs as references."""
    schema = copy.deepcopy(params)
    props = schema.get("properties", {})
    for name in PENGUINEER_HIDDEN & set(props):
        del props[name]
    for name, prop in props.items():
        if _is_path(prop):
            desc = prop.get("description", "")
            props[name] = {"type": "string", "description": f"{desc}. {FILE_INPUT}" if desc else FILE_INPUT}
    schema["required"] = [r for r in schema.get("required", []) if r in props]
    schema.pop("title", None)
    if "$defs" in schema and "$ref" not in str(props):
        del schema["$defs"]
    return schema


def tools() -> dict[str, dict]:
    """Tool definitions, keyed by tool name."""
    import penguineer
    from penguineer.core import default_registry

    schemas = penguineer.schemas()
    out = {}
    for name, command in COMMANDS.items():
        help_text = default_registry.get(command).help.split("\n\n")[0].strip()
        out[name] = {
            "name": name,
            "description": f"penguineer {command} (v{penguineer.__version__}): {help_text} Runs in the kernel on "
                           "this run's query snapshots; the full result is kept as a dict pg_<call_id> for "
                           "record_finding.",
            "input_schema": _input_schema(schemas[command]["params"]),
        }
    return out


def cell(call_id: str, tool: str, inp: dict) -> str:
    """The notebook cell that runs one tool call."""
    return f"pg_{call_id} = alc.penguineer({call_id!r}, {COMMANDS[tool]!r}, {inp!r})"
