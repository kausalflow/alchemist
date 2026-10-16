"""Command line: `alchemist <command>`. Run `alchemist --help` for the list."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import index_dataset
from .config import _home, load_settings
from .db import is_postgres
from .paths import as_path, copy_tree, read_yaml


def _svc():
    from .run import Services

    return Services(load_settings())


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _model(name: str, phase: str):
    if name == "scripted":
        from .agent import ScriptedModel
        from .demo import script

        return ScriptedModel(script.PLAN_TURNS if phase == "plan" else script.ANALYSIS_TURNS)
    from .llm import PydanticAIModel

    return PydanticAIModel(load_settings(), None if name in ("bedrock", "default") else name)


def _user(a, svc=None):
    """The acting user: verified identity-provider claims (--claims) or a local user from access.yaml (--user)."""
    if getattr(a, "claims", None):
        svc = svc or _svc()
        return svc.access.user_from_claims(json.loads(Path(a.claims).read_text()))
    if not a.user:
        sys.exit("give --user (local user in access.yaml) or --claims (verified token claims as JSON)")
    return a.user


def _reviewer(name: str, spec: str | None = None):
    if name == "scripted":
        from .demo import script
        from .run import ScriptedReviewer

        return ScriptedReviewer(script.REVIEW)
    if spec:
        from .llm import PydanticAIModel

        return PydanticAIModel(load_settings(), spec, effort="medium")
    return None


# ---------------------------------------------------------------- commands

def cmd_demo_setup(a) -> None:
    from .demo.generate import generate

    s = load_settings()
    home = Path(_home()) if is_postgres(s.db_path) else Path(s.db_path).parent
    datasets = generate(home)
    if a.lake:
        # Put the demo lake on S3 (or any cloudpathlib location) and register it there.
        lake, target = str(home / "lake"), a.lake.rstrip("/")
        copy_tree(lake, target)
        for spec in datasets.values():
            for key in ("s3_prefix", "metadata_uri"):
                spec[key] = spec[key].replace(lake, target, 1)
        print(f"Uploaded the demo lake to {target}")
    svc = _svc()
    for ds_id, spec in datasets.items():
        svc.catalog.register(id=ds_id, actor="data_admin", **spec)
        res = index_dataset(svc.catalog, s, ds_id, use_model=a.use_model)
        # Rerunning setup on the same home finds the cards unchanged; approve only what is still a draft.
        status = svc.catalog.latest_card(ds_id, status=None)["status"]
        if status == "draft" and not a.no_approve:
            svc.catalog.approve(ds_id, res["version"], reviewer="data_admin")
            status = "approved"
        note = f"missing facts: {res['missing']}" if "missing" in res else "unchanged since the last setup"
        print(f"{ds_id}: card v{res['version']} {status}; {note}")
    print(f"\nDemo home: {home.resolve()}")


def cmd_catalog(a) -> None:
    svc, s = _svc(), load_settings()
    if a.action == "list":
        _print([{**d, "cards": [c for c in svc.catalog.cards() if c["dataset_id"] == d["id"]]} for d in svc.catalog.datasets()])
    elif a.action == "register":
        svc.catalog.register(id=a.id, s3_prefix=a.prefix, file_glob=a.glob, file_format=a.format, plant=a.plant,
                             metadata_uri=a.metadata, owner=a.owner or "", actor=a.user)
        print(f"registered {a.id}")
    elif a.action == "index":
        ids = [a.id] if a.id else [d["id"] for d in svc.catalog.datasets()]
        for i in ids:
            _print(index_dataset(svc.catalog, s, i, use_model=a.use_model, force=a.force, drafter=a.drafter))
    elif a.action == "show":
        _print(svc.catalog.latest_card(a.id, status=None))
    elif a.action == "approve":
        card = json.loads(Path(a.card_file).read_text()) if a.card_file else None
        svc.catalog.approve(a.id, a.version, reviewer=a.user, card=card)
        print(f"approved {a.id}@{a.version}")
    elif a.action == "reject":
        svc.catalog.reject(a.id, a.version, reviewer=a.user)
        print(f"rejected {a.id}@{a.version}")
    elif a.action == "suggestions":
        _print(svc.catalog.suggestions())
    elif a.action == "health":
        rows = []
        for d in svc.catalog.datasets():
            approved, latest = svc.catalog.latest_card(d["id"]), svc.catalog.latest_card(d["id"], status=None)
            rows.append({"dataset": d["id"], "approved_version": approved and approved["version"],
                         "waiting_for_review": bool(latest and latest["status"] == "draft"),
                         "missing_facts": (latest or {}).get("card", {}).get("missing", []),
                         "no_metadata_file": not (d["metadata_uri"] and as_path(d["metadata_uri"]).exists())})
        _print(rows)


def cmd_ask(a) -> None:
    from .run import approve, ask

    svc = _svc()
    scope = json.loads(a.scope) if a.scope else {}
    if a.plants:
        scope["plants"] = a.plants.split(",")
    user = _user(a, svc)
    out = ask(svc, user=user, workspace=a.workspace, project=a.project, question=a.question, scope=scope,
              model=_model(a.model, "plan"))
    print(f"Run {out['run_id']}: {out['status']} ({out['ended']})")
    if out["plan"]:
        _print(out["plan"])
    if a.auto_approve and out["status"] == "awaiting_approval":
        res = approve(svc, out["run_id"], user=user, model=_model(a.model, "analysis"), reviewer=_reviewer(a.model, a.reviewer_model))
        _report(res)
    elif out["status"] == "awaiting_approval":
        print(f"\nReview the plan, then: alchemist approve {out['run_id']} " + (f"--claims {a.claims}" if a.claims else f"--user {a.user}"))


def cmd_approve(a) -> None:
    from .run import approve

    plan = json.loads(Path(a.plan_file).read_text()) if a.plan_file else None
    svc = _svc()
    res = approve(svc, a.run_id, user=_user(a, svc), plan=plan, model=_model(a.model, "analysis"), reviewer=_reviewer(a.model, a.reviewer_model))
    _report(res)


def cmd_note(a) -> None:
    from .run import note

    svc = _svc()
    note(svc, a.run_id, user=_user(a, svc), text=a.text)
    print(f"Note left on run {a.run_id}; the agent reads it before its next step.")


def cmd_send_back(a) -> None:
    from .run import plan, send_back

    svc = _svc()
    send_back(svc, a.run_id, user=_user(a, svc), text=a.text)
    out = plan(svc, a.run_id, model=_model(a.model, "plan"))
    print(f"Run {out['run_id']}: {out['status']} ({out['ended']})")
    if out["plan"]:
        _print(out["plan"])


def _report(res: dict) -> None:
    print(f"\nRun {res['run_id']}: {res['status']}")
    if res.get("conclusion"):
        print("\n" + res["conclusion"])
    print(f"Folder: {res['path']}\nSealed: {res['seal_sha256']}")


def cmd_runs(a) -> None:
    _print(_svc().catalog.runs())


def cmd_status(a) -> None:
    svc = _svc()
    row = svc.catalog.run(a.run_id)
    run = as_path(row["path"])
    q = read_yaml(run.parent.parent / "question.yaml")
    out = {"run": row, "question": q["text"], "question_status": q["status"]}
    if a.plan:
        out["plan"] = q.get("plan")
    if (run / "report" / "conclusion.md").exists():
        out["conclusion"] = (run / "report" / "conclusion.md").read_text()
    _print(out)


def cmd_replay(a) -> None:
    from .run import replay

    svc = _svc()
    res = replay(svc, a.run_id, user=_user(a, svc), mode=a.mode)
    print(f"{a.mode} of {a.run_id} -> run {res['run_id']}: {res['status']}")
    _print(res["result_check"])
    if res["cell_errors"]:
        _print(res["cell_errors"])
    print(f"Folder: {res['path']}")
    if res["status"] not in ("completed",):
        sys.exit(1)


def cmd_verify(a) -> None:
    from .workspace import verify

    row = _svc().catalog.run(a.run_id)
    res = verify(row["path"], row["seal_sha256"])
    _print(res)
    if not res["ok"]:
        sys.exit(1)


def cmd_plugins(a) -> None:
    from .plugins import KINDS, get_registry

    reg = get_registry()
    if a.action == "list":
        _print(reg.summary())
    else:
        _print({k: {n: getattr(reg, k).provided_by(n) for n in getattr(reg, k).names()} for k in KINDS})


def cmd_demo_run(a) -> None:
    """Whole loop offline: ask, approve, replay, verify."""
    from .run import approve, ask, replay
    from .demo import script
    from .workspace import verify

    svc = _svc()
    out = ask(svc, user="eng_p1", workspace="quality", project="line2-yield", question=script.QUESTION,
              scope=script.SCOPE, model=_model("scripted", "plan"))
    print(f"1. Planned run {out['run_id']}: {out['status']}")
    try:
        res = approve(svc, out["run_id"], user="eng_p1", model=_model("scripted", "analysis"),
                      reviewer=_reviewer("scripted"))
    except BaseException as e:  # a crash or Ctrl+C must not leave the run counting against the user's run limit
        from .api.jobs import mark_failed

        mark_failed(svc, out["run_id"], e if isinstance(e, Exception) else "interrupted")
        raise
    if res["status"] != "completed":  # nothing to replay; say why instead of failing on missing findings
        sys.exit(f"2. The analysis did not finish ({res['status']}): {res.get('analysis_ended')}\n"
                 f"   Run folder: {res['path']}{_first_tool_error(res['path'])}")
    print(f"2. Analysed and sealed: {res['status']}\n\n{res['conclusion']}")
    rep = replay(svc, out["run_id"], user="eng_p1")
    st = rep["result_check"]["status"]
    print(f"3. Replay {rep['run_id']}: {rep['status']}, {st['compared']} findings compared, {st['mismatches']} mismatches")
    print(f"4. Verify original: {verify(res['path'], res['seal_sha256'])['ok']}")
    print(f"\nReport: {as_path(res['path']) / 'report' / 'report.html'}")


def _first_tool_error(run_dir: str) -> str:
    """The first failed tool call in a run's transcript: in the scripted demo, the step every later one tripped on."""
    log = as_path(run_dir) / "logs" / "transcript.jsonl"
    if not log.exists():
        return ""
    for line in log.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("is_error"):
            return f"\n   First failed step ({rec['tool']}):\n{str(rec.get('output', ''))[:3000]}"
    return ""


