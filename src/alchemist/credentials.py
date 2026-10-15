"""Per-run S3 credentials: the credential broker.

Each run's sandbox gets its own short-lived AWS credentials from STS. They come
from assuming the run role (`ALC_RUN_ROLE_ARN`) with an inline session policy
that allows only:

- reading the registered prefixes of the datasets pinned for the run (its plants), and
- reading and writing the run's own folder, except SEALED.

The effective permissions are the intersection of the run role's own policy and
the session policy, so S3 enforces the access model even if the agent's code
tries to read anything else. Without a run role (laptops, tests) the sandbox
uses the process's own credentials and the tool layer enforces the same rules.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

# STS limit on the packed session policy; the JSON text is a close, conservative proxy.
MAX_POLICY_CHARS = 2048

# Variables that would let the kernel fall back to the orchestrator's own identity.
AMBIENT_AWS_VARS = ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
                    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
                    "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
                    "AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE")


@dataclass(frozen=True)
class RunCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: str
    role_arn: str
    session_name: str
    policy_sha256: str

    def env(self) -> dict[str, str]:
        """Environment for the sandbox kernel: these keys and nothing it could fall back to."""
        return {"AWS_ACCESS_KEY_ID": self.access_key_id, "AWS_SECRET_ACCESS_KEY": self.secret_access_key,
                "AWS_SESSION_TOKEN": self.session_token, "AWS_EC2_METADATA_DISABLED": "true"}

    def record(self) -> dict:
        """What the manifest keeps: who and what, never the keys."""
        return {"mode": "scoped", "role": self.role_arn, "session": self.session_name,
                "expires": self.expiration, "policy_sha256": self.policy_sha256}


def _split(uri: str) -> tuple[str, str]:
    u = urlparse(uri)
    return u.netloc, u.path.lstrip("/").rstrip("/")


def _glob_prefix(glob: str) -> str:
    """The fixed part of a dataset glob: s3://lake/plant=P01/historian/date=*/*.parquet -> s3://lake/plant=P01/historian."""
    parts = glob.split("/")
    for i, p in enumerate(parts):
        if any(c in p for c in "*?["):
            return "/".join(parts[:i])
    return "/".join(parts[:-1])


def _minimal(prefixes: set[str]) -> list[str]:
    """Drop prefixes already covered by a shorter one; never widens access."""
    out: list[str] = []
    for p in sorted(prefixes):
        if not any(p == q or p.startswith(q + "/") for q in out):
            out.append(p)
    return out


def run_policy(run_dir: str, read_globs: list[str]) -> dict:
    """IAM session policy for one run. Only s3:// locations are included; local paths need no policy."""
    reads: dict[str, set[str]] = {}
    for g in read_globs:
        if g.startswith("s3://"):
            bucket, key = _split(_glob_prefix(g))
            reads.setdefault(bucket, set()).add(key)
    statements = []
    for bucket, keys in sorted(reads.items()):
        keys_list = _minimal(keys)
        statements.append({"Sid": f"ReadData{len(statements)}", "Effect": "Allow", "Action": "s3:GetObject",
                           "Resource": [f"arn:aws:s3:::{bucket}/{k}/*" for k in keys_list]})
        statements.append({"Sid": f"ListData{len(statements)}", "Effect": "Allow", "Action": "s3:ListBucket",
                           "Resource": f"arn:aws:s3:::{bucket}",
                           "Condition": {"StringLike": {"s3:prefix": [p for k in keys_list for p in (k, f"{k}/*")]}}})
    if run_dir.startswith("s3://"):
        bucket, key = _split(run_dir)
        statements += [
            {"Sid": "RunFolder", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
             "Resource": f"arn:aws:s3:::{bucket}/{key}/*"},
            {"Sid": "ListRunFolder", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{bucket}",
             "Condition": {"StringLike": {"s3:prefix": [key, f"{key}/*"]}}},
            {"Sid": "NeverSeal", "Effect": "Deny", "Action": ["s3:PutObject", "s3:DeleteObject"],
             "Resource": f"arn:aws:s3:::{bucket}/{key}/SEALED"},
        ]
    return {"Version": "2012-10-17", "Statement": statements}


