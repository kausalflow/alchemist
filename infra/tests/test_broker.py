import datetime as dt
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "alchemist_infra" / "lambdas" / "broker"))
import handler as broker  # noqa: E402

NOW = dt.datetime(2026, 10, 15, 8, 0, tzinfo=dt.timezone.utc)
RUN = "01JA7Y3KABCDEF"
ENV = {"WORKSPACE_BUCKET": "ws", "KNOWLEDGE_BUCKET": "kn", "LAKE_BUCKETS": "lake-a,lake-b",
       "RUN_ROLE_ARN": "arn:aws:iam::111111111111:role/run", "GRANTS_PREFIX": "_grants/"}


def grant(**over):
    g = {"schema": "alchemist.grant/v1", "run_id": RUN, "user": "eng_p1", "plants": ["P01"],
         "read_prefixes": ["s3://lake-a/plant=P01/"], "knowledge_prefixes": ["s3://kn/P01/"],
         "run_prefix": f"s3://ws/workspaces/quality/projects/p/questions/q/runs/{RUN}/",
         "token_sha256": {"kernel": hashlib.sha256(b"secret").hexdigest(),
                          "query": hashlib.sha256(b"query-secret").hexdigest()},
         "expires_at": "2026-10-15T09:00:00Z"}
    g.update(over)
    return g


def check(g, token="secret", scope="kernel"):
    broker.validate_grant(g, RUN, token, workspace_bucket="ws", lake_buckets={"lake-a", "lake-b"},
                          knowledge_bucket="kn", now=NOW, scope=scope)


def test_valid_grant_passes():
    check(grant())
    check(grant(), "query-secret", "query")


@pytest.mark.parametrize("token,scope", [("secret", "query"), ("query-secret", "kernel"), ("secret", "admin")])
def test_each_scope_takes_only_its_own_token(token, scope):
    """The kernel holds the kernel token; it must not get the query scope, which reads the lake (PA-25)."""
    with pytest.raises(broker.BrokerError, match="bad token|unknown scope"):
        check(grant(), token, scope)


def test_a_grant_without_scope_tokens_is_refused():
    with pytest.raises(broker.BrokerError, match="bad token"):
        check(grant(token_sha256=hashlib.sha256(b"secret").hexdigest()))


@pytest.mark.parametrize("over,token,msg", [
    ({}, "wrong", "bad token"),
    ({"run_id": "OTHER"}, "secret", "another run"),
    ({"expires_at": "2026-10-15T07:59:00Z"}, "secret", "expired"),
    ({"read_prefixes": ["s3://elsewhere/plant=P01/"]}, "secret", "lake buckets"),
    ({"read_prefixes": ["s3://lake-a/"]}, "secret", "lake buckets"),  # a whole bucket is never a plant prefix
    ({"knowledge_prefixes": ["s3://lake-a/P01/"]}, "secret", "knowledge bucket"),
    ({"run_prefix": "s3://ws/workspaces/quality/projects/p/questions/q/runs/OTHER/"}, "secret", "run's folder"),
    ({"run_prefix": f"s3://other/workspaces/q/runs/{RUN}/"}, "secret", "run's folder"),
    ({"schema": "alchemist.grant/v0"}, "secret", "schema"),
])
def test_bad_grants_are_refused(over, token, msg):
    with pytest.raises(broker.BrokerError, match=msg):
        check(grant(**over), token)


RUN_ARN = f"arn:aws:s3:::ws/workspaces/quality/projects/p/questions/q/runs/{RUN}/"


