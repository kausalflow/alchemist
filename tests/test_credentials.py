import datetime as dt

import pytest

from alchemist.credentials import CredentialBroker, run_policy

RUN = "s3://alchemist-workspace/workspaces/quality/projects/line2/questions/Q1/runs/R1"
GLOBS = ["s3://lake/plant=P01/historian/line2/date=*/*.parquet", "s3://lake/plant=P01/quality/lots/*.parquet",
         "s3://lake/plant=P01/historian/line2/date=2026-08-01/*.parquet", "/local/only/*.csv"]


def _stmts(policy):
    return {s["Sid"]: s for s in policy["Statement"]}


def test_policy_reads_only_registered_prefixes_and_writes_only_run_folder():
    st = _stmts(run_policy(RUN, GLOBS))
    reads = st["ReadData0"]["Resource"]
    assert reads == ["arn:aws:s3:::lake/plant=P01/historian/line2/*", "arn:aws:s3:::lake/plant=P01/quality/lots/*"]
    assert st["ListData1"]["Condition"]["StringLike"]["s3:prefix"][0] == "plant=P01/historian/line2"
    key = "workspaces/quality/projects/line2/questions/Q1/runs/R1"
    assert st["RunFolder"]["Resource"] == f"arn:aws:s3:::alchemist-workspace/{key}/*"
    assert st["NeverSeal"]["Effect"] == "Deny" and st["NeverSeal"]["Resource"].endswith(f"{key}/SEALED")
    assert not any("local" in str(s) for s in st.values())


def test_local_run_needs_no_policy_statements():
    assert run_policy("/tmp/run", ["/tmp/lake/*.parquet"])["Statement"] == []


class FakeSTS:
    def __init__(self):
        self.calls = []

    def assume_role(self, **kw):
        self.calls.append(kw)
        return {"Credentials": {"AccessKeyId": "ASIA1", "SecretAccessKey": "s", "SessionToken": "t",
                                "Expiration": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)}}


def test_broker_assumes_run_role_with_session_policy_and_tags():
    sts = FakeSTS()
    b = CredentialBroker("arn:aws:iam::1:role/run", duration_seconds=900, sts=sts)
    c = b.issue(run_id="R1", user_id="kim@x.com", workspace="quality", run_dir=RUN, read_globs=GLOBS)
    call = sts.calls[0]
    assert call["RoleSessionName"] == "alc-run-R1" and call["DurationSeconds"] == 900
    assert call["SourceIdentity"] == "kim@x.com"
    assert {t["Key"]: t["Value"] for t in call["Tags"]} == {"alc:run": "R1", "alc:user": "kim@x.com", "alc:workspace": "quality"}
    assert c.env()["AWS_SESSION_TOKEN"] == "t" and c.expiration == "2026-01-01T00:00:00Z"
    assert "s" not in c.record().values()


def test_broker_disabled_without_role():
    assert CredentialBroker(None).issue(run_id="R", user_id="u", workspace="w", run_dir=RUN, read_globs=GLOBS) is None


def test_oversized_policy_is_refused():
    globs = [f"s3://lake/plant=P01/dataset_{i:03d}/*.parquet" for i in range(60)]
    with pytest.raises(ValueError, match="session policy"):
        CredentialBroker("arn:aws:iam::1:role/run", sts=FakeSTS()).issue(
            run_id="R", user_id="u", workspace="w", run_dir=RUN, read_globs=globs)


# ------------------------------------------------------------------ in-process refresh

def _creds(key: str, expires: dt.datetime):
    from alchemist.credentials import RunCredentials

    return RunCredentials(access_key_id=key, secret_access_key="s", session_token="t",
                          expiration=expires.strftime("%Y-%m-%dT%H:%M:%SZ"), role_arn="arn:role", session_name="alc-run-R",
                          policy_sha256="p")


def test_refresher_reissues_only_when_expiry_is_near(tmp_path):
    import json
    import stat

    from alchemist.credentials import RefreshingCredentials

    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    issued = iter([_creds("ASIA2", t0 + dt.timedelta(hours=2))])
    r = RefreshingCredentials(lambda: next(issued), first=_creds("ASIA1", t0 + dt.timedelta(hours=1)),
                              refresh_before=1800, config_dir=str(tmp_path))
    assert stat.S_IMODE(r.file.stat().st_mode) == 0o600
    assert json.loads(r.file.read_text())["AccessKeyId"] == "ASIA1"
    assert not r.refresh_if_due(t0 + dt.timedelta(minutes=29))
    assert r.refresh_if_due(t0 + dt.timedelta(minutes=31))
    body = json.loads(r.file.read_text())
    assert body == {"Version": 1, "AccessKeyId": "ASIA2", "SecretAccessKey": "s", "SessionToken": "t",
                    "Expiration": "2026-01-01T02:00:00Z"}
    assert r.issued == 2 and r.record()["refresh"] == "worker" and "s" not in r.record().values()
    r.stop()
    assert not tmp_path.exists()


def test_refresher_keeps_old_keys_when_sts_fails(tmp_path):
    from alchemist.credentials import RefreshingCredentials

    calls = []

    def issue():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("throttled")
        return _creds("ASIA2", dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc))

    r = RefreshingCredentials(issue, first=_creds("ASIA1", dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)),
                              check_every=0.01, config_dir=str(tmp_path)).start()
    import time

    deadline = time.monotonic() + 5
    while r.current.access_key_id != "ASIA2" and time.monotonic() < deadline:
        time.sleep(0.01)
    r.stop()
    assert r.current.access_key_id == "ASIA2" and len(calls) == 2


def test_kernel_env_reads_the_file_through_credential_process(tmp_path, monkeypatch):
    """The kernel holds no keys: boto3 asks `alchemist broker credentials --file` and treats them as refreshable."""
    import os
    import sys
    from pathlib import Path

    from alchemist.credentials import AMBIENT_AWS_VARS, RefreshingCredentials

    r = RefreshingCredentials(lambda: None, first=_creds("ASIA1", dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)),
                              config_dir=str(tmp_path))
    env = r.env()
    assert f"broker credentials --file {r.file}" in Path(env["AWS_CONFIG_FILE"]).read_text()
    assert all(env[k] is None for k in ("AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"))
    assert set(AMBIENT_AWS_VARS) <= set(env)
    for k in list(os.environ):
        if k.startswith("AWS_"):
            monkeypatch.delenv(k)
    for k, v in env.items():
        if v is not None:
            monkeypatch.setenv(k, v)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sys.path))
    import boto3

    creds = boto3.Session().get_credentials()
    assert creds.method == "custom-process" and creds.access_key == "ASIA1" and creds.refresh_needed() is False
    r.stop()


def test_credentials_file_missing_is_refused(tmp_path):
    from alchemist.credentials import BrokerDenied, read_credentials_file

    with pytest.raises(BrokerDenied, match="no credentials"):
        read_credentials_file(str(tmp_path / "gone.json"))