def _tag(value: str, limit: int = 256) -> str:
    """STS tag values and session names allow letters, digits, spaces and _.:/=+-@ only."""
    return re.sub(r"[^\w .:/=+@-]", "_", value)[:limit]


class Broker(Protocol):
    """What run.py needs from a credential broker; another implementation (e.g. a broker service) can replace STS."""

    def issue(self, *, run_id: str, user_id: str, workspace: str, run_dir: str, read_globs: list[str],
              policy_out=None) -> RunCredentials | None: ...


class CredentialBroker:
    """Issues each run its own scoped credentials, or None when no run role is configured."""

    def __init__(self, role_arn: str | None, *, duration_seconds: int = 3600, region: str | None = None, sts=None):
        self.role_arn = role_arn or None
        self.duration = duration_seconds
        self.region = region
        self._sts = sts

    @classmethod
    def from_settings(cls, settings) -> "CredentialBroker":
        return cls(settings.run_role_arn, duration_seconds=settings.run_credential_seconds, region=settings.aws_region)

    @property
    def enabled(self) -> bool:
        return self.role_arn is not None

    def sts(self):
        if self._sts is None:
            import boto3

            self._sts = boto3.client("sts", region_name=self.region)
        return self._sts

    def issue(self, *, run_id: str, user_id: str, workspace: str, run_dir: str, read_globs: list[str],
              policy_out=None) -> RunCredentials | None:
        if not self.enabled:
            return None
        policy = run_policy(str(run_dir), read_globs)
        text = json.dumps(policy, separators=(",", ":"))
        if len(text) > MAX_POLICY_CHARS:
            raise ValueError(f"session policy for run {run_id} is {len(text)} characters (limit {MAX_POLICY_CHARS}); "
                             "register datasets under fewer prefixes")
        if policy_out is not None:
            from .paths import write_json

            write_json(policy_out, policy)
        session = _tag(f"alc-run-{run_id}", 64)
        resp = self.sts().assume_role(
            RoleArn=self.role_arn, RoleSessionName=session, Policy=text, DurationSeconds=self.duration,
            SourceIdentity=re.sub(r"[^\w+=,.@-]", "_", user_id)[:64].ljust(2, "_"),
            Tags=[{"Key": "alc:run", "Value": _tag(run_id)}, {"Key": "alc:user", "Value": _tag(user_id)},
                  {"Key": "alc:workspace", "Value": _tag(workspace)}])
        c = resp["Credentials"]
        exp = c["Expiration"]
        exp = exp.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(exp, dt.datetime) else str(exp)
        return RunCredentials(access_key_id=c["AccessKeyId"], secret_access_key=c["SecretAccessKey"],
                              session_token=c["SessionToken"], expiration=exp, role_arn=self.role_arn,
                              session_name=session, policy_sha256=hashlib.sha256(text.encode()).hexdigest())


# ------------------------------------------------------------------ separate sandbox: grants and the broker Lambda
#
# With workflow.sandbox_mode = separate the orchestrator never holds data credentials. The API writes a grant for
# each run (below); the sandbox's AWS credential_process (`alchemist broker credentials`) calls the broker Lambda
# with the run's token, and the Lambda (infra/alchemist_infra/lambdas/broker) checks the grant and returns scoped
# credentials. boto3 and DuckDB call the process again when the credentials expire, so a run is not limited to the
# one hour a chained role session lasts.

GRANT_SCHEMA = "alchemist.grant/v1"
PROFILE = "alchemist-run"
# Variables that would make boto3 or DuckDB use keys other than the broker's.
STATIC_AWS_VARS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_PROFILE",
                   "AWS_SHARED_CREDENTIALS_FILE")


class BrokerDenied(Exception):
    pass


def new_token() -> str:
    import secrets

    return secrets.token_urlsafe(32)


