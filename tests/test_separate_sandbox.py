"""workflow.sandbox_mode = separate: the kernel in its own task, credentials from the broker, report and seal as
their own steps. Everything here runs locally: the sandbox server on a free port, the broker Lambda as a fake."""

import copy
import dataclasses
import datetime as dt
import importlib.util
import io
import json
import os
import threading
from pathlib import Path

import pytest

from alchemist.agent import ScriptedModel
from alchemist.credentials import (BrokerDenied, LambdaBroker, credential_process, make_grant, new_token,
                                   process_profile_env, read_prefixes, token_sha256)
from alchemist.demo import script
from alchemist.paths import duckdb_s3_secret, read_json
from alchemist.run import (ScriptedReviewer, analyse, ask, execute_replay, report_step, seal_step, start_approval,
                           start_replay)
from alchemist.sandbox_server import KernelHost, make_server
from alchemist.workspace import verify

from test_sfn_launcher import FakeS3, FakeSfn

ROOT = Path(__file__).resolve().parents[1]
CREDS = {"Version": 1, "AccessKeyId": "ASIA1", "SecretAccessKey": "s", "SessionToken": "t",
         "Expiration": "2030-01-01T00:00:00Z"}


def _broker_lambda():
    """The infrastructure's broker function, loaded from infra/ so both sides are checked against one contract."""
    spec = importlib.util.spec_from_file_location(
        "broker_handler", ROOT / "infra" / "alchemist_infra" / "lambdas" / "broker" / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeLambda:
    def __init__(self, response):
        self.response, self.calls = response, []

    def invoke(self, **kw):
        self.calls.append(kw)
        out = {"Payload": io.BytesIO(json.dumps(self.response).encode())}
        if self.response.get("errorMessage"):
            out["FunctionError"] = "Unhandled"
        return out


# ------------------------------------------------------------------ broker client and credential_process

def test_lambda_broker_returns_credential_process_json():
    fake = FakeLambda(CREDS)
    assert LambdaBroker("arn:fn", client=fake).fetch("R1", "tok") == CREDS
    assert json.loads(fake.calls[0]["Payload"]) == {"run_id": "R1", "token": "tok"}


@pytest.mark.parametrize("response", [{"error": "denied"}, {"errorMessage": "boom"}])
def test_lambda_broker_refusal_is_an_error(response):
    with pytest.raises(BrokerDenied):
        LambdaBroker("arn:fn", client=FakeLambda(response)).fetch("R1", "tok")


def test_credential_process_never_resolves_through_its_own_profile():
    env = {"ALC_RUN_ID": "R1", "ALC_BROKER_TOKEN": "tok", "ALC_BROKER_FUNCTION": "arn:fn",
           "AWS_PROFILE": "alchemist-run", "AWS_CONFIG_FILE": "/tmp/x", "AWS_ACCESS_KEY_ID": "AKIA"}
    fake = FakeLambda(CREDS)
    assert credential_process(env, broker=LambdaBroker("arn:fn", client=fake)) == CREDS
    assert not {"AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_ACCESS_KEY_ID"} & set(env)


def test_credential_process_needs_run_and_token():
    with pytest.raises(BrokerDenied):
        credential_process({"ALC_BROKER_FUNCTION": "arn:fn"})


def test_profile_points_boto3_and_duckdb_at_the_credential_process(tmp_path, monkeypatch):
    env = process_profile_env(str(tmp_path), ["/venv/bin/python", "-m", "alchemist.cli", "broker", "credentials"])
    assert "credential_process = /venv/bin/python -m alchemist.cli broker credentials" in Path(env["AWS_CONFIG_FILE"]).read_text()
    assert env["AWS_SESSION_TOKEN"] is None and env["AWS_PROFILE"] == "alchemist-run"
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    sql = duckdb_s3_secret()
    assert "chain 'process'" in sql and "refresh auto" in sql and "key_id" not in sql


def test_boto3_takes_refreshable_credentials_from_the_process(tmp_path, monkeypatch):
    """The credentials expire; boto3 must treat them as refreshable rather than fixed for the session."""
    script_file = tmp_path / "creds.sh"
    script_file.write_text("#!/bin/sh\necho '" + json.dumps(CREDS) + "'\n")
    script_file.chmod(0o755)
    env = process_profile_env(str(tmp_path), [str(script_file)])
    for k in list(os.environ):
        if k.startswith("AWS_"):
            monkeypatch.delenv(k)
    for k, v in env.items():
        if v is not None:
            monkeypatch.setenv(k, v)
    import boto3

    creds = boto3.Session().get_credentials()
    assert creds.method == "custom-process" and creds.access_key == "ASIA1" and creds.refresh_needed() is False


# ------------------------------------------------------------------ grants

RUN_DIR = "s3://ws-bucket/workspaces/quality/projects/line2/questions/Q1/runs/R1"


def test_grant_is_accepted_by_the_broker_lambda():
    h = _broker_lambda()
    token = new_token()
    grant = make_grant(run_id="R1", user="eng_p1", plants=["P01"], run_dir=RUN_DIR,
                       read_globs=["s3://lake/plant=P01/historian/date=*/*.parquet", "s3://lake/plant=P01/lots/*.parquet",
                                   "/local/only/*.csv"],
                       token_sha256=token_sha256(token), expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1))
    assert grant["read_prefixes"] == ["s3://lake/plant=P01/historian/", "s3://lake/plant=P01/lots/"]
    assert grant["run_prefix"] == RUN_DIR + "/"
    h.validate_grant(grant, "R1", token, workspace_bucket="ws-bucket", lake_buckets={"lake"}, knowledge_bucket="kn")
    policy = json.dumps(h.session_policy(grant))
    assert "lake/plant=P01/historian/*" in policy and "runs/R1/SEALED" in policy
    with pytest.raises(h.BrokerError, match="bad token"):
        h.validate_grant(grant, "R1", "other", workspace_bucket="ws-bucket", lake_buckets={"lake"}, knowledge_bucket="kn")


