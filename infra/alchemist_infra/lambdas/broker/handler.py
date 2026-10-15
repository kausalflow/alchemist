"""Credential broker: turns a run's grant into short-lived, scoped S3 credentials for its sandbox.

The API writes s3://<workspace>/_grants/<run_id>.json when it creates a run (schema alchemist.grant/v1):

    {"schema": "alchemist.grant/v1", "run_id": "...", "user": "...", "plants": ["P01"],
     "read_prefixes": ["s3://lake/plant=P01/"], "knowledge_prefixes": ["s3://knowledge/P01/"],
     "run_prefix": "s3://workspace/workspaces/w/projects/p/questions/q/runs/<run_id>/",
     "token_sha256": "<hex>", "expires_at": "2026-10-15T12:00:00Z"}

The sandbox calls this function (through its credential_process) with {"run_id": ..., "token": ...}. The broker checks
the token against the grant, assumes the run role with a session policy limited to the grant's prefixes, and returns
the credential_process JSON. The run role's own policy is the ceiling; the session policy only narrows it.

Only boto3 and the standard library, so the function ships as a plain asset.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re

GRANT_SCHEMA = "alchemist.grant/v1"
MAX_POLICY_CHARS = 2048  # STS limit on a session policy (packed size is checked by STS as well)
_TAG_SAFE = re.compile(r"[^\w .:/=+\-@]")


class BrokerError(Exception):
    pass


def _split(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise BrokerError(f"not an s3 uri: {uri!r}")
    bucket, _, key = uri[5:].partition("/")
    if not bucket:
        raise BrokerError(f"no bucket in {uri!r}")
    if key and not key.endswith("/"):
        key += "/"
    return bucket, key


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_time(value: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def validate_grant(grant: dict, run_id: str, token: str, *, workspace_bucket: str, lake_buckets: set[str],
                   knowledge_bucket: str, now: dt.datetime | None = None) -> None:
    """Reject a grant that is expired, for another run, outside the allowed buckets, or with the wrong token."""
    if grant.get("schema") != GRANT_SCHEMA:
        raise BrokerError("unknown grant schema")
    if grant.get("run_id") != run_id:
        raise BrokerError("grant is for another run")
    expected = grant.get("token_sha256") or ""
    if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected):
        raise BrokerError("bad token")
    if _parse_time(grant["expires_at"]) <= (now or _now()):
        raise BrokerError("grant expired")
    bucket, key = _split(grant["run_prefix"])
    if bucket != workspace_bucket or not key.startswith("workspaces/") or not key.endswith(f"/runs/{run_id}/"):
        raise BrokerError("run_prefix is not this run's folder in the workspace bucket")
    for uri in grant.get("read_prefixes", []):
        b, k = _split(uri)
        if b not in lake_buckets or not k:
            raise BrokerError(f"read prefix outside the registered lake buckets: {uri}")
    for uri in grant.get("knowledge_prefixes", []):
        b, _ = _split(uri)
        if b != knowledge_bucket:
            raise BrokerError(f"knowledge prefix outside the knowledge bucket: {uri}")


def session_policy(grant: dict) -> dict:
    """Least-privilege policy for one run: read its prefixes, read and write only its own folder."""
    read = list(grant.get("read_prefixes", [])) + list(grant.get("knowledge_prefixes", []))
    run_bucket, run_key = _split(grant["run_prefix"])
    get_arns, list_by_bucket = [], {}
    for uri in read + [grant["run_prefix"]]:
        b, k = _split(uri)
        get_arns.append(f"arn:aws:s3:::{b}/{k}*")
        list_by_bucket.setdefault(b, []).append(f"{k}*")
    statements = [
        {"Effect": "Allow", "Action": "s3:GetObject", "Resource": sorted(set(get_arns))},
        {"Effect": "Allow", "Action": ["s3:PutObject", "s3:PutObjectTagging", "s3:AbortMultipartUpload"],
         "Resource": f"arn:aws:s3:::{run_bucket}/{run_key}*"},
    ]
    for b, prefixes in sorted(list_by_bucket.items()):
        statements.append({"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{b}",
                           "Condition": {"StringLike": {"s3:prefix": sorted(set(prefixes))}}})
    policy = {"Version": "2012-10-17", "Statement": statements}
    if len(json.dumps(policy, separators=(",", ":"))) > MAX_POLICY_CHARS:
        raise BrokerError("grant has too many prefixes for a session policy; see S3 Access Grants in the design")
    return policy


def _tag(value: str) -> str:
    return _TAG_SAFE.sub("_", value)[:256] or "-"


def issue(run_id: str, token: str, *, s3, sts, env: dict | None = None, now: dt.datetime | None = None) -> dict:
    env = env or os.environ
    workspace_bucket = env["WORKSPACE_BUCKET"]
    body = s3.get_object(Bucket=workspace_bucket, Key=f"{env.get('GRANTS_PREFIX', '_grants/')}{run_id}.json")["Body"]
    grant = json.loads(body.read())
    validate_grant(grant, run_id, token, workspace_bucket=workspace_bucket,
                   lake_buckets={b for b in env["LAKE_BUCKETS"].split(",") if b},
                   knowledge_bucket=env["KNOWLEDGE_BUCKET"], now=now)
    remaining = int((_parse_time(grant["expires_at"]) - (now or _now())).total_seconds())
    resp = sts.assume_role(
        RoleArn=env["RUN_ROLE_ARN"],
        RoleSessionName=f"run-{run_id}"[:64],
        Policy=json.dumps(session_policy(grant), separators=(",", ":")),
        DurationSeconds=max(900, min(3600, remaining)),
        Tags=[{"Key": "run_id", "Value": _tag(run_id)},
              {"Key": "user", "Value": _tag(str(grant.get("user", "")))},
              {"Key": "plants", "Value": _tag(":".join(grant.get("plants", [])))}],
    )
    c = resp["Credentials"]
    exp = c["Expiration"]
    return {"Version": 1, "AccessKeyId": c["AccessKeyId"], "SecretAccessKey": c["SecretAccessKey"],
            "SessionToken": c["SessionToken"],
            "Expiration": exp.isoformat() if hasattr(exp, "isoformat") else str(exp)}


def handler(event, context):  # pragma: no cover - thin AWS wrapper
    import boto3

    try:
        return issue(str(event["run_id"]), str(event["token"]), s3=boto3.client("s3"), sts=boto3.client("sts"))
    except (BrokerError, KeyError) as e:
        print(json.dumps({"decision": "deny", "run_id": event.get("run_id"), "reason": str(e)}))
        return {"error": "denied"}
