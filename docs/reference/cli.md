# Command line

```text
alchemist <command> [options]
```

Run `alchemist <command> --help` for the options of any command. Output that describes records (runs, cards, plans) is
printed as JSON.

## `demo`

| Command | Does |
| --- | --- |
| `alchemist demo setup [--use-model] [--no-approve] [--lake URI]` | generate the synthetic plant under `ALC_HOME`, register its datasets and index them. `--use-model` drafts cards with `ALC_SUB_MODEL`; `--no-approve` leaves drafts for review; `--lake s3://...` uploads the lake there and registers it on S3 |
| `alchemist demo run` | ask, approve, replay and verify the demo question offline, with the scripted model |

## `catalog`

```text
alchemist catalog {list,register,index,show,approve,reject,suggestions,health} [options]
```

| Action | Options | Does |
| --- | --- | --- |
| `list` | | registry rows, each with its card versions |
| `register` | `--id --prefix --plant` (required), `--glob` (default `*.parquet`), `--format` (`parquet`, `csv`, `json`), `--metadata`, `--owner` | add or replace a dataset in the registry |
| `index` | `--id` (default: all), `--use-model`, `--force` | draft a new card version where the metadata file changed |
| `show` | `--id` | latest card version, any status |
| `approve` | `--id --version`, `--card-file` | approve a draft, optionally replacing it with an edited card |
| `reject` | `--id --version` | reject a draft |
| `suggestions` | | catalog suggestions filed by runs and engineers |
| `health` | | approved version, waiting drafts, missing facts and missing metadata files per dataset |

All actions accept `--user` (default `data_admin`), recorded in the audit log.

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
| `--model` | lead model spec (`provider:model-id`); default `ALC_LEAD_MODEL`; `scripted` for the demo |
| `--reviewer-model` | reviewer spec; default `ALC_SUB_MODEL` |
| `--auto-approve` | approve the plan as proposed and continue straight into the analysis |

## `approve`

```text
alchemist approve RUN_ID (--user USER | --claims FILE) [--plan-file FILE] [--model SPEC] [--reviewer-model SPEC]
```

Approves the plan (only the asker or an admin may), runs the analysis, review and report, and seals the run.
`--plan-file` takes an edited plan JSON; its version is bumped.

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
