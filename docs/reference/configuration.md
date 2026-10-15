# Configuration

Alchemist reads its settings from environment variables, with local-friendly defaults under `ALC_HOME`.

## Storage

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_HOME` | `./alchemist-home` | base folder for the defaults below and the demo data |
| `ALC_WORKSPACE` | `$ALC_HOME/workspace` | root of the workspace store; `s3://alchemist-workspace` in production |
| `ALC_DB` | `$ALC_HOME/alchemist.db` | catalog and run index (SQLite in the MVP) |
| `ALC_ACCESS` | `$ALC_HOME/access.yaml` | plants, workspaces, identity-provider group mapping, optional local users ([Access model](../concepts/access.md)) |
| `ALC_KNOWLEDGE` | `$ALC_HOME/knowledge` | documents (`*.md`) and structured knowledge (`structured/*.yaml`) |

## S3 and per-run credentials

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_RUN_ROLE_ARN` | unset | role assumed once per run with a session policy limited to the run's data and folder; unset means the sandbox uses the process's credentials |
| `ALC_RUN_CREDENTIAL_SECONDS` | `3600` | lifetime of each of a run's credential sessions (one hour at most with role chaining); the worker issues a new one before it expires |
| `AWS_ENDPOINT_URL_S3` | unset | custom S3 endpoint (MinIO, moto, LocalStack), used by boto3, cloudpathlib and DuckDB |

## Separate sandbox

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_SANDBOX_MODE` | `in_process` | `separate`: workers drive a sandbox task's kernel and leave report and seal to their own steps; the Step Functions launcher writes broker grants ([Deployment contract](deployment.md#two-sandbox-modes)) |
| `ALC_GRANT_HOURS` | `8` | how long a run's broker grant stays valid after approval |
| `ALC_GRANTS_PREFIX` | `_grants/` | where the launcher writes grants in the workspace bucket |
| `ALC_SANDBOX_TASK_ARN`, `ALC_CLUSTER_ARN`, `ALC_SANDBOX_PORT` | unset, unset, `8888` | how a worker finds the sandbox task's kernel |
| `ALC_BROKER_FUNCTION`, `ALC_BROKER_TOKEN`, `ALC_RUN_ID` | unset | on the sandbox task: the broker Lambda, the run's token, the run it serves |

See [S3 and the workspace](../guides/s3.md).

## Models

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_LEAD_MODEL` | `anthropic.claude-opus-5-5` | lead model spec; a bare id means Bedrock |
| `ALC_SUB_MODEL` | `anthropic.claude-sonnet-5-5` | reviewer and catalog-card drafter |
| `ALC_ALLOWED_PROVIDERS` | `bedrock` | comma-separated providers plant data may go to; `*` for all |
| `ALC_EFFORT` | `high` | thinking effort of the lead; `off` disables thinking |
| `AWS_REGION` | `eu-central-1` | Bedrock region |

Provider credentials are read the usual way: the AWS credential chain for Bedrock, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY`, `OPENAI_API_KEY`. See [Model providers](../guides/models.md).

## Plugins

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_PLUGINS` | `*` | installed plugins to load (entry-point names, comma-separated); `*` for all, `none` for built-ins only |
| `ALC_PLUGIN_MODULES` | unset | importable modules to load as plugins without an entry point, e.g. `site_plugins.acme` |
| `ALC_ACCESS_PROVIDER` | `yaml` | registered access provider for users, plant groups and workspaces; `yaml` reads `ALC_ACCESS` with its local users; `oidc` (alias `alb-oidc`) accepts only identity-provider claims |
| `ALC_GROUP_CLAIM` | `groups` | token claim holding the groups; `identity.group_claim` in `access.yaml` takes precedence |

See [Plugins](../guides/plugins.md).

## Budgets

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_MAX_STEPS` | `60` | tool calls per planning or analysis step |
| `ALC_MAX_OUTPUT_TOKENS` | `400000` | output tokens per planning or analysis step |
| `ALC_MAX_ACTIVE_RUNS_PER_USER` | `3` | runs one person may have planning or analysing at once (0: no limit) |
| `ALC_MAX_ACTIVE_RUNS` | `0` | runs planning or analysing at once in total (0: no limit) |
| `ALC_MODEL_RETRY_SECONDS` | `300` | how long a throttled model call keeps retrying |

## Web app and API

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_AUTH` | `dev` | `dev`: the user comes from the `X-Alchemist-User` header or `alc_user` cookie (local only); `alb-oidc`: the load balancer's signed OIDC header |
| `ALC_ALB_ARN` | unset | in `alb-oidc` mode, the only load balancer whose tokens are accepted |
| `ALC_OIDC_USER_CLAIM` | `email` | claim used as the user id (falls back to `sub`) |
| `ALC_OIDC_GROUPS_CLAIM` | `groups` | claim holding the `alc-*` group names (`ALC_GROUP_CLAIM` is accepted too); when the access model has `user_from_claims`, its `identity:` settings in `access.yaml` decide instead |
| `ALC_API_MODEL` | unset | `scripted` plays the offline demo script instead of calling a model (`alchemist serve --scripted`) |
| `ALC_LAUNCHER` | unset | `package.module:factory` for a launcher that runs plan, analysis and replay steps elsewhere; default runs them on a thread pool in the API process |
| `ALC_RECONCILE_SECONDS` | `60` | how often the API closes runs whose step died (0: never) |
| `ALC_LOCAL_WORKERS` | `2` | steps `LocalLauncher` runs at once; more wait in a queue |
| `ALC_SERVICES_TTL` | `60` | seconds the API reuses the access model and knowledge index between requests |
| `ALC_WEB_DIST` | `web/dist` | built web app to serve at `/`; the API alone runs if it is missing |

See [Web app](../guides/web-app.md) and [REST API](api.md).

## Security and provenance

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_PSEUDONYM_KEY` | `dev-only-key` | key for pseudonymised columns; set it from a secret store in production |
| `ALC_IMAGE_DIGEST` | unset | container image digest, recorded in each manifest |

!!! warning

    Change `ALC_PSEUDONYM_KEY` before using real data. The same key must be used for a replay to reproduce
    pseudonymised values.
