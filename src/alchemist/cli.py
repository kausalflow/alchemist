"""Command line: `alchemist <command>`. Run `alchemist --help` for the list."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import index_dataset
from .config import load_settings
from .paths import as_path, read_yaml


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
    from .agent import BedrockModel

    return BedrockModel(load_settings())


def _reviewer(name: str):
    if name == "scripted":
        from .demo import script
        from .run import ScriptedReviewer

        return ScriptedReviewer(script.REVIEW)
    return None


# ---------------------------------------------------------------- commands

def cmd_demo_setup(a) -> None:
    from .demo.generate import generate

    s = load_settings()
    home = Path(s.db_path).parent
    datasets = generate(home)
    svc = _svc()
    for ds_id, spec in datasets.items():
        svc.catalog.register(id=ds_id, actor="data_admin", **spec)
        res = index_dataset(svc.catalog, s, ds_id, use_model=a.use_model)
        if not a.no_approve:
            svc.catalog.approve(ds_id, res["version"], reviewer="data_admin")
        print(f"{ds_id}: card v{res['version']} {'approved' if not a.no_approve else 'drafted'}; missing facts: {res.get('missing')}")
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
            _print(index_dataset(svc.catalog, s, i, use_model=a.use_model, force=a.force))
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
    out = ask(svc, user=a.user, workspace=a.workspace, project=a.project, question=a.question, scope=scope,
              model=_model(a.model, "plan"))
    print(f"Run {out['run_id']}: {out['status']} ({out['ended']})")
    if out["plan"]:
        _print(out["plan"])
    if a.auto_approve and out["status"] == "awaiting_approval":
        res = approve(svc, out["run_id"], user=a.user, model=_model(a.model, "analysis"), reviewer=_reviewer(a.model))
        _report(res)
    elif out["status"] == "awaiting_approval":
        print(f"\nReview the plan, then: alchemist approve {out['run_id']} --user {a.user}")


def cmd_approve(a) -> None:
    from .run import approve

    plan = json.loads(Path(a.plan_file).read_text()) if a.plan_file else None
    res = approve(_svc(), a.run_id, user=a.user, plan=plan, model=_model(a.model, "analysis"), reviewer=_reviewer(a.model))
    _report(res)


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

    res = replay(_svc(), a.run_id, user=a.user, mode=a.mode)
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


def cmd_demo_run(a) -> None:
    """Whole loop offline: ask, approve, replay, verify."""
    from .run import approve, ask, replay
    from .demo import script
    from .workspace import verify

    svc = _svc()
    out = ask(svc, user="eng_p1", workspace="quality", project="line2-yield", question=script.QUESTION,
              scope=script.SCOPE, model=_model("scripted", "plan"))
    print(f"1. Planned run {out['run_id']}: {out['status']}")
    res = approve(svc, out["run_id"], user="eng_p1", model=_model("scripted", "analysis"), reviewer=_reviewer("scripted"))
    print(f"2. Analysed and sealed: {res['status']}\n\n{res['conclusion']}")
    rep = replay(svc, out["run_id"], user="eng_p1")
    st = rep["result_check"]["status"]
    print(f"3. Replay {rep['run_id']}: {rep['status']}, {st['compared']} findings compared, {st['mismatches']} mismatches")
    print(f"4. Verify original: {verify(res['path'], res['seal_sha256'])['ok']}")
    print(f"\nReport: {Path(res['path']) / 'report' / 'report.html'}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="alchemist", description="AI research system for process questions")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="synthetic plant data and an offline end-to-end run")
    dsub = d.add_subparsers(dest="demo_cmd", required=True)
    ds = dsub.add_parser("setup", help="generate demo data, register and index the datasets")
    ds.add_argument("--use-model", action="store_true", help="draft catalog cards with Claude on Bedrock")
    ds.add_argument("--no-approve", action="store_true", help="leave drafts for an admin to review")
    ds.set_defaults(fn=cmd_demo_setup)
    dsub.add_parser("run", help="ask, approve, replay and verify the demo question offline").set_defaults(fn=cmd_demo_run)

    c = sub.add_parser("catalog", help="manage the data catalog")
    c.add_argument("action", choices=["list", "register", "index", "show", "approve", "reject", "suggestions", "health"])
    c.add_argument("--id")
    c.add_argument("--version", type=int)
    c.add_argument("--prefix")
    c.add_argument("--glob", default="*.parquet")
    c.add_argument("--format", default="parquet")
    c.add_argument("--plant")
    c.add_argument("--metadata")
    c.add_argument("--owner")
    c.add_argument("--card-file", help="edited card JSON to approve")
    c.add_argument("--user", default="data_admin")
    c.add_argument("--use-model", action="store_true")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_catalog)

    k = sub.add_parser("ask", help="ask a question; plans and waits for approval")
    k.add_argument("question")
    k.add_argument("--user", required=True)
    k.add_argument("--workspace", required=True)
    k.add_argument("--project", required=True)
    k.add_argument("--plants", help="comma-separated plants in scope")
    k.add_argument("--scope", help='JSON, e.g. {"line": "L2", "period": ["2026-08-03", "2026-08-09"]}')
    k.add_argument("--model", choices=["bedrock", "scripted"], default="bedrock")
    k.add_argument("--auto-approve", action="store_true")
    k.set_defaults(fn=cmd_ask)

    ap = sub.add_parser("approve", help="approve (optionally edit) a plan and run the analysis")
    ap.add_argument("run_id")
    ap.add_argument("--user", required=True)
    ap.add_argument("--plan-file", help="edited plan JSON")
    ap.add_argument("--model", choices=["bedrock", "scripted"], default="bedrock")
    ap.set_defaults(fn=cmd_approve)

    sub.add_parser("runs", help="list runs").set_defaults(fn=cmd_runs)
    st = sub.add_parser("status", help="show a run")
    st.add_argument("run_id")
    st.add_argument("--plan", action="store_true")
    st.set_defaults(fn=cmd_status)

    r = sub.add_parser("replay", help="re-execute a sealed run on its snapshots or on current data")
    r.add_argument("run_id")
    r.add_argument("--user", required=True)
    r.add_argument("--mode", choices=["replay", "rerun"], default="replay")
    r.set_defaults(fn=cmd_replay)

    v = sub.add_parser("verify", help="check a sealed run folder against its SEALED hashes")
    v.add_argument("run_id")
    v.set_defaults(fn=cmd_verify)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
