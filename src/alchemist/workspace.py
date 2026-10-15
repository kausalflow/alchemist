"""Workspace layout on S3 (or a local folder), and sealing.

    <root>/workspaces/<workspace>/
        workspace.yaml
        projects/<project>/
            project.yaml
            knowledge/
            questions/<question-id>/
                question.yaml
                runs/<run-id>/
                    manifest.json  findings.json  notebook.ipynb  SEALED
                    inputs/  work/  figures/  report/  logs/
"""

from __future__ import annotations

import hashlib
import json
import os
import stat

from .catalog import now
from .paths import as_path, create_exclusive, is_cloud, new_id, read_yaml, sha256_file, write_yaml


def workspace_dir(root, workspace: str):
    return as_path(root) / "workspaces" / workspace


def project_dir(root, workspace: str, project: str):
    return workspace_dir(root, workspace) / "projects" / project


def ensure_project(root, workspace: str, project: str, owner: str, plants: list[str]) -> None:
    ws = workspace_dir(root, workspace)
    if not (ws / "workspace.yaml").exists():
        write_yaml(ws / "workspace.yaml", {"schema": "alchemist.workspace/v1", "name": workspace, "created_at": now()})
    pd = project_dir(root, workspace, project)
    if not (pd / "project.yaml").exists():
        write_yaml(pd / "project.yaml", {"schema": "alchemist.project/v1", "name": project, "owner": owner,
                                         "plants": sorted(plants), "created_at": now()})


def create_question(root, workspace: str, project: str, *, text: str, asker: str, scope: dict) -> tuple[str, object]:
    qid = new_id()
    qdir = project_dir(root, workspace, project) / "questions" / qid
    write_yaml(qdir / "question.yaml", {"schema": "alchemist.question/v1", "id": qid, "text": text, "asker": asker,
                                        "scope": scope, "status": "planning", "plan": None, "created_at": now()})
    return qid, qdir


def update_question(qdir, **fields) -> dict:
    q = read_yaml(as_path(qdir) / "question.yaml")
    q.update(fields)
    q["updated_at"] = now()
    write_yaml(as_path(qdir) / "question.yaml", q)
    return q


def create_run(qdir) -> tuple[str, object]:
    rid = new_id()
    rdir = as_path(qdir) / "runs" / rid
    for sub in ("inputs", "work", "figures", "report", "logs"):
        keep = rdir / sub / ".keep"
        if not is_cloud(keep):
            keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text("")
    return rid, rdir


def _files(run):
    run = as_path(run)
    for f in sorted(run.rglob("*"), key=str):
        if f.is_file() and f.name != "SEALED":
            yield str(f.relative_to(run)), f


def seal(run, run_id: str) -> str:
    """Write SEALED (SHA-256 per file) and return the hash of SEALED itself.

    SEALED is created only if it does not exist (an S3 conditional put), so a run
    is sealed once. The sandbox kernel has stopped by now, and its per-run
    credentials may never write SEALED; anything changed or added later shows up
    in verify(). Locally the files are also made read-only.
    """
    run = as_path(run)
    files = {rel: sha256_file(f) for rel, f in _files(run)}
    body = {"schema": "alchemist.sealed/v1", "run_id": run_id, "sealed_at": now(), "files": files}
    text = json.dumps(body, indent=2, sort_keys=True) + "\n"
    try:
        create_exclusive(run / "SEALED", text)
    except FileExistsError:
        raise ValueError(f"run {run_id} is already sealed") from None
    if not is_cloud(run):
        for _, f in _files(run):
            os.chmod(f, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.chmod(run / "SEALED", stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return hashlib.sha256(text.encode()).hexdigest()


def verify(run, expected_seal_sha256: str | None = None) -> dict:
    run = as_path(run)
    sealed_file = run / "SEALED"
    if not sealed_file.exists():
        return {"ok": False, "problems": ["run is not sealed"]}
    text = sealed_file.read_text()
    problems = []
    if expected_seal_sha256 and hashlib.sha256(text.encode()).hexdigest() != expected_seal_sha256:
        problems.append("SEALED does not match the hash recorded at seal time")
    recorded = json.loads(text)["files"]
    actual = {rel: sha256_file(f) for rel, f in _files(run)}
    for rel, digest in recorded.items():
        if rel not in actual:
            problems.append(f"missing: {rel}")
        elif actual[rel] != digest:
            problems.append(f"changed: {rel}")
    for rel in actual.keys() - recorded.keys():
        problems.append(f"added after seal: {rel}")
    return {"ok": not problems, "files": len(recorded), "problems": problems}
