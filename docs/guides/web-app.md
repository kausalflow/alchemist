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
demo question (the Ask page offers it). Without `--scripted`, runs use the role models (`ALC_PLANNING_MODEL`, `ALC_ANALYSIS_MODEL`,
`ALC_REVIEWER_MODEL`, `ALC_SMALL_MODEL`, or an admin's choice) exactly as `alchemist ask` does; see
[Model providers](models.md).

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

The [UI tour](../ui-tour.md) has a screenshot of every screen below, in light and dark mode.

The app follows one question through its gates: the agent drafts a plan, the asker approves it, the analysis runs,
and a sealed report comes out. People act only at the gates.

| Screen | What you do there |
| --- | --- |
| Board | every question in the current workspace (switch it at the top of the side menu) as a card in its stage: drafting plan, needs approval, running, sealed (closed runs behind a toggle). "Waiting on you" lists plans to approve and paused runs. Filter by project, by your own runs, or by text |
| Ask | type the question, pick workspace and project, optionally narrow the line, period and plants; plants you cannot read are shown locked. Similar past questions appear as you type, with a rerun shortcut for sealed ones |
| Approval gate | read each candidate cause with its method and data; untick, edit, add or reorder causes, see which data the plan will read and the run limits, then approve. Nothing reads the data lake before this. A plan expires 14 days after it was drafted |
| Running | each query, computation and finding as it happens, which causes have findings, the step and token limits, and Pause or Stop, each saying what will happen. A stopped run still writes and seals a partial report. Steer the agent leaves a note it reads before its next step; each note shows whether it has been read. Figures appear as they are made, under their step and in a Figures so far strip, and each query shows a preview of its result (rows, time range, a sparkline per numeric column) |
| Sealed report | the answer first, then findings, data, methods and plan, notebook and the full report; beside them how to reproduce the run, its replays and reruns, and your verdict |
| Reports | all runs in your workspaces as a table |
| Catalog | approved datasets for your plants: description, grain, time zone, join keys, quirks and which columns the model never sees; suggest a fix |
| Admin | catalog admins and admins only; see below |
| About | which models, budgets, agent tools and plugins this deployment uses |

### Admin area

Only catalog admins and admins see the Admin link. It has one catalog per plant, plus "All plants (shared)", which
every plant's questions also search. Each plant has:

| Tab | What you do there |
| --- | --- |
| Overview | counts of data sources, documents, fact tables and lines; what needs attention (drafts to review, data without a metadata file, documents past their review date, suggestions); and a box to type a question and see what an agent working on this plant would find |
| Data sources | the plant's datasets and their cards: review a draft against the approved version in plain words (what a row is, units, which columns the model may see), approve, edit, reject or redraft, and register a new data source |
| Documents and facts | add SOPs, FMEAs, incident reports and specs (Markdown or text) with the lines they cover and a review date; save new versions; edit reference fact tables as YAML |
| Lines and equipment | the plant's lines; saved lines become searchable for the agent |

Add a plant from the side menu. Who may read a plant's data is still decided by the `alc-plant-<code>` group (or
`access.yaml`); the Workspaces and Access groups pages show the current setup read-only, since
those are set in the identity provider. On Models and limits, admins choose the model for each role and set the run limits, including how long a run may work. See [Domain knowledge](../concepts/knowledge.md) for
where documents and facts are stored.

### What a run page shows

The tabs and side panels on a finished run are how a reader checks the answer without trusting it. The table lists what
each part shows (the run page groups them as Findings, Data, Methods and plan, Notebook and Full report, with
provenance beside them):

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
sealed run linked to the original, and its first tab says how many findings matched. On the board a finished replay
or rerun does not get a card of its own: it stacks under the original, whose card shows how many there are and whether
the latest matched. **Runs of this question**, beside the report, lists the original and each replay or rerun with
when it ran, who ran it and whether it matched.
