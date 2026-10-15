# Deployment contract

What the infrastructure in `infra/` gives the application, and what it expects back. Commands are config values
(`commands:` in `infra/config/<env>.yaml`), so a rename on the app side needs no infrastructure code change.

## Two sandbox modes

`workflow.sandbox_mode` picks how the analysis kernel runs:

| Mode | Kernel | Data credentials | Report and seal | Status |
| --- | --- | --- | --- | --- |
| `in_process` (default) | inside the analyse task | the analyse task assumes the run role (`ALC_RUN_ROLE_ARN`) with a per-run session policy; fixed for one hour | done by `worker analyse` | default |
| `separate` | its own sandbox task (`sandbox serve`) with no route to Bedrock | the kernel's `credential_process` calls the broker Lambda, again whenever they expire; the sandbox role can do nothing else | `worker report` and `worker seal` steps | built; not yet run in an AWS account |

In `in_process` mode the process that calls Bedrock also holds data credentials for the run, so the split between
model access and data access is enforced by the app, not by AWS. In `separate` mode the orchestrator drives the kernel
over HTTP, sees cell outputs (as the model does) but no query snapshots (`inputs/*.parquet`) or `work/`, and cannot
write `SEALED`.

## Containers

All tasks run the one image built from the repository `Dockerfile` (entry point `alchemist`) or the pinned image in
`image:`. The container user is `alchemist`.

| Task | Command (arguments to `alchemist`) | Subnets | Role can |
| --- | --- | --- | --- |
| api (service) | `api --host 0.0.0.0 --port 8080`; health check `GET /healthz` returns 200 | api (HTTPS egress) | read the workspace bucket (presigned downloads); write `workspaces/*` outside run folders, `_grants/` and `_approvals/`; start, stop and read executions; send task success or failure |
| orchestrator | `worker plan --run-id <id>`, `worker analyse --run-id <id>`, `worker replay --run-id <id>`; separate: also `worker report --run-id <id>`, and `analyse`/`replay` with `--sandbox-task-arn <arn>` | app | invoke allow-listed Bedrock models; read and write run folders and `question.yaml`; in-process: assume the run role; separate: no `inputs/*.parquet` or `work/`, no `SEALED`; describe tasks in the cluster |
| sandbox (separate only) | `sandbox serve --port 8888` | sandbox | invoke the broker, nothing else |
| sealer (separate only) | `worker seal --run-id <id>` | app | read run folders, write `SEALED` |
| indexer | `catalog index` and `knowledge ingest` (nightly), `db migrate` (by hand) | app | read the lake and knowledge buckets; invoke the sub and embedding models |

In `in_process` mode the orchestrator task gets the sandbox's size (`sizes.sandbox`), because it runs the kernel.

## Environment

