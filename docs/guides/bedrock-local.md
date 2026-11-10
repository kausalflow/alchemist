# Try Bedrock on your laptop

This guide connects a local Alchemist to Amazon Bedrock with your own AWS sign-in, checks that each model answers, and
runs a real question on the demo data. It takes about ten minutes once you have Bedrock access.

You need the [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), an AWS identity
that may call Bedrock in some region, and Alchemist installed as in [Getting started](../getting-started.md).

## How Alchemist finds credentials and the region

Alchemist has its own names for the AWS profile and region, so it never changes your other AWS work:

| Setting | Used for | Falls back to |
| --- | --- | --- |
| `ALC_AWS_PROFILE`, `ALC_AWS_REGION` | storage (S3) and every AWS client Alchemist uses | `AWS_PROFILE`, `AWS_REGION` (region default `eu-central-1`) |
| `ALC_BEDROCK_PROFILE`, `ALC_BEDROCK_REGION` | model calls only | `ALC_AWS_PROFILE`, `ALC_AWS_REGION` |

A profile is any profile in `~/.aws/config`, including IAM Identity Center (SSO) profiles. With no profile set,
the standard AWS credential chain applies (access-key variables, the `default` profile, a container or instance role);
a [Bedrock API key](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) in `AWS_BEARER_TOKEN_BEDROCK`
also works for model calls. The region in your profile and `AWS_DEFAULT_REGION` are not used.

Access-key variables (`AWS_ACCESS_KEY_ID`, ...) left in the environment win over `ALC_AWS_PROFILE` for storage. If a
check fails with "security token ... invalid", clear them.

## 1. Sign in and write a settings file

Make a profile for Alchemist once, or two if model calls and storage use different AWS identities:

```bash
aws configure sso --profile alchemist-models      # or: aws configure --profile alchemist-models  (access keys)
aws configure sso --profile alchemist-data
```

Put the settings in a YAML file, keyed by the variable names. Alchemist reads the file you name with `--config` (before
the command) or `ALC_CONFIG`, else `alchemist.yaml` in the folder you run it from (`uv run` runs from the repository
folder, where `alchemist.yaml` is git-ignored). Pointing `ALC_CONFIG` at the file once, as a user environment variable,
is the simplest: only Alchemist reads it.

```powershell
[Environment]::SetEnvironmentVariable("ALC_CONFIG", "C:\alc\alchemist.yaml", "User")   # new windows pick it up
```

```yaml
# C:\alc\alchemist.yaml
ALC_HOME: C:\alc
ALC_BEDROCK_PROFILE: alchemist-models
ALC_BEDROCK_REGION: eu-central-1
ALC_AWS_PROFILE: alchemist-data       # storage; not needed while the demo uses local files
ALC_AWS_REGION: eu-central-1
```

A variable set in the environment wins over the file, for a one-off change in one shell. Then sign in:

```bash
aws sso login --profile alchemist-models          # SSO profiles only; repeat when the session expires
aws sts get-caller-identity --profile alchemist-models
```

## 2. Find the model ids you can call

In most regions Claude models on Bedrock are called through an **inference profile**: an id with a geography prefix
such as `eu.anthropic.claude-sonnet-5-5`, or an application inference profile ARN your AWS admins created. List what
your region offers:

```bash
aws bedrock list-inference-profiles --region eu-central-1 --profile alchemist-models --query "inferenceProfileSummaries[?contains(inferenceProfileId,'claude')].inferenceProfileId"
```

 If a model is missing, request access to it on
the **Model access** page of the Bedrock console in that region. Prefer a profile in your own geography (`eu.`, `us.`,
...) over `global.` unless your data policy allows it; see [Model providers](models.md#bedrock-settings-worth-agreeing-with-your-security-team).

## 3. Set the models

A bare id or ARN means Bedrock, so the ids from step 2 go in as they are. Add them to the settings file:

```yaml
ALC_PLANNING_MODEL: eu.anthropic.claude-opus-5-5
ALC_ANALYSIS_MODEL: eu.anthropic.claude-sonnet-5-5
ALC_REVIEWER_MODEL: eu.anthropic.claude-opus-5-5
ALC_SMALL_MODEL: eu.anthropic.claude-haiku-5-5
```

Bedrock is allowed by default, so `ALC_ALLOWED_PROVIDERS` needs no change.

## 4. Check that each model answers

```bash
uv run alchemist models            # each role's model, the settings file, the region and the profile
uv run alchemist models check      # sends each role one tiny request
```

Without `ALC_CONFIG`, pass the file: `uv run alchemist --config C:\alc\alchemist.yaml models check`.

```text
Settings file: C:\alc\alchemist.yaml (set ALC_HOME, ALC_BEDROCK_PROFILE, ...)
Bedrock region: eu-central-1 (ALC_BEDROCK_REGION); Bedrock AWS profile: alchemist-models (ALC_BEDROCK_PROFILE)
planning  eu.anthropic.claude-opus-5-5: answered 'OK'
analysis  eu.anthropic.claude-sonnet-5-5: answered 'OK'
reviewer  eu.anthropic.claude-opus-5-5: answered 'OK'
small     eu.anthropic.claude-haiku-5-5: answered 'OK'
```

A model saved by an admin on the **Models and limits** page replaces the variable for that role, so the check shows
what runs would really use. When a model fails, the check prints the error and a hint:

| Error says | Fix |
| --- | --- |
| Unable to locate credentials | `aws sso login --profile ...` or `aws configure`, and set `ALC_BEDROCK_PROFILE` or `ALC_AWS_PROFILE` |
| The security token included in the request is invalid | stale access-key variables override the profile; clear them, or check the profile names |
| ExpiredToken, or the SSO session expired | `aws sso login` again |
| on-demand throughput isn't supported ... inference profile | use the inference profile id from step 2 instead of the bare `anthropic.` id |
| AccessDeniedException | request model access in the Bedrock console; the IAM policy must allow `bedrock:InvokeModel` on the model or profile |
| model identifier is invalid, not found | the id is not offered in the Bedrock region; check step 2 for that region |

## 5. Ask a real question

`alchemist demo run` always plays the scripted model, so it never calls Bedrock. Ask the demo question yourself
instead:

```bash
uv run alchemist demo setup
uv run alchemist ask "Why was quality low on Line 2 from 3 to 9 August 2026?" --user eng_p1 --workspace quality --project line2-yield --plants P01 --auto-approve
```

Without `--auto-approve` the run stops after planning so you can read and approve the plan; see
[Asking real questions](real-questions.md). To use the web app instead, run `uv run alchemist serve` (without
`--scripted`) and open <http://127.0.0.1:8000>.

A full run makes many model calls, mostly on the analysis model. Watch the first few runs in the Bedrock console's
usage metrics before running many.
