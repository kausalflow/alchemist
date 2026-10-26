"""Data sources: AVEVA PI (against an in-process PI Web API) and lake tables, synced into the lake and the catalog."""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
import yaml

pytest.importorskip("penguineer.connectors")

from fake_piwebapi import PASSWORD, T0, USER, FakePIWebAPI  # noqa: E402

from alchemist import sources  # noqa: E402
from alchemist.catalog import Catalog, sample_dataset  # noqa: E402
from alchemist.config import Settings  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    s = Settings(workspace_root=str(tmp_path / "ws"), db_path=str(tmp_path / "alc.db"),
                 access_file=str(tmp_path / "access.yaml"), knowledge_root=str(tmp_path / "knowledge"))
    s.ensure_local_dirs()
    return s, Catalog(s.db_path), tmp_path / "lake"


def pi_config(server, lake, **extra) -> sources.SourcesConfig:
    return sources.parse({"lake": str(lake), "sources": [{
        "id": "p01-pi", "plant": "P01", "kind": "pi", "url": server.url, "auth": "basic", "username": USER,
        "data_server": "PISRV", "tags": ["FIC101.PV", "TI102.PV", "NOPE.PV"],
        "event_frames": {"database": "\\\\AFSRV\\Plant", "template": "Batch"}, **extra}]}, "unused")


def test_config_is_checked():
    ok = {"id": "a", "plant": "P1", "kind": "lake", "source": "/x/*.parquet"}
    assert sources.parse({"sources": [ok]}, "/lake").get("a").tables == ("historian",)
    for bad, match in [({**ok, "id": "Has Space"}, "id"), ({**ok, "plant": None}, "plant"),
                       ({**ok, "kind": "opcua"}, "kind"), ({**ok, "table": "events"}, "table"),
                       ({**ok, "storage_options": {"aws_secret_access_key": "x"}}, "credential"),
                       ({"id": "b", "plant": "P1", "kind": "pi", "url": "https://h/piwebapi", "password": "x",
                         "tags": ["t"]}, "credential"),
                       ({"id": "b", "plant": "P1", "kind": "pi", "url": "https://h/piwebapi"}, "tags")]:
        with pytest.raises(sources.SourceError, match=match):
            sources.parse({"sources": [bad]}, "/lake")
    with pytest.raises(sources.SourceError, match="duplicate"):
        sources.parse({"sources": [ok, ok]}, "/lake")
    assert sources.parse_duration("30d") == timedelta(days=30)
    assert sources.parse_time("2026-10-01T02:00") == datetime(2026, 10, 1, 2, tzinfo=UTC)


def test_pi_sync_writes_days_registers_and_drafts(env, monkeypatch):
    s, catalog, lake = env
    monkeypatch.setenv("P01_PI_PASSWORD", PASSWORD)
    monkeypatch.delenv("PI_WEBAPI_PASSWORD", raising=False)
    with FakePIWebAPI(auth="basic") as server:
        cfg = pi_config(server, lake, password_env="P01_PI_PASSWORD")
        src = cfg.get("p01-pi")
        assert sources.check(src)["tags_found"] == 2
        res = sources.sync(cfg, src, catalog, s, start=T0 + timedelta(hours=1), end=T0 + timedelta(hours=4))
    assert "PI_WEBAPI_PASSWORD" not in __import__("os").environ  # the source's variable was only borrowed
    assert res.start == T0.isoformat()  # whole days
    assert res.rows["historian"] == 240 + 62 and res.files == {"historian": 1, "lots": 1}
    assert any("NOPE.PV" in n for n in res.notes)

    day = pd.read_parquet(lake / "P01" / "p01-pi" / "historian" / "2026-10-01.parquet")
    assert list(day.columns) == ["timestamp", "tag", "value", "quality"]
    assert str(day["timestamp"].dt.tz) == "UTC"
    timeout = day[(day.tag == "FIC101.PV") & (day.timestamp == pd.Timestamp(T0 + timedelta(minutes=30)))]
    assert timeout.value.isna().all() and timeout.quality.iloc[0] == "I/O Timeout"
    lots = pd.read_parquet(lake / "P01" / "p01-pi" / "lots" / "lots.parquet")
    assert list(lots.lot) == ["L-001", "L-002"]  # L-003 starts at the end of the window

    meta = yaml.safe_load((lake / "P01" / "p01-pi" / "historian" / "_metadata.yaml").read_text())
    assert meta["tag_dictionary"]["FIC101.PV"] == {"description": "Feed flow", "unit": "m3/h", "range_low": 0.0,
                                                   "range_high": 50.0, "path": "\\\\PISRV\\FIC101.PV"}
    hist = catalog.dataset("p01-pi-historian")
    assert (hist["plant"], hist["file_glob"]) == ("P01", "*.parquet")
    card = catalog.latest_card("p01-pi-historian", status=None)
    assert card["status"] == "draft"  # an admin approves before anyone can query it
    assert card["card"]["time_zone"] == "UTC" and card["card"]["join_keys"] == ["tag", "timestamp"]
    assert {c["name"]: c.get("unit") for c in card["card"]["columns"]}["value"] == "per tag"
    assert catalog.queryable({"P01"}) == []
    files, sample = sample_dataset(catalog, hist)
    assert len(files) == 1 and len(sample) == 200 and "tag" in sample.columns

    state = sources.state(cfg, src)
    assert state["synced_to"] == (T0 + timedelta(hours=4)).isoformat()
    assert sources.describe(cfg)[0]["datasets"] == ["p01-pi-historian", "p01-pi-lots"]


