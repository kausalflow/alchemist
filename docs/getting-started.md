# Getting started

The quickest way to see Alchemist work is the offline demo. It generates a synthetic plant, registers and indexes its
datasets, and plays a scripted investigation through the real agent loop and tools, so it needs no model access.

## Install

Alchemist needs Python 3.11 or later and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/kausalflow/alchemist
cd alchemist
uv sync --extra dev
```

All state (catalog database, access file, knowledge, workspace, synthetic lake) goes under `ALC_HOME`, which defaults
to `./alchemist-home`.

```bash
export ALC_HOME=./alchemist-home
```

!!! note "Windows"
    Run folders nest deeply, and Windows limits paths to 259 characters unless long paths are enabled. Use a short
    home such as `$env:ALC_HOME = "C:\alc"` in PowerShell (Alchemist warns when the home is too deep). The
    deployment runs on Linux; Windows is supported for trying the demo locally.

## Run the demo

```bash
uv run alchemist demo setup
uv run alchemist demo run
```

`demo setup` writes synthetic data, metadata files, documents and access groups, then registers and indexes four
datasets. It prints each catalog card and the facts the indexer could not find:

```text
historian_line2: card v1 approved; missing facts: []
quality_lots: card v1 approved; missing facts: []
resin_lots: card v1 approved; missing facts: ['grain', 'time_zone', 'join_keys', 'unit for cert_mfi_g10min']
historian_line5: card v1 approved; missing facts: ['grain', 'join_keys', 'unit for barrel_temp_c']
```

Running `demo setup` again on the same `ALC_HOME` is safe: cards whose metadata did not change are kept and print
`unchanged since the last setup`. To start over from scratch, delete the `ALC_HOME` folder first.

`demo run` goes through the whole loop: ask, plan, approve, analyse, review, report, seal, replay and verify.

```text
1. Planned run 01M4YWJM8XP0T9TMW0HB82YTDG: awaiting_approval
2. Analysed and sealed: completed

# Line 2 first-pass yield, 3 to 9 August 2026

First-pass yield on Line 2 fell by 3.63 percentage points against the 13 Jul to 2 Aug baseline. The drop follows
resin lot R-118: its lots yielded 4.72 percentage points less than other lots in the same week, and their melt ran
1.99 degC cooler, which SOP-114 links to voids. Line speed and recipe did not change.

**Top causes**

1. Resin lot R-118 (new supplier, low melt flow index) (high confidence; F2)
2. Lower melt temperature on R-118 lots, the likely mechanism (medium confidence; F3)

**Ruled out:** line speed (F4); recipe change (F5); normal variation (F1)
...
3. Replay 01M4YWJTW955KZPE5GEFSNA9JM: completed, 5 findings compared, 0 mismatches
4. Verify original: True
```

Run ids are ULIDs, so yours will differ. Open the `report/report.html` path printed at the end to read the full report,
or `notebook.ipynb` next to it to see every cell the agent ran.

## What the demo data contains

The synthetic plant is built to exercise the parts of the system that matter in real investigations:

- **A planted cause.** From 3 August most Line 2 lots run on resin lot R-118 (low melt flow), which lowers melt
  temperature by about 2 °C and first-pass yield by about 5 points. Line speed and recipe do not change.
- **A sensor gap.** Temperature sensor TT-204 has a six-hour gap on 5 August, stored as nulls.
- **A second plant.** Plant P02 has its own historian dataset, which the demo user `eng_p1` may not read.
- **Messy metadata.** Each dataset's metadata file uses a different layout (JSON with `fields`, YAML with `tags`, a
  one-line YAML), as real owners' files do.
- **Sensitive columns.** `operator_name` is marked as personal data and never reaches the model; `crew` is
  pseudonymised.

## Run the tests

```bash
uv run pytest -q
```

## Next steps

- Read [How a run works](concepts/runs.md) to understand what the demo just did.
- Point Alchemist at a real model with [Model providers](guides/models.md) and
  [Asking real questions](guides/real-questions.md).
- Register your own datasets with the [catalog admin guide](guides/catalog-admin.md).
