# Deploy on-prem or in your cloud

For a pilot, Alchemist runs where the plant's data already is: on one server in the plant network with docker
compose, or in the customer's own Kubernetes cluster (EKS, AKS, GKE, OpenShift, on-prem) with the Helm chart. Both use
the same image, `ghcr.io/kausalflow/alchemist`, and neither needs AWS. (The AWS-native architecture, with a Step
Functions workflow per run and separate sandbox tasks, is in [Deploy to AWS](deploy-aws.md).)

| | docker compose | Helm |
| --- | --- | --- |
| Where | one Linux server or VM (8+ cores, 32 GB; a GPU if the model runs there too) | the customer's cluster |
| Database | bundled PostgreSQL 17 | theirs (RDS, Azure Database, Cloud SQL, CloudNativePG ...) |
| Run folders and lake | a local volume, or their S3-compatible store | their S3 or S3-compatible store |
| Models | bundled Ollama (`--profile local-llm`), any server in the network, or an approved cloud API | a model server in the cluster or network, optional bundled Ollama, or an approved cloud API |
| Analysis kernels | `sandbox` service on an internal network | `sandbox` Deployment behind a NetworkPolicy |
| Sign-in | pick a user (`dev`, trial only) or the plant's identity provider (`oidc`) | the plant's identity provider (`oidc`) |
| Data sources | `sync` service every 15 minutes | CronJob every 15 minutes |

```mermaid
flowchart LR
  U[engineer's browser] --> P[sign-in proxy<br/>oauth2-proxy / ingress auth]
  P -->|ID token| A[alchemist serve<br/>API + web app + runs]
  A --> DB[(PostgreSQL)]
  A --> W[(workspace + lake<br/>volume or S3)]
  A --> M[model server<br/>vLLM / Ollama, or an approved API]
  A -->|cells, with a token| K[alchemist sandbox serve<br/>one kernel per run]
  K --> W
  S[alchemist sources sync] --> PI[(AVEVA PI<br/>PI Web API)]
  S --> LT[(lake tables)]
  S --> W
  S --> DB
```

## docker compose