def test_query_policy_is_limited_to_the_grant():
    p = broker.session_policy(grant(), "query")
    text = json.dumps(p)
    get = next(s for s in p["Statement"] if s["Action"] == "s3:GetObject")
    assert get["Resource"] == sorted(["arn:aws:s3:::lake-a/plant=P01/*", "arn:aws:s3:::kn/P01/*", f"{RUN_ARN}*"])
    put = next(s for s in p["Statement"] if "s3:PutObject" in s["Action"])
    # The query step writes results and their record, so never the seal or the inputs pinned at approval.
    assert put["Resource"] == [f"{RUN_ARN}inputs/*.parquet", f"{RUN_ARN}inputs/queries.json"]
    lists = {s["Resource"]: s["Condition"]["StringLike"]["s3:prefix"] for s in p["Statement"]
             if s["Action"] == "s3:ListBucket"}
    assert lists == {"arn:aws:s3:::kn": ["P01/*"], "arn:aws:s3:::lake-a": ["plant=P01/*"],
                     "arn:aws:s3:::ws": [f"workspaces/quality/projects/p/questions/q/runs/{RUN}/*"]}
    assert "plant=P02" not in text and "lake-b" not in text


def test_kernel_policy_reads_no_lake_data():
    p = broker.session_policy(grant())  # the kernel scope is the default
    text = json.dumps(p)
    assert "lake-a" not in text and "plant=P01" not in text
    put = next(s for s in p["Statement"] if s["Effect"] == "Allow" and "s3:PutObject" in s["Action"])
    assert put["Resource"] == f"{RUN_ARN}*"
    deny = next(s for s in p["Statement"] if s["Effect"] == "Deny")
    assert deny["Resource"] == [f"{RUN_ARN}SEALED", f"{RUN_ARN}inputs/*"]


def test_too_many_prefixes_is_an_error():
    many = [f"s3://lake-a/plant=P{i:03d}/line={j}/" for i in range(20) for j in range(5)]
    with pytest.raises(broker.BrokerError, match="too many prefixes"):
        broker.session_policy(grant(read_prefixes=many), "query")


class FakeS3:
    def __init__(self, body):
        self.body, self.calls = body, []

    def get_object(self, Bucket, Key):
        self.calls.append((Bucket, Key))
        return {"Body": io.BytesIO(json.dumps(self.body).encode())}


class FakeSts:
    def __init__(self):
        self.kw = None

    def assume_role(self, **kw):
        self.kw = kw
        return {"Credentials": {"AccessKeyId": "AK", "SecretAccessKey": "SK", "SessionToken": "ST",
                                "Expiration": NOW + dt.timedelta(hours=1)}}


def test_issue_returns_credential_process_json():
    s3, sts = FakeS3(grant()), FakeSts()
    out = broker.issue(RUN, "secret", s3=s3, sts=sts, env=ENV, now=NOW)
    assert out["Version"] == 1 and out["AccessKeyId"] == "AK" and out["Expiration"].startswith("2026-10-15T09:00")
    assert s3.calls == [("ws", f"_grants/{RUN}.json")]
    assert sts.kw["RoleArn"] == ENV["RUN_ROLE_ARN"] and sts.kw["DurationSeconds"] == 3600
    assert {t["Key"]: t["Value"] for t in sts.kw["Tags"]} == {"run_id": RUN, "user": "eng_p1", "plants": "P01",
                                                              "scope": "kernel"}
    assert json.loads(sts.kw["Policy"]) == broker.session_policy(grant())
    broker.issue(RUN, "query-secret", s3=FakeS3(grant()), sts=sts, env=ENV, now=NOW, scope="query")
    assert json.loads(sts.kw["Policy"]) == broker.session_policy(grant(), "query")


def test_issue_shortens_the_session_to_the_grant_but_not_below_sts_minimum():
    sts = FakeSts()
    broker.issue(RUN, "secret", s3=FakeS3(grant(expires_at="2026-10-15T08:05:00Z")), sts=sts, env=ENV, now=NOW)
    assert sts.kw["DurationSeconds"] == 900


def test_issue_refuses_a_wrong_token_without_calling_sts():
    sts = FakeSts()
    with pytest.raises(broker.BrokerError):
        broker.issue(RUN, "nope", s3=FakeS3(grant()), sts=sts, env=ENV, now=NOW)
    assert sts.kw is None
