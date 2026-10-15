# Model providers

Alchemist reaches models through [Pydantic AI](https://ai.pydantic.dev), so the provider is a setting. Models are set
per role with Pydantic AI specs, `provider:model-id`. A bare id means Amazon Bedrock.

| Role | Setting | Default | Does |
| --- | --- | --- | --- |
| Lead | `ALC_LEAD_MODEL` | `anthropic.claude-opus-5-5` (Bedrock) | plans and analyses |
| Sub | `ALC_SUB_MODEL` | `anthropic.claude-sonnet-5-5` (Bedrock) | independent reviewer; drafts catalog cards |

## Supported providers

| Provider | Example spec | Credentials |
| --- | --- | --- |
| Amazon Bedrock (default) | `bedrock:anthropic.claude-opus-5-5`, or an inference profile ARN | AWS credential chain, `AWS_REGION` |
| Anthropic API | `anthropic:claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `google-gla:gemini-3-pro` (Vertex AI: `google-vertex:...`) | `GOOGLE_API_KEY` |
| OpenAI | `openai:gpt-5` | `OPENAI_API_KEY` |

Bedrock ids and ARNs contain `:` themselves, so anything that does not start with a known provider prefix is read as a
Bedrock id.

## The provider allow-list

Plant data is private. Prompts carry schemas, summary statistics, first rows and plots, so **any provider other than
Bedrock is refused** until it is added to `ALC_ALLOWED_PROVIDERS`:

```bash
export ALC_ALLOWED_PROVIDERS=bedrock                 # default
export ALC_ALLOWED_PROVIDERS=bedrock,anthropic       # comma-separated
export ALC_ALLOWED_PROVIDERS='*'                     # every provider; only if your data policy allows it
```

A refused spec fails before any request is sent:

```text
ProviderNotAllowed: provider 'openai' is not in ALC_ALLOWED_PROVIDERS (bedrock). Plant data may only go to approved
providers; add it deliberately if your data policy allows it.
```

Add a provider deliberately, after your data policy allows it. The allow-list applies to the lead, the reviewer and the
catalog-card drafter alike.

## Choosing models

```bash
export ALC_LEAD_MODEL=bedrock:anthropic.claude-opus-5-5
export ALC_SUB_MODEL=bedrock:anthropic.claude-sonnet-5-5

# per-run override of the lead (the provider must be allowed)
alchemist ask "..." --model google-gla:gemini-3-pro --user eng_p1 --workspace quality --project line2-yield

# review with a different provider than the lead, for an independent second opinion
alchemist approve <run-id> --user eng_p1 --reviewer-model openai:gpt-5
```

`--model scripted` replays the demo script instead of calling a model; it is only meaningful with the demo data.

The manifest records the exact `provider:model` used for each role, so older reports stay interpretable after a model
change.

## Reasoning effort

`ALC_EFFORT` (default `high`) sets the thinking effort of the lead. The reviewer runs at `medium` and the catalog-card
drafter at `low`. `off` disables thinking; `max` is sent as `xhigh`.

## Bedrock settings worth agreeing with your security team

These are recommendations from the design, not settings Alchemist makes for you:

- Pin models to **application inference profile ARNs** in one geography (for example an `eu.` profile) that matches
  your data residency rules; avoid `global.` profiles unless approved.
- Use the zero-retention mode where the model supports it, and keep models that require human review retention off the
  list unless security approves.
- Reach Bedrock through a VPC interface endpoint, log invocations to your own KMS-encrypted bucket, and put Bedrock
  Guardrails sensitive-information filters on model traffic.
- Use one inference profile per team or workspace, so spend is tagged in Cost Explorer.

## Why Alchemist keeps its own tool loop

Only the single step "send messages, get a response" goes through Pydantic AI. The loop itself (budgets, transcript,
provenance, the order of tool calls) is Alchemist's own code in `agent.py`, so every model call and tool call passes
through code that records it, whichever provider answers.