| Variable | On | Value |
| --- | --- | --- |
| `ALC_ENV` | all | environment name |
| `AWS_REGION` | all | stack region |
| `ALC_WORKSPACE` | all | `s3://<workspace bucket>` |
| `ALC_KNOWLEDGE` | all | `s3://<knowledge bucket>` |
| `ALC_ACCESS` | all | `s3://<workspace bucket>/_config/access.yaml` (`storage.access_file_key`), uploaded by an admin |
| `ALC_GRANTS_PREFIX` | all | `_grants/` |
| `ALC_LAKE_BUCKETS` | all | comma-separated lake bucket names (the ceiling of the run role; read prefixes come from the catalog) |
| `ALC_PLANT_PREFIX_TEMPLATE` | all | informational; the app derives prefixes from catalog registrations |
| `ALC_ALLOWED_PROVIDERS` | all | `bedrock` |
| `ALC_SANDBOX_MODE` | all | `workflow.sandbox_mode` |
| `ALC_IMAGE_DIGEST` | all | image digest when the image is pinned, else empty |
| `ALC_PLUGINS` | all | `plugins.enabled`, comma-separated; `none` when empty |
| `ALC_PLUGIN_MODULES` | all | `plugins.modules`, comma-separated |
| `ALC_EXTRA_PACKAGES` | all | `plugins.packages` installed into the image, for the manifest |
| (any) | all | `plugins.env` |
| `ALC_DB` | api, orchestrator, sealer, indexer | `/data/alchemist.db` on the shared EFS volume while `storage.shared_sqlite` is on |
| `ALC_DB_HOST`, `ALC_DB_PORT`, `ALC_DB_NAME`, `ALC_DB_USER`, `ALC_DB_PASSWORD` | api, orchestrator, sealer, indexer | Aurora PostgreSQL, for when the app moves the index there |
| `ALC_LEAD_MODEL`, `ALC_SUB_MODEL`, `ALC_EMBEDDING_MODEL` | api, orchestrator, indexer | from `bedrock:` |
| `ALC_BEDROCK_GUARDRAIL_ID`, `ALC_BEDROCK_GUARDRAIL_VERSION` | api, orchestrator, indexer | when `bedrock.guardrail` is on |
| `ALC_AUTH` | api | `alb-oidc`: verify the ALB's signed `x-amzn-oidc-data` header |
| `ALC_ALB_ARN` | api | the ALB that must have signed the header |
| `ALC_OIDC_GROUPS_CLAIM`, `ALC_GROUP_CLAIM` | api | the groups claim, from `oidc.group_claim` |
| `ALC_ACCESS_PROVIDER` | api | `plugins.access_provider`, default `alb-oidc`: users only from verified claims; plants, workspaces and group patterns from `ALC_ACCESS` |
| `ALC_LAUNCHER` | api | `alchemist.sfn_launcher:StepFunctionsLauncher` |
| `ALC_STATE_MACHINE_ARN` | api | the run workflow |
| `ALC_GRANT_HOURS` | api | how long a broker grant lasts after approval or resume: `workflow.analysis_timeout_hours` × (1 + `workflow.analysis_retries`) + 2 |
| `ALC_APPROVALS` | api | `s3://<workspace bucket>/_approvals/` |
| `ALC_RUN_ROLE_ARN`, `ALC_RUN_CREDENTIAL_SECONDS` | orchestrator (in-process) | the run role; `3600` |
| `ALC_PSEUDONYM_KEY` | indexer, orchestrator (in-process), sandbox (separate) | from Secrets Manager |
| `ALC_CLUSTER_ARN`, `ALC_SANDBOX_PORT` | orchestrator | to look up the sandbox's address (separate) |
| `ALC_BROKER_FUNCTION`, `ALC_BROKER_TOKEN` | sandbox (separate) | broker Lambda ARN; the run's broker token |
| `ALC_RUN_ID` | workflow tasks | set per execution |

## HTTP routing and identity

CloudFront serves the SPA (`web_dist_path`, default `web/dist`, built with `cd web && npm ci && npm run build`) at `/`
and forwards `/api/*` and `/oauth2/*` to the ALB with all headers, cookies and query strings, uncached. API routes
therefore live under `/api`. Paths without a file extension outside those prefixes are rewritten to `/index.html` for
client-side routing.

The ALB authenticates every forwarded request with OIDC and passes the claims in the signed `x-amzn-oidc-data`
header. The API verifies the signature with the key from `public-keys.auth.elb.<region>.amazonaws.com`, which is on
the internet, so the API service runs in its own `api` subnets routed through one NAT gateway
(`network.api_egress`). Orchestrator, sandbox, indexers and the database have no route out.

## Run workflow

The API's launcher (`alchemist.sfn_launcher`) starts one execution of `ALC_STATE_MACHINE_ARN` per run:

