# Run folder

A run folder is the source of truth for a run; the database only indexes it. Every YAML and JSON file carries a
`schema` field.

```text
runs/<run-id>/
  manifest.json            alchemist.manifest/v1: everything needed to understand and replay the run
  findings.json            alchemist.findings/v1: claims with their computed values and hashes
  notebook.ipynb           every executed cell with outputs (nbformat 4)
  SEALED                   alchemist.sealed/v1: SHA-256 of every other file
  inputs/
    <sha256>.parquet       query result snapshots, named by content hash
    queries.json           per query: SQL, datasets, source objects, rows, snapshot, result hash, card versions
    views.json             queryable datasets pinned at approval: glob, format, plant, card version, column exposure
    knowledge_index.json   structured knowledge copied for the run's plants
    knowledge/<name>.json  the structured knowledge itself
    parent_findings.json   replays and reruns only: the parent's findings to compare against
  work/
    result_check.json      finding-by-finding comparison with the parent
  figures/                 PNGs from notebook cells
  report/
    conclusion.md          the conclusion card
    report.html            the full report
  logs/
    plan_transcript.jsonl  planning model and tool calls
    transcript.jsonl       analysis model and tool calls
    checkpoint.json        conversation, budget and notebook position after the last step (pause, resume, recovery)
    citations.json         document passages returned to the agent
    review.json            the reviewer's verdict and issues
    report_input.json      what the agent passed to build_report
```

## `manifest.json`

| Key | Holds |
| --- | --- |
| `run_id`, `mode`, `parent_run`, `status` | identity; `mode` is `live`, `replay` or `rerun` |
| `question` | id, text and scope |
| `plan` | the plan with its version, `approved_by` and `approved_at` |
| `access` | user, workspace, project and plants |
| `environment` | Python, platform, package versions, `uv_lock_sha256`, image digest |
| `model` | `lead` and `reviewer` specs and the `prompts` hash of system prompts and tool definitions (plugin tools included) |
| `plugins` | loaded plugins with module, distribution, version and contributions; replays report `plugin_drift` against it |
| `catalog` | `{dataset, card_version}` for each pinned view |
| `knowledge` | citations and structured knowledge used |
| `queries` | the contents of `inputs/queries.json` |
| `cells` | id, kind and SHA-256 of every code cell |
| `findings`, `result_hashes` | the findings file and `{finding id: value hash}` |
| `usage` | budget use per step |

## `findings.json`

```json
{
  "id": "F2",
  "hypothesis": "H1",
  "statement": "How much lower first-pass yield was on R-118 lots than on other lots in the same week.",
  "value": 4.72,
  "unit": "percentage points",
  "verdict": "supported",
  "confidence": "high",
  "evidence": ["q1", "q3", "FMEA-L2", "PI-2025-07"],
  "value_sha256": "e4f99244..."
}
```

`verdict` is `supported`, `refuted` or `inconclusive`; `confidence` is `high`, `medium` or `low`. In a replay each
finding also carries `matches_original`.

## Notebook cells

Each code cell has `metadata.alchemist` with its `cell_id` and `kind`: `parameters` (run mode and seed), `setup`,
`query`, `python`, `finding` or `check`. A query cell reads `df_q1 = alc.query("q1", sql)`; a finding cell calls
`alc.finding("F1", ...)`; the last cell is `alc.check_results()`.
