# Alchemist

Alchemist answers process questions such as "why was quality low on Line 2 from 3 to 9 August?" by querying the plant
data lake, running Python in a sandbox, and grounding its reasoning in plant documents. Every answer comes with a short
conclusion, a full report and the executed notebook, and every run can be replayed by a person or another agent with
identical numbers.

This repository is the MVP of the [technical design](https://claude.ai/code/artifact/22c0c2a8-5aa9-407b-9303-544974b79b6b)
(product design: [here](https://claude.ai/code/artifact/860e0a60-e35c-4351-b5ec-2cfbb2053c95)).

## Documentation

The full documentation is in [`docs/`](docs/index.md) and builds with [Zensical](https://zensical.org):

```bash
uvx zensical serve   # preview on http://localhost:8000
```

## Try it offline

```bash
uv sync --extra dev
export ALC_HOME=./alchemist-home
uv run alchemist demo setup   # synthetic plant data, metadata files, documents, access groups; catalog indexed
uv run alchemist demo run     # ask -> plan -> approve -> analyse -> review -> report -> seal -> replay -> verify
uv run pytest -q
```

The demo plays a scripted investigation through the real agent loop and tools, so it needs no model access. The
synthetic data has a planted cause (resin lot R-118 lowers melt temperature by about 2 °C and yield by about 5 points),
a sensor gap, a second plant the demo user may not read, and metadata files in three different layouts.

## Use it in the browser

```bash
cd web && npm ci && npm run build && cd ..
uv run alchemist serve --scripted   # http://127.0.0.1:8000; drop --scripted to use your models
```

Ask a question, review and edit the plan, watch the run, then read the conclusion with the data, queries, notebook,
citations and review behind it. See [Web app](docs/guides/web-app.md) and [REST API](docs/reference/api.md).

## Choose the model provider

Models are set per role with [Pydantic AI](https://ai.pydantic.dev) specs, `provider:model-id`. A bare id means Bedrock.

| Provider | Example | Credentials |
| --- | --- | --- |
| Amazon Bedrock (default) | `bedrock:anthropic.claude-opus-5-5` or an inference profile ARN | AWS credential chain, `AWS_REGION` |
| Anthropic API | `anthropic:claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `google-gla:gemini-3-pro` | `GOOGLE_API_KEY` |
| OpenAI | `openai:gpt-5` | `OPENAI_API_KEY` |

```bash
export ALC_LEAD_MODEL=bedrock:anthropic.claude-opus-5-5   # plans and analyses
export ALC_SUB_MODEL=bedrock:anthropic.claude-sonnet-5-5  # reviewer and catalog-card drafter
export ALC_ALLOWED_PROVIDERS=bedrock                      # default; plant data goes only to listed providers
alchemist ask "..." --model google-gla:gemini-3-pro ...   # per-run override (provider must be allowed)
alchemist ask ... --reviewer-model openai:gpt-5           # review with a different provider than the lead
```

Because plant data is private, any provider other than Bedrock is refused until it is added to `ALC_ALLOWED_PROVIDERS`
(comma-separated, `*` for all). The manifest records the exact `provider:model` used for each role.

## Run it on a real model

```bash
export AWS_REGION=eu-central-1   # Bedrock only
uv run alchemist catalog index --use-model --force  # draft catalog cards from free-form metadata with Claude
uv run alchemist ask "Why was quality low on Line 2 from 3 to 9 August 2026?" \
    --user eng_p1 --workspace quality --project line2-yield --plants P01
uv run alchemist status <run-id> --plan             # read the plan
uv run alchemist approve <run-id> --user eng_p1      # optionally --plan-file edited.json
uv run alchemist replay <run-id> --user eng_p1       # exact replay from the stored snapshots
uv run alchemist replay <run-id> --user eng_p1 --mode rerun   # same code on current data
uv run alchemist verify <run-id>
```

## Deploy to AWS

The CDK app in [`infra/`](infra) deploys the target architecture (VPC without NAT, Aurora, ECS Fargate, Step Functions
per run, credential broker, CloudFront and an ALB with OIDC). Every account-specific value is in
`infra/config/<env>.yaml`; see [Deploy to AWS](docs/guides/deploy-aws.md).

```bash
cd infra && uv sync --extra dev && uv run pytest -q
npx aws-cdk@2 synth -c env=dev --quiet   # no AWS credentials needed
```

## How a run works

1. **Plan.** The agent searches the catalog and the documents and submits a plan: metric, scope, baseline, hypotheses
   (always including "no abnormality"), method and datasets per hypothesis. The run stops for approval.
2. **Analyse.** After approval the agent works in a fresh Jupyter kernel. `run_query` takes one read-only SELECT over
   `ds.<dataset>` views, checked by DuckDB's own parser; each dataset's glob is expanded once and every source object's
   key, ETag and modified time is recorded; the result is stored as a content-hashed Parquet snapshot in the run folder.
   `record_finding` evaluates the supporting number in the kernel, so no number is typed by the model.
3. **Review.** A second model reviews the notebook and findings once, at the end; the agent addresses the issues.
4. **Report.** The conclusion uses `{{F1}}` placeholders filled from findings; a typed measurement is rejected.
5. **Seal.** `manifest.json` records question, plan, access, environment, models and prompt version, catalog card
   versions, citations, queries, cells and result hashes. `SEALED` lists a SHA-256 for every file.
6. **Replay.** The notebook re-executes on the snapshots (or on current data) and compares every finding's hash.

## What is enforced where

| Rule | MVP | Target design |
| --- | --- | --- |
| Plant groups decide data, workspaces decide projects | identity-provider groups (or local users in `access.yaml`); tool layer, plus per-run STS credentials enforced by S3 when `ALC_RUN_ROLE_ARN` is set | same, always with per-run credentials |
| Only approved, registered datasets are queryable | views built from approved cards; SQL guard | same, plus the run role can only read registered prefixes |
| Hidden and pseudonymised columns | dropped or keyed-hashed in the views | same |
| The kernel cannot reach the model or the internet | not enforced (local process) | separate Fargate task, no route to Bedrock, no NAT |
| Sealed runs cannot change | SEALED written once (S3 conditional put), run credentials may not write it, SHA-256 tamper evidence | same |
| Catalog and run index | SQLite, or PostgreSQL with `ALC_DB=postgresql://...` | Aurora PostgreSQL (pgvector for knowledge passages is planned) |
| Document search | keyword ranking | hybrid (embeddings + full text) |

## Layout

```
src/alchemist/
  catalog.py    registry, catalog cards (drafted by Claude or a heuristic), search, suggestions, run index, audit
  sqlguard.py   read-only SQL guard on DuckDB's parser
  nb.py         helpers used inside the kernel as `alc` (query, finding, align, effect, check_results)
  sandbox.py    one Jupyter kernel per run; every call becomes a notebook cell
  llm.py        model access through Pydantic AI (Bedrock, Anthropic, Gemini, OpenAI), provider allow-list
  agent.py      tool loop with budgets and a transcript; ScriptedModel for offline runs
  prompts.py    system prompts and tool definitions (their hash goes into each manifest)
  run.py        ask / approve / replay lifecycle and the tool handlers
  access.py     plant groups and workspaces from identity-provider claims or access.yaml
  credentials.py  per-run STS credentials scoped to the run's data and folder
  report.py     conclusion card and HTML report
  workspace.py  S3 (or local) folder layout, sealing and verification
  knowledge.py  document passages and structured knowledge
  demo/         synthetic plant and the scripted investigation
```

Paths go through cloudpathlib, so setting `ALC_WORKSPACE=s3://alchemist-workspace` and registering `s3://` dataset
prefixes uses S3 directly. With `ALC_RUN_ROLE_ARN` set, each run's kernel gets its own STS credentials that can read
only its plants' registered prefixes and write only its run folder. See the S3 guide in the docs.

## Not in the MVP yet

pgvector search, PDF reports, rerunning on a new scope, and the evaluation benchmark of past investigations.
