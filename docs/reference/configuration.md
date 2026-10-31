# Configuration

Alchemist reads its settings from environment variables, with local-friendly defaults under `ALC_HOME`.

## Storage

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_HOME` | `./alchemist-home` | base folder for the defaults below and the demo data |
| `ALC_WORKSPACE` | `$ALC_HOME/workspace` | root of the workspace store; `s3://alchemist-workspace` in production |
| `ALC_DB` | `$ALC_HOME/alchemist.db` | app database (catalog, run index, audit): a SQLite file, or a `postgresql://` URL |
| `ALC_DB_HOST`, `ALC_DB_PORT`, `ALC_DB_NAME`, `ALC_DB_USER` | unset, `5432`, `alchemist`, `alchemist` | PostgreSQL connection when `ALC_DB` is unset (the AWS stack sets these) |
| `ALC_DB_PASSWORD` | unset | PostgreSQL password, added at connect time so it never appears in `ALC_DB` or logs |
| `ALC_DB_SSLMODE` | `require` | libpq `sslmode` for the URL built from `ALC_DB_HOST` |
| `ALC_DB_POOL_SIZE` | `10` | PostgreSQL connections per process |
| `ALC_ACCESS` | `$ALC_HOME/access.yaml` | plants, workspaces, identity-provider group mapping, optional local users ([Access model](../concepts/access.md)) |
| `ALC_KNOWLEDGE` | `$ALC_HOME/knowledge` | documents (`*.md`) and structured knowledge (`structured/*.yaml`) |
| `ALC_SOURCES` | `$ALC_HOME/sources.yaml` | data sources `alchemist sources sync` keeps in the lake: PI servers and lake tables ([Data sources](../guides/data-sources.md)) |
| `ALC_LAKE` | `$ALC_HOME/lake` | where synced sources are written (a folder or `s3://` prefix); `lake:` in `sources.yaml` wins |

## S3 and per-run credentials

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_RUN_ROLE_ARN` | unset | role assumed once per run with a session policy limited to the run's data and folder (AWS STS, or the store's own STS at `AWS_ENDPOINT_URL`: MinIO, Ceph); with `ALC_SANDBOX_URL` the session goes to the sandbox service. Unset means the sandbox uses the process's credentials |
| `ALC_RUN_CREDENTIAL_SECONDS` | `3600` | lifetime of each of a run's credential sessions (one hour at most with role chaining); the worker issues a new one before it expires |
| `AWS_ENDPOINT_URL_S3` | unset | custom S3 endpoint (MinIO, moto, LocalStack), used by boto3, cloudpathlib and DuckDB |

## Sandbox service (docker compose, Helm)

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_SANDBOX_URL` | unset | on the API: run every analysis kernel in the sandbox service at this address (`alchemist sandbox serve`); the worker still reports and seals. Unset: the kernel is a child of the worker |
| `ALC_SANDBOX_TOKEN` | unset | on both: the bearer token the API sends and the sandbox requires. Never passed to kernels |
| `ALC_SANDBOX_MAX_KERNELS` | `4` | on the sandbox: kernels running at once; a run that finds none free fails to start its analysis |
| `ALC_SANDBOX_IDLE_SECONDS` | `14400` | on the sandbox: a kernel unused this long is stopped when another run needs room |
| `ALC_KERNEL_JAIL` | `auto` | on the sandbox: confine each kernel with Linux Landlock to its run's datasets and folder where the host supports it; `required` refuses runs where it does not; `off` |
| `ALC_SANDBOX_REQUIRE_SCOPED` | `0` | on the sandbox: `1` starts kernels only with the run's own scoped session, which the API sends when `ALC_RUN_ROLE_ARN` is set |

See [Deploy on-prem or in your cloud](../guides/deploy-on-prem.md#the-analysis-sandbox).

## Separate sandbox (AWS)

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
| `ALC_PLANNING_MODEL` | `ALC_LEAD_MODEL`, else `bedrock:anthropic.claude-opus-5-5` | writes the plan; a bare id means Bedrock |
| `ALC_ANALYSIS_MODEL` | `ALC_LEAD_MODEL`, else `bedrock:anthropic.claude-sonnet-5-5` | writes the queries and Python and decides what to check next |
| `ALC_REVIEWER_MODEL` | `ALC_SUB_MODEL`, else `bedrock:anthropic.claude-opus-5-5` | independent reviewer of the conclusion |
| `ALC_SMALL_MODEL` | `ALC_SUB_MODEL`, else `bedrock:anthropic.claude-haiku-5-5` | small jobs: drafting catalog cards |
| `ALC_LEAD_MODEL`, `ALC_SUB_MODEL` | unset | older shared settings, still read: lead for planning and analysis, sub for the reviewer and small jobs |
| `ALC_ALLOWED_PROVIDERS` | `bedrock` | comma-separated providers plant data may go to; `*` for all |
| `ALC_EFFORT` | `high` | thinking effort of the lead; `off` disables thinking |
| `AWS_REGION` | `eu-central-1` | Bedrock region |

An admin's choice on the admin "Models and limits" page overrides these for new steps (it is saved in the app
database). Provider credentials are read the usual way: the AWS credential chain for Bedrock, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY`, `OPENAI_API_KEY`. See [Model providers](../guides/models.md).

## Local models

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_LOCAL_LLM_URL` | unset | OpenAI-compatible address of a model server in your network, for `local:<model>` specs (vLLM, Ollama, llama.cpp, LM Studio, TGI), e.g. `http://ollama:11434/v1` |
| `ALC_LOCAL_LLM_API_KEY` | unset | key for that server, if it wants one |

