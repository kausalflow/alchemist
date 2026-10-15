# Configuration

Alchemist reads its settings from environment variables, with local-friendly defaults under `ALC_HOME`.

## Storage

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_HOME` | `./alchemist-home` | base folder for the defaults below and the demo data |
| `ALC_WORKSPACE` | `$ALC_HOME/workspace` | root of the workspace store; `s3://alchemist-workspace` in production |
| `ALC_DB` | `$ALC_HOME/alchemist.db` | catalog and run index (SQLite in the MVP) |
| `ALC_ACCESS` | `$ALC_HOME/access.yaml` | plants, workspaces, identity-provider group mapping, optional local users ([Access model](../concepts/access.md)) |
| `ALC_KNOWLEDGE` | `$ALC_HOME/knowledge` | documents (`*.md`) and structured knowledge (`structured/*.yaml`) |

## S3 and per-run credentials

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_RUN_ROLE_ARN` | unset | role assumed once per run with a session policy limited to the run's data and folder; unset means the sandbox uses the process's credentials |
| `ALC_RUN_CREDENTIAL_SECONDS` | `3600` | lifetime of a run's credentials (one hour at most with role chaining) |
| `AWS_ENDPOINT_URL_S3` | unset | custom S3 endpoint (MinIO, moto, LocalStack), used by boto3, cloudpathlib and DuckDB |

See [S3 and the workspace](../guides/s3.md).

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

## Plugins

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALC_PLUGINS` | `*` | installed plugins to load (entry-point names, comma-separated); `*` for all, `none` for built-ins only |
| `ALC_PLUGIN_MODULES` | unset | importable modules to load as plugins without an entry point, e.g. `site_plugins.acme` |
| `ALC_ACCESS_PROVIDER` | `yaml` | registered access provider for users, plant groups and workspaces; `yaml` reads `ALC_ACCESS` with its local users; `oidc` (alias `alb-oidc`) accepts only identity-provider claims |
| `ALC_GROUP_CLAIM` | `groups` | token claim holding the groups; `identity.group_claim` in `access.yaml` takes precedence |

See [Plugins](../guides/plugins.md).

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
