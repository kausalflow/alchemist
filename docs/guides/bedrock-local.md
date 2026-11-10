# Try Bedrock on your laptop

This guide connects a local Alchemist to Amazon Bedrock with your own AWS sign-in, checks that each model answers, and
runs a real question on the demo data. It takes about ten minutes once you have Bedrock access.

You need the [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), an AWS identity
that may call Bedrock in some region, and Alchemist installed as in [Getting started](../getting-started.md).

## How Alchemist finds credentials and the region

- **Credentials** come from the standard AWS credential chain (boto3): `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` /
  `AWS_SESSION_TOKEN` variables first, then the profile named by `AWS_PROFILE` (or `default`) in `~/.aws/config` and
  `~/.aws/credentials`, including IAM Identity Center (SSO) profiles, then a container or instance role. A
  [Bedrock API key](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) in `AWS_BEARER_TOKEN_BEDROCK`
  also works.
- **The region** is `AWS_REGION` only, default `eu-central-1`. The region in your profile and `AWS_DEFAULT_REGION` are
  not used, so set `AWS_REGION` to the region where you have Bedrock access.
- **A separate identity for the models.** `ALC_BEDROCK_PROFILE` names an AWS profile used only for model calls, and
  `ALC_BEDROCK_REGION` a region used only for them. Storage (S3) and everything else keep `AWS_PROFILE` and
  `AWS_REGION`. A profile named this way also wins over access-key variables, for model calls.

Leftover access-key variables win over `AWS_PROFILE`. If a check fails with "security token ... invalid", clear them.

### Keeping it out of your other AWS work

`$env:` variables in PowerShell last only for that window and what you start from it, so they do not change your
other windows or the `default` profile. Put the settings in a script and dot-source it when you work on Alchemist:

```powershell
# C:\alc\bedrock.ps1   load with:  . C:\alc\bedrock.ps1
$env:ALC_HOME            = "C:\alc"
$env:ALC_BEDROCK_PROFILE = "alchemist-models"   # model calls (Bedrock)
$env:ALC_BEDROCK_REGION  = "eu-central-1"
$env:AWS_PROFILE         = "alchemist-data"     # storage (S3); leave unset for local files
$env:AWS_REGION          = "eu-central-1"
```

Avoid setting `AWS_PROFILE` as a permanent Windows variable: every AWS tool would then use it.

## 1. Sign in

=== "PowerShell"

    ```powershell
    aws sso login --profile my-profile      # SSO profiles only; skip for access-key profiles
    $env:AWS_PROFILE = "my-profile"         # or $env:ALC_BEDROCK_PROFILE for model calls only
    $env:AWS_REGION  = "eu-central-1"
    aws sts get-caller-identity --profile my-profile   # shows the account and role you are using
    ```

=== "bash"

    ```bash
    aws sso login --profile my-profile
    export AWS_PROFILE=my-profile
    export AWS_REGION=eu-central-1
    aws sts get-caller-identity
    ```

## 2. Find the model ids you can call

In most regions Claude models on Bedrock are called through an **inference profile**: an id with a geography prefix
such as `eu.anthropic.claude-sonnet-5-5`, or an application inference profile ARN your AWS admins created. List what
your region offers:

```bash
aws bedrock list-inference-profiles --region $AWS_REGION \
    --query "inferenceProfileSummaries[?contains(inferenceProfileId,'claude')].inferenceProfileId"
```

(In PowerShell use `$env:AWS_REGION` and put the command on one line.) If a model is missing, request access to it on
the **Model access** page of the Bedrock console in that region. Prefer a profile in your own geography (`eu.`, `us.`,
...) over `global.` unless your data policy allows it; see [Model providers](models.md#bedrock-settings-worth-agreeing-with-your-security-team).

## 3. Set the models

A bare id or ARN means Bedrock, so the ids from step 2 go in as they are. Bedrock is allowed by default, so
`ALC_ALLOWED_PROVIDERS` needs no change.

=== "PowerShell"

    ```powershell
    $env:ALC_HOME           = "C:\alc"
    $env:ALC_PLANNING_MODEL = "eu.anthropic.claude-opus-5-5"
    $env:ALC_ANALYSIS_MODEL = "eu.anthropic.claude-sonnet-5-5"
    $env:ALC_REVIEWER_MODEL = "eu.anthropic.claude-opus-5-5"
    $env:ALC_SMALL_MODEL    = "eu.anthropic.claude-haiku-5-5"
    ```

=== "bash"

    ```bash
    export ALC_PLANNING_MODEL=eu.anthropic.claude-opus-5-5
    export ALC_ANALYSIS_MODEL=eu.anthropic.claude-sonnet-5-5
    export ALC_REVIEWER_MODEL=eu.anthropic.claude-opus-5-5
    export ALC_SMALL_MODEL=eu.anthropic.claude-haiku-5-5
    ```

`$env:` variables last for the PowerShell window. Set them again in a new window, or put them in a small script you
dot-source (`. .\bedrock.ps1`).

## 4. Check that each model answers

```bash
uv run alchemist models            # each role's model, the region and the profile
uv run alchemist models check      # sends each role one tiny request
```

```text
Bedrock region: eu-central-1 (AWS_REGION); Bedrock AWS profile: my-profile (AWS_PROFILE)
planning  eu.anthropic.claude-opus-5-5: answered 'OK'
analysis  eu.anthropic.claude-sonnet-5-5: answered 'OK'
reviewer  eu.anthropic.claude-opus-5-5: answered 'OK'
small     eu.anthropic.claude-haiku-5-5: answered 'OK'
```

A model saved by an admin on the **Models and limits** page replaces the variable for that role, so the check shows
what runs would really use. When a model fails, the check prints the error and a hint:

| Error says | Fix |
| --- | --- |
| Unable to locate credentials | `aws sso login --profile ...` or `aws configure`, and set `ALC_BEDROCK_PROFILE` or `AWS_PROFILE` |
| The security token included in the request is invalid | stale access-key variables override the profile; clear them, or check `AWS_PROFILE` |
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
