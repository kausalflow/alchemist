# Command line

```text
alchemist <command> [options]
```

Run `alchemist <command> --help` for the options of any command. Output that describes records (runs, cards, plans) is
printed as JSON.

## `demo`

| Command | Does |
| --- | --- |
| `alchemist demo setup [--use-model] [--no-approve] [--lake URI]` | generate the synthetic plant under `ALC_HOME`, register its datasets and index them. `--use-model` drafts cards with `ALC_SMALL_MODEL`; `--no-approve` leaves drafts for review; `--lake s3://...` uploads the lake there and registers it on S3 |
| `alchemist demo run` | ask, approve, replay and verify the demo question offline, with the scripted model |

## `serve`

```text
alchemist serve [--host 127.0.0.1] [--port 8000] [--scripted]
```

Runs the [REST API](api.md) under `/api` and, when `web/dist` has been built, the [web app](../guides/web-app.md) at
`/`. `--scripted` plays the offline demo script instead of calling a model.

`alchemist api` is the same command (the name the container deployment uses).

## `worker`

```text
alchemist worker [--concurrency N] [--drain] [--scripted]
alchemist worker {plan,analyse,report,seal,replay} --run-id RUN [--sandbox-task-arn ARN | --sandbox-url URL]
```

Without a step, takes steps from the run queue (`ALC_LAUNCHER=queue` on the API) and runs them, `--concurrency` at a
time (default `ALC_WORKER_CONCURRENCY` or 2), until it gets SIGTERM or Ctrl-C; then it finishes the steps in hand and
exits. `--drain` exits once the queue is empty. Start as many workers as you want runs going at once, on one computer
or many: they only need the same database and workspace. See
[Running many runs](../concepts/runs.md#running-many-runs-in-the-background).

With a step, runs one step of an existing run in this process and exits: planning of a created run, the analysis of
an approved run, or a replay created by the API. With `ALC_LAUNCHER=step-functions` (AWS) each step is an ECS task
started by the run's Step Functions execution this way.

With the default `ALC_SANDBOX_MODE=in_process` the kernel is a child of the worker, and `analyse` and `replay` also
report and seal the run. With `ALC_SANDBOX_MODE=separate` they drive the kernel of a separate sandbox task, found by
its ECS task ARN (`--sandbox-task-arn`, or `ALC_SANDBOX_TASK_ARN`; the worker waits for it to run and looks up its
address in `ALC_CLUSTER_ARN` on `ALC_SANDBOX_PORT`) or given directly (`--sandbox-url`), and leave sealing to two more
steps:

| Step | Does |
| --- | --- |
| `report` | after `analyse`: if the analysis died before reporting, writes the manifest and, when there are findings, a partial report; otherwise does nothing |
| `seal` | writes `SEALED` and indexes its hash; a run still marked running is marked failed. Safe to retry |

## `sandbox serve`

```text
alchemist sandbox serve [--host 0.0.0.0] [--port 8888]
```

The analysis kernels as their own service: one sandbox task per run on AWS (`ALC_SANDBOX_MODE=separate`), or one
sandbox service for every run in docker compose and the Helm chart (`ALC_SANDBOX_URL` on the API). It serves one
kernel per run folder over a small HTTP API (`GET /healthz`, `POST /start`, `POST /execute`, `POST /stop`) that
`worker analyse --sandbox-url` and the API use, and builds the kernels' environment itself, without the database
settings or its own token. With `ALC_BROKER_FUNCTION` set, the kernel has no AWS keys: its AWS profile's
`credential_process` is `alchemist broker credentials`, so boto3, cloudpathlib and DuckDB get the run's credentials
from the broker and fetch new ones when they expire. With `ALC_RUN_ID` set it serves only that run's folder. Without
`ALC_BROKER_FUNCTION` the kernel uses the credentials the API sent for the run (`POST /start` and
`POST /credentials`, when `ALC_RUN_ROLE_ARN` is set), or else the ambient ones. Where Linux Landlock is available each
kernel confines itself to its run's datasets and folder (`ALC_KERNEL_JAIL`). With `ALC_WORKSPACE` set it only starts kernels for
folders in that workspace, and with `ALC_SANDBOX_TOKEN` set it answers only requests that carry the token
(`Authorization: Bearer ...`). In AWS only the orchestrator's security group can reach the port; in docker compose
and Kubernetes only the API can (an internal network, a NetworkPolicy).

## `broker credentials`

```text
alchemist broker credentials [--run-id RUN] [--file PATH]
```

An AWS `credential_process`: invokes the broker Lambda (`ALC_BROKER_FUNCTION`) with the run id (`ALC_RUN_ID`) and its
token (`ALC_BROKER_TOKEN`) and prints the credential-process JSON, or exits non-zero if the broker refuses. It ignores
`AWS_PROFILE` and `AWS_CONFIG_FILE`, so it calls the Lambda with the sandbox task's own role rather than through the
profile that runs it.

With `--file` (in-process kernels) it prints the credentials the worker keeps fresh in that file instead; the worker
writes the file and points the kernel's profile at it, so this is not run by hand.

## `catalog`

```text
alchemist catalog {list,register,index,show,approve,reject,suggestions,health} [options]
```

| Action | Options | Does |
| --- | --- | --- |
| `list` | | registry rows, each with its card versions |
| `register` | `--id --prefix --plant` (required), `--glob` (default `*.parquet`), `--format` (`parquet`, `csv`, `json`, or a format a plugin adds), `--metadata`, `--owner` | add or replace a dataset in the registry |
| `index` | `--id` (default: all), `--use-model`, `--drafter NAME`, `--force` | draft a new card version where the metadata file changed, with the `heuristic` drafter (`model` with `--use-model`, or any registered drafter) |
| `show` | `--id` | latest card version, any status |
| `approve` | `--id --version`, `--card-file` | approve a draft, optionally replacing it with an edited card |
| `reject` | `--id --version` | reject a draft |
| `suggestions` | | catalog suggestions filed by runs and engineers |
| `health` | | approved version, waiting drafts, missing facts and missing metadata files per dataset |

All actions accept `--user` (default `data_admin`), recorded in the audit log.

## `sources`

```text
alchemist sources {init,list,check,sync} [--id ID] [--file sources.yaml] [--start T] [--end T] [--every 15m] [--no-register]
```

| Action | Does |
| --- | --- |
| `init` | write an example `sources.yaml` (to `ALC_SOURCES`) |
| `list` | every source, its datasets, where it lands in the lake, and how far it is synced |
| `check` | reach each source without reading data: the PI Web API and each listed tag, or the lake table's columns; exits 1 if any fails |
| `sync` | read each source from where its last sync ended (or `backfill` back) up to now, write whole UTC days into the lake, register its datasets and draft their cards. `--start`/`--end` (ISO 8601, UTC unless zoned) choose the window; `--every 15m` keeps syncing (the docker compose `sync` service); `--no-register` only writes files |

See [Data sources](../guides/data-sources.md).

## `ask`

```text
alchemist ask QUESTION (--user USER | --claims FILE) --workspace WS --project PROJECT
              [--plants P01,P02] [--scope JSON] [--model SPEC] [--reviewer-model SPEC] [--auto-approve]
```

Checks access, creates the question and run, runs the planning step and stops for approval.

| Option | Meaning |
| --- | --- |
| `--user` | a local user from `access.yaml` |
| `--claims` | JSON file with the verified claims of an identity-provider token, instead of `--user` |
| `--plants` | comma-separated plants in scope; each must be readable by the user in this workspace |
| `--scope` | JSON scope, e.g. `{"line": "L2", "period": ["2026-08-03", "2026-08-09"]}` |
| `--model` | model spec (`provider:model-id`) for this step; default the planning or analysis model; `scripted` for the demo |
| `--reviewer-model` | reviewer spec; default the reviewer model |
| `--auto-approve` | approve the plan as proposed and continue straight into the analysis |

## `approve`

```text
alchemist approve RUN_ID (--user USER | --claims FILE) [--plan-file FILE] [--model SPEC] [--reviewer-model SPEC]
```

Approves the plan (only the asker or an admin may), runs the analysis, review and report, and seals the run.
`--plan-file` takes an edited plan JSON; its version is bumped.

## `note`, `send-back`

```text
alchemist note RUN_ID TEXT (--user USER | --claims FILE)
alchemist send-back RUN_ID TEXT (--user USER | --claims FILE) [--model SPEC]
```

`note` leaves a note for the agent on a planning, waiting, running or paused run (owner or admin); the step running
it, in any process, reads it before its next model call. `send-back` returns a plan waiting for approval to planning
with the note (asker or admin), plans again in this process and prints the revised plan. See
[Steering a run with notes](../concepts/runs.md#steering-a-run-with-notes).

## `runs`, `status`

| Command | Does |
| --- | --- |
| `alchemist runs` | every run in the index: ids, workspace, project, user, mode, parent, status, plants, seal hash |
| `alchemist status RUN_ID [--plan]` | the run's row, question text and status, the conclusion if built, and with `--plan` the plan |

## `replay`

```text
alchemist replay RUN_ID (--user USER | --claims FILE) [--mode {replay,rerun}]
```

Verifies the sealed parent, then re-executes its notebook as a new run, on its snapshots (`replay`, default) or on
current data (`rerun`). Prints the comparison table and any cell errors. Exits non-zero unless the new run completed.

## `whoami`

```text
alchemist whoami (--user USER | --claims FILE)
```

Prints the plants, workspaces and roles the access model gives the user, and the plants a run would read in each of
their workspaces. Use it to check the `identity` mapping in `access.yaml` against a real token's claims.

## `verify`

```text
alchemist verify RUN_ID
```

Checks the run folder against `SEALED` and the seal hash recorded at sealing. Lists every missing, changed or added file
and exits non-zero on any problem.

## `plugins`

```text
alchemist plugins [list|show]
```

`list` prints the loaded plugins with their versions and contributions; `show` prints every extension point and which
plugin provides each name. Plugins can add their own commands, which then appear in `alchemist --help`. See
[Plugins](../guides/plugins.md).
