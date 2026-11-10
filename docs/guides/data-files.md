# Upload data files

People can upload their own data, such as a LIMS export, a lab spreadsheet or a list of setpoints, and ask a
question about it alongside the catalog's data. Excel (`.xlsx`, `.xlsm`, `.xls`) and CSV (`.csv`, `.tsv`) files
are accepted, up to `ALC_UPLOAD_MAX_MB` (200 MB by default).

## How it works

1. **Upload to a project.** On the Ask page, name the project and pick the files under *Data files*. With the
   command line, use `alchemist upload FILE --workspace WS --project P`, or `alchemist ask ... --file FILE`.
2. **Each sheet becomes a table.** Every sheet with data (or the one table in a CSV file) is converted to Parquet
   and gets a view name such as `ds.up_lims_export_lab_results`. Empty sheets are skipped and listed.
3. **Check the conversion.** For each table the page shows the header row it found, the columns (cleaned names,
   the header as written in the file, type and unit), the first rows, and notes on every choice the converter made.
   Untick *Model sees it* to keep a column in the sandbox only. This works like a never-to-model column in the
   catalog, and only the person who uploaded the file (or an admin) can change it.
4. **Attach it to a question.** Tick the files the question should use. The planning and analysis agents see their
   tables next to the catalog datasets. Queries go through the same SQL guard, run credentials and snapshots as
   lake data, so the run replays exactly.

Files belong to the project, so later questions in the same project can use them too.

## What the converter does

The converter is deliberately plain, and it writes down every choice it makes. The notes are shown to you, and
the agent reads them as the table's quirks.

| Situation | What happens |
| --- | --- |
| Title rows above the table | the header is the first of the top 30 rows that is mostly filled with text; rows above it are skipped |
| A units row under the header | short text cells above numbers are read as units |
| `Temperature (°C)`, `MFI [g/10min]` | the column is `temperature` with unit `°C`; the header as written is kept |
| Numbers with a few exceptions (`<0.1`, `n/a`) | when at least 80% of the values are numbers, the column is numeric, the exceptions are null, and the text as written is kept in `<column>__raw` |
| `215,5` | `,` is read as the decimal separator when no value uses `.`; `1,234.5` is read as a thousands separator |
| Dates written as text | read as dates when at least 90% parse; day before month when a first part is above 12 or the dates use dots, otherwise month before day (flagged as ambiguous) |
| Text and numbers mixed in a column | kept as text |
| Formulas | the value Excel last saved |

The time zone of a file's timestamps is unknown unless you give it when you upload (*Time zone of its timestamps*,
or `--time-zone`).

## Access and provenance

- An upload belongs to its workspace and project. Anyone in the workspace can use it.
- Tag it with a plant (*Plant whose data it is*, or `--plant`) when it holds a plant's data. Then only people
  entitled to that plant can see it, and only runs with that plant in scope can use it.
- The file is stored once, unchanged, next to its tables (see the layout below). Its SHA-256, the sheet, the header
  row and the converter version go into the run's `inputs/views.json` and `manifest.json` (`uploads`).
- Each upload and each change to which columns the model sees is in the audit log (`upload.create`,
  `upload.exposure`).

```text
<project>/uploads/<upload-id>/
  upload.json                    alchemist.upload/v1: file, uploader, plant, conversion of each sheet
  original/<file name>           the file as uploaded
  tables/<table>/data.parquet    one table per sheet
```

Files are converted in the API process, which caps the size of an `.xlsx` once unpacked (2 GiB) and the cells of a
sheet (50 million). For files larger than that, register the data as a [catalog dataset](catalog-admin.md) instead.
