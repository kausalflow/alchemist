# Pilot on your laptop

The fewest steps to try Alchemist the way an engineer would: start it on a Windows laptop with Amazon Bedrock, upload
an Excel file, ask a question about it, steer the analysis while it works, and read the answer. Plan for about 20
minutes the first time. This is for trying it out; the hosted version on AWS works the same way in the browser.

You need Git, [uv](https://docs.astral.sh/uv/), [Node.js](https://nodejs.org) 20 or later, and an AWS profile that may
call Bedrock. Commands are for PowerShell.

## 1. Install (once)

```powershell
git clone https://github.com/kausalflow/alchemist
cd alchemist
uv sync
cd web; npm ci; npm run build; cd ..
```

## 2. Write the settings file (once)

Save this as `alchemist.yaml` in the `alchemist` folder, with your own profile, region and model ids. Keep the
single quotes around the Windows path.

```yaml
ALC_HOME: 'C:\alc'
ALC_BEDROCK_PROFILE: your-aws-profile
ALC_BEDROCK_REGION: eu-west-1
ALC_PLANNING_MODEL: eu.anthropic.claude-sonnet-4-5-20250929-v1:0
ALC_ANALYSIS_MODEL: eu.anthropic.claude-sonnet-4-5-20250929-v1:0
ALC_REVIEWER_MODEL: eu.anthropic.claude-sonnet-4-5-20250929-v1:0
ALC_SMALL_MODEL: eu.anthropic.claude-sonnet-4-5-20250929-v1:0
```

Then create the sign-in users and a workspace. This also adds a small synthetic demo plant, which you can ignore.

```powershell
uv run alchemist demo setup
```

## 3. Check the models answer

Sign in to AWS the way you normally do for that profile, then:

```powershell
uv run alchemist models check
```

Every line should end with `answered 'OK'`. If one does not, the line says what to fix; the table in
[Try Bedrock on your laptop](bedrock-local.md#4-check-that-each-model-answers) explains each error.

## 4. Start the app

```powershell
uv run alchemist serve
```

Open <http://127.0.0.1:8000> and sign in as **eng_p1**. Leave the PowerShell window open while you use it.

## 5. Ask a question about your own file

1. Click **Ask a question** and type the question as you would ask a colleague, for example *"Which of these batches
   had low yield, and what do they have in common?"*
2. Under **Project**, give it a name such as `pilot`. Files belong to the project, so later questions can reuse them.
3. Under **Data files**, pick your Excel or CSV file. Open *Columns and first rows* to check it was read correctly;
   untick *Model sees it* for a column the AI should not see (names, for example). Alchemist describes the file in a
   few sentences and offers three or four starter questions; click one to use it as your question.
4. Leave the scope empty unless you want to limit it to a line or dates, and click **Draft a plan**.

The project then appears in the side menu. Its page lists every question asked in it and its files, and has a
**Background for Alchemist** box: write there once what you would tell a colleague about the process, and Alchemist
reads it before every question in the project.

## 6. Check the plan

After a minute the AI proposes the candidate causes it will test, and the data for each. Untick causes you do not
want, add one you suspect with **Add a cause**, or **Send back with a note** to have it redraft. Leave
*Let me review the result before it is sealed* ticked, then **Approve and run**.

## 7. Steer it while it works

A line at the top says what the AI is doing right now and how long ago its last step was. Below it, the page shows
each step in plain words, with each chart and data preview as it is made.

- To comment on one chart or calculation, click **Comment on this step** under it, finish the sentence, and click
  **Send note**. The AI reads it before its next step.
- To give a general instruction ("ignore the March data, the sensor was off"), type it in the note box on the right.
- **Pause after this step** and **Stop and write a partial report** are on the right too.

## 8. Read the answer and ask for more

When it finishes, the run waits for you with its conclusion, figures and notebook. Ask for more (use **Comment on
this step** here as well), and it carries on with the same notebook and reports again. When you are happy, click
**Finish and seal this report**. The sealed answer has the data, method and notebook behind it, and anyone can
replay it.

To dig further into a sealed answer, type under **Ask a follow-up**, for example *"Is R-118 worse on every crew?"*.
Alchemist starts from the answer, with the same files, drafts a short plan for you to approve, and keeps the sealed
answer as it is. Follow-ups are listed under their question on the project page.

The page opens in the **Simple** view, which hides code and SQL. Switch to **Detailed** at the top right to see the
queries and Python.

## Good to know

- A full run makes many model calls. Watch the first few in the Bedrock console before running many.
- `alchemist serve --scripted` plays the built-in demo without calling Bedrock. It only knows the demo question, and
  *Ask for more* ends its run, because the script has nothing more to do.
- If a question stops, its page says what went wrong in plain words and what to do, for example that the AWS
  sign-in has expired. *Details for IT* holds the technical message.
- To start again from nothing, stop the app and delete the `ALC_HOME` folder.
