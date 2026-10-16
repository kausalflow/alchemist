from alchemist.catalog import heuristic_draft


def test_heuristic_draft_maps_free_form_metadata():
    meta = {"summary": "Lots", "tz": "UTC", "granularity": "per lot", "join_on": ["lot_id"],
            "fields": {"yield": {"units": "%", "min": 0, "max": 100}, "operator": {"pii": True},
                       "crew": {"pseudonymize": True}, "ghost": {"desc": "not in data"}}}
    card = heuristic_draft("q", meta, {"yield": "DOUBLE", "operator": "VARCHAR", "crew": "VARCHAR", "speed": "DOUBLE"})
    cols = {c["name"]: c for c in card["columns"]}
    assert card["time_zone"] == "UTC" and card["join_keys"] == ["lot_id"]
    assert cols["yield"]["unit"] == "%" and cols["yield"]["valid_max"] == 100
    assert cols["operator"]["exposure"] == "never_to_model"
    assert cols["crew"]["exposure"] == "pseudonymise"
    assert "unit for speed" in card["missing"]
    assert any("ghost" in m for m in card["missing"])


def test_search_respects_plants_and_approval(svc):
    names = {h["dataset"] for h in svc.catalog.search("historian barrel temperature", {"P01"})}
    assert "historian_line2" in names and "historian_line5" not in names
    assert "historian_line5" in {h["dataset"] for h in svc.catalog.search("historian barrel temperature", {"P02"})}
    # resin_lots is still a draft, so it is not queryable
    assert "resin_lots" not in {d["dataset"]["id"] for d in svc.catalog.queryable({"P01"})}
    draft = svc.catalog.latest_card("resin_lots", status=None)
    svc.catalog.approve("resin_lots", draft["version"], reviewer="data_admin")
    assert "resin_lots" in {d["dataset"]["id"] for d in svc.catalog.queryable({"P01"})}


def test_reindex_only_when_metadata_changes(svc):
    from alchemist.catalog import index_dataset

    assert index_dataset(svc.catalog, svc.settings, "historian_line2")["status"] == "unchanged"


def test_demo_setup_can_be_rerun_on_the_same_home(tmp_path, monkeypatch, capsys):
    from alchemist import cli

    for var in ("ALC_DB", "ALC_DB_HOST", "ALC_WORKSPACE", "ALC_ACCESS", "ALC_KNOWLEDGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ALC_HOME", str(tmp_path))
    cli.main(["demo", "setup", "--no-approve"])
    cli.main(["demo", "setup"])  # unchanged drafts from the first setup get approved
    cli.main(["demo", "setup"])  # and a third run leaves the approved cards alone
    out = capsys.readouterr().out
    assert "historian_line2: card v1 draft;" in out
    assert out.count("historian_line2: card v1 approved; unchanged since the last setup") == 2
