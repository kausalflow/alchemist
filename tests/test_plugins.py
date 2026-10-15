import json
import os
from pathlib import Path

import pytest

from alchemist import llm, plugins
from alchemist.access import AccessModel
from alchemist.agent import ScriptedModel
from alchemist.catalog import Catalog, index_dataset
from alchemist.config import Settings
from alchemist.demo import script
from alchemist.paths import read_json
from alchemist.plugins import (AccessProvider, CardDrafter, DataFormat, ModelProvider, PluginError, Registry, Tool,
                               set_registry)

EXAMPLE_SRC = Path(__file__).resolve().parents[1] / "examples" / "alchemist-example-plugin" / "src"


@pytest.fixture
def registry():
    """A fresh process-wide registry for one test, reset afterwards."""
    r = Registry()
    set_registry(r)
    yield r
    set_registry(None)


@pytest.fixture
def example_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLE_SRC))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(EXAMPLE_SRC), os.environ.get("PYTHONPATH", "")]))


def test_builtins_are_registered_like_plugins():
    r = Registry()
    assert r.data_formats.names() == ["csv", "json", "parquet"]
    assert r.storage_backends.names() == ["file", "s3"]
    assert r.card_drafters.names() == ["heuristic", "model"]
    assert "bedrock" in r.model_providers and "yaml" in r.access_providers
    assert [p["name"] for p in r.summary()] == ["builtin"]


def test_duplicates_need_replace():
    r = Registry()

    def bad(reg):
        reg.data_formats.add(DataFormat("csv", "read_csv"))

    with pytest.raises(PluginError, match="already registered by plugin 'builtin'"):
        r.register_plugin("bad", bad)

    def good(reg):
        reg.data_formats.add(DataFormat("csv", "read_csv", options="delim = ';'"), replace=True)

    info = r.register_plugin("semicolon", good)
    assert info.contributions == ["data_formats:csv (replaced)"]
    assert r.data_formats.get("csv").scan(["a.csv"]) == "read_csv(['a.csv'], delim = ';')"
    assert r.data_formats.provided_by("csv") == "semicolon"


def test_entry_points_respect_alc_plugins(monkeypatch):
    calls = []

    class Dist:
        name, version = "acme-alchemist", "2.1.0"

    class EP:
        def __init__(self, name):
            self.name, self.value, self.dist = name, f"acme_{name}:register", Dist()

        def load(self):
            return lambda r: (calls.append(self.name), r.analysis_methods.add(f"m_{self.name}", len))

    monkeypatch.setattr(plugins.importlib.metadata, "entry_points", lambda group: [EP("a"), EP("b")])
    r = Registry()
    r.load_entry_points("b")
    assert calls == ["b"] and r.analysis_methods.names() == ["m_b"]
    assert r.plugins["b"].version == "2.1.0" and r.plugins["b"].distribution == "acme-alchemist"
    assert Registry().load_entry_points("none") == []
    with pytest.raises(PluginError, match="not installed"):
        Registry().load_entry_points("a,zzz")


def test_example_plugin_contributions(example_on_path):
    r = Registry()
    info = r.load_module("alchemist_example")
    assert info.version == "0.1.0"
    assert set(info.contributions) >= {"analysis_methods:cusum", "tools:define_term", "knowledge_packs:extrusion-basics",
                                       "data_formats:tsv", "metadata_parsers:toml", "report_renderers:markdown",
                                       "cli_commands:glossary"}
    shift = r.analysis_methods.get("cusum")([10.0] * 30 + [13.0] * 30, target=10.0)
    assert shift["direction"] == "up" and 30 <= shift["alarm_index"] < 40
    assert r.method_docs()["cusum"].startswith("Two-sided CUSUM")


def test_plugin_format_parser_and_drafter(registry, example_on_path, tmp_path):
    registry.load_module("alchemist_example")
    calls = []

    def strict(req):
        calls.append(req)
        from alchemist.catalog import heuristic_draft
        return heuristic_draft(req.dataset_id, req.metadata, req.sample_columns)

    registry.register_plugin("strict", lambda r: r.card_drafters.add(CardDrafter("strict", strict)))
    data = tmp_path / "lake" / "kiln"
    data.mkdir(parents=True)
    (data / "part1.tsv").write_text("ts\tkiln_temp_c\n2026-08-01 00:00\t812.5\n2026-08-01 00:01\t813.0\n")
    (data / "meta.toml").write_text('description = "Kiln temperatures"\ngrain = "1 min"\n'
                                    '[columns.kiln_temp_c]\nunit = "degC"\n')
    cat = Catalog(str(tmp_path / "db.sqlite"))
    cat.register(id="kiln", s3_prefix=str(data), file_glob="*.tsv", file_format="tsv", plant="P01",
                 metadata_uri=str(data / "meta.toml"))
    res = index_dataset(cat, Settings(), "kiln", drafter="strict")
    card = cat.latest_card("kiln", status=None)
    assert card["drafted_by"] == "strict" and calls[0].metadata["grain"] == "1 min"
    assert card["card"]["description"] == "Kiln temperatures"
    assert {c["name"]: c.get("unit") for c in card["card"]["columns"]} == {"ts": None, "kiln_temp_c": "degC"}
    assert res["checks"] == []
    with pytest.raises(ValueError, match="file_format must be one of"):
        cat.register(id="x", s3_prefix=str(data), file_glob="*", file_format="xlsx", plant="P01", metadata_uri=None)


