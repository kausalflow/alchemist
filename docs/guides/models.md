# Model providers

Alchemist reaches models through [Pydantic AI](https://ai.pydantic.dev), so the provider is a setting. Models are set
per role with Pydantic AI specs, `provider:model-id`. A bare id means Amazon Bedrock.

| Role | Setting | Default | Does |
| --- | --- | --- | --- |
| Planning | `ALC_PLANNING_MODEL` | `anthropic.claude-opus-5-5` (Bedrock) | searches the catalog and knowledge, writes the plan |
| Analysis | `ALC_ANALYSIS_MODEL` | `anthropic.claude-sonnet-5-5` (Bedrock) | writes the queries and Python, decides what to check next |
| Reviewer | `ALC_REVIEWER_MODEL` | `anthropic.claude-opus-5-5` (Bedrock) | independently checks the conclusion before the report |
| Small jobs | `ALC_SMALL_MODEL` | `anthropic.claude-haiku-5-5` (Bedrock) | drafts catalog cards from metadata files |

The defaults put the strongest model where calls are few and mistakes are costly (the plan, and the review that guards
against a confident wrong conclusion), a strong and faster model on the many analysis calls, and a small model on
short jobs. Measure on your own questions before relying on any choice.

Each role's model is, in order:

1. an admin's choice on the admin **Models and limits** page (saved in the app database and audited);
2. the role's own variable above;
3. the older shared variable: `ALC_LEAD_MODEL` for planning and analysis, `ALC_SUB_MODEL` for the reviewer and small
   jobs (deployments that set only these keep working as before);
4. the default.

An admin's choice applies to steps that start after it is saved; a run already analysing keeps its model. A saved choice
whose provider is later removed from `ALC_ALLOWED_PROVIDERS` is ignored (the page says so), so the allow-list always wins.
On Bedrock the deployment's IAM role must also be allowed to call the chosen model or inference profile.

## Supported providers

| Provider | Example spec | Credentials |
| --- | --- | --- |
| Amazon Bedrock (default) | `bedrock:anthropic.claude-opus-5-5`, or an inference profile ARN | AWS credential chain, `AWS_REGION` |
| Anthropic API | `anthropic:claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `google:gemini-3-pro` (Vertex AI: `google-cloud:gemini-3-pro`) | `GOOGLE_API_KEY`; Vertex uses Google Cloud credentials |
| OpenAI | `openai:gpt-5` | `OPENAI_API_KEY` |
| A model server in your network | `local:qwen3:32b`, `local:gpt-oss:120b` | `ALC_LOCAL_LLM_URL`, optionally `ALC_LOCAL_LLM_API_KEY` |

Bedrock ids and ARNs contain `:` themselves, so anything that does not start with a known provider prefix is read as a
Bedrock id.

The older Pydantic AI prefixes `google-gla:` and `google-vertex:` still work and are read as `google:` and
`google-cloud:`. In `ALC_ALLOWED_PROVIDERS`, `google` (Gemini API) and `google-cloud` (Vertex AI) are separate entries.

## Local models

For a plant whose data may not leave its network, run the model there too. `local:<model>` sends requests to any server
that speaks the OpenAI chat completions API: [vLLM](https://docs.vllm.ai), [Ollama](https://ollama.com),
llama.cpp's `llama-server`, LM Studio or Hugging Face TGI. Everything after `local:` is the server's own model name,
colons included (Ollama tags such as `qwen3:32b`).

```bash
export ALC_LOCAL_LLM_URL=http://gpu-node.plant.internal:8000/v1   # the server's OpenAI-compatible address
export ALC_LOCAL_LLM_API_KEY=...                                  # only if the server wants one
export ALC_PLANNING_MODEL=local:qwen3:32b
export ALC_ANALYSIS_MODEL=local:qwen3:32b
export ALC_REVIEWER_MODEL=local:qwen3:32b
export ALC_SMALL_MODEL=local:qwen3:32b
export ALC_ALLOWED_PROVIDERS=local                                # nothing else receives plant data
```

The [docker compose stack](deploy-on-prem.md) starts Ollama with `--profile local-llm`, and the Helm chart has an
optional Ollama StatefulSet (`ollama.enabled`) for clusters without a model server yet.

What to expect:

- **The model must call tools.** Planning and analysis are tool loops (search the catalog, run a query, record a
  finding). Pick a model the server runs with tool calling: with vLLM start it with `--enable-auto-tool-choice` and the
  model's `--tool-call-parser`; Ollama enables tools per model.
- **Quality follows the model.** The method, the reviewer and the sealed evidence are the same, so a weaker model is
  slower to a conclusion and more often says "inconclusive", but cannot type a number into a report: every number
  still comes from a computed finding. Use the reviewer role for the strongest model you have, and compare on a few
  past investigations before a pilot relies on it.
- **Thinking effort** (`ALC_EFFORT`) is usually not sent to `local:` servers: Pydantic AI drops it for models it does
  not know to reason. Set the model's reasoning on the server instead where it has a setting.

Pydantic AI's own `ollama:` and `vllm:` prefixes also work (with `OLLAMA_BASE_URL` / `VLLM_BASE_URL`); `local:` is the
one to prefer, because one allow-list entry and one address cover whichever server the plant runs.

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

Add a provider deliberately, after your data policy allows it. The allow-list applies to every role alike, and to admins'
choices on the Models and limits page.

## Choosing models

```bash
export ALC_PLANNING_MODEL=bedrock:anthropic.claude-opus-5-5
export ALC_ANALYSIS_MODEL=bedrock:anthropic.claude-sonnet-5-5
export ALC_REVIEWER_MODEL=bedrock:anthropic.claude-opus-5-5
export ALC_SMALL_MODEL=bedrock:anthropic.claude-haiku-5-5

# override the model of one step (planning with ask, analysis with approve; the provider must be allowed)
alchemist ask "..." --model google:gemini-3-pro --user eng_p1 --workspace quality --project line2-yield

# review with a different provider than the lead, for an independent second opinion
alchemist approve <run-id> --user eng_p1 --reviewer-model openai:gpt-5
```

`--model scripted` replays the demo script instead of calling a model; it is only meaningful with the demo data.

The manifest records the exact `provider:model` used for planning, analysis and the reviewer, so older reports and
their replays stay interpretable after a model change. A replay calls no model.

## Reasoning effort

`ALC_EFFORT` (default `high`) sets the thinking effort of planning and analysis. The reviewer runs at `medium` and small
jobs at `low`. `off` disables thinking; `max` is sent as `xhigh`.

## Bedrock settings worth agreeing with your security team

These are recommendations from the design, not settings Alchemist makes for you:

- Pin models to **application inference profile ARNs** in one geography (for example an `eu.` profile) that matches
  your data residency rules; avoid `global.` profiles unless approved.
- Use the zero-retention mode where the model supports it, and keep models that require human review retention off the
  list unless security approves.
- Reach Bedrock through a VPC interface endpoint, log invocations to your own KMS-encrypted bucket, and put Bedrock
  Guardrails sensitive-information filters on model traffic.
- Use one inference profile per team or workspace, so spend is tagged in Cost Explorer.

## Why the tool loop is plain code

Only the single step "send messages, get a response" goes through Pydantic AI. The loop itself (budgets, transcript,
pauses, the order of tool calls) is plain code in `penguineer.agent`, so every model call and tool call passes
through code that records it, whichever provider answers. Alchemist checks the provider against its allow-list before
it builds the model, so penguineer only ever calls a model Alchemist approved.
