# Domain knowledge

Domain knowledge lives in two forms: **documents** the agent searches and cites, and **structured knowledge** the code
uses directly. Documents explain; structured knowledge constrains.

Both live under `ALC_KNOWLEDGE` (default `$ALC_HOME/knowledge`).

## Documents

Markdown files with YAML front matter. Each heading starts a new passage.

```markdown
---
id: SOP-114
title: Melt temperature window, Line 2
version: 3
plant: P01
review_date: 2027-02-01
---
# Melt temperature window
Barrel zone 3 melt temperature must stay within 228 to 234 degC. ...

# Low-MFI resin
A resin lot with low melt flow index needs more energy to reach the same melt temperature; ...
```

| Field | Meaning |
| --- | --- |
| `id` | stable document id used in citations (defaults to the file name) |
| `title` | shown in results and reports |
| `version` | recorded with every citation |
| `plant` | a plant code, or `global`; runs only see documents of their plants |
| `review_date` | after this date, citations are flagged as stale |

`search_knowledge` ranks passages by keyword relevance (the target design uses hybrid embedding and full-text search
over the same passages) and returns the document id, version, section and staleness. Every passage returned is added to
the run's citations and the manifest.

Useful documents: SOPs, process descriptions, FMEAs, spec sheets, a plant glossary, and past investigation reports. Past
investigations are especially valuable: the next similar question starts from what was learned last time.

## Structured knowledge

YAML files in `structured/`, each with a `plant` and a `version`:

```yaml
# structured/line2_process.yaml
plant: P01
version: 3
line: L2
residence_time_min: 45
melt_temp_window_c: [228, 234]
validated_line_speed_mpm: [38, 46]
```

At approval, the files for the run's plants are copied into `inputs/knowledge/<name>.json`, so code reads them with
`alc.knowledge("line2_process")` and a replay sees exactly the same values.

## Editing knowledge in the web app

Catalog admins can also add and edit documents and fact tables in the web app's admin area, per plant. It writes the
same files: a new document goes to `plants/<plant>/<ID>.md`, a new fact table to `structured/<name>.yaml`, and an
edit keeps the file where it is, bumps `version` and records `updated_at` and `updated_by`. The previous version is
kept under `.history/`, which search ignores. Documents and facts from plugin knowledge packs are read-only there.

Documents saved there may also carry `type` (SOP, FMEA, incident report, ...) and `lines` (the lines they apply to).
Saving a plant's lines writes a generated document, `LINES-<plant>`, so the agent finds line names and what they make.

## Documents are data

Shift logs, documents and data values are treated as data, never as instructions. The agent has no write, email or web
tools, so an instruction injected into a document has nothing harmful to call.