def cmd_access(a) -> None:
    """Show how the access model sees a user: from verified token claims, or a local user."""
    from dataclasses import asdict

    svc = _svc()
    u = svc.access.resolve(_user(a, svc))
    out = {k: sorted(v) if isinstance(v, frozenset) else v for k, v in asdict(u).items()}
    out["workspace_plants"] = {}
    for ws in sorted(u.workspaces if not u.is_admin else svc.access.data.get("workspaces", {})):
        try:
            out["workspace_plants"][ws] = sorted(svc.access.run_plants(u, ws))
        except Exception as e:  # noqa: BLE001 - shown to the operator as is
            out["workspace_plants"][ws] = f"denied: {e}"
    _print(out)


def cmd_serve(a) -> None:
    import os

    import uvicorn

    if a.scripted:
        os.environ["ALC_API_MODEL"] = "scripted"
    from .api.app import create_app

    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="info")


def sandbox_url_for_task(task_arn: str, *, cluster: str | None = None, port: int | None = None, ecs=None,
                         timeout: float = 900) -> str:
    """Address of a sandbox task started by the workflow, once it is running (ECS DescribeTasks)."""
    import os
    import time

    if ecs is None:
        import boto3

        ecs = boto3.client("ecs")
    cluster = cluster or os.environ.get("ALC_CLUSTER_ARN") or task_arn.split(":task/", 1)[-1].split("/", 1)[0]
    port = port or int(os.environ.get("ALC_SANDBOX_PORT", "8888"))
    deadline = time.monotonic() + timeout
    while True:
        tasks = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])["tasks"]
        task = tasks[0] if tasks else {}
        status = task.get("lastStatus")
        if status == "RUNNING":
            ip = next((d["value"] for a in task.get("attachments", []) for d in a.get("details", [])
                       if d.get("name") == "privateIPv4Address"), None)
            if ip:
                return f"http://{ip}:{port}"
        if status in ("DEACTIVATING", "STOPPING", "DEPROVISIONING", "STOPPED"):
            raise SystemExit(f"sandbox task stopped before the analysis: {task.get('stoppedReason', status)}")
        if time.monotonic() > deadline:
            raise SystemExit(f"sandbox task {task_arn} is still {status} after {timeout:.0f}s")
        time.sleep(5)


