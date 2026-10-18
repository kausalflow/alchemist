"""System prompts and tool definitions. Their hash is recorded in every manifest.

The investigation method (plan, data health first, one hypothesis at a time, findings, review, placeholders) and
its tools come from penguineer (`penguineer.agent.protocol`); the prompts here add Alchemist's data and tools.
"""

from __future__ import annotations

import hashlib
import json

from penguineer.agent import protocol

from . import penguineer_tools

PLAN_SYSTEM = f"""You are Alchemist, a careful process engineer investigating a question about a chemical plant.

This is the planning step. Use search_catalog and search_knowledge to learn which data exists and what the
plant's documents (FMEA, SOPs, past investigations) say about likely causes. Datasets are ds.<dataset> views.
{protocol.PLAN_STEP}

{protocol.PLAN_NOTES}

Only datasets returned by search_catalog exist for you. {protocol.UNTRUSTED}"""

ANALYSIS_SYSTEM = f"""You are Alchemist, a careful process engineer. Work through the approved plan below with evidence you compute.

Your data and tools:
- run_query: DuckDB SQL over ds.<dataset> views only. Results are saved as pinned snapshots and returned to you
  as summaries; the full frame is available in the notebook as df_<query_id>.
- The penguineer_* tools run penguineer's engines on your query snapshots (pass query ids such as "q2" for file
  inputs, an earlier call id such as "p3" for the table that call wrote, and knowledge:<name> for the plant
  graph): penguineer_data_health for faulty tags, penguineer_lag to fit the delay from a cause to an effect
  instead of assuming one, penguineer_align to line lots up with the process values that made them (take the
  dead time from the plant graph or structured knowledge), penguineer_compare for a two-group comparison,
  penguineer_investigate to rank root causes of one off-spec lot along the plant graph. Prefer them over
  hand-written code for these steps; each keeps its full result in the kernel as pg_<call_id>, so findings can
  cite its values (for example "round(pg_p2['diff'], 2)").
- run_python: a persistent Jupyter kernel (pandas, numpy, scipy, matplotlib, and the `alc` helpers) for plots
  and checks no penguineer tool covers. record_finding evaluates its value_expression in this kernel.

{protocol.ANALYSIS_STEPS}

{protocol.ANALYSIS_NOTES}

If a dataset's description is wrong or missing a fact you needed, call suggest_catalog_fix. {protocol.UNTRUSTED}"""

REVIEW_SYSTEM = protocol.REVIEW_SYSTEM

TOOLS = {
    "search_catalog": {
        "name": "search_catalog",
        "description": "Search the data catalog. Returns approved datasets this run may read, with their ds.<dataset> view name, columns, units, grain, time zone, join keys and known quirks.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    "search_knowledge": {
        "name": "search_knowledge",
        "description": "Search plant documents (SOPs, FMEA, process descriptions, past investigations). Returns passages with document id and version for citation.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    "run_query": {
        "name": "run_query",
        "description": "Run one read-only DuckDB SELECT over ds.<dataset> views. The result is saved as a content-hashed snapshot, loaded in the kernel as df_<query_id>, and summarised for you (row count, columns, stats, first rows).",
        "input_schema": {"type": "object", "properties": {
            "sql": {"type": "string"}, "purpose": {"type": "string"}}, "required": ["sql", "purpose"]},
    },
    "run_python": {
        "name": "run_python",
        "description": "Run a cell in the run's persistent Jupyter kernel. Query results are in df_<query_id>. Returns printed output, truncated, and figures.",
        "input_schema": {"type": "object", "properties": {
            "code": {"type": "string"}, "purpose": {"type": "string"}}, "required": ["code", "purpose"]},
    },
    "suggest_catalog_fix": {
        "name": "suggest_catalog_fix",
        "description": "Report a gap or error in a dataset's catalog card (unknown unit, wrong time zone, missing join key). Admins review it.",
        "input_schema": {"type": "object", "properties": {
            "dataset": {"type": "string"}, "kind": {"type": "string"}, "text": {"type": "string"}},
            "required": ["dataset", "text"]},
    },
}

TOOLS.update(protocol.TOOLS)  # submit_plan, record_finding, finish_analysis, build_report
TOOLS.update(penguineer_tools.tools())

PLAN_TOOLS = ["search_catalog", "search_knowledge", "submit_plan"]
ANALYSIS_TOOLS = ["search_catalog", "search_knowledge", "run_query", "run_python", *penguineer_tools.COMMANDS,
                  "record_finding", "suggest_catalog_fix", "finish_analysis", "build_report"]


def tool_list(names: list[str], extra: list | None = None) -> list[dict]:
    """Built-in tool definitions by name, followed by plugin tools (alchemist.plugins.Tool)."""
    return [TOOLS[n] for n in names] + [t.definition() for t in extra or []]


def prompt_version() -> str:
    """Hash of the prompts and every tool definition the model can see, plugin tools included."""
    from .plugins import get_registry

    plugin_tools = {t.name: {**t.definition(), "phases": list(t.phases)} for t in get_registry().tools}
    blob = json.dumps({"plan": PLAN_SYSTEM, "analysis": ANALYSIS_SYSTEM, "review": REVIEW_SYSTEM, "tools": TOOLS,
                       **({"plugin_tools": plugin_tools} if plugin_tools else {})}, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]
