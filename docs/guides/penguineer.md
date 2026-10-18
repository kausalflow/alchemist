# Root-cause engines from penguineer

Alchemist uses [penguineer](https://github.com/kausalflow/penguineer) for the analysis steps that decide what the
answer is: data health checks, dead times between tags, and ranking root causes of an off-spec lot along the plant
graph. penguineer is an ordinary dependency (`penguineer>=0.2,<0.3` in `pyproject.toml`); Alchemist imports it and
never the other way round.

| Alchemist owns | penguineer owns |
| --- | --- |
| the catalog, SQL guard and query snapshots | reading historian, LIMS and lot exports |
| the agent loop, plan approval, steering, review | data health, lag fitting, hypothesis ranking |
| the sandbox kernel, the run folder, sealing and replay | its run record (`run.json`) and report files |

## The tools

In the analysis step the agent has one tool per penguineer command. Each tool's input schema is the command's own
Pydantic `Params` schema, so it follows penguineer's version without changes here.

| Tool | penguineer command | What it gives the agent |
| --- | --- | --- |
| `penguineer_data_health` | `data health` | frozen tags, gaps, spikes, out-of-range values, timestamp problems |
| `penguineer_lag` | `lag` | the delay from a cause tag to an effect tag or lab property: fixed, flow-driven, or with mixing |
| `penguineer_investigate` | `investigate` | ranked root-cause hypotheses for one off-spec lot, with what was ruled out and why |
| `penguineer_graph_check` | `graph check` | problems in a plant graph, and inferred edges to confirm |
| `penguineer_graph_lags` | `graph lags` | dead times along the graph's `flows_to` edges, estimated from data |

`init`, `graph draft` and `bench homeostat` are left out: they set up a penguineer project or evaluate penguineer
itself, which is not part of answering a question.

## How a call runs

A file input is never a path. The agent passes a query id of the run (`"q2"`), and penguineer reads that query's
snapshot, or `"knowledge:<name>"` for structured knowledge such as the plant graph. The orchestrator turns the call
into one notebook cell:

```python
pg_p1 = alc.penguineer('p1', 'data health', {'data': 'q2', 'tags': ['barrel_temp_c', 'line_speed_mpm']})
```

1. **Stage.** In the kernel, `alc.penguineer` looks up the snapshot of each query id and writes structured knowledge
   out as YAML, in a folder of its own.
2. **Call.** It runs the command with `penguineer.execute(...)` in that folder, with the run's seed, its own cache
   folder and no LLM. The LLM options (`llm`, `llm_model`, `llm_dry_run`) and output paths (`out`, `in_place`) are
   not in the tool schema and are refused if given.
3. **Return.** penguineer's outputs (`run.json`, `health.json`, reports) are copied to
   `work/penguineer/<call id>/` in the run folder. The result comes back as a dict in `pg_<call id>`, with paths
   relative to the run folder, and the agent sees penguineer's text summary and the result.
4. **Record and seal.** The agent records findings with a `value_expression` over the result, for example
   `pg_p1['counts']['critical']`, so no number is typed by a model. The run is sealed with penguineer's outputs in it.

Because the call is an ordinary cell on stored snapshots, a replay re-runs it and compares the findings taken from
it like any others.

## In the demo

The demo's data-health step calls `penguineer_data_health` on the historian query. penguineer finds the six-hour gap
in TT-204 on 5 August, and finding F6 rules the sensor fault out as the cause of the yield drop.

## Shaping data for penguineer

penguineer reads a historian export in long form (`timestamp`, `tag`, `value`) or wide form (a timestamp column and
one column per tag), a LIMS export with `lot`, `sample_point`, `property`, `value` and `sampled_at`, and lot windows
with `lot`, `start` and `end`. Select and rename columns in the query to match; the
[penguineer docs](https://kausalflow.com/penguineer/) describe each format.