def test_read_prefixes_drop_covered_prefixes():
    assert read_prefixes(["s3://lake/a/*.parquet", "s3://lake/a/b/*.parquet", "s3://other/x/y=*/*.csv"]) == [
        "s3://lake/a/", "s3://other/x/"]


class _Svc:
    def __init__(self, rows, workspace="s3://ws-bucket", grant_hours=8):
        self.rows = rows
        self.settings = dataclasses.make_dataclass("S", ["workspace_root", "grant_hours"])(workspace, grant_hours)
        self.catalog = self
        self.db = self

    def run(self, run_id):
        return self.rows[run_id]

    def audit(self, *args):
        pass

    def close(self):
        pass


def test_launcher_writes_grants_and_passes_the_token_only_in_the_execution(tmp_path):
    from alchemist.sfn_launcher import StepFunctionsLauncher

    run = tmp_path / "runs" / "R1"
    (run / "inputs").mkdir(parents=True)
    rows = {"R1": {"run_id": "R1", "user_id": "eng_p1", "plants": '["P01"]', "path": str(run), "mode": "live"}}
    s3, sfn = FakeS3(), FakeSfn()
    l = StepFunctionsLauncher(lambda: _Svc(rows), state_machine_arn="arn:sm", approvals="s3://ws-bucket/_approvals/",
                              sfn=sfn, s3=s3, sandbox_mode="separate")
    l.plan("R1")
    token = json.loads(sfn.started[0]["input"])["broker_token"]
    grant = json.loads(s3.objects[("ws-bucket", "_grants/R1.json")])
    assert grant["token_sha256"] == token_sha256(token) and token not in json.dumps(grant)
    assert grant["read_prefixes"] == [] and grant["expires_at"] <= dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    (run / "inputs" / "views.json").write_text(json.dumps({"lots": {"glob": "s3://lake/plant=P01/lots/*.parquet"}}))
    l.analyse("R1", "eng_p1")
    grant = json.loads(s3.objects[("ws-bucket", "_grants/R1.json")])
    assert grant["read_prefixes"] == ["s3://lake/plant=P01/lots/"] and grant["token_sha256"] == token_sha256(token)
    assert grant["expires_at"] > (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_in_process_launcher_writes_no_grant():
    from alchemist.sfn_launcher import StepFunctionsLauncher

    s3, sfn = FakeS3(), FakeSfn()
    l = StepFunctionsLauncher(None, state_machine_arn="arn:sm", approvals="s3://ws/_approvals/", sfn=sfn, s3=s3,
                              sandbox_mode="in_process")
    l.plan("R1")
    assert "broker_token" not in json.loads(sfn.started[0]["input"]) and not any("_grants" in k for _, k in s3.objects)


def test_replay_grant_reads_no_lake_data(tmp_path):
    from alchemist.sfn_launcher import StepFunctionsLauncher

    run = tmp_path / "R2"
    (run / "inputs").mkdir(parents=True)
    (run / "inputs" / "views.json").write_text(json.dumps({"lots": {"glob": "s3://lake/plant=P01/lots/*.parquet"}}))
    rows = {"R2": {"run_id": "R2", "user_id": "u", "plants": '["P01"]', "path": str(run), "mode": "replay"}}
    s3 = FakeS3()
    StepFunctionsLauncher(lambda: _Svc(rows), state_machine_arn="arn:sm", approvals="s3://ws-bucket/_approvals/",
                          sfn=FakeSfn(), s3=s3, sandbox_mode="separate").replay("R2")
    assert json.loads(s3.objects[("ws-bucket", "_grants/R2.json")])["read_prefixes"] == []


# ------------------------------------------------------------------ the sandbox server and the split steps

@pytest.fixture(scope="module")
def sandbox_url():
    server = make_server("127.0.0.1", 0, KernelHost())
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.kernel_host.stop()


@pytest.fixture(scope="module")
def separate_svc(svc):
    s = copy.copy(svc)
    s.settings = dataclasses.replace(svc.settings, sandbox_mode="separate")
    return s


def test_kernel_host_serves_only_its_own_run():
    with pytest.raises(PermissionError):
        KernelHost({"ALC_RUN_ID": "R1"}).start("/w/runs/R2")


def test_broker_mode_builds_the_kernel_environment_itself():
    host = KernelHost({"ALC_BROKER_FUNCTION": "arn:fn", "ALC_RUN_ID": "R1", "ALC_DB_HOST": "db"})
    extra, record = host.credentials_env()
    assert record == {"mode": "broker", "function": "arn:fn"}
    assert "broker credentials" in Path(extra["AWS_CONFIG_FILE"]).read_text()


@pytest.fixture(scope="module")
def separate_run(svc, separate_svc, sandbox_url):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")
    out = ask(separate_svc, user="eng_p1", workspace="quality", project="line2-separate", question=script.QUESTION,
              scope=script.SCOPE, model=ScriptedModel(script.PLAN_TURNS))
    start_approval(separate_svc, out["run_id"], user="eng_p1")
    res = analyse(separate_svc, out["run_id"], user="eng_p1", model=ScriptedModel(script.ANALYSIS_TURNS),
                  reviewer=ScriptedReviewer(script.REVIEW), sandbox_url=sandbox_url)
    return res


def test_analysis_on_the_sandbox_leaves_sealing_to_the_seal_step(separate_svc, separate_run):
    assert separate_run["status"] == "completed" and separate_run["seal_sha256"] is None
    run = Path(separate_run["path"])
    assert not (run / "SEALED").exists() and (run / "report" / "report.html").exists()
    m = read_json(run / "manifest.json")
    assert m["access"]["credentials"] == {"mode": "ambient"}  # no broker on a laptop
    assert set(m["result_hashes"]) == {"F1", "F2", "F3", "F4", "F5"}
    assert any(p.name.endswith(".png") for p in (run / "figures").iterdir())

    assert report_step(separate_svc, separate_run["run_id"])["ended"] == "already reported"
    sealed = seal_step(separate_svc, separate_run["run_id"])
    assert sealed["status"] == "completed" and verify(run, sealed["seal_sha256"])["ok"]
    assert seal_step(separate_svc, separate_run["run_id"])["seal_sha256"] == sealed["seal_sha256"]  # a retry is a no-op


def test_replay_on_the_sandbox_matches(separate_svc, separate_run, sandbox_url):
    seal_step(separate_svc, separate_run["run_id"])
    row = start_replay(separate_svc, separate_run["run_id"], user="eng_p1")
    rep = execute_replay(separate_svc, row["run_id"], sandbox_url=sandbox_url)
    assert rep["status"] == "completed" and rep["seal_sha256"] is None
    assert rep["result_check"]["status"] == {"mode": "replay", "compared": 5, "mismatches": 0}
    assert seal_step(separate_svc, row["run_id"])["status"] == "completed"


def test_report_step_finishes_a_run_whose_analysis_died(svc, separate_svc):
    out = ask(separate_svc, user="eng_p1", workspace="quality", project="line2-crash", question=script.QUESTION,
              scope=script.SCOPE, model=ScriptedModel(script.PLAN_TURNS))
    start_approval(separate_svc, out["run_id"], user="eng_p1")  # Analyse never reports back
    rep = report_step(separate_svc, out["run_id"])
    assert rep["status"] == "failed"  # nothing was found, so there is no partial report
    assert read_json(Path(out["path"]) / "manifest.json")["status"] == "failed"
    sealed = seal_step(separate_svc, out["run_id"])
    assert sealed["status"] == "failed" and svc.catalog.run(out["run_id"])["seal_sha256"]


def test_separate_mode_needs_a_sandbox_address(separate_svc, separate_run):
    from alchemist.run import RunContext

    ctx = RunContext(separate_svc, separate_svc.catalog.run(separate_run["run_id"]), user="eng_p1")
    with pytest.raises(ValueError, match="sandbox"):
        separate_svc.sandbox(ctx, {}, run_mode="live")


def test_local_kernel_uses_tcp_on_windows(monkeypatch):
    """ZeroMQ has no IPC sockets on Windows; the kernel must still start there, over localhost TCP."""
    from alchemist import sandbox

    assert sandbox.kernel_transport("win32") == {"transport": "tcp", "ip": "127.0.0.1"}
    assert sandbox.kernel_transport("linux")["transport"] == "ipc"
    windows = sandbox.kernel_transport("win32")
    monkeypatch.setattr(sandbox, "kernel_transport", lambda: windows)
    k = sandbox.LocalKernel(sandbox.kernel_env("."))
    k.start()
    try:
        out = []
        k.execute_interactive("print(6 * 7)", output_hook=lambda m: out.append(m["content"].get("text", "")), timeout=30)
        assert "42" in "".join(out)
    finally:
        k.stop()
