"""The app database layer: migrations, placeholders and compare-and-set, on SQLite and (when a server is given in
ALC_TEST_DATABASE_URL) PostgreSQL."""

import os
import threading

import pytest

from alchemist import db as dbmod
from alchemist.catalog import Catalog
from alchemist.config import Settings

BACKENDS = ["sqlite"] + (["postgres"] if os.environ.get("ALC_TEST_DATABASE_URL") else [])


@pytest.fixture(params=BACKENDS)
def target(request, tmp_path):
    if request.param == "sqlite":
        yield str(tmp_path / "db.sqlite")
        return
    from conftest import fresh_postgres

    yield from fresh_postgres(os.environ["ALC_TEST_DATABASE_URL"])


def test_migrations_apply_once(target):
    a = dbmod.connect(target)
    b = dbmod.connect(target)  # a second open finds everything applied
    versions = [r["version"] for r in a.execute("select version from schema_migrations order by version")]
    assert versions == [m[0] for m in dbmod.MIGRATIONS]
    a.close(), b.close()


def test_catalog_round_trip(target):
    cat = Catalog(target)
    cat.register(id="ds1", s3_prefix="s3://lake/a/", file_glob="*.parquet", file_format="parquet", plant="P1",
                 metadata_uri=None)
    cat.register(id="ds1", s3_prefix="s3://lake/b/", file_glob="*.parquet", file_format="parquet", plant="P1",
                 metadata_uri=None)  # registering again replaces the row
    assert [d["s3_prefix"] for d in cat.datasets()] == ["s3://lake/b"]
    card = {"schema": "alchemist.card/v1", "description": "reactor temperature 100% of the time", "columns": []}
    v = cat.add_draft("ds1", card, "etag", "test", [])
    assert cat.add_draft("ds1", card, "etag2", "test", []) == v + 1
    cat.approve("ds1", v, reviewer="admin")
    with pytest.raises(ValueError):
        cat.approve("ds1", v, reviewer="admin")  # no longer a draft
    assert cat.latest_card("ds1")["version"] == v
    hits = cat.search("reactor temperature", {"P1"})
    assert [h["dataset"] for h in hits] == ["ds1"]
    assert cat.search("reactor", {"P2"}) == []
    assert cat.suggest("ds1", "u", "fix", "unit?") < cat.suggest("ds1", "u", "fix", "range?")
    cat.db.close()


def test_run_index_and_compare_and_set(target):
    cat = Catalog(target)
    cat.upsert_run(run_id="R1", path="s3://ws/r1", status="awaiting_approval", user_id="u1")
    cat.upsert_run(run_id="R1", status="awaiting_approval", plants='["P1"]')  # partial update keeps the rest
    assert cat.run("R1")["path"] == "s3://ws/r1"

    results = []
    barrier = threading.Barrier(8)

    def approve():
        c = Catalog(target)
        try:
            barrier.wait()
            results.append(c.transition("R1", "awaiting_approval", "running"))
        finally:
            c.db.close()

    threads = [threading.Thread(target=approve) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1  # exactly one of the racing approvals wins
    assert cat.run("R1")["status"] == "running"
    assert [r["run_id"] for r in cat.active_runs(user_id="u1")] == ["R1"]
    cat.db.close()


def test_transaction_rolls_back(target):
    cat = Catalog(target)
    with pytest.raises(RuntimeError):
        with cat.db.transaction():
            cat.db.execute("insert into audit_events(at, actor, action, target) values (?,?,?,?)", ("t", "a", "x", "y"))
            raise RuntimeError
    assert cat.db.execute("select count(*) as n from audit_events").fetchone()["n"] == 0
    cat.db.close()


def test_settings_build_a_postgres_url_from_the_stack_variables(monkeypatch):
    monkeypatch.delenv("ALC_DB", raising=False)
    monkeypatch.setenv("ALC_DB_HOST", "db.cluster.local")
    monkeypatch.setenv("ALC_DB_PORT", "5432")
    monkeypatch.setenv("ALC_DB_NAME", "alchemist")
    monkeypatch.setenv("ALC_DB_USER", "alchemist_admin")
    monkeypatch.setenv("ALC_DB_PASSWORD", "s3cr:t@")
    s = Settings()
    assert s.db_path == "postgresql://alchemist_admin@db.cluster.local:5432/alchemist?sslmode=require"
    assert "s3cr" not in s.db_path  # the password is added at connect time only
    info = dbmod._conninfo(s.db_path)
    assert "password" in info and "application_name=alchemist" in info
