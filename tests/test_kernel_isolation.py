"""The analysis kernel runs code the model wrote: it must not see the server's secrets, other files, or widen its run."""

import os
from pathlib import Path

import pytest

from alchemist import fsjail
from alchemist.agent import ScriptedModel
from alchemist.demo import script
from alchemist.paths import read_json, write_json
from alchemist.run import RunContext, ask, pinned_inputs, start_approval
from alchemist.sandbox import kernel_base_env, kernel_env, kernel_jail
from alchemist.safehtml import markdown_html


def test_kernel_env_is_an_allow_list():
    base = {"PATH": "/usr/bin", "HOME": "/home/a", "LC_ALL": "C", "AWS_REGION": "eu-west-1", "AWS_PROFILE": "p",
            "ALC_DB": "postgresql://master:pw@db/alc", "ALC_DB_PASSWORD": "pw", "ANTHROPIC_API_KEY": "sk-ant",
            "OPENAI_API_KEY": "sk", "ALC_SANDBOX_TOKEN": "t", "ALC_BEDROCK_PROFILE": "models",
            "AWS_BEARER_TOKEN_BEDROCK": "b", "ALC_OIDC_CLIENT_SECRET": "s", "ALC_PSEUDONYM_KEY": "k",
            "ALC_PLUGINS": "*", "MY_PLUGIN_ENDPOINT": "x", "MY_PLUGIN_SECRET": "y"}
    env = kernel_base_env(base)
    assert set(env) == {"PATH", "HOME", "LC_ALL", "AWS_REGION", "AWS_PROFILE", "ALC_PSEUDONYM_KEY", "ALC_PLUGINS"}
    # An operator can let a plugin's setting through, by name or prefix.
    assert "MY_PLUGIN_ENDPOINT" in kernel_base_env({**base, "ALC_KERNEL_ENV": "MY_PLUGIN_ENDPOINT"})
    assert {"MY_PLUGIN_ENDPOINT", "MY_PLUGIN_SECRET"} <= set(kernel_base_env({**base, "ALC_KERNEL_ENV": "MY_PLUGIN_*"}))
    full = kernel_env("/runs/R1", {"AWS_PROFILE": None, "ALC_PSEUDONYM_KEY": "run"}, base=base)
    assert full["ALC_RUN_DIR"] == "/runs/R1" and "AWS_PROFILE" not in full and full["ALC_PSEUDONYM_KEY"] == "run"
    assert "Path" in kernel_base_env({"Path": "C:\\Windows"})  # Windows names are case-insensitive


def test_kernel_jail_modes(monkeypatch):
    monkeypatch.setattr(fsjail, "available", lambda: False)
    assert kernel_jail("/runs/R1", ["/lake"], mode="auto") is None
    assert kernel_jail("/runs/R1", ["/lake"], mode="off") is None
    with pytest.raises(PermissionError):
        kernel_jail("/runs/R1", ["/lake"], mode="required")
    with pytest.raises(ValueError):
        kernel_jail("/runs/R1", [], mode="maybe")
    monkeypatch.setattr(fsjail, "available", lambda: True)
    assert kernel_jail("/runs/R1", ["/lake", "s3://b/x"], ["/creds"]) == {"read": ["/lake", "/creds"],
                                                                          "write": ["/runs/R1"]}
    assert kernel_jail("s3://ws/runs/R1", [])["write"] == []


@pytest.fixture(scope="module")
def approved(svc):
    d = svc.catalog.latest_card("resin_lots", status=None)
    if d["status"] == "draft":
        svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")
    out = ask(svc, user="eng_p1", workspace="quality", project="line2-isolation", question=script.QUESTION,
              scope=script.SCOPE, model=ScriptedModel(script.PLAN_TURNS))
    start_approval(svc, out["run_id"], user="eng_p1")
    return svc.catalog.run(out["run_id"])


def _run(sb, code: str) -> str:
    res = sb.execute(code, kind="python")
    return "".join(res.text) + (res.error or "")


