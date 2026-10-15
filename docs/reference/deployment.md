# Deployment contract

What the infrastructure in `infra/` gives the application, and what it expects back. Commands are config values
(`commands:` in `infra/config/<env>.yaml`), so a rename on the app side needs no infrastructure code change.

## Containers

All tasks run the one image built from the repository `Dockerfile` (entry point `alchemist`) or the pinned image in
`image:`. The container user is `alchemist`.

| Task | Command (arguments to `alchemist`) | Subnets | Role can |
| --- | --- | --- | --- |
| api (service) | `api --host 0.0.0.0 --port 8080`; health check `GET /healthz` returns 200 | app | read the workspace bucket; write `workspaces/*` outside run folders and `_grants/`; start, stop and read executions; send task success or failure |
| orchestrator | `worker plan --run-id <id>`, `worker analyse --run-id <id> --sandbox-task-arn <arn>`, `worker report --run-id <id>` | app | invoke allow-listed Bedrock models; read run folders except `inputs/` and `work/`; write run folders and `question.yaml`; describe tasks in the cluster; send task success or failure |
| sandbox | `sandbox serve --port 8888` (Jupyter Kernel Gateway) | sandbox | invoke the broker, nothing else |
| sealer | `worker seal --run-id <id>` | app | read run folders, write `SEALED` |
| indexer | `catalog index` and `knowledge ingest` (nightly), `db migrate` (by hand) | app | read the lake and knowledge buckets; invoke the sub and embedding models |

## Environment

| Variable | On | Value |
| --- | --- | --- |
| `ALC_ENV` | all | environment name |
| `AWS_REGION` | all | stack region |
| `ALC_WORKSPACE` | all | `s3://<workspace bucket>` |
| `ALC_KNOWLEDGE` | all | `s3://<knowledge bucket>` |
| `ALC_GRANTS_PREFIX` | all | `_grants/` |
| `ALC_LAKE_BUCKETS` | all | comma-separated lake bucket names |
| `ALC_PLANT_PREFIX_TEMPLATE` | all | e.g. `s3://{bucket}/plant={plant}/` |
| `ALC_ALLOWED_PROVIDERS` | all | `bedrock` |
| `ALC_IMAGE_DIGEST` | all | image digest when the image is pinned, else empty |
| `ALC_DB_HOST`, `ALC_DB_PORT`, `ALC_DB_NAME` | api, orchestrator, sealer, indexer | Aurora writer endpoint, port, `alchemist` |
| `ALC_DB` | api, orchestrator, sealer, indexer | `postgresql://<host>:<port>/alchemist`, without credentials |
| `ALC_DB_USER`, `ALC_DB_PASSWORD` | api, orchestrator, sealer, indexer | from the generated database secret |
| `ALC_LEAD_MODEL`, `ALC_SUB_MODEL`, `ALC_EMBEDDING_MODEL` | api, orchestrator, indexer | from `bedrock:` |
| `ALC_BEDROCK_GUARDRAIL_ID`, `ALC_BEDROCK_GUARDRAIL_VERSION` | api, orchestrator, indexer | when `bedrock.guardrail` is on |
| `ALC_IDENTITY`, `ALC_GROUP_CLAIM` | api | `alb-oidc`, `groups` |
| `ALC_STATE_MACHINE_ARN` | api | the run workflow |
| `ALC_CLUSTER_ARN` | orchestrator | to look up the sandbox's address |
| `ALC_PSEUDONYM_KEY` | sandbox, indexer | from Secrets Manager |
| `ALC_BROKER_FUNCTION` | sandbox | broker Lambda ARN |
| `ALC_RUN_ID` | workflow tasks | set per execution |
| `ALC_TASK_TOKEN`, `ALC_SKIP_APPROVAL` | plan task | the approval task token; `true` or `false` |
| `ALC_BROKER_TOKEN` | sandbox | the run's broker token |
| `ALC_SANDBOX_TASK_ARN` | report task | the sandbox to build the report in |