def _sandbox_url(a) -> str | None:
    import os

    if getattr(a, "sandbox_url", None):
        return a.sandbox_url
    arn = getattr(a, "sandbox_task_arn", None) or os.environ.get("ALC_SANDBOX_TASK_ARN")
    return sandbox_url_for_task(arn) if arn else None


def cmd_worker(a) -> None:
    """One step of a run in its own process (an ECS task started by the run's Step Functions execution)."""
    from .api.jobs import mark_failed

    svc = _svc()
    try:
        _worker_step(a, svc)
    except Exception as e:  # say so in the database now, not when the API next reconciles
        from .run import checkpoint_of

        # An analysis with a checkpoint stays running: the workflow retries the step, which resumes from it (and
        # if no retry is left, the API's reconciler pauses the run so a person can resume it).
        if not (a.step == "analyse" and checkpoint_of(svc.catalog.run(a.run_id)["path"])):
            mark_failed(svc, a.run_id, e)
        raise


def _worker_step(a, svc) -> None:
    from .api.jobs import models, stop_requested
    from .run import analyse, execute_replay, plan, report_step, seal_step

    row = svc.catalog.run(a.run_id)
    if a.step == "plan":
        model, _ = models(svc.settings, "plan")
        out = plan(svc, a.run_id, model=model, should_stop=lambda: stop_requested(svc, a.run_id))
    elif a.step == "analyse":
        model, reviewer = models(svc.settings, "analysis")
        q = read_yaml(as_path(row["path"]).parent.parent / "question.yaml")
        user = (q.get("plan") or {}).get("approved_by") or row["user_id"]
        out = analyse(svc, a.run_id, user=user, model=model, reviewer=reviewer,
                      should_stop=lambda: stop_requested(svc, a.run_id), sandbox_url=_sandbox_url(a))
    elif a.step == "report":
        out = report_step(svc, a.run_id)
    elif a.step == "seal":
        out = seal_step(svc, a.run_id)
    else:
        out = execute_replay(svc, a.run_id, sandbox_url=_sandbox_url(a))
    _print({k: v for k, v in out.items() if k in ("run_id", "status", "ended", "analysis_ended", "seal_sha256")})
    failed = out["status"] in ("failed", "cancelled") and a.step != "seal"
    if failed or (a.step == "plan" and out["status"] != "awaiting_approval"):
        sys.exit(1)  # ends the workflow instead of waiting for an approval that cannot come


