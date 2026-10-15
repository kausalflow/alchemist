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