## HTTP routing and identity

CloudFront serves the SPA (`web_dist_path`, default `web/dist`) at `/` and forwards `/api/*` and `/oauth2/*` to the
ALB with all headers, cookies and query strings, uncached. API routes therefore live under `/api`. Paths without a
file extension outside those prefixes are rewritten to `/index.html` for client-side routing.

The ALB authenticates every forwarded request with OIDC and passes the claims in the `x-amzn-oidc-data` header (a JWT)
and the user id in `x-amzn-oidc-identity`. Groups are in the claim named by `ALC_GROUP_CLAIM`.

## Run workflow

The API starts one execution of `ALC_STATE_MACHINE_ARN` per run, with this input (all fields required):

```json
{"run_id": "01JA...", "question_id": "01JA...", "workspace": "quality", "project": "line2-yield",
 "run_prefix": "s3://<workspace>/workspaces/quality/projects/line2-yield/questions/01JA.../runs/01JA.../",
 "broker_token": "<random, per run>", "skip_approval": false}
```

| State | Does |
| --- | --- |
| Prepare | builds the commands with the run id |
| Plan | runs the plan task with `ALC_TASK_TOKEN` and waits up to `workflow.approval_timeout_days`. The plan task creates the run folder, drafts the plan, stores the token with the run and exits. The API calls `SendTaskSuccess` on approval (or `SendTaskFailure` on rejection); with `skip_approval` the plan task sends it itself |
| StartSandbox | starts the sandbox task and keeps its ARN |
| Analyse | runs the orchestrator to completion, within `workflow.analysis_timeout_hours` |
| Report | builds the (possibly partial) report in the sandbox; runs even when Analyse failed |
| StopSandbox | stops the sandbox, so no data writer remains |
| Seal | writes `SEALED` and indexes the run |

A failure before the sandbox starts goes straight to Seal. To stop a run and still get a partial report, the API
should ask the orchestrator to stop (for example a flag in the run record) rather than call `StopExecution`, which
ends the execution without Report and Seal.

## Credential broker

When the API creates a run it writes `s3://<workspace>/_grants/<run_id>.json`:

```json
{"schema": "alchemist.grant/v1", "run_id": "01JA...", "user": "eng_p1", "plants": ["P01"],
 "read_prefixes": ["s3://lake/plant=P01/"], "knowledge_prefixes": ["s3://<knowledge>/P01/"],
 "run_prefix": "s3://<workspace>/workspaces/.../runs/01JA.../",
 "token_sha256": "<sha256 hex of broker_token>", "expires_at": "2026-10-15T14:00:00Z"}
```

The sandbox's AWS `credential_process` invokes `ALC_BROKER_FUNCTION` with `{"run_id": ..., "token": ...}` and prints
the result, which is the standard credential-process JSON (`Version`, `AccessKeyId`, `SecretAccessKey`,
`SessionToken`, `Expiration`), or `{"error": "denied"}`. The broker:

- refuses a grant with another run id, a wrong token, a past `expires_at`, a `run_prefix` that is not
  `workspaces/.../runs/<run_id>/` in the workspace bucket, a read prefix outside the registered lake buckets (or a
  whole bucket), or a knowledge prefix outside the knowledge bucket;
- assumes the run role for at most one hour (role chaining), tagged with run id, user and plants for CloudTrail;
- passes a session policy that allows `GetObject` and `ListBucket` on the read, knowledge and run prefixes and
  `PutObject` only under the run prefix. The run role itself never allows writing `SEALED`.

A session policy holds about 2 KB, enough for tens of prefixes. A plant whose data is scattered over many prefixes
needs S3 Access Grants instead (technical design, open question 2).

## Workspace bucket conventions

- `work/` intermediates expire after `storage.work_expiry_days` when tagged `alchemist:tier=work`.
- `_grants/` is written only by the API and read only by the broker.
- Everything under `workspaces/*/runs/*` can be written only by the run, orchestrator and sealer roles.