See [Local models](../guides/models.md#local-models).

## Data source credentials

Never in `sources.yaml`; read from the environment when a source is synced.

| Variable | Meaning |
| --- | --- |
| `PI_WEBAPI_PASSWORD` | PI Web API password for `auth: basic` (or the variable a source names with `password_env`) |
| `PI_WEBAPI_TOKEN` | PI Web API token for `auth: bearer` (or the variable a source names with `token_env`) |
| `PI_WEBAPI_USER` | PI Web API user when a source sets no `username` |
| cloud SDK variables | lake sources on S3, GCS or Azure: `AWS_*`, `GOOGLE_APPLICATION_CREDENTIALS`, `AZURE_STORAGE_*`, or the workload's role |

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
| `ALC_MAX_RUN_MINUTES` | `120` | default time limit of a run's analysis, in minutes of working time ([Time limit](../concepts/runs.md#time-limit)) |
| `ALC_MAX_RUN_MINUTES_CAP` | `480` | the longest time limit a person may choose when approving a plan |

An admin's value on the admin "Models and limits" page overrides `ALC_MAX_STEPS`, `ALC_MAX_OUTPUT_TOKENS`,
`ALC_MAX_RUN_MINUTES` and `ALC_MAX_RUN_MINUTES_CAP` (saved in the app database, audited as `limits.update`).

| `ALC_MAX_ACTIVE_RUNS_PER_USER` | `3` | runs one person may have planning or analysing at once (0: no limit) |
| `ALC_MAX_ACTIVE_RUNS` | `0` | runs planning or analysing at once in total (0: no limit) |
| `ALC_MODEL_RETRY_SECONDS` | `300` | how long a throttled model call keeps retrying |

## Web app and API

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_AUTH` | `dev` | `dev`: the user comes from the `X-Alchemist-User` header or `alc_user` cookie (local only); `alb-oidc`: the load balancer's signed OIDC header; `oidc`: an ID token from any OIDC provider, forwarded by a sign-in proxy ([Single sign-on](../guides/single-sign-on.md#any-oidc-provider-outside-aws)) |
| `ALC_OIDC_ISSUER` | unset | in `oidc` mode, the provider's issuer URL; its discovery document names the signing keys, and tokens from any other issuer are refused |
| `ALC_OIDC_AUDIENCE` | unset | in `oidc` mode (required), the client id the tokens must be issued to |
| `ALC_OIDC_TOKEN_HEADER` | `authorization` | in `oidc` mode, the header the proxy puts the token in (`Bearer ` is optional) |
| `ALC_OIDC_JWKS_URL` | unset | in `oidc` mode, the signing keys' address, when the discovery document is not reachable from the API |
| `ALC_ALB_ARN` | unset | in `alb-oidc` mode, the only load balancer whose tokens are accepted |
| `ALC_OIDC_USER_CLAIM` | `email` | claim used as the user id (falls back to `sub`) |
| `ALC_OIDC_GROUPS_CLAIM` | `groups` | claim holding the `alc-*` group names (`ALC_GROUP_CLAIM` is accepted too); when the access model has `user_from_claims`, its `identity:` settings in `access.yaml` decide instead |
| `ALC_API_MODEL` | unset | `scripted` plays the offline demo script instead of calling a model (`alchemist serve --scripted`) |
| `ALC_LAUNCHER` | `local` | where plan, analysis and replay steps run: `local` on a thread pool in the API process; `queue` in `alchemist worker` processes that take them from the run queue in the database ([Running many runs](../concepts/runs.md#running-many-runs-in-the-background)); `step-functions` one Step Functions execution per run (AWS); or `package.module:factory` for another launcher |
| `ALC_RECONCILE_SECONDS` | `60` | how often the API closes runs whose step died, and (queue) re-queues steps whose worker died (0: never) |
| `ALC_LOCAL_WORKERS` | `2` | steps `local` runs at once; more wait in memory |
| `ALC_QUEUE_EMBEDDED_WORKERS` | `0` | with `queue`, steps the API process also runs itself, so one `alchemist serve` needs no separate worker |
| `ALC_WORKER_CONCURRENCY` | `2` | steps each `alchemist worker` runs at once (`--concurrency`) |
| `ALC_JOB_LEASE_SECONDS` | `120` | a step whose worker sent no heartbeat for this long is taken to have lost its worker |
| `ALC_JOB_RETRIES` | `2` | times such a step is queued again (an analysis continues from its checkpoint) before its run is paused or failed |
| `ALC_WORKER_POLL_SECONDS` | `2` | how often an idle worker looks for a queued step |
| `ALC_QUEUE_METRICS_NAMESPACE` | unset | CloudWatch namespace the API sends the queue's `QueuedSteps`, `RunningSteps` and `Backlog` to (AWS: what the worker service scales on) |
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
