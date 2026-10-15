"""The whole loop on S3: workspace, lake, sealing and per-run credentials, against a local moto server.

The moto server speaks the S3 and STS HTTP APIs, so boto3, cloudpathlib and DuckDB's httpfs in the sandbox kernel
all talk to it exactly as they would to AWS. moto does not evaluate session policies; their content is checked
separately in test_credentials.py.
"""

import json

import pytest

pytest.importorskip("moto")

from alchemist.agent import ScriptedModel  # noqa: E402
from alchemist.demo import script  # noqa: E402

REGION = "eu-central-1"
ROLE = "arn:aws:iam::123456789012:role/alchemist-run"


@pytest.fixture(scope="module")
def s3_svc(tmp_path_factory):
    import boto3
    from cloudpathlib import S3Client
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    with pytest.MonkeyPatch.context() as mp:
        for k, v in {"AWS_ENDPOINT_URL": f"http://{host}:{port}", "AWS_ACCESS_KEY_ID": "test",
                     "AWS_SECRET_ACCESS_KEY": "test", "AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION}.items():
            mp.setenv(k, v)
        mp.delenv("AWS_PROFILE", raising=False)
        mp.setattr(S3Client, "_default_client", None)
        s3 = boto3.client("s3")
        for b in ("lake", "alchemist-workspace"):
            s3.create_bucket(Bucket=b, CreateBucketConfiguration={"LocationConstraint": REGION})

        from alchemist.catalog import Catalog, index_dataset
        from alchemist.config import Settings
        from alchemist.demo.generate import generate
        from alchemist.paths import copy_tree
        from alchemist.run import Services

        home = tmp_path_factory.mktemp("alc-s3")
        datasets = generate(home)
        copy_tree(home / "lake", "s3://lake/demo")
        s = Settings(workspace_root="s3://alchemist-workspace", db_path=str(home / "alchemist.db"),
                     access_file=str(home / "access.yaml"), knowledge_root=str(home / "knowledge"),
                     run_role_arn=ROLE, aws_region=REGION)
        svc = Services(s)
        assert isinstance(svc.catalog, Catalog)
        for ds_id, spec in datasets.items():
            spec = {k: (v.replace(str(home / "lake"), "s3://lake/demo") if isinstance(v, str) else v) for k, v in spec.items()}
            svc.catalog.register(id=ds_id, actor="data_admin", **spec)
            res = index_dataset(svc.catalog, s, ds_id)
            svc.catalog.approve(ds_id, res["version"], reviewer="data_admin")
        yield svc
    server.stop()


@pytest.fixture(scope="module")
def s3_run(s3_svc):
    from alchemist.run import ScriptedReviewer, approve, ask

    claims = {"preferred_username": "kim", "groups": ["alc-plant-P01", "alc-ws-quality"]}
    user = s3_svc.access.user_from_claims(claims)
    out = ask(s3_svc, user=user, workspace="quality", project="line2", question=script.QUESTION, scope=script.SCOPE,
              model=ScriptedModel(script.PLAN_TURNS))
    assert out["status"] == "awaiting_approval"
    return approve(s3_svc, out["run_id"], user=user, model=ScriptedModel(script.ANALYSIS_TURNS),
                   reviewer=ScriptedReviewer(script.REVIEW)), user


def test_run_on_s3_completes_and_seals(s3_run):
    from alchemist.paths import as_path, read_json
    from alchemist.workspace import verify

    res, _ = s3_run
    assert res["status"] == "completed", res
    assert res["path"].startswith("s3://alchemist-workspace/workspaces/quality/projects/line2/questions/")
    run = as_path(res["path"])
    findings = {f["id"]: f for f in read_json(run / "findings.json")["findings"]}
    assert 4.0 < findings["F2"]["value"] < 5.5
    queries = read_json(run / "inputs" / "queries.json")
    assert all(o["key"].startswith("s3://lake/demo/plant=P01/") and o["etag"] for o in queries["q2"]["objects"])
    assert verify(res["path"], res["seal_sha256"])["ok"]


def test_run_used_scoped_credentials(s3_run):
    from alchemist.paths import as_path, read_json

    res, _ = s3_run
    run = as_path(res["path"])
    m = read_json(run / "manifest.json")
    cred = m["access"]["credentials"]
    assert cred["mode"] == "scoped" and cred["role"] == ROLE and cred["session"].startswith("alc-run-")
    assert m["access"]["user"] == "kim"
    policy = json.dumps(read_json(run / "logs" / "session_policy.json"))
    assert "plant=P01" in policy and "plant=P02" not in policy
    assert "AKIA" not in (run / "notebook.ipynb").read_text() and "test" not in json.dumps(cred)


def test_sealed_run_cannot_be_resealed_and_tampering_shows(s3_run):
    from alchemist.paths import as_path
    from alchemist.workspace import seal, verify

    res, _ = s3_run
    run = as_path(res["path"])
    with pytest.raises(ValueError, match="already sealed"):
        seal(run, res["run_id"])
    (run / "work" / "late.txt").write_text("added after the seal")
    check = verify(res["path"], res["seal_sha256"])
    assert not check["ok"] and "added after seal: work/late.txt" in check["problems"]
    (run / "work" / "late.txt").unlink()


def test_replay_on_s3_matches(s3_svc, s3_run):
    from alchemist.run import replay

    res, user = s3_run
    rep = replay(s3_svc, res["run_id"], user=user)
    assert rep["status"] == "completed", rep
    assert rep["result_check"]["status"] == {"mode": "replay", "compared": 5, "mismatches": 0}


def test_other_plant_user_cannot_replay(s3_svc, s3_run):
    from alchemist.access import AccessDenied
    from alchemist.run import replay

    res, _ = s3_run
    other = s3_svc.access.user_from_claims({"sub": "p2", "groups": ["alc-plant-P02", "alc-ws-quality"]})
    with pytest.raises(AccessDenied):
        replay(s3_svc, res["run_id"], user=other)


def test_rerun_on_s3_reads_lake_with_run_credentials(s3_svc, s3_run):
    from alchemist.paths import as_path, read_json
    from alchemist.run import replay

    res, user = s3_run
    rep = replay(s3_svc, res["run_id"], user=user, mode="rerun")
    assert rep["status"] == "completed", rep
    assert read_json(as_path(rep["path"]) / "manifest.json")["access"]["credentials"]["mode"] == "scoped"


def test_separate_sandbox_kernel_reads_s3_through_the_credential_process(s3_svc, tmp_path):
    """A separate sandbox's kernel has no keys in its environment: boto3, cloudpathlib and DuckDB all get them from
    the profile's credential_process (the broker in AWS; a script here) and DuckDB refreshes them as they expire."""
    import os
    import subprocess
    import sys

    from alchemist.credentials import process_profile_env

    calls = tmp_path / "calls"
    creds = tmp_path / "creds.sh"
    creds.write_text("#!/bin/sh\necho x >> " + str(calls) + "\n"
                     "echo '{\"Version\": 1, \"AccessKeyId\": \"test\", \"SecretAccessKey\": \"test\", "
                     "\"SessionToken\": \"t\", \"Expiration\": \"2099-01-01T00:00:00Z\"}'\n")
    creds.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_") or k in ("AWS_ENDPOINT_URL", "AWS_REGION")}
    env.update({k: v for k, v in process_profile_env(str(tmp_path), [str(creds)]).items() if v is not None})
    code = """
import duckdb
from cloudpathlib import S3Path
from alchemist.paths import duckdb_s3_secret
files = sorted(str(p) for p in S3Path("s3://lake/demo").rglob("*.parquet"))
con = duckdb.connect()
con.execute("load httpfs")
con.execute(duckdb_s3_secret())
print(con.execute(f"select count(*) from read_parquet('{files[0]}')").fetchone()[0])
"""
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip()) > 0
    assert len(calls.read_text().split()) >= 2  # boto3 and DuckDB each asked the process
