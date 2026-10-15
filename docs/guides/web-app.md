# Web app

Engineers ask questions, review plans, follow runs and read answers in the browser. The web app is a React single-page
app over the [REST API](../reference/api.md); it does nothing the API does not allow, so the same access rules apply
to the browser, the command line and other agents.

## Run it locally

Build the web app once (needs Node 20 or later), set up the demo, then start the server:

```bash
cd web && npm ci && npm run build && cd ..
export ALC_HOME=./alchemist-home
uv run alchemist demo setup
uv run alchemist serve --scripted
```

Open <http://127.0.0.1:8000>. The API is under `/api`, with interactive docs at `/api/docs`.

`--scripted` plays the offline demo script instead of calling a model, so it needs no credentials but only answers the
demo question (the Ask page offers it). Without `--scripted`, runs use `ALC_LEAD_MODEL` and `ALC_SUB_MODEL` exactly as
`alchemist ask` does; see [Model providers](models.md).

To work on the web app itself, run the API and Vite's dev server side by side; Vite forwards `/api` to port 8000 and
reloads on every change:

```bash
uv run alchemist serve --scripted      # terminal 1
cd web && npm run dev                  # terminal 2, then open http://localhost:5173
```

## Signing in

Locally (`ALC_AUTH=dev`, the default) the first page lists the users in `access.yaml`; pick one to act as them.
Anyone can pick any user, so this mode is for laptops and tests only.

In AWS (`ALC_AUTH=alb-oidc`) the load balancer signs users in with the corporate identity provider and passes their
claims to the API in a signed header. The API checks the signature and maps group names to rights:

| Group | Grants |
| --- | --- |
| `alc-plant-<code>` | read data and knowledge of that plant (`alc-plant-p01` → `P01`) |
| `alc-global` | read data of all plants |
| `alc-ws-<area>` | see and work on projects in that workspace |
| `alc-catalog-admin` | register datasets and review catalog cards |
| `alc-admin` | everything, including other people's runs |

Set `ALC_ALB_ARN` to the load balancer's ARN; tokens signed by anything else are refused. See
[Configuration](../reference/configuration.md#web-app-and-api).

## Screens

| Screen | What you do there |
| --- | --- |
| Ask | type the question, pick workspace and project, optionally narrow the line, period and plants. Similar past questions you may open appear as you type |
| Plan review | read each candidate cause with its method and data; edit, strike, add or reorder causes, then approve. Nothing reads the data lake before this |
| Live run | each query, computation and finding as it happens, with a Stop button. A stopped run still writes and seals a partial report |
| Answer | the conclusion card and every finding with its value, verdict, confidence and evidence; record whether you confirm it |
| Runs | all runs in your workspaces, by workspace and project |
| Catalog | approved datasets for your plants: description, grain, time zone, join keys, quirks and which columns the model never sees; suggest a fix |
| Catalog admin | health of every card, drafts to approve, edit or reject, suggestions, and registering a dataset |
| About | which models, budgets, agent tools and plugins this deployment uses |

### What a run page shows

The tabs on a finished run are how a reader checks the answer without trusting it:

| Tab | Shows |
| --- | --- |
| Answer | conclusion and findings; each finding links to the notebook cell that computed it and to its evidence |
| Plan | the plan as approved, with its version and who approved it |
| Data used | the datasets and catalog card versions pinned at approval, which columns were hidden or pseudonymised, and every query with its SQL, source objects (ETag, size, time), row count and a preview of the stored snapshot |
| Notebook | every executed cell with outputs and figures; download `notebook.ipynb` to run it yourself |
| Knowledge | document passages the agent read (with versions, flagged when past their review date) and structured knowledge copied into the run |
| Review | the independent reviewer's verdict and issues |
| Agent steps | what the model said and every tool call with its input and output |
| Report | the full HTML report |
| Provenance | models, prompt hash, packages, lockfile, access and budget; verify the seal, download the run folder, replay or rerun |

A workspace member who may not read every plant a run used sees only its question, owner and status.

### Replay and rerun

On the Provenance tab, **Replay on snapshots** re-executes the run's notebook in a fresh kernel on the data it stored
and compares every finding; **Rerun on current data** does the same against the lake as it is now. Both create a new,
sealed run linked to the original, and its first tab says how many findings matched.