def test_plugin_model_provider_obeys_allow_list(registry):
    from pydantic_ai.models.test import TestModel

    registry.register_plugin("inhouse", lambda r: r.model_providers.add(
        ModelProvider("inhouse", lambda model_id, s: TestModel(custom_output_text=f"hi from {model_id}"))))
    assert llm.split_spec("inhouse:chem-7b") == ("inhouse", "chem-7b")
    with pytest.raises(llm.ProviderNotAllowed):
        llm.build_model("inhouse:chem-7b", Settings(allowed_providers=["bedrock"]))
    s = Settings(allowed_providers=["inhouse"])
    assert isinstance(llm.build_model("inhouse:chem-7b", s), TestModel)
    assert llm.complete(s, "inhouse:chem-7b", system="", user="hello") == "hi from chem-7b"


def test_plugin_access_provider(registry, tmp_path):
    from alchemist.run import Services

    class Directory(AccessModel):
        def __init__(self):
            super().__init__({"plants": ["P01"], "workspaces": {"quality": {}}})

        def user(self, user_id):
            from alchemist.access import User
            return User(user_id, frozenset({"P01"}), False, False, frozenset({"quality"}))

    registry.register_plugin("idp", lambda r: r.access_providers.add(AccessProvider("idp", lambda s: Directory())))
    s = Settings(db_path=str(tmp_path / "db.sqlite"), access_provider="idp", knowledge_root=str(tmp_path / "k"))
    svc = Services(s)
    assert svc.access.run_plants(svc.access.user("anyone"), "quality") == frozenset({"P01"})


def test_plugin_drift():
    from alchemist.run import plugin_drift

    then = [{"name": "builtin", "version": None}, {"name": "a", "version": "1"}, {"name": "b", "version": "1"}]
    now = [{"name": "builtin", "version": None}, {"name": "a", "version": "2"}, {"name": "c", "version": "1"}]
    assert plugin_drift(then, now) == ["b removed", "c added", "a 1 -> 2"]


def test_tool_names_may_not_shadow_builtins(registry, svc):
    from alchemist.run import plugin_tools

    registry.register_plugin("shadow", lambda r: r.tools.add(Tool("run_query", "x", {"type": "object"}, lambda c, i: "")))
    with pytest.raises(ValueError, match="built-in tool names"):
        plugin_tools("analysis", None)


def test_run_with_example_plugin(svc, example_on_path, monkeypatch):
    """A plugin tool, a kernel method and a report renderer, through a real run; recorded in the manifest."""
    from alchemist import prompts
    from alchemist.run import approve, ask

    base_hash = prompts.prompt_version()
    monkeypatch.setenv("ALC_PLUGIN_MODULES", "alchemist_example")
    set_registry(None)
    try:
        d = svc.catalog.latest_card("resin_lots", status=None)
        if d["status"] == "draft":
            svc.catalog.approve("resin_lots", d["version"], reviewer="data_admin")
        plan_turns = [[("define_term", {"term": "mfi"})]] + script.PLAN_TURNS
        out = ask(svc, user="eng_p1", workspace="quality", project="plugins", question=script.QUESTION,
                  scope=script.SCOPE, model=ScriptedModel(plan_turns))
        assert out["status"] == "awaiting_approval"
        analysis_turns = [[("run_python", {"code": "shift = alc.methods.cusum([10.0] * 30 + [13.0] * 30, target=10.0)\nshift",
                                           "purpose": "plugin method"})]] + script.ANALYSIS_TURNS
        from alchemist.run import ScriptedReviewer
        res = approve(svc, out["run_id"], user="eng_p1", model=ScriptedModel(analysis_turns),
                      reviewer=ScriptedReviewer(script.REVIEW))
        assert res["status"] == "completed"
        run = Path(res["path"])
        manifest = read_json(run / "manifest.json")
        assert [p["name"] for p in manifest["plugins"]] == ["builtin", "alchemist_example"]
        assert manifest["model"]["prompts"] != base_hash
        assert any(c["doc"] == "GLOSS-EXT" and c["section"] == "MFI" for c in manifest["knowledge"]["citations"])
        assert (run / "report" / "report.md").read_text().startswith("# ")
        nb = json.loads((run / "notebook.ipynb").read_text())
        out_text = json.dumps([c["outputs"] for c in nb["cells"] if "alc.methods.cusum" in "".join(c["source"])])
        assert "'direction': 'up'" in out_text
        assert "Melt flow index" in (run / "logs" / "plan_transcript.jsonl").read_text()
    finally:
        set_registry(None)
