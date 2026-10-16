# Catalog admin guide

Catalog admins (assigned by the data team) register datasets, review the cards the indexer drafts, and act on
suggestions. All commands take `--user` for the audit log; it defaults to `data_admin`.

Everything here can also be done in the web app's **Admin** area, which only catalog admins and admins see. It is
organised by plant: each plant (and "All plants (shared)") has its data sources and their cards, its documents and
reference facts, and its lines, plus a box to test what an agent would find for a question at that plant. See
[Web app](web-app.md#admin-area).

## Register a dataset

```bash
alchemist catalog register \
    --id historian_line2 \
    --prefix s3://plant-data/plant=P01/historian/line2 \
    --glob 'date=*/*.parquet' \
    --format parquet \
    --plant P01 \
    --metadata s3://plant-data/plant=P01/historian/line2/_meta.yaml \
    --owner "process data team"
```

| Option | Meaning |
| --- | --- |
| `--id` | dataset id; the agent queries it as `ds.<id>` |
| `--prefix` | S3 prefix (or local folder) of the data files |
| `--glob` | file pattern under the prefix, default `*.parquet` |
| `--format` | `parquet` (default), `csv` or `json` |
| `--plant` | the plant code, or `global` for data every plant may read |
| `--metadata` | path to the owner's YAML or JSON metadata file, any layout |
| `--owner` | who maintains the data and the metadata file |

Registering the same id again replaces its registry row.

## Draft cards

```bash
alchemist catalog index --id historian_line2             # heuristic draft
alchemist catalog index --id historian_line2 --use-model # drafted by ALC_SMALL_MODEL
alchemist catalog index                                  # every registered dataset
alchemist catalog index --force                          # even if the metadata file is unchanged
```

Indexing drafts a new card version only when the metadata file's ETag changed since the last draft, so it is safe to
run on a schedule. The output lists the facts the drafter could not find and the checks that failed against the data
sample.

## Review a draft

```bash
alchemist catalog show --id historian_line2 > card.json   # latest version, any status
```

Check in particular:

- **Units** for every numeric column, and valid ranges where they exist.
- **Time zone and time meaning**: is `ts` the start or end of an interval, local time or UTC?
- **Join keys** and the grain.
- **Exposure**: personal names, prices and recipe secrets must be `never_to_model`; crew or person ids should be
  `pseudonymise`.
- **Checks**: a named column that is missing from the data, or values outside the stated range.

Then approve as is, approve your edited card, or reject:

```bash
alchemist catalog approve --id historian_line2 --version 2
alchemist catalog approve --id historian_line2 --version 2 --card-file edited-card.json
alchemist catalog reject  --id historian_line2 --version 2
```

`--card-file` takes the card object itself (the `card` field of `catalog show`), with `"schema": "alchemist.card/v1"`.
Only approved cards are searchable or queryable. Runs record `dataset@card_version`, so a report always says which
description of the data it relied on.

## Keep the catalog healthy

```bash
alchemist catalog health        # per dataset: approved version, draft waiting, missing facts, no metadata file
alchemist catalog suggestions   # gaps the agent or engineers reported
alchemist catalog list          # registry rows with all card versions
```

A suggestion is a request, not an edit: the dataset owner or an admin updates the metadata file, re-indexes, and
approves the new draft. The agent never edits metadata files or cards.
