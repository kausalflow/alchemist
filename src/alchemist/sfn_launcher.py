"""Launcher for AWS: each run is one Step Functions execution (see infra/alchemist_infra/workflow.py).

Selected with ALC_LAUNCHER=alchemist.sfn_launcher:StepFunctionsLauncher. The API calls

- plan(run_id) right after creating a run: starts the execution, which plans and then waits for approval;
- analyse(run_id, user) right after recording the approval: releases the waiting execution;
- replay(run_id) right after creating a replay run: starts an execution in replay mode.

Approval is a task token the workflow stores at <ALC_APPROVALS><run_id>/token.json. This launcher writes
approved.json next to it and sends the token; if the token is not stored yet (approval came before planning
finished), the workflow's approval function sends it as soon as it stores it.

With a separate sandbox (ALC_SANDBOX_MODE=separate) the launcher also writes each run's broker grant at
<workspace bucket>/<ALC_GRANTS_PREFIX><run_id>.json and passes the run's broker token in the execution input,
which the workflow hands only to the sandbox task. A new run's grant is written already expired with no data
prefixes; approval rewrites it with the prefixes of the pinned views and an expiry ALC_GRANT_HOURS ahead, before the
run is released. The grant holds only the token's SHA-256.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Callable


def _split(uri: str, name: str = "ALC_APPROVALS") -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"{name} must be an s3:// prefix, got {uri!r}")
    bucket, _, key = uri[5:].partition("/")
    return bucket, key if not key or key.endswith("/") else key + "/"


class StepFunctionsLauncher:
    def __init__(self, services_factory: Callable[[], object] | None = None, *, state_machine_arn: str | None = None,
                 approvals: str | None = None, sfn=None, s3=None, sandbox_mode: str | None = None,
                 grants_prefix: str | None = None):
        self.services = services_factory
        self.state_machine_arn = state_machine_arn or os.environ["ALC_STATE_MACHINE_ARN"]
        self.bucket, self.prefix = _split(approvals or os.environ["ALC_APPROVALS"])
        self.separate = (sandbox_mode or os.environ.get("ALC_SANDBOX_MODE", "in_process")) == "separate"
        self.grants_prefix = grants_prefix or os.environ.get("ALC_GRANTS_PREFIX", "_grants/")
        if sfn is None or s3 is None:
            import boto3

            sfn = sfn or boto3.client("stepfunctions")
            s3 = s3 or boto3.client("s3")
        self.sfn, self.s3 = sfn, s3

    def _start(self, run_id: str, mode: str, token: str | None = None) -> None:
        body = {"run_id": run_id, "mode": mode, **({"broker_token": token} if token else {})}
        self.sfn.start_execution(stateMachineArn=self.state_machine_arn, name=f"{run_id}-{mode}"[:80],
                                 input=json.dumps(body))

    # ---------- broker grants (separate sandbox) ----------
    def _grant_key(self, run_id: str) -> tuple[str, str]:
        bucket, _ = _split(self.services().settings.workspace_root, "ALC_WORKSPACE")
        return bucket, f"{self.grants_prefix}{run_id}.json"

    def _write_grant(self, run_id: str, token_sha: str, *, views: bool, hours: float) -> None:
        from .credentials import make_grant
        from .paths import as_path, read_json

        svc = self.services()
        row = svc.catalog.run(run_id)
        views_file = as_path(row["path"]) / "inputs" / "views.json"
        globs = [v["glob"] for v in read_json(views_file).values()] if views and views_file.exists() else []
        if row.get("mode") == "replay":
            globs = []  # a replay reads only the snapshots in its own folder
        grant = make_grant(run_id=run_id, user=row["user_id"], plants=json.loads(row["plants"]), run_dir=row["path"],
                           read_globs=globs, token_sha256=token_sha,
                           expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours))
        bucket, key = self._grant_key(run_id)
        self.s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(grant).encode())

    def _grant_hours(self) -> float:
        return float(self.services().settings.grant_hours)

    def _new_grant(self, run_id: str, *, ready: bool) -> str | None:
        if not self.separate:
            return None
        from .credentials import new_token, token_sha256

        token = new_token()
        self._write_grant(run_id, token_sha256(token), views=ready, hours=self._grant_hours() if ready else 0)
        return token

    def plan(self, run_id: str) -> None:
        self._start(run_id, "run", self._new_grant(run_id, ready=False))

    def replay(self, run_id: str) -> None:
        self._start(run_id, "replay", self._new_grant(run_id, ready=True))

    def analyse(self, run_id: str, user: str) -> None:
        if self.separate:  # open the grant before the run is released
            bucket, key = self._grant_key(run_id)
            old = json.loads(self.s3.get_object(Bucket=bucket, Key=key)["Body"].read())
            self._write_grant(run_id, old["token_sha256"], views=True, hours=self._grant_hours())
        approval = json.dumps({"schema": "alchemist.approval/v1", "run_id": run_id, "approved_by": user,
                               "approved_at": dt.datetime.now(dt.timezone.utc).isoformat()})
        self.s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}{run_id}/approved.json", Body=approval.encode())
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=f"{self.prefix}{run_id}/token.json")["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return  # still planning; the approval function releases the run when it stores the token
        token = json.loads(body)["task_token"]
        try:
            self.sfn.send_task_success(taskToken=token, output=approval)
        except (self.sfn.exceptions.TaskTimedOut, self.sfn.exceptions.InvalidToken):
            pass  # already released by the approval function
