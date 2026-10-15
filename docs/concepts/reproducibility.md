# Reproducibility

The model's reasoning is not deterministic, so reproducibility in Alchemist means **re-executing the recorded
computation on pinned inputs**, not asking the model again. A replay needs no model access at all.

## Why each run snapshots its data

The S3 buckets of the data lake keep no object versions, so a file a run read can change or disappear later. Each run
therefore stores what it used in its own folder:

- every query result, as Parquet named by the SHA-256 of its content (`inputs/<sha256>.parquet`);
- the list of source objects each query read, with key, ETag, size and last-modified time, so drift is easy to spot;
- the view definitions, approved card versions and structured knowledge pinned at approval.

The folder is sealed when the run ends.

## What the manifest records

| Item | Recorded as | Why |
| --- | --- | --- |
| Question and plan | text, scope, the approved plan with its version, who approved it and when | what was asked and what was attempted |
| Access | user, workspace, project, plants | which data rights the run used |
| Data | each query's SQL, datasets, source objects, row count, snapshot path, result hash, card versions | same query, same rows, verifiable by hash |
| Code | every executed cell in order with its SHA-256, in one notebook | the analysis itself, readable and runnable |
| Environment | Python, platform, package versions, `uv.lock` hash, image digest (`ALC_IMAGE_DIGEST`); the seed is in the notebook's parameters cell | same library versions |
| Knowledge | document id, version, section and staleness for every citation | which domain claims the conclusion leans on |
| Model | `provider:model` for lead and reviewer, prompt and tool definitions hash; full transcripts sit in `logs/` | audit of how the agent reached its plan |
| Findings | each claim with its value hash, linked to cells, queries and documents | every number in the conclusion traces to evidence |

See [Run folder](../reference/run-folder.md) for the files.

## Three ways to reproduce

=== "A person, with the CLI"

    ```bash
    alchemist replay <run-id> --user <you>
    ```

    Verifies the parent's seal, re-executes its notebook on the stored snapshots as a new run, and prints a table
    comparing every finding with the original. Any mismatch marks the replay `mismatch` and exits non-zero.

=== "A person, in Jupyter"

    Open the run's `notebook.ipynb`, set `run_mode = 'replay'` in the first cell, and run all cells. The last cell,
    `alc.check_results()`, compares finding hashes with the original run.

=== "Another agent"

    Read `manifest.json` and `findings.json` (plain JSON with `schema` fields), then replay, rerun or audit the run
    through the same tools. An agent never has to re-read the transcript to know what was computed.

## Has the data changed since?

```bash
alchemist replay <run-id> --user <you> --mode rerun
```

A rerun executes the same SQL on current data and reports, per query, rows then and now, whether the result hash
changed and how many source objects are new or changed.

## How numbers stay honest

- Findings are computed in the kernel by `record_finding`; the model supplies an expression, not a value.
- Values are canonicalised (floats to 12 significant digits, `-0.0` to `0.0`) before hashing, so replays compare
  exactly.
- The conclusion uses `{{Fn}}` placeholders. A measured number typed into the text fails the report check.
- Seeds are fixed (`seed = 1729`) and DuckDB runs single-threaded, so repeated fits on the same image give the same
  numbers.

## Sealing

`SEALED` lists a SHA-256 for every file; its own hash is stored in the catalog database and the audit log.
`alchemist verify <run-id>` reports any file that is missing, changed or added after sealing. Locally the files are also
made read-only. In the target design the run's write credentials expire at sealing, and S3 Object Lock adds an
AWS-enforced layer if versioning is allowed on Alchemist's own workspace bucket.
