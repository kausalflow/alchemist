"""Launcher for AWS: each run is one Step Functions execution (see infra/alchemist_infra/workflow.py).

Selected with ALC_LAUNCHER=alchemist.sfn_launcher:StepFunctionsLauncher. The API calls

- plan(run_id) right after creating a run: starts the execution, which plans and then waits for approval;
- analyse(run_id, user) right after recording the approval: releases the waiting execution;
- replay(run_id) right after creating a replay run: starts an execution in replay mode;
- cancel(run_id) after a run was cancelled while planning or waiting for approval: stops its execution;
- reconcile() every ALC_RECONCILE_SECONDS: a run whose execution ended (a task killed or timed out, approval timed
  out) while the database still says it is busy is closed as failed, or expired if it was waiting for approval.

Approval is a task token the workflow stores at <ALC_APPROVALS><run_id>/token.json. This launcher writes
approved.json next to it and sends the token; if the token is not stored yet (approval came before planning
finished), the workflow's approval function sends it as soon as it stores it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Callable


def _split(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"ALC_APPROVALS must be an s3:// prefix, got {uri!r}")
    bucket, _, key = uri[5:].partition("/")
    return bucket, key if not key or key.endswith("/") else key + "/"


class StepFunctionsLauncher:
    def __init__(self, services_factory: Callable[[], object] | None = None, *, state_machine_arn: str | None = None,
                 approvals: str | None = None, sfn=None, s3=None):
        self.services = services_factory
        self.state_machine_arn = state_machine_arn or os.environ["ALC_STATE_MACHINE_ARN"]
        self.bucket, self.prefix = _split(approvals or os.environ["ALC_APPROVALS"])
        if sfn is None or s3 is None:
            import boto3

            sfn = sfn or boto3.client("stepfunctions")
            s3 = s3 or boto3.client("s3")
        self.sfn, self.s3 = sfn, s3

    def _name(self, run_id: str, mode: str) -> str:
        return f"{run_id}-{mode}"[:80]

    def _execution_arn(self, run_id: str, mode: str) -> str:
        # arn:aws:states:<region>:<account>:stateMachine:<name> -> ...:execution:<name>:<execution name>
        return self.state_machine_arn.replace(":stateMachine:", ":execution:", 1) + ":" + self._name(run_id, mode)

    def _start(self, run_id: str, mode: str) -> None:
        self.sfn.start_execution(stateMachineArn=self.state_machine_arn, name=self._name(run_id, mode),
                                 input=json.dumps({"run_id": run_id, "mode": mode}))
        if self.services:
            svc = self.services()
            try:
                svc.catalog.audit("system", "run.launch", run_id, {"launcher": "sfn", "mode": mode})
            finally:
                svc.catalog.db.close()

    def cancel(self, run_id: str) -> None:
        try:
            self.sfn.stop_execution(executionArn=self._execution_arn(run_id, "run"), cause="cancelled in Alchemist")
        except self.sfn.exceptions.ExecutionDoesNotExist:
            pass

    def reconcile(self) -> None:
        if not self.services:
            return
        from .api.jobs import last_launch
        from .run import OPEN, end_run

        svc = self.services()
        try:
            for row in svc.catalog.active_runs(OPEN):
                launch = last_launch(svc, row["run_id"])
                if not launch or launch.get("launcher") != "sfn":
                    continue  # started some other way (the CLI): not ours to judge
                try:
                    ex = self.sfn.describe_execution(executionArn=self._execution_arn(row["run_id"], launch["mode"]))
                except self.sfn.exceptions.ExecutionDoesNotExist:
                    continue
                if ex["status"] == "RUNNING":
                    continue
                why = f"workflow {ex['status'].lower()}: {ex.get('error') or ''} {ex.get('cause') or ''}".strip()
                if row["status"] == "awaiting_approval":
                    end_run(svc, row["run_id"], "expired", actor="system", reason=f"plan was not approved in time ({why})",
                            from_status=("awaiting_approval",))
                else:
                    end_run(svc, row["run_id"], "failed", actor="system", reason=why, from_status=("planning", "running"))
        finally:
            svc.catalog.db.close()

    def plan(self, run_id: str) -> None:
        self._start(run_id, "run")

    def replay(self, run_id: str) -> None:
        self._start(run_id, "replay")

    def analyse(self, run_id: str, user: str) -> None:
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
