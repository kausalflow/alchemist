"""Plan approval: keep the workflow's task token until the API approves the plan.

The run workflow invokes this function with {"run_id", "token"} and waits on the token. The function stores it at
s3://<workspace>/_approvals/<run_id>/token.json. The API's launcher, on approval, writes approved.json next to it and
sends the token. Whichever of the two writes second sends SendTaskSuccess, so an approval that arrives before the
token was stored (plan approval skipped) is not lost; a duplicate send is ignored.
"""

from __future__ import annotations

import json
import os


def store_and_maybe_release(run_id: str, token: str, *, s3, sfn, bucket: str, prefix: str) -> dict:
    s3.put_object(Bucket=bucket, Key=f"{prefix}{run_id}/token.json",
                  Body=json.dumps({"schema": "alchemist.approval-token/v1", "task_token": token}).encode())
    try:
        approved = s3.get_object(Bucket=bucket, Key=f"{prefix}{run_id}/approved.json")["Body"].read()
    except s3.exceptions.NoSuchKey:
        return {"stored": True, "released": False}
    try:
        sfn.send_task_success(taskToken=token, output=approved.decode() or "{}")
    except (sfn.exceptions.TaskTimedOut, sfn.exceptions.InvalidToken):
        pass  # the launcher sent it first
    return {"stored": True, "released": True}


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    import boto3

    return store_and_maybe_release(str(event["run_id"]), str(event["token"]), s3=boto3.client("s3"),
                                   sfn=boto3.client("stepfunctions"), bucket=os.environ["WORKSPACE_BUCKET"],
                                   prefix=os.environ["APPROVALS_PREFIX"])