def test_pi_resync_is_idempotent_and_continues(env, monkeypatch):
    s, catalog, lake = env
    monkeypatch.setenv("PI_WEBAPI_PASSWORD", PASSWORD)
    with FakePIWebAPI(auth="basic") as server:
        cfg = pi_config(server, lake)
        src = cfg.get("p01-pi")
        sources.sync(cfg, src, catalog, s, start=T0, end=T0 + timedelta(hours=2))
        first = (lake / "P01" / "p01-pi" / "historian" / "2026-10-01.parquet").read_bytes()
        meta = catalog.latest_card("p01-pi-historian", status=None)["version"]
        # The next sync starts where the last ended, re-reads the whole day and replaces it.
        res = sources.sync(cfg, src, catalog, s, end=T0 + timedelta(hours=4))
    assert res.start == T0.isoformat()
    second = pd.read_parquet(lake / "P01" / "p01-pi" / "historian" / "2026-10-01.parquet")
    assert len(second) == 240 + 62 and len(first) < len(second.to_parquet())
    assert catalog.latest_card("p01-pi-historian", status=None)["version"] == meta  # same metadata, no new draft


def test_lake_source_wide_local_time_to_long_utc(env, tmp_path):
    s, catalog, lake = env
    src_dir = tmp_path / "export"
    src_dir.mkdir()
    times = pd.date_range("2026-03-29 00:00", periods=6, freq="h")  # Berlin switches to summer time at 02:00
    pd.DataFrame({"Zeit": times.strftime("%Y-%m-%d %H:%M"), "TI-1": range(6), "FI-2": [1.5] * 6}).to_csv(
        src_dir / "hist.csv", index=False)
    cfg = sources.parse({"lake": str(lake), "sources": [{
        "id": "p02-csv", "plant": "P02", "kind": "lake", "source": str(src_dir / "*.csv"),
        "columns": {"timestamp": "Zeit"}, "timezone": "Europe/Berlin", "backfill": "3d"}]}, "unused")
    res = sources.sync(cfg, cfg.get("p02-csv"), catalog, s, start=datetime(2026, 3, 28, tzinfo=UTC),
                       end=datetime(2026, 3, 30, tzinfo=UTC))
    assert res.files == {"historian": 2}  # 00:00 and 01:00 Berlin are still 28 March in UTC
    d28 = pd.read_parquet(lake / "P02" / "p02-csv" / "historian" / "2026-03-28.parquet")
    assert set(d28.tag) == {"TI-1", "FI-2"} and d28.timestamp.max() == pd.Timestamp("2026-03-28 23:00", tz="UTC")
    assert list(d28.columns) == ["timestamp", "tag", "value", "quality"]
    meta = yaml.safe_load((lake / "P02" / "p02-csv" / "historian" / "_metadata.yaml").read_text())
    assert meta["source_columns"]["timestamp"] == "Zeit"
    assert catalog.dataset("p02-csv-historian")["plant"] == "P02"


def test_sync_to_s3(env, monkeypatch, tmp_path):
    pytest.importorskip("moto")
    import boto3
    from cloudpathlib import S3Client
    from moto.server import ThreadedMotoServer

    s, catalog, _ = env
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    try:
        host, port = server.get_host_and_port()
        for k, v in {"AWS_ENDPOINT_URL": f"http://{host}:{port}", "AWS_ACCESS_KEY_ID": "test",
                     "AWS_SECRET_ACCESS_KEY": "test", "AWS_REGION": "eu-central-1",
                     "AWS_DEFAULT_REGION": "eu-central-1", "PI_WEBAPI_PASSWORD": PASSWORD}.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        monkeypatch.setattr(S3Client, "_default_client", None)
        boto3.client("s3").create_bucket(Bucket="lake", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"})
        with FakePIWebAPI(auth="basic") as pi_server:
            cfg = pi_config(pi_server, "s3://lake/plants")
            sources.sync(cfg, cfg.get("p01-pi"), catalog, s, start=T0, end=T0 + timedelta(hours=4))
        keys = sorted(o["Key"] for o in boto3.client("s3").list_objects_v2(Bucket="lake")["Contents"])
        assert "plants/P01/p01-pi/historian/2026-10-01.parquet" in keys and "plants/P01/p01-pi/_sync.json" in keys
        files, sample = sample_dataset(catalog, catalog.dataset("p01-pi-historian"))
        assert str(files[0]).startswith("s3://lake/") and len(sample) > 0
    finally:
        server.stop()


def test_cli_init_list_and_sync(tmp_path, monkeypatch, capsys):
    from alchemist.cli import main

    monkeypatch.setenv("ALC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PI_WEBAPI_PASSWORD", PASSWORD)
    for k in ("ALC_SOURCES", "ALC_LAKE", "ALC_DB", "ALC_WORKSPACE", "ALC_ACCESS", "ALC_KNOWLEDGE"):
        monkeypatch.delenv(k, raising=False)
    main(["sources", "init"])
    assert "p01-pi" in (tmp_path / "home" / "sources.yaml").read_text()
    with FakePIWebAPI(auth="basic") as server:
        cfg = {"sources": [{"id": "p01-pi", "plant": "P01", "kind": "pi", "url": server.url, "auth": "basic",
                            "username": USER, "data_server": "PISRV", "tags": ["FIC101.PV"]}]}
        (tmp_path / "home" / "sources.yaml").write_text(yaml.safe_dump(cfg))
        main(["sources", "check"])
        main(["sources", "sync", "--start", "2026-10-01T00:00Z", "--end", "2026-10-01T04:00Z"])
    capsys.readouterr()
    main(["sources", "list"])
    out = capsys.readouterr().out
    assert '"synced_to": "2026-10-01T04:00:00+00:00"' in out
    assert (tmp_path / "home" / "lake" / "P01" / "p01-pi" / "historian" / "2026-10-01.parquet").exists()
