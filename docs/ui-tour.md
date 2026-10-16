# UI tour

What the web app looks like today, screen by screen, following the offline demo question from asking to a sealed,
replayed report. The images switch with the site's light and dark theme. [Web app](guides/web-app.md) explains each
screen in more detail.

!!! note "These screenshots are generated"
    `scripts/ui_previews/run.sh` walks the demo in a headless browser and saves every screen here, so a pull request
    that changes the UI shows the before and after in its diff. Ids and times are replaced with fixed stand-ins (runs
    are `01DEMO…`, the clock is 1 September 2026), so a screen's image changes only when the screen does. See
    [Contributing](contributing.md#ui-screenshots).

## The asker's flow

### Sign in

Locally (`ALC_AUTH=dev`) you pick a user from `access.yaml`. In AWS the load balancer signs you in instead and this page never shows.

![Sign in](assets/ui/sign-in-light.png#only-light)
![Sign in](assets/ui/sign-in-dark.png#only-dark)

### Ask a question

Type the question, narrow the line, period and plants if you like, and pick the workspace and project. Plants you cannot read are shown locked. Nothing reads the data lake yet.

![Ask a question](assets/ui/ask-light.png#only-light)
![Ask a question](assets/ui/ask-dark.png#only-dark)

### Approve the plan

The agent drafts candidate causes, each with its method and data. Untick, edit, add or reorder causes, check which datasets it will read, then approve. This is the first gate.

![Approve the plan](assets/ui/approval-gate-light.png#only-light)
![Approve the plan](assets/ui/approval-gate-dark.png#only-dark)

### Send the plan back

Instead of editing the plan yourself, say what should change. The agent revises it with your note and it comes back
here as the next version; nothing reads the data lake in between.

![Send the plan back](assets/ui/approval-send-back-light.png#only-light)
![Send the plan back](assets/ui/approval-send-back-dark.png#only-dark)

### Revised plan

The revised plan says which version it replaces and the note it answered; earlier versions are one click away.

![Revised plan](assets/ui/approval-revised-light.png#only-light)
![Revised plan](assets/ui/approval-revised-dark.png#only-dark)

### Board: waiting on you

Every question in the workspace sits in the column of its stage. A drafted plan waits under Needs approval until its owner acts.

![Board: waiting on you](assets/ui/board-needs-approval-light.png#only-light)
![Board: waiting on you](assets/ui/board-needs-approval-dark.png#only-dark)

### Running

Each query, computation and finding appears as it happens, with the causes that already have findings and the run's limits. Pause and Stop each say what they will do. Steer the agent leaves it a note, such as a sensor you know drifted; the agent reads it before its next step, and each note shows whether it has been read.

![Running](assets/ui/running-light.png#only-light)
![Running](assets/ui/running-dark.png#only-dark)

### Running: figures as they are made

Figures so far, at the top of the log, collects every plot the agent has made; each also appears under the step
that made it. Every query shows its rows, time range and a small line of each numeric column with its range and
missing values, so gaps or odd data stand out before any statistics run.

![Running: a figure under its step](assets/ui/running-figure-light.png#only-light)
![Running: a figure under its step](assets/ui/running-figure-dark.png#only-dark)

### Sealed report

The answer comes first, then the findings that every number in it comes from. Beside them: how to reproduce the run, other runs of the same question, and your verdict.

![Sealed report](assets/ui/report-light.png#only-light)
![Sealed report](assets/ui/report-dark.png#only-dark)

### Report: Data

The datasets and catalog card versions pinned at approval, which columns were hidden or pseudonymised, and every query with its stored snapshot.

![Report: Data](assets/ui/report-data-light.png#only-light)
![Report: Data](assets/ui/report-data-dark.png#only-dark)

### Report: Methods and plan

The plan as approved and the document passages and reference facts the agent used.

![Report: Methods and plan](assets/ui/report-methods-light.png#only-light)
![Report: Methods and plan](assets/ui/report-methods-dark.png#only-dark)

### Report: Notes

Every note people left for the agent, and when it read each one. The full report has the same list.

![Report: Notes](assets/ui/report-notes-light.png#only-light)
![Report: Notes](assets/ui/report-notes-dark.png#only-dark)

### Report: Notebook

Every executed cell with its outputs; download `notebook.ipynb` to run it yourself.

![Report: Notebook](assets/ui/report-notebook-light.png#only-light)
![Report: Notebook](assets/ui/report-notebook-dark.png#only-dark)

### Report: Provenance

Models, prompt hash, packages and the seal; verify it or download the run folder.

![Report: Provenance](assets/ui/report-provenance-light.png#only-light)
![Report: Provenance](assets/ui/report-provenance-dark.png#only-dark)

### Replay

A replay re-executes the notebook on the stored snapshots and compares every finding with the original. Rerun does the same on the lake as it is now.

![Replay](assets/ui/replay-light.png#only-light)
![Replay](assets/ui/replay-dark.png#only-dark)

### Board

Sealed runs stay on the board; closed ones are behind a toggle. Replays and reruns stack under the run they reproduce, with a count and whether the latest one matched; one still running keeps its own card under Running until it finishes. The run page lists each of them under **Runs of this question**, with when it ran, who ran it and whether it matched.

![Board](assets/ui/board-light.png#only-light)
![Board](assets/ui/board-dark.png#only-dark)

## Reading

### Reports

All runs in your workspaces as a table.

![Reports](assets/ui/reports-light.png#only-light)
![Reports](assets/ui/reports-dark.png#only-dark)

### Data catalog

The approved datasets of the plants you may read.

![Data catalog](assets/ui/catalog-light.png#only-light)
![Data catalog](assets/ui/catalog-dark.png#only-dark)

### Dataset card

What a row is, units, grain, time zone, join keys, quirks, and which columns the model never sees. Anyone can suggest a fix.

![Dataset card](assets/ui/dataset-light.png#only-light)
![Dataset card](assets/ui/dataset-dark.png#only-dark)

### About this deployment

Which models, budgets, agent tools and plugins this deployment uses.

![About this deployment](assets/ui/about-light.png#only-light)
![About this deployment](assets/ui/about-dark.png#only-dark)

## Admin area

Only catalog admins and admins see it. Each plant has its own catalog, and "All plants (shared)" is searched for every plant's questions.

### Admin home

Plant catalogs and the organisation pages.

![Admin home](assets/ui/admin-light.png#only-light)
![Admin home](assets/ui/admin-dark.png#only-dark)

### Plant overview

Counts, what needs attention, and a box to see what an agent working on this plant would find.

![Plant overview](assets/ui/admin-plant-overview-light.png#only-light)
![Plant overview](assets/ui/admin-plant-overview-dark.png#only-dark)

### Data sources

The plant's datasets and their cards: review drafts against the approved version, approve, edit, reject or redraft, and register new data.

![Data sources](assets/ui/admin-plant-data-light.png#only-light)
![Data sources](assets/ui/admin-plant-data-dark.png#only-dark)

### Documents and facts

SOPs, FMEAs, incident reports and specs with the lines they cover and a review date, plus reference fact tables.

![Documents and facts](assets/ui/admin-plant-knowledge-light.png#only-light)
![Documents and facts](assets/ui/admin-plant-knowledge-dark.png#only-dark)

### Lines and equipment

The plant's lines; saved lines become searchable for the agent.

![Lines and equipment](assets/ui/admin-plant-lines-light.png#only-light)
![Lines and equipment](assets/ui/admin-plant-lines-dark.png#only-dark)

### Shared documents

Documents and facts that apply to every plant.

![Shared documents](assets/ui/admin-shared-knowledge-light.png#only-light)
![Shared documents](assets/ui/admin-shared-knowledge-dark.png#only-dark)

### Workspaces

Read-only: which plants each workspace covers.

![Workspaces](assets/ui/admin-workspaces-light.png#only-light)
![Workspaces](assets/ui/admin-workspaces-dark.png#only-dark)

### Access groups

Read-only: who may read which plant, as set in the identity provider or `access.yaml`.

![Access groups](assets/ui/admin-access-light.png#only-light)
![Access groups](assets/ui/admin-access-dark.png#only-dark)

### Models and limits

Read-only: the models and run limits from configuration.

![Models and limits](assets/ui/admin-models-light.png#only-light)
![Models and limits](assets/ui/admin-models-dark.png#only-dark)