def cmd_db(a) -> None:
    from .db import LATEST, connect

    s = load_settings()
    db = connect(s.db_path)  # opening applies any pending migrations
    try:
        version = db.execute("select max(version) as v from schema_migrations").fetchone()["v"]
    finally:
        db.close()
    print(f"{db.dialect} database at schema version {version} (latest {LATEST})")


def cmd_sandbox(a) -> None:
    from .sandbox_server import serve

    serve(a.host, a.port)


def cmd_broker(a) -> None:
    """AWS credential_process for a run's kernel: this run's credentials, as JSON, from the broker Lambda (separate
    sandbox) or from the file the worker keeps fresh (--file, in-process)."""
    import os

    from .credentials import BrokerDenied, credential_process, read_credentials_file

    if a.run_id:
        os.environ["ALC_RUN_ID"] = a.run_id
    try:
        print(json.dumps(read_credentials_file(a.file) if a.file else credential_process()))
    except BrokerDenied as e:
        sys.exit(str(e))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="alchemist", description="AI research system for process questions")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="synthetic plant data and an offline end-to-end run")
    dsub = d.add_subparsers(dest="demo_cmd", required=True)
    ds = dsub.add_parser("setup", help="generate demo data, register and index the datasets")
    ds.add_argument("--use-model", action="store_true", help="draft catalog cards with the sub model (ALC_SUB_MODEL)")
    ds.add_argument("--no-approve", action="store_true", help="leave drafts for an admin to review")
    ds.add_argument("--lake", help="upload the demo lake here (e.g. s3://my-bucket/alchemist-demo/lake) and register it there")
    ds.set_defaults(fn=cmd_demo_setup)
    dsub.add_parser("run", help="ask, approve, replay and verify the demo question offline").set_defaults(fn=cmd_demo_run)

    c = sub.add_parser("catalog", help="manage the data catalog")
    c.add_argument("action", choices=["list", "register", "index", "show", "approve", "reject", "suggestions", "health"])
    c.add_argument("--id")
    c.add_argument("--version", type=int)
    c.add_argument("--prefix")
    c.add_argument("--glob", default="*.parquet")
    c.add_argument("--format", default="parquet", help="a registered data format (see `alchemist plugins show`)")
    c.add_argument("--plant")
    c.add_argument("--metadata")
    c.add_argument("--owner")
    c.add_argument("--card-file", help="edited card JSON to approve")
    c.add_argument("--user", default="data_admin")
    c.add_argument("--use-model", action="store_true")
    c.add_argument("--drafter", help="registered card drafter for `index` (default: heuristic, or model with --use-model)")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_catalog)

    k = sub.add_parser("ask", help="ask a question; plans and waits for approval")
    k.add_argument("question")
    k.add_argument("--user")
    k.add_argument("--claims", help="verified identity-provider token claims (JSON file); replaces --user")
    k.add_argument("--workspace", required=True)
    k.add_argument("--project", required=True)
    k.add_argument("--plants", help="comma-separated plants in scope")
    k.add_argument("--scope", help='JSON, e.g. {"line": "L2", "period": ["2026-08-03", "2026-08-09"]}')
    k.add_argument("--model", default="default",
                   help="provider:model-id for the lead model (e.g. anthropic:claude-opus-5-5), or 'scripted' for the offline demo; default ALC_LEAD_MODEL")
    k.add_argument("--reviewer-model", help="provider:model-id for the independent reviewer; default ALC_SUB_MODEL")
    k.add_argument("--auto-approve", action="store_true")
    k.set_defaults(fn=cmd_ask)

    ap = sub.add_parser("approve", help="approve (optionally edit) a plan and run the analysis")
    ap.add_argument("run_id")
    ap.add_argument("--user")
    ap.add_argument("--claims", help="verified identity-provider token claims (JSON file); replaces --user")
    ap.add_argument("--reviewer-model", help="provider:model-id for the independent reviewer; default ALC_SUB_MODEL")
    ap.add_argument("--plan-file", help="edited plan JSON")
    ap.add_argument("--model", default="default",
                   help="provider:model-id for the lead model (e.g. anthropic:claude-opus-5-5), or 'scripted' for the offline demo; default ALC_LEAD_MODEL")
    ap.set_defaults(fn=cmd_approve)

    nt = sub.add_parser("note", help="leave a note the agent reads before its next step")
    nt.add_argument("run_id")
    nt.add_argument("text")
    nt.add_argument("--user")
    nt.add_argument("--claims", help="verified identity-provider token claims (JSON file); replaces --user")
    nt.set_defaults(fn=cmd_note)

    sb_ = sub.add_parser("send-back", help="send a plan back with a note; the agent revises it for approval")
    sb_.add_argument("run_id")
    sb_.add_argument("text")
    sb_.add_argument("--user")
    sb_.add_argument("--claims", help="verified identity-provider token claims (JSON file); replaces --user")
    sb_.add_argument("--model", default="default",
                     help="provider:model-id for the lead model, or 'scripted' for the offline demo; default ALC_LEAD_MODEL")
    sb_.set_defaults(fn=cmd_send_back)

    sub.add_parser("runs", help="list runs").set_defaults(fn=cmd_runs)
    st = sub.add_parser("status", help="show a run")
    st.add_argument("run_id")
    st.add_argument("--plan", action="store_true")
    st.set_defaults(fn=cmd_status)

    r = sub.add_parser("replay", help="re-execute a sealed run on its snapshots or on current data")
    r.add_argument("run_id")
    r.add_argument("--user")
    r.add_argument("--claims", help="verified identity-provider token claims (JSON file); replaces --user")
    r.add_argument("--mode", choices=["replay", "rerun"], default="replay")
    r.set_defaults(fn=cmd_replay)

    w = sub.add_parser("whoami", help="show the plants, workspaces and roles a user gets")
    w.add_argument("--user")
    w.add_argument("--claims", help="verified identity-provider token claims (JSON file); replaces --user")
    w.set_defaults(fn=cmd_access)

    v = sub.add_parser("verify", help="check a sealed run folder against its SEALED hashes")
    v.add_argument("run_id")
    v.set_defaults(fn=cmd_verify)

    sv = sub.add_parser("serve", aliases=["api"], help="run the API and the web app")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--scripted", action="store_true",
                    help="plan and analyse with the offline demo script instead of a model (demo question only)")
    sv.set_defaults(fn=cmd_serve)

    w = sub.add_parser("worker", help="run one step of a run (planning, analysis, report, seal or replay) in this process")
    w.add_argument("step", choices=["plan", "analyse", "report", "seal", "replay"])
    w.add_argument("--run-id", required=True)
    w.add_argument("--sandbox-task-arn", help="analyse/replay on this separate sandbox task (ECS); default ALC_SANDBOX_TASK_ARN")
    w.add_argument("--sandbox-url", help="analyse/replay on the sandbox kernel API at this address")
    w.set_defaults(fn=cmd_worker)

    dbp = sub.add_parser("db", help="the app database (SQLite file or PostgreSQL)")
    dbsub = dbp.add_subparsers(dest="db_cmd", required=True)
    dbsub.add_parser("migrate", help="apply pending schema migrations and print the schema version").set_defaults(fn=cmd_db)

    sb = sub.add_parser("sandbox", help="the analysis kernel as its own service (separate sandbox mode)")
    sbsub = sb.add_subparsers(dest="sandbox_cmd", required=True)
    sbs = sbsub.add_parser("serve", help="serve one run's kernel over HTTP for `worker analyse --sandbox-url`")
    sbs.add_argument("--host", default="0.0.0.0")
    sbs.add_argument("--port", type=int, default=8888)
    sbs.set_defaults(fn=cmd_sandbox)

    br = sub.add_parser("broker", help="credential_process for a run's kernel")
    brsub = br.add_subparsers(dest="broker_cmd", required=True)
    brc = brsub.add_parser("credentials", help="AWS credential_process: print this run's S3 credentials from the broker")
    brc.add_argument("--run-id", help="default ALC_RUN_ID; the token comes from ALC_BROKER_TOKEN")
    brc.add_argument("--file", help="in-process: read the credentials the worker keeps fresh in this file instead")
    brc.set_defaults(fn=cmd_broker)

    pl = sub.add_parser("plugins", help="list loaded plugins, or show every registered extension")
    pl.add_argument("action", choices=["list", "show"], nargs="?", default="list")
    pl.set_defaults(fn=cmd_plugins)

    from .plugins import get_registry

    for command in get_registry().cli_commands:
        cp = sub.add_parser(command.name, help=f"{command.help} (plugin {get_registry().cli_commands.provided_by(command.name)})")
        if command.add_arguments:
            command.add_arguments(cp)
        cp.set_defaults(fn=command.run)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
