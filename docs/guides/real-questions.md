# Asking real questions

This guide runs a question on a real model. It uses the demo data so the commands work as written; for your own data,
register datasets first with the [catalog admin guide](catalog-admin.md).

## 1. Set up the model

=== "Bedrock (default)"

    ```bash
    export AWS_REGION=eu-central-1          # and any AWS credentials the credential chain can find
    export ALC_PLANNING_MODEL=bedrock:anthropic.claude-opus-5-5
    export ALC_ANALYSIS_MODEL=bedrock:anthropic.claude-sonnet-5-5
    export ALC_REVIEWER_MODEL=bedrock:anthropic.claude-opus-5-5
    export ALC_SMALL_MODEL=bedrock:anthropic.claude-haiku-5-5
    ```

=== "Another provider"

    ```bash
    export ANTHROPIC_API_KEY=...
    export ALC_ALLOWED_PROVIDERS=bedrock,anthropic   # only if your data policy allows it
    export ALC_PLANNING_MODEL=anthropic:claude-opus-5-5
    export ALC_ANALYSIS_MODEL=anthropic:claude-sonnet-5-5
    export ALC_REVIEWER_MODEL=anthropic:claude-opus-5-5
    export ALC_SMALL_MODEL=anthropic:claude-haiku-5-5
    ```

See [Model providers](models.md) for the full list and the allow-list.

## 2. Draft catalog cards with the model (optional)

The heuristic indexer is enough for the demo. With a model, cards are drafted from the free-form metadata and a sample
of the data:

```bash
uv run alchemist catalog index --use-model --force
uv run alchemist catalog health
```

New drafts need approval before the agent can use them; see [Review a draft](catalog-admin.md#review-a-draft).

## 3. Ask

```bash
uv run alchemist ask "Why was quality low on Line 2 from 3 to 9 August 2026?" \
    --user eng_p1 --workspace quality --project line2-yield --plants P01
```

Optionally pass a scope as JSON, for example
`--scope '{"line": "L2", "period": ["2026-08-03", "2026-08-09"]}'`. The run plans and stops, printing the plan and its
run id.

## 4. Review and approve the plan

```bash
uv run alchemist status <run-id> --plan
```

Check the metric, the baseline and its reason, and the hypotheses. To change the plan, copy the `plan` object into a file, edit it
(strike or add hypotheses, change the baseline), and pass it on approval:

```bash
uv run alchemist approve <run-id> --user eng_p1                          # as proposed
uv run alchemist approve <run-id> --user eng_p1 --plan-file edited.json  # with your edits
```

Approval runs the analysis, the review and the report, then seals the run and prints the conclusion and the run
folder. To skip the approval stop for a question, pass `--auto-approve` to `ask`.

## 5. Read the answer

Each answer has three layers:

1. **Conclusion card** (`report/conclusion.md`, also printed): the answer with the size of the effect, ranked causes
   with confidence and the finding behind each, what was ruled out, and the recommended next check.
2. **Report** (`report/report.html`): question, scope and baseline; data used; hypotheses and methods; one section per
   finding; cited documents with versions; limitations; and how to reproduce.
3. **Notebook** (`notebook.ipynb`): every cell the agent ran, with outputs.

## 6. Reproduce and check

```bash
uv run alchemist replay <run-id> --user eng_p1               # exact replay from the stored snapshots
uv run alchemist replay <run-id> --user eng_p1 --mode rerun  # same code on current data
uv run alchemist verify <run-id>                             # seal check
uv run alchemist runs                                        # every run, with status and seal hash
```

## When a run stops early

If the analysis exceeds its step or token budget, or fails, the run builds a partial report from the findings
recorded so far, lists why it stopped under limitations, and is sealed with status `partial`.

When time is nearly up, the agent is told to conclude with what it has, and at the limit the run pauses with that
preliminary conclusion on its page. Read it, then either **Continue for N more minutes**, which carries on in the same
run from where it stopped, or **Finish with this report** to seal it as it stands. If your questions need
more room, give a run a longer time limit when you approve it, or ask an admin to raise the limits on the Models and
limits page (or `ALC_MAX_STEPS`, `ALC_MAX_OUTPUT_TOKENS`, `ALC_MAX_RUN_MINUTES`).
