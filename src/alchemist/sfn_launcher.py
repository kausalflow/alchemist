"""Launcher for AWS: each run is one Step Functions execution (see infra/alchemist_infra/workflow.py).

Selected with ALC_LAUNCHER=alchemist.sfn_launcher:StepFunctionsLauncher. The API calls

- plan(run_id) right after creating a run: starts the execution, which plans and then waits for approval;
- analyse(run_id, user) right after recording the approval: releases the waiting execution;
- replay(run_id) right after creating a replay run: starts an execution in replay mode.

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

    def _start(self, run_id: str, mode: str) -> None:
        self.sfn.start_execution(stateMachineArn=self.state_machine_arn, name=f"{run_id}-{mode}"[:80],
                                 input=json.dumps({"run_id": run_id, "mode": mode}))

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
