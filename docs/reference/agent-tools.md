# Agent tools

The agent acts only through these tools; every call and result goes to the run's transcript. Tool definitions and
system prompts live in `src/alchemist/prompts.py`, and their hash is recorded in each manifest.

## Planning step

| Tool | Input | Returns |
| --- | --- | --- |
| `search_catalog` | `query` | approved datasets the run may read: view name, card version, plant, description, grain, time zone, join keys, quirks, columns (without `never_to_model` ones), missing facts |
| `search_knowledge` | `query` | passages with document id, version, section, review date and a stale flag |
| `submit_plan` | `metric`, `scope` (`period`, `baseline`, `baseline_reason`), `hypotheses` (`id`, `cause`, `method`, `datasets`, `source`), `notes` | confirmation; rejected if a dataset is not available to the run |

## Analysis step

All planning tools except `submit_plan`, plus:

| Tool | Input | Returns |
| --- | --- | --- |
| `run_query` | `sql`, `purpose` | cell id, snapshot path, frame name `df_<qid>`, row count, column types, summary statistics, first rows |
| `run_python` | `code`, `purpose` | cell id, printed output (truncated), figures |
| `penguineer_data_health`, `penguineer_lag`, `penguineer_investigate`, `penguineer_graph_check`, `penguineer_graph_lags` | the penguineer command's own parameters; file inputs are query ids or `knowledge:<name>` | cell id, penguineer's summary, the result (kept in the kernel as `pg_<call_id>`); see [penguineer](../guides/penguineer.md) |
| `record_finding` | `hypothesis`, `statement`, `value_expression`, `unit`, `verdict`, `confidence`, `evidence` | finding id and its computed value |
| `suggest_catalog_fix` | `dataset`, `kind`, `text` | suggestion id |
| `finish_analysis` | `summary` | the independent review |
| `build_report` | `title`, `conclusion`, `top_causes`, `ruled_out`, `next_check`, `method_notes`, `limitations` | the conclusion text, or why the report was rejected |

## Rules the tools enforce

- `run_query` accepts one `SELECT` over `ds.<dataset>` views the run may read; table functions, `read_*` functions and
  functions such as `glob` and `getenv` are rejected.
- The penguineer tools read only the run's query snapshots and structured knowledge, and never call an LLM.
- `record_finding` evaluates `value_expression` in the kernel; the model never supplies the number.
- `build_report` requires `finish_analysis` first, and rejects measured numbers typed into the conclusion instead of
  `{{Fn}}` (or `{{Fn.value}}`) placeholders.
- Budgets stop the loop at `ALC_MAX_STEPS` tool calls or `ALC_MAX_OUTPUT_TOKENS`, and a partial report is built.

## Kernel helpers (`alc`)

Code the agent runs can use these helpers from `alchemist.nb`, imported as `alc`:

| Helper | Does |
| --- | --- |
| `alc.query(qid, sql)` | run, read back or rerun one recorded query, depending on the run mode |
| `alc.finding(fid, statement=..., value=..., ...)` | record one claim with its value |
| `alc.align(lots, process, start=, end=, ts=, residence_min=, values=, by=)` | average process values over the window that produced each lot, shifted back by the residence time |
| `alc.effect(a, b)` | difference in means, Welch's t-test p-value and Cohen's d |
| `alc.penguineer(call_id, command, params)` | run a penguineer command on the run's snapshots; outputs in `work/penguineer/<call_id>/` |
| `alc.knowledge(name)` | structured knowledge copied into the run |
| `alc.check_results()` | compare findings with the parent run |
| `alc.methods.<name>(...)` | an analysis method added by a plugin; `alc.methods.list()` shows them |

Plugins can also add agent tools for either step; they are listed in `alchemist plugins show`, and the analysis
step's first message names the available `alc.methods`. See [Plugins](../guides/plugins.md).
