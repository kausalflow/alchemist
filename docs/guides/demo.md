# Give a demo

`alchemist demo serve` shows the whole Alchemist process to an audience without anyone waiting for a model: ask a
question, read and send back the plan, approve it, watch the live notebook fill in, steer with a note or a comment on
a notebook cell, read the answer, ask for more, ask a follow-up and replay the run. Nothing calls a model or AWS, so
it works offline on a laptop.

Runs play a scripted investigation of the demo question through the real agent loop and tools: the queries, Python
cells, figures, findings, review, report, seal and replay are all real, on synthetic Line 2 data with a planted cause
(resin lot R-118). The script waits 2 seconds before each agent step, so a full analysis takes about half a minute,
slow enough to follow and fast enough not to wait.

## Start it

From the repository folder (with `uv sync` done once):

```bash
uv run alchemist demo serve
```

Then open <http://127.0.0.1:8000> and sign in as **eng_p1**. The first start takes about a minute: it creates the demo
data, builds the web app if `npm` is installed and it is not built yet, and prepares one finished answer.

The demo keeps its own folder (`alchemist-demo` in your home folder), separate from `ALC_HOME`, and ignores your model,
database and AWS settings, so it never touches a real setup. Options:

| Option | What it does |
| --- | --- |
| `--pace 1` | Seconds per agent step (default 2; `0` runs as fast as it can) |
| `--fresh` | Delete the demo folder first, to start a demo from a clean board |
| `--port 8001` | Another port, if 8000 is in use |
| `--home C:\alc-demo` | Another demo folder (or set `ALC_DEMO_HOME`) |
| `--no-example` | Do not prepare a finished answer |

With Docker instead of a checkout (once this version is published):

```bash
docker run --rm -p 8000:8000 -e ALC_DEMO_HOME=/tmp/demo ghcr.io/kausalflow/alchemist demo serve --host 0.0.0.0
```

## What to show

A walkthrough of about ten minutes. Each step works in any order, and you can skip any of them.

1. **The project.** Open project *line2-yield* from the board. It already has a *Background for Alchemist* note
   (what normal looks like, what the team suspects) and one finished answer you can open straight away.
2. **A data file.** Upload `Line 2 lab void counts.xlsx` from the `demo-files` folder of the demo folder (the start
   message prints the full path). Alchemist reads the title and units rows and describes the file in plain words,
   with starter questions.
3. **Ask.** On *Ask a question*, click *Use the demo question*, tick the lab file, and ask. A plan appears in a few
   seconds.
4. **Send the plan back.** Click *Send back with a note* and write, for example, *"Please also check whether the night shift crew made a difference"*.
   The revised plan has a new candidate cause for it.
5. **Approve.** Leave *Let me review the result before it is sealed* on. The live view fills step by step: queries with
   previews, data health, charts, findings. Switch between *Activity* and *Notebook*, and *Simple* and *Detailed*.
6. **Steer it.** While it runs, send a note or use *Comment on this step* under a chart (or *Comment* on a cell in the Notebook tab). The agent answers it in the activity feed,
   adds a matching chart to the notebook and names the note in the report. Notes about **crews** or shifts, **melt
   temperature** or the setpoint, the **resin** or supplier, **line speed** or **melt pressure** each get their own
   chart; anything else gets a daily yield chart.
7. **Review and ask for more.** When the report is ready, ask for one more thing (for example *"Can you also look at
   the melt pressure?"*). It adds the check and rebuilds the report. Then click *Finish and seal this report*.
8. **Follow up.** Under the answer, click *Use the demo follow-up* (*"Which lots ran on R-118, and how much
   first-pass output did we lose?"*) and ask. It plays a short second investigation that builds on the first answer.
9. **Replay.** On the sealed answer, click *Replay*: the same notebook re-runs on the stored data and every number
   matches.

## Limits

- The script answers the demo question and the demo follow-up. Any other question plays the same Line 2
  investigation, so stick to the suggested ones (the banner at the top says demo mode is on).
- Notes get a scripted reply chosen by keywords, not a real answer to what was written.
- `alchemist serve --scripted` uses the same script with no pacing (set `ALC_DEMO_PACE` to pace it), on your own
  `ALC_HOME`.
