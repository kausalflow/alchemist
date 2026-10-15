# Alchemist

Alchemist answers process questions such as *"why was quality low on Line 2 from 3 to 9 August?"* by querying the
plant data lake, running Python in a sandbox, and grounding its reasoning in plant documents. Every answer comes with a
short conclusion, a full report and the executed notebook, and every run can be replayed by a person or another agent
with identical numbers.

<div class="grid cards" markdown>

-   **Try it in two commands**

    ---

    The offline demo plays a full investigation on a synthetic plant, with no model access needed.

    [Getting started](getting-started.md)

-   **Understand a run**

    ---

    Plan, approve, analyse, review, report, seal, replay: what happens at each step and why.

    [How a run works](concepts/runs.md)

-   **Pick a model provider**

    ---

    Bedrock by default; Anthropic, Gemini or OpenAI through Pydantic AI, behind an allow-list.

    [Model providers](guides/models.md)

-   **Look things up**

    ---

    Every command, environment variable, run file and agent tool.

    [Reference](reference/cli.md)

</div>

## What Alchemist guarantees

Transparent
:   Each run records the data it read, the code it ran, the documents it cited, and the models it used. The conclusion
    is built from recorded findings, so every number in it traces back to a notebook cell.

Reproducible
:   The data lake has no object versioning, so each run snapshots the query results it used into its own folder. A
    replay re-executes the notebook on those snapshots and compares every finding's hash with the original. A replay
    needs no model call.

Governed
:   Data access follows plant groups; project visibility follows workspaces. Only datasets with an admin-approved
    catalog card can be queried, sensitive columns are dropped or pseudonymised before the model sees them, and plant
    data goes only to model providers on the allow-list.

## The building blocks

| Piece | What it is | Read more |
| --- | --- | --- |
| Data lake | S3 buckets (or local folders), read with DuckDB and [cloudpathlib](https://cloudpathlib.drivendata.org) | [S3 and the workspace](guides/s3.md) |
| Catalog | A registry of datasets plus reviewed *catalog cards* drafted from each dataset's own YAML/JSON metadata | [Data catalog](concepts/data-catalog.md) |
| Access model | Plant groups decide data, workspaces decide projects | [Access model](concepts/access.md) |
| Knowledge | Searchable plant documents and structured facts (spec limits, residence times) | [Domain knowledge](concepts/knowledge.md) |
| Agent | A tool loop over any [Pydantic AI](https://ai.pydantic.dev) model, with budgets and a transcript | [Agent tools](reference/agent-tools.md) |
| Sandbox | One Jupyter kernel per run; every call becomes a notebook cell | [How a run works](concepts/runs.md) |
| Run folder | The run's own S3 prefix, sealed with a SHA-256 per file when the run ends | [Run folder](reference/run-folder.md) |

!!! note "This is the MVP"

    This repository implements the core run loop with a command line. The web app, REST API, AWS infrastructure and
    the separate sandbox container described in the design documents are not built yet. See
    [MVP and target design](design.md) for what is enforced where today.
