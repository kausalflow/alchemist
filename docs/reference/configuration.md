# Configuration

Alchemist reads its settings from environment variables, with local-friendly defaults under `ALC_HOME`.

## Storage

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_HOME` | `./alchemist-home` | base folder for the defaults below and the demo data |
| `ALC_WORKSPACE` | `$ALC_HOME/workspace` | root of the workspace store; `s3://alchemist-workspace` in production |
| `ALC_DB` | `$ALC_HOME/alchemist.db` | catalog and run index (SQLite in the MVP) |
| `ALC_ACCESS` | `$ALC_HOME/access.yaml` | plant groups, workspaces and users (stands in for identity-provider groups) |
| `ALC_KNOWLEDGE` | `$ALC_HOME/knowledge` | documents (`*.md`) and structured knowledge (`structured/*.yaml`) |

## Models

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_LEAD_MODEL` | `anthropic.claude-opus-5-5` | lead model spec; a bare id means Bedrock |
| `ALC_SUB_MODEL` | `anthropic.claude-sonnet-5-5` | reviewer and catalog-card drafter |
| `ALC_ALLOWED_PROVIDERS` | `bedrock` | comma-separated providers plant data may go to; `*` for all |
| `ALC_EFFORT` | `high` | thinking effort of the lead; `off` disables thinking |
| `AWS_REGION` | `eu-central-1` | Bedrock region |

Provider credentials are read the usual way: the AWS credential chain for Bedrock, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY`, `OPENAI_API_KEY`. See [Model providers](../guides/models.md).

## Budgets

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_MAX_STEPS` | `60` | tool calls per planning or analysis step |
| `ALC_MAX_OUTPUT_TOKENS` | `400000` | output tokens per planning or analysis step |

## Security and provenance

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_PSEUDONYM_KEY` | `dev-only-key` | key for pseudonymised columns; set it from a secret store in production |
| `ALC_IMAGE_DIGEST` | unset | container image digest, recorded in each manifest |

!!! warning

    Change `ALC_PSEUDONYM_KEY` before using real data. The same key must be used for a replay to reproduce
    pseudonymised values.