def test_in_process_kernel_sees_no_server_secrets_or_other_files(svc, approved, monkeypatch):
    monkeypatch.setenv("ALC_DB", "postgresql://master:hunter2@db/alc")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    ctx = RunContext(svc, approved, user="eng_p1")
    views = pinned_inputs(svc, approved["run_id"], ctx.run)["views"]
    with svc.sandbox(ctx, views, run_mode="live") as sb:
        sb.setup()
        out = _run(sb, "import os\nprint(sorted(k for k in os.environ if 'ALC_DB' in k or 'API_KEY' in k))")
        assert "[]" in out, out
        assert ctx.credentials["filesystem"] == (f"landlock v{fsjail.abi()}" if fsjail.available() else "shared")
        if not fsjail.available():
            pytest.skip("no Landlock on this machine: the kernel is not confined to its files")
        # The access file and the app database are outside the run: unreadable. So is the worker's environment.
        for path in (svc.settings.access_file, svc.settings.db_path, f"/proc/{os.getpid()}/environ"):
            if "://" in path:
                continue
            out = _run(sb, f"open({path!r}, 'rb').read(1)")
            assert "PermissionError" in out, (path, out)
        out = _run(sb, "import os\nopen(os.path.join(os.environ['ALC_RUN_DIR'], 'work-ok.txt'), 'w').write('x')\nprint('ok')")
        assert "ok" in out
        # It still reads the datasets its run was given.
        out = _run(sb, "df = alc.query('qx', 'select count(*) as n from ds.resin_lots')\nprint(int(df.n[0]) > 0)")
        assert "True" in out, out
    (Path(ctx.run) / "work-ok.txt").unlink()


def test_a_run_cannot_widen_its_own_inputs(svc, approved):
    run = Path(approved["path"])
    pinned = svc.catalog.run_inputs(approved["run_id"])
    assert pinned and pinned["views"] == read_json(run / "inputs" / "views.json")
    # The run's own code rewrites its copy to add another plant's data...
    tampered = {**pinned["views"], "p02_lots": {"glob": "/lake/plant=P02/**/*.parquet", "format": "parquet",
                                                "plant": "P02", "card_version": 1, "columns": []}}
    write_json(run / "inputs" / "views.json", tampered)
    try:
        # ...but credentials, grants, the manifest and replays come from the record made at approval.
        assert "p02_lots" not in pinned_inputs(svc, approved["run_id"], run)["views"]
        ctx = RunContext(svc, approved, user="eng_p1")
        assert "p02_lots" not in {c["dataset"] for c in ctx.write_manifest("running")["catalog"]}
    finally:
        write_json(run / "inputs" / "views.json", pinned["views"])


def test_conclusion_html_is_sanitised():
    html = markdown_html("**Cause** <script>alert(1)</script><img src=x onerror=alert(1)> "
                         "[x](javascript:alert(1)) [ok](https://example.com) <a href=\"#\" onclick=\"go()\">y</a>\n\n"
                         "| a |\n|---|\n| 1 |", extensions=["tables"])
    assert "<strong>Cause</strong>" in html and "<table>" in html and 'href="https://example.com"' in html
    for bad in ("<script", "onerror", "onclick", "javascript:", "<img"):
        assert bad not in html, (bad, html)


def test_report_html_is_sanitised(tmp_path):
    from alchemist.report import render

    ctx = {"run_id": "R1", "created": "", "asker": "a", "workspace": "w", "plants": ["P01"], "question": "q",
           "plan": {"scope": {}, "hypotheses": []}, "review": {}, "citations": [], "run_path": str(tmp_path),
           "env": {"packages": {}}, "models": {}, "steering": []}
    render(tmp_path, ctx=ctx, report_input={"title": "T", "conclusion": "Done <script>alert(1)</script>"},
           findings=[], queries={})
    html = (tmp_path / "report" / "report.html").read_text()
    assert "Done" in html and "<script>alert" not in html