def read_prefixes(read_globs: list[str]) -> list[str]:
    """Grant read prefixes (s3://bucket/key/) for dataset globs, the same prefixes run_policy allows."""
    by_bucket: dict[str, set[str]] = {}
    for g in read_globs:
        if g.startswith("s3://"):
            bucket, key = _split(_glob_prefix(g))
            by_bucket.setdefault(bucket, set()).add(key)
    return [f"s3://{b}/{k}/" for b, keys in sorted(by_bucket.items()) for k in _minimal(keys)]


def make_grant(*, run_id: str, user: str, plants: list[str], run_dir: str, read_globs: list[str], token_sha256: str,
               expires_at: dt.datetime) -> dict:
    """The grant the broker Lambda checks before it issues credentials for a run (alchemist.grant/v1)."""
    run_prefix = str(run_dir).rstrip("/") + "/"
    return {"schema": GRANT_SCHEMA, "run_id": run_id, "user": user, "plants": sorted(plants),
            "read_prefixes": read_prefixes(read_globs), "knowledge_prefixes": [],
            "run_prefix": run_prefix, "token_sha256": token_sha256,
            "expires_at": expires_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LambdaBroker:
    """Client of the broker Lambda: turns a run id and its token into credential_process JSON."""

    def __init__(self, function: str, *, region: str | None = None, client=None):
        self.function = function
        self.region = region
        self._client = client

    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("lambda", region_name=self.region)
        return self._client

    def fetch(self, run_id: str, token: str) -> dict:
        resp = self.client().invoke(FunctionName=self.function, InvocationType="RequestResponse",
                                    Payload=json.dumps({"run_id": run_id, "token": token}).encode())
        body = json.loads(resp["Payload"].read() or b"{}")
        if resp.get("FunctionError") or "error" in body or "AccessKeyId" not in body:
            raise BrokerDenied(f"the credential broker refused run {run_id}: {body.get('error') or body}")
        return body

    def record(self) -> dict:
        """What the manifest keeps."""
        return {"mode": "broker", "function": self.function}


def credential_process(env: dict | None = None, *, broker: LambdaBroker | None = None) -> dict:
    """Body of `alchemist broker credentials`: ask the broker for this run's credentials.

    Runs as the kernel's AWS credential_process, so it must not resolve credentials through the profile that calls
    it: the Lambda is invoked with the sandbox task's own role, which may do nothing but invoke the broker.
    """
    import os

    env = os.environ if env is None else env
    for k in ("AWS_PROFILE", "AWS_CONFIG_FILE", *STATIC_AWS_VARS):
        env.pop(k, None)
    run_id, token = env.get("ALC_RUN_ID"), env.get("ALC_BROKER_TOKEN")
    if not (run_id and token and (broker or env.get("ALC_BROKER_FUNCTION"))):
        raise BrokerDenied("ALC_RUN_ID, ALC_BROKER_TOKEN and ALC_BROKER_FUNCTION must be set")
    broker = broker or LambdaBroker(env["ALC_BROKER_FUNCTION"], region=env.get("AWS_REGION"))
    return broker.fetch(run_id, token)


def process_profile_env(config_dir: str, command: list[str]) -> dict[str, str | None]:
    """Kernel environment that resolves S3 credentials only through `command` (a credential_process).

    Writes an AWS config file with one profile and returns the variables pointing boto3 and DuckDB at it; None
    removes a variable. boto3 refreshes credential_process credentials before they expire; DuckDB's secret is created
    with `refresh auto` (paths.duckdb_s3_secret).
    """
    import shlex
    from pathlib import Path

    path = Path(config_dir) / "aws-config"
    path.write_text(f"[profile {PROFILE}]\ncredential_process = {shlex.join(command)}\n")
    path.chmod(0o600)
    return {"AWS_CONFIG_FILE": str(path), "AWS_PROFILE": PROFILE, "AWS_SDK_LOAD_CONFIG": "1",
            "ALC_S3_CREDENTIALS": "process", "AWS_EC2_METADATA_DISABLED": "true",
            **{k: None for k in STATIC_AWS_VARS}}
