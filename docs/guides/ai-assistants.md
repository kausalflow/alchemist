# AI assistants (Claude, Copilot, Cursor)

Alchemist can be used from the AI assistant you already work in: Claude Desktop, Claude Code, GitHub Copilot in VS Code,
Cursor, or anything else that speaks the [Model Context Protocol](https://modelcontextprotocol.io) (MCP), the open
standard these tools use to call outside tools. Once connected you can ask the assistant things like:

- "Which datasets do we have on Line 2 extrusion?"
- "Ask Alchemist why first-pass yield on Line 2 dropped in the week of 3 August, in the quality workspace, project line2."
- "Show me the plan." … "Approve it." … "What did it find? Show me the charts."
- "Tell the run to leave out the March sensor data." / "Ask a follow-up: was it the same for Line 3?"

The assistant works through Alchemist's own API **as you**: it sees only what you may see in the web app, a plan still
waits for your approval before any plant data is read, and every note and approval is recorded under your name.

## Before you connect: what the assistant sees

Whatever a tool returns goes to the assistant's model. That includes conclusions, findings, figures, dataset
descriptions and the steps of an analysis, which can contain your plant's numbers. Alchemist's protections still apply
(columns marked `never_to_model` are never returned, pseudonymised columns stay pseudonymised), but the assistant's
model may run outside the company, with its own retention rules.

**Use only an assistant your company has approved for this data** (for example Claude or Copilot under your company's
enterprise agreement). If in doubt, ask IT before connecting it to real plant data, or start with the demo.

To let the assistant read but not act (no asking, approving, notes, pausing or stopping), add `--read-only` (local) or
set `ALC_MCP=read-only` on the server. `ALC_MCP=off` turns the server's `/api/mcp` off.

## Connect

You need a running Alchemist (`alchemist serve`, see [Web app](web-app.md) or the [pilot guide](pilot.md)) and the
name you sign in as. There are two ways to connect; pick whichever your assistant supports.

| | Local command (stdio) | URL (HTTP) |
| --- | --- | --- |
| How | The assistant starts `alchemist mcp`, which talks to the server | The assistant calls `http://<server>/api/mcp` |
| Needs | Alchemist installed on the same computer | Only the address |
| Good for | Claude Desktop | VS Code Copilot, Claude Code, Cursor |

The examples use the demo user `eng_p1` and a server on `http://127.0.0.1:8000`; use your own user name (and address).
On a laptop (`ALC_AUTH=dev`) the user name is all it takes; see [Signing in to a shared server](#signing-in-to-a-shared-server)
for a server with corporate sign-in.

### Claude Desktop

Open **Settings → Developer → Edit Config**, which opens `claude_desktop_config.json`
(on Windows `%APPDATA%\Claude\claude_desktop_config.json`), and add:

```json
{
  "mcpServers": {
    "alchemist": {
      "command": "uv",
      "args": ["--directory", "C:\\Users\\you\\alchemist", "run", "alchemist", "mcp", "--user", "eng_p1"]
    }
  }
}
```

`--directory` is the folder you cloned Alchemist into (use `\\` in Windows paths). If Claude says it cannot find `uv`,
put its full path as `command` (PowerShell: `(Get-Command uv).Source`). Restart Claude Desktop; Alchemist's tools show
under the tools button in the chat box.

### Claude Code

```powershell
claude mcp add --transport http alchemist http://127.0.0.1:8000/api/mcp --header "X-Alchemist-User: eng_p1"
```

Or the local command: `claude mcp add alchemist -- uv --directory C:\Users\you\alchemist run alchemist mcp --user eng_p1`.
Check with `claude mcp list`, or `/mcp` inside Claude Code.

### GitHub Copilot in VS Code

Copilot uses MCP tools in **Agent** mode. Add a file `.vscode/mcp.json` to your folder (or run **MCP: Add Server…** from
the command palette):

```json
{
  "servers": {
    "alchemist": {
      "type": "http",
      "url": "http://127.0.0.1:8000/api/mcp",
      "headers": { "X-Alchemist-User": "eng_p1" }
    }
  }
}
```

Click **Start** above the server in that file, open Copilot Chat, switch to **Agent**, and check that the Alchemist
tools are ticked under the tools button. Your organisation's Copilot policy must allow MCP servers; if the tools do not
show, ask your GitHub admin.

### Cursor and other assistants

Most assistants take the same two settings. Cursor (`~/.cursor/mcp.json`):

```json
{ "mcpServers": { "alchemist": { "url": "http://127.0.0.1:8000/api/mcp", "headers": { "X-Alchemist-User": "eng_p1" } } } }
```

For an assistant that only runs commands, use `alchemist mcp --url http://127.0.0.1:8000 --user eng_p1` as the command.

## The tools

| Tool | What it does |
| --- | --- |
| `whoami` | Your plants, workspaces and roles |
| `list_projects` | Workspaces and their projects |
| `get_project` | A project's background, uploaded data files and documents |
| `search_datasets`, `get_dataset` | The data catalog (only datasets your plants may read) |
| `find_similar_questions` | Earlier questions with similar words, to reuse an answer |
| `list_runs`, `get_run` | Runs, with status, plan, conclusion, findings and notes |
| `get_run_steps` | What a running analysis is doing |
| `get_figure` | One chart from a run, as an image |
| `ask_question` | Ask a question (optionally with uploaded data files, or as a follow-up of a finished run) |
| `approve_plan`, `send_back_plan` | Approve the plan, or send it back with changes |
| `add_note` | Steer a run with a note |
| `pause_run`, `resume_run`, `stop_run` | Pause, continue or stop a run |

The assistant is told to approve a plan or stop a run only when you say so, and most assistants ask before calling a
tool that changes something. Uploading files, verdicts and the admin area stay in the web app.

## Signing in to a shared server

- **Laptop or demo (`ALC_AUTH=dev`)**: the user name, as above (`--user` or the `X-Alchemist-User` header). Anyone can
  claim any user in this mode, so do not open such a server to others.
- **Server with OIDC sign-in (`ALC_AUTH=oidc`)**: pass your ID token: `alchemist mcp --url https://alchemist.example.com
  --token <token>` (or `ALC_MCP_TOKEN`), or the header `Authorization: Bearer <token>` in the assistant's settings. ID
  tokens expire (often after an hour), so this is for trying it out.
- **AWS deployment behind the load balancer's sign-in (`ALC_AUTH=alb-oidc`)**: the load balancer signs people in through
  the browser, which assistants cannot do. For now, connect assistants to a local or on-prem Alchemist; personal access
  tokens for assistants would be the next step if you need this.

## When something goes wrong

The assistant shows Alchemist's answer in plain words. The usual ones:

- *Could not reach Alchemist … Is `alchemist serve` running?* Start the server, or fix `--url`.
- *Alchemist does not know who you are.* Set `--user` / the `X-Alchemist-User` header (or the token) to a user in
  `access.yaml` (or your sign-in).
- *Alchemist said no (403)*: you may not see that run or plant, or only the asker may approve its plan, as in the web app.

Under the hood: `alchemist mcp` speaks MCP over stdin/stdout and calls the REST API; `POST /api/mcp` on the server is
the same tools over MCP's streamable HTTP transport (plain JSON answers, no sessions). Both are in
`src/alchemist/mcp_server.py`.
