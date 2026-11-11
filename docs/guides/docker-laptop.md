# Try the Docker setup on your laptop

This runs the same Docker setup a server would run (the app, a worker, the analysis sandbox and a PostgreSQL
database), on your laptop, with Claude on Amazon Bedrock through your own AWS profile. Use it to check the setup
before IT hosts it. It takes about 15 minutes, most of it downloading.

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) running, the `alchemist` repository (only
the `deploy/compose` folder is used), and an AWS profile that may call Bedrock. Commands are for PowerShell. If you
already use the [pilot guide](pilot.md), stop `alchemist serve` first: both use port 8000.

## 1. Write the settings

In `deploy/compose`, copy the example settings:

```powershell
cd alchemist\deploy\compose
copy .env.example .env
notepad .env
```

In `.env`, change the three secrets at the top to any long random strings, comment out the Option A model lines
(put `#` in front of the `ALC_LOCAL_...`, `ALC_..._MODEL` and `ALC_ALLOWED_PROVIDERS=local` lines), and add at the
end, with your own profile, region, models and Windows user folder:

```ini
ALC_PLANNING_MODEL=eu.anthropic.claude-sonnet-4-5-20250929-v1:0
ALC_ANALYSIS_MODEL=eu.anthropic.claude-sonnet-4-5-20250929-v1:0
ALC_REVIEWER_MODEL=eu.anthropic.claude-sonnet-4-5-20250929-v1:0
ALC_SMALL_MODEL=eu.anthropic.claude-sonnet-4-5-20250929-v1:0
ALC_ALLOWED_PROVIDERS=bedrock
ALC_BEDROCK_REGION=eu-west-1
ALC_BEDROCK_PROFILE=your-aws-profile
ALC_AWS_DIR=C:\Users\you\.aws
```

No quotes are needed in `.env`, and backslashes are fine. `ALC_AWS_DIR` is the folder that holds your AWS `config`
and `credentials` files.

## 2. Start it

Sign in to AWS the way you normally do for that profile, then:

```powershell
docker compose -f docker-compose.yml -f docker-compose.aws-profile.yml up -d --wait
```

The first start downloads the image (about 1.5 GB). The second file mounts your AWS folder, read-only, into the app
and the worker so they can call Bedrock. The analysis sandbox never gets it, so code the AI writes cannot use your
AWS sign-in.

## 3. Check the models answer

```powershell
docker compose exec api alchemist models check
```

Every line should end with `answered 'OK'`. If one does not, the line says what to fix; the table in
[Try Bedrock on your laptop](bedrock-local.md#4-check-that-each-model-answers) explains each error.

## 4. Use it

Open <http://localhost:8000> and sign in as **engineer**. Then follow steps 5 to 8 of the
[pilot guide](pilot.md#5-ask-a-question-about-your-own-file): name a project, upload an Excel file, ask, approve the
plan, steer it, and read the answer.

There is no plant data in this setup, only the files you upload. The users and plants come from
`config/access.yaml`; on the hosted version they come from your company sign-in instead.

## When something goes wrong

| What you see | What to do |
| --- | --- |
| `set POSTGRES_PASSWORD in .env` (or another `set ... in .env`) | `.env` is missing that line, or you ran the command outside `deploy/compose` |
| port 8000 is already allocated | stop `alchemist serve`, or set `ALC_PORT=8001` in `.env` and open that port |
| `models check` says Unable to locate credentials, or the profile could not be found | check `ALC_AWS_DIR` points at the folder with `config` and `credentials`, and the profile name matches |
| a model worked yesterday and fails today with an expired token | sign in to AWS again on the laptop; the containers read the refreshed files |
| a run stays in Drafting plan | `docker compose logs worker` shows what the worker is doing or why it failed |

## Stop, update, start over

```powershell
docker compose -f docker-compose.yml -f docker-compose.aws-profile.yml down       # stop; your runs are kept
docker compose -f docker-compose.yml -f docker-compose.aws-profile.yml pull       # get the newest version
docker compose -f docker-compose.yml -f docker-compose.aws-profile.yml down -v    # stop and delete everything
```

## On AWS

The hosted version uses the same `docker-compose.yml` without the AWS profile file: the server gets an IAM role that
may call Bedrock, IT's load balancer signs people in, and the users come from the company groups. See
[Deploy on-prem or in your cloud](deploy-on-prem.md) and [Single sign-on](single-sign-on.md).
