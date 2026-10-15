# REST API

The web app, the command line and other agents all go through one REST API (FastAPI). The OpenAPI spec is served at
`/api/openapi.json` and browsable at `/api/docs`.

```bash
alchemist serve [--host 127.0.0.1] [--port 8000] [--scripted]   # API and the built web app
uvicorn alchemist.api.app:app --host 0.0.0.0 --port 8000        # the same, in a container
```

## Who is calling

Every request is made as a user, resolved by `alchemist.api.identity`:

- `ALC_AUTH=dev` (default): the `X-Alchemist-User` header or the `alc_user` cookie names a user in `access.yaml`.
- `ALC_AUTH=alb-oidc`: the load balancer's signed `x-amzn-oidc-data` header; groups map to plants and workspaces as in
  [Web app](../guides/web-app.md#signing-in). The signature is checked against the load balancer's public key, fetched
  from `public-keys.auth.elb.<region>.amazonaws.com`, so the API needs outbound HTTPS to that host.

A request without a valid user gets `401`. Rights then follow [Access](../concepts/access.md): a run's contents are
returned only to a workspace member entitled to every plant the run used; other members get its question, owner and
status.

## Endpoints

All paths are under `/api`.

### Session

| Method and path | Does | Who |
| --- | --- | --- |
| `GET /me` | user, plants, workspaces and roles | any user |
| `GET /meta` | models, budgets, agent tools, loaded plugins, form options | any user |
| `GET /workspaces` | workspaces with their plants and projects | members |
| `GET /dev/users` | users in `access.yaml` (dev mode only) | anyone |

### Questions and runs

| Method and path | Does | Who |
| --- | --- | --- |
| `POST /questions` | create a question and its first run, and start planning; body `{workspace, project, question, scope}` | workspace member, within their plants |
| `GET /questions/similar?q=` | past questions the user may open, by word overlap | any user |
| `GET /runs?workspace=&project=&question_id=` | runs in the user's workspaces, newest first | members |
| `GET /runs/{id}` | question, plan, permissions, conclusion, findings, queries, datasets, knowledge, review, replay check, manifest, children, verdicts | entitled viewer |
| `POST /runs/{id}/plan:approve` | approve the plan, or send `{plan}` to approve an edited one; starts the analysis | who asked, or an admin |
| `GET /runs/{id}/events` | live progress (server-sent events, below) | entitled viewer |
| `POST /runs/{id}:pause` | pause a running analysis after its current step; it can be resumed later | run owner, admin |
| `POST /runs/{id}:resume` | continue a paused run from its last step | run owner, admin |
| `POST /runs/{id}:stop` | planning or waiting for approval: cancel now; running: stop after the current step, then build and seal a partial report | run owner, admin |
| `POST /runs/{id}:replay` | new run in `replay` (snapshots) or `rerun` (current data) mode; body `{mode}` | entitled viewer |
| `POST /runs/{id}/verdict` | `{verdict: confirmed \| partly_confirmed \| rejected, note, findings}` | workspace member |
| `GET /runs/{id}/steps` | planning and analysis transcripts as steps | entitled viewer |
| `GET /runs/{id}/notebook` | executed cells with text and image outputs | entitled viewer |
| `GET /runs/{id}/snapshots/{query}?limit=` | first rows and columns of a stored query result | entitled viewer |
| `GET /runs/{id}/files/{path}` | one file from the run folder (`?download=true` to save it) | entitled viewer |
| `GET /runs/{id}/bundle` | the whole run folder as a zip | entitled viewer |
| `GET /runs/{id}/verify` | check the folder against `SEALED` | entitled viewer |

On S3, `files/{path}` answers with a redirect to a presigned GET URL valid for five minutes, issued only after the
entitlement check. `GET /healthz` (and `/api/health`) answer `200` for load balancer health checks.

Reports are served with a content security policy that blocks scripts, since their text comes from a model.

### Catalog

| Method and path | Does | Who |
| --- | --- | --- |
| `GET /datasets?q=` | approved datasets for the user's plants; ranked by `q` | any user |
| `GET /datasets/{id}` | registry entry and approved card; admins also get every version, sample checks and suggestions | readers of that plant |
| `POST /datasets/{id}/suggestions` | file a fix to a card | readers of that plant |
| `POST /datasets` | register a dataset | catalog admin |
| `POST /datasets/{id}:index` | draft a new card version; `{use_model, force}` | catalog admin |
| `POST /datasets/{id}/cards/{v}:approve` | approve a draft, optionally with an edited `{card}` | catalog admin |
| `POST /datasets/{id}/cards/{v}:reject` | reject a draft | catalog admin |
| `GET /catalog/health` | approved version, waiting drafts, missing facts, sample checks per dataset | catalog admin |
| `GET /catalog/suggestions` | all suggestions | catalog admin |

### Admin area (plant catalogs)

One catalog per plant, plus `global`, the shared catalog every plant's questions also search. Catalog admins and
admins only.

| Method and path | Does |
| --- | --- |
| `GET /admin/plants` | every plant (from `access.yaml` and those added here) with counts of datasets, waiting drafts, documents, overdue documents, fact tables and lines |
| `POST /admin/plants` | add or rename a plant: `{id, name, description}`. Who may read its data is still decided by the `alc-plant-<id>` group |
| `GET /admin/plants/{plant}` | the plant's datasets (with card status), documents, fact tables, lines and open suggestions |
| `PUT /admin/plants/{plant}/lines` | replace the plant's lines `{lines: [{id, name, description}]}`; also writes the searchable document `LINES-<plant>` |
| `POST /admin/plants/{plant}/preview` | `{question}`: what an agent on a question scoped to that plant would find (catalog search, knowledge search, fact tables, lines) |
| `GET /admin/documents/{id}` | a document's details and Markdown body |
| `POST /admin/documents` | save a document `{id?, plant, title, type, lines, review_date, content}`; an existing id gets the next version |
| `GET /admin/facts/{name}` | a reference fact table as YAML |
| `POST /admin/facts` | save a fact table `{name, plant, description, content}` (YAML mapping); an existing name gets the next version |

## Progress events

`GET /runs/{id}/events` is a `text/event-stream`:

| Event | Data |
| --- | --- |
| `status` | `{status}` whenever the run's status changes |
| `step` | `{phase, type, ...}`: a model turn (`text`, `usage`) or a tool call (`tool`, `input`, `output`, `is_error`, `seconds`) |
| `end` | `{status}` once the run is `completed`, `partial`, `failed` or `mismatch`; the stream then closes |

Events are read from the run folder's transcripts, so they work whichever process or container runs the step.

## How long steps run

`POST /questions`, `plan:approve` and `:replay` return at once; the step runs through a launcher
(`alchemist.api.jobs`). Locally that is `LocalLauncher`, a thread pool in the API process. Each step needs only the
database row and the run folder, so in AWS a launcher that starts the run's Step Functions execution (and sends the
task token on approval) can replace it with `ALC_LAUNCHER=package.module:factory`, where `factory(services_factory)`
returns an object with `plan(run_id)`, `analyse(run_id, user)`, `replay(run_id)`, `cancel(run_id)` (a run cancelled
while planning or waiting) and `resume(run_id)` (a paused run; its analysis step continues from the checkpoint) and `reconcile()` (close or pause
runs whose step died; called every `ALC_RECONCILE_SECONDS`).
Starting a run, approving or replaying answers 429 when `ALC_MAX_ACTIVE_RUNS_PER_USER` or `ALC_MAX_ACTIVE_RUNS` is
reached.

The run lifecycle in `alchemist.run` is split the same way: `create` and `plan`, `start_approval` and `analyse`,
`start_replay` and `execute_replay`. A stop request is an audit event that the agent loop checks between steps.

## Extending the API and the web app

- **Routes:** a package can add endpoints by declaring an entry point in the `alchemist.api` group that names an
  `APIRouter` (or a function returning one). It is mounted under `/api/ext/<name>` and listed in `GET /meta`.
- **Lists in the UI:** agent tools, plugins, dataset formats and card drafters come from `GET /meta`, which reads the
  plugin registry (`alchemist.plugins`) when present, so a plugin shows up without a web release.
- **Run page tabs:** `web/src/runTabs.tsx` is a registry of tabs (`id`, `label`, `show(run)`, `render(run)`); add one
  with `registerRunTab`.
- **Identity:** `alchemist.api.identity.resolve` is the one place that turns a request into a user.