| API call | Launcher does |
| --- | --- |
| run created | separate: writes the run's grant, expired and without data prefixes. `StartExecution` with `{"run_id": ..., "mode": "run"}` (separate: plus `"broker_token"`) |
| plan approved | separate: rewrites the grant with the pinned views' prefixes, valid for `ALC_GRANT_HOURS`. Writes `_approvals/<run_id>/approved.json` and sends the stored task token |
| replay run created | separate: writes the grant (no lake prefixes for a replay, the views' for a rerun). `StartExecution` with `{"run_id": ..., "mode": "replay"}` (separate: plus `"broker_token"`) |

| State | Does |
| --- | --- |
| Prepare, Mode | build the commands; choose run, replay or resume (resume skips to the analysis) |
| Plan | `worker plan` to completion |
| WaitForApproval | the approval function stores the task token at `_approvals/<run_id>/token.json`; the execution waits (up to `workflow.approval_timeout_days`) until the launcher sends it. If the approval was written first, the function sends it at once |
| Analyse | in-process: `worker analyse`, which also builds the report and seals the run (a model failure still ends in a sealed partial run). Separate: StartSandbox (with the broker token), Analyse (with the sandbox's task ARN), Report, StopSandbox and Seal; Report and Seal run even when Analyse fails. Analyse is retried up to `workflow.analysis_retries` times when its task fails or times out; each attempt resumes from the run's checkpoint. A paused run's Report and Seal do nothing |
| Replay | in-process: `worker replay`. Separate: StartReplaySandbox, Replay, StopReplaySandbox and SealReplay |

Stopping and pausing are audit events (`run.stop`, `run.pause`) that workers check between steps; the API calls
`StopExecution` only for a run cancelled while planning or waiting for approval, since it would end an analysis without
a report. Resuming a paused run starts a new execution, `<run_id>-resume-<time>`, with `"mode": "resume"`.

## Credential broker (separate mode)

The API's launcher writes `s3://<workspace>/_grants/<run_id>.json` (`alchemist.credentials.make_grant`) when it
starts a run's execution, and rewrites it on approval (see Run workflow above):

```json
{"schema": "alchemist.grant/v1", "run_id": "01JA...", "user": "eng_p1", "plants": ["P01"],
 "read_prefixes": ["s3://lake/line2/historian/"], "knowledge_prefixes": ["s3://<knowledge>/P01/"],
 "run_prefix": "s3://<workspace>/workspaces/.../runs/01JA.../",
 "token_sha256": "<sha256 hex of broker_token>", "expires_at": "2026-10-15T14:00:00Z"}
```

`read_prefixes` are the catalog registrations of the run's pinned views, as in `alchemist.credentials.run_policy`;
`knowledge_prefixes` is empty because structured knowledge is copied into the run's `inputs/`. The sandbox kernel's
AWS profile has `credential_process = alchemist broker credentials`, which invokes `ALC_BROKER_FUNCTION` with
`{"run_id": ..., "token": ...}` and prints the result, the standard credential-process JSON (`Version`,
`AccessKeyId`, `SecretAccessKey`, `SessionToken`, `Expiration`); the broker answers `{"error": "denied"}` otherwise.
boto3 and cloudpathlib call it again before the credentials expire, and DuckDB's S3 secret is created with
`refresh auto`, so an analysis may run past the one-hour session limit while the grant lasts. The broker:

- refuses a grant with another run id, a wrong token, a past `expires_at`, a `run_prefix` that is not
  `workspaces/.../runs/<run_id>/` in the workspace bucket, a read prefix outside the registered lake buckets (or a
  whole bucket), or a knowledge prefix outside the knowledge bucket;
- assumes the run role for at most one hour (role chaining), tagged with run id, user and plants for CloudTrail;
- passes a session policy that allows `GetObject` and `ListBucket` on the read, knowledge and run prefixes,
  `PutObject` only under the run prefix, and denies writing or deleting the run's `SEALED`, the same rules as
  `alchemist.credentials.run_policy`.

A session policy holds about 2 KB, enough for tens of prefixes. A plant whose data is scattered over many prefixes
needs S3 Access Grants instead (technical design, open question 2).

## Workspace bucket conventions

- `work/` intermediates expire after `storage.work_expiry_days` when tagged `alchemist:tier=work`.
- `_grants/` is written only by the API and read only by the broker; `_approvals/` only by the API and the approval
  function.
- `_config/access.yaml` holds the access rules (plants, workspaces, group-name patterns, `identity.group_claim`).
- Everything under `workspaces/*/runs/*` can be written only by the run, orchestrator and sealer roles.