Everything is in [`deploy/compose`](https://github.com/kausalflow/alchemist/tree/main/deploy/compose).

```bash
git clone https://github.com/kausalflow/alchemist && cd alchemist/deploy/compose
cp .env.example .env        # set POSTGRES_PASSWORD, ALC_PSEUDONYM_KEY, ALC_SANDBOX_TOKEN, the models and sign-in
$EDITOR config/access.yaml  # plants, workspaces and users (or group mapping)
$EDITOR config/sources.yaml # PI servers and lake tables to sync
docker compose --profile local-llm up -d    # or without the profile, with a model server you already have
```

Open `http://<server>:8000`. Then:

1. `docker compose exec api alchemist sources check` reaches each source; the `sync` service starts reading them.
2. In the web app, **Admin → the plant → Data sources**: approve each drafted card once (see
   [Data sources](data-sources.md#catalog-cards)).
3. Put plant documents (operating procedures, P&ID notes, known issues) into `config/knowledge/`.
4. Ask a question.

To try it without plant data first, load the synthetic demo into the same stack:

```bash
docker compose exec -e ALC_ACCESS=/data/access.yaml -e ALC_KNOWLEDGE=/data/knowledge api \
  sh -c "alchemist demo setup && alchemist demo run"
```

The stack keeps run folders and the lake on the `alchemist-home` volume. To use the plant's object store (AWS S3,
MinIO, Ceph, NetApp StorageGRID ...), set `ALC_WORKSPACE`, `ALC_LAKE` and the `AWS_*` variables in `.env`.

`deploy/compose/smoke.sh` is the end-to-end check CI runs on every change: the stack comes up on PostgreSQL, the demo
investigation runs, replays and verifies with its kernels in the sandbox (which must reach neither PostgreSQL nor the
internet), and a PI source syncs from a fake PI Web API into the lake and the catalog.

## Helm

The chart is in [`deploy/helm/alchemist`](https://github.com/kausalflow/alchemist/tree/main/deploy/helm/alchemist).
It expects a PostgreSQL database, an S3-compatible bucket for the workspace and the lake, and a Secret with the
credentials:

```bash
kubectl create namespace alchemist
kubectl -n alchemist create secret generic alchemist \
  --from-literal=ALC_PSEUDONYM_KEY="$(openssl rand -hex 32)" \
  --from-literal=ALC_DB_PASSWORD=... \
  --from-literal=PI_WEBAPI_PASSWORD=...          # and AWS_* keys if the pods have no workload identity
helm install alchemist deploy/helm/alchemist -n alchemist -f my-values.yaml
```

```yaml
# my-values.yaml
image: {tag: "0.1.0"}               # pin a release or digest
database: {host: alchemist.postgres.plant.internal}
storage:
  workspace: s3://plant-alchemist-workspace
  lake: s3://plant-alchemist-lake
  endpoint: https://s3.plant.internal  # leave empty for AWS S3
serviceAccount:
  annotations: {eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/alchemist}  # or GKE/AKS workload identity
models:
  planning: local:qwen3:32b
  analysis: local:qwen3:32b
  reviewer: local:qwen3:32b
  small: local:qwen3:32b
  localUrl: http://vllm.ml.svc:8000/v1
auth:
  oidc:
    issuer: https://login.microsoftonline.com/<tenant-id>/v2.0
    audience: <client-id>
access: |
  plants: [P01]
  workspaces:
    quality: {plants: [P01]}
  identity:
    group_aliases: {"5f1c...": alc-plant-P01}   # Entra ID sends group ids
sources: |
  sources:
    - {id: p01-pi, plant: P01, kind: pi, url: https://pi.plant.internal/piwebapi, auth: basic,
       username: svc-alchemist, data_server: PISRV01, tag_filter: "L2.*", backfill: 90d}
ingress:
  enabled: true
  className: nginx
  annotations:
    nginx.ingress.kubernetes.io/auth-url: https://$host/oauth2/auth
    nginx.ingress.kubernetes.io/auth-signin: https://$host/oauth2/start?rd=$escaped_request_uri
    nginx.ingress.kubernetes.io/auth-response-headers: Authorization
  hosts: [{host: alchemist.plant.internal, paths: ["/"]}]
```

The chart runs:

- the **API** (one replica, `Recreate`): the web app, the REST API and the runs; a `db migrate` init container applies
  schema migrations first;
- the **sandbox** (`sandbox.enabled`), where each run's analysis kernel runs, see [below](#the-analysis-sandbox);
- the **sync** CronJob (`sync.schedule`), `concurrencyPolicy: Forbid`;
- optionally **Ollama** as a StatefulSet with a GPU (`ollama.enabled`), when the cluster has no model server yet.

Pods run as uid 10001, non-root, without privilege escalation. The workspace and the lake need the S3 API; for Google
Cloud Storage use its XML API with HMAC keys (`storage.endpoint: https://storage.googleapis.com`). Lake *sources* can
be on GCS or Azure natively: the sync reads them there and writes the copy to the S3 lake.

## Sign-in

`ALC_AUTH=dev` lets anyone who reaches the API act as any user in `access.yaml`. Use it for a trial on one machine
only. For a pilot, use `oidc`: a sign-in proxy signs users in with the plant's identity provider and forwards the ID
token, which Alchemist verifies itself. Groups map to plants and workspaces as everywhere else. See
[Single sign-on](single-sign-on.md#any-oidc-provider-outside-aws).

## Keeping plant data in the network

With `local:` models and `ALC_ALLOWED_PROVIDERS=local`, no prompt, query result or document leaves the network: the
allow-list refuses every other provider before a request is sent, and admins cannot pick one on the Models page. The
image also ships DuckDB's S3 extensions, so nothing is downloaded at run time. What still needs network access:

| From | To | Why |
| --- | --- | --- |
| API | the model server | planning, analysis, review |
| API | the sandbox | running each analysis cell |
| sandbox | the object store (not with the compose volume) | the run's queries and files |
| API | the identity provider's keys (`jwks_uri`) | verifying ID tokens; or serve them locally with `ALC_OIDC_JWKS_URL` |
| API, sync | PostgreSQL, the object store | catalog, runs, lake |
| sync | PI Web API, lake sources | reading plant data (read-only) |
| the host | `ghcr.io` | pulling the image; or mirror it into the plant's registry |

## The analysis sandbox

The analysis model writes Python, and that code runs in a kernel. In both deployments the kernels run in their own
container, `alchemist sandbox serve`, not in the API:

- **No credentials but storage.** The sandbox gets the pseudonym key and the object store keys (or, in Kubernetes,
  the service account's workload identity), nothing else: no database password, no model or PI keys, no
  sign-in settings, no Kubernetes API token. Kernels do not even see the token the API drives the sandbox with.
- **No network but storage.** In docker compose the sandbox's only network is `internal`, with no route out; it
  reaches the API's requests and nothing else (with the workspace on the local volume it needs no network at all).
  In Kubernetes a NetworkPolicy admits only the API's pods and lets kernels reach DNS and
  `sandbox.networkPolicy.egress`, which by default is HTTPS anywhere: narrow it to the object store's address.
- **Only the API drives it.** It answers only requests with its token (`ALC_SANDBOX_TOKEN`; the chart generates
  one), and only starts kernels for run folders in the workspace.
- **Hardened container.** Read-only root file system, all capabilities dropped, no privilege escalation, uid 10001.

Each run has its own kernel process; `ALC_SANDBOX_MAX_KERNELS` (Helm: `sandbox.maxKernels`) caps how many run at
once. To run kernels inside the API container instead, set `ALC_SANDBOX_URL=` (empty) in `.env`, or
`sandbox.enabled: false`.

With the workspace on an object store, give the sandbox its own keys if you can (`SANDBOX_AWS_ACCESS_KEY_ID` and
`SANDBOX_AWS_SECRET_ACCESS_KEY` in `.env`; `sandbox.existingSecret` or `sandbox.serviceAccountName` in Helm), limited
to reading the lake and reading and writing the workspace, and attach the compose sandbox to a network that reaches
the store (see the comment in `docker-compose.yml`).

## What this deployment does not enforce yet

Compared with the [AWS architecture](deploy-aws.md), all runs share one sandbox and its storage rights: a kernel's
SQL is read-only and limited to the run's approved datasets, and every result is sealed, but Python in one run's
kernel could read another plant's lake files or another run's folder. On AWS each run gets its own sandbox task and
credentials scoped to its datasets and folder (`ALC_RUN_ROLE_ARN`, the credential broker), which need AWS STS. Until
that comes to these deployments, keep one Alchemist installation per group of plants whose users may see each
other's data.
