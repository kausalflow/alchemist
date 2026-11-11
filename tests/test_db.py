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
    assert s.db_path == "postgresql://alchemist_admin@db.cluster.local:5432/alchemist?sslmode=verify-full"
    assert "s3cr" not in s.db_path  # the password is added at connect time only
    info = dbmod._conninfo(s.db_path)
    assert "password" in info and "application_name=alchemist" in info


# ---------------------------------------------------------------- TLS (PA-22)

def _params(conninfo: str) -> dict:
    from psycopg.conninfo import conninfo_to_dict

    return conninfo_to_dict(conninfo)


def test_the_stack_url_verifies_the_server_certificate(monkeypatch):
    for k in ("ALC_DB", "ALC_DB_SSLMODE", "ALC_DB_SSLROOTCERT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALC_DB_HOST", "db.cluster.local")
    p = _params(dbmod._conninfo(dbmod.database_url()))
    assert p["sslmode"] == "verify-full" and p["sslrootcert"] == "system"
    monkeypatch.setenv("ALC_DB_SSLROOTCERT", r"C:\alc\rds ca.pem")  # a Windows path needs no URL quoting
    assert _params(dbmod._conninfo(dbmod.database_url()))["sslrootcert"] == r"C:\alc\rds ca.pem"
    # A URL that names its own CA keeps it.
    assert _params(dbmod._conninfo("postgresql://u@h/d?sslmode=verify-full&sslrootcert=/x.pem"))["sslrootcert"] == "/x.pem"


def test_an_unverified_connection_to_a_remote_host_is_logged(monkeypatch, caplog):
    monkeypatch.delenv("ALC_DB_SSLROOTCERT", raising=False)
    with caplog.at_level("WARNING", logger="alchemist.db"):
        dbmod._conninfo("postgresql://u@localhost/d")
        assert not caplog.records  # a laptop or test server
        dbmod._conninfo("postgresql://u@db.plant.internal/d?sslmode=require")
    assert "does not check the server's certificate" in caplog.text


@pytest.mark.skipif(not os.environ.get("ALC_TEST_DATABASE_CA"),
                    reason="needs ALC_TEST_DATABASE_CA: the CA of a TLS server at ALC_TEST_DATABASE_URL")
def test_verify_full_against_a_real_server(monkeypatch, tmp_path):
    import psycopg

    url = os.environ["ALC_TEST_DATABASE_URL"]
    monkeypatch.setenv("ALC_DB_SSLROOTCERT", os.environ["ALC_TEST_DATABASE_CA"])
    sep = "&" if "?" in url else "?"
    with psycopg.connect(dbmod._conninfo(url + sep + "sslmode=verify-full")) as c:
        assert c.execute("select ssl from pg_stat_ssl where pid = pg_backend_pid()").fetchone()[0]
    # A CA that did not sign the server's certificate is refused.
    other = tmp_path / "other-ca.pem"
    other.write_text(_self_signed_ca())
    monkeypatch.setenv("ALC_DB_SSLROOTCERT", str(other))
    with pytest.raises(psycopg.OperationalError, match="certificate verify failed"):
        psycopg.connect(dbmod._conninfo(url + sep + "sslmode=verify-full"))


def _self_signed_ca() -> str:
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "not-the-db-ca")])
    now = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# ---------------------------------------------------------------- least-privilege app login (PA-22)

def test_scram_verifier_matches_rfc7677():
    # RFC 7677 section 3: user "user", password "pencil", salt W22ZaJ0SNY7soEsUEjb6gQ==, 4096 iterations.
    v = dbmod.scram_verifier("pencil", salt=__import__("base64").b64decode("W22ZaJ0SNY7soEsUEjb6gQ=="))
    assert v.startswith("SCRAM-SHA-256$4096:W22ZaJ0SNY7soEsUEjb6gQ==$")
    stored, server = v.split("$")[2].split(":")
    assert server == "wfPLwcE6nTWhTAmQ7tl2KeoiWGPlZqQxSrmfPwDl2dU="
    assert stored == "WG5d8oPm3OtcPnkdi4Uo7BkeZkBFzpcXkuLmtbsT4qY="


@pytest.fixture
def owner_and_app(request):
    """A fresh database migrated by the owner login, and a new app login for it (dropped afterwards)."""
    if not os.environ.get("ALC_TEST_DATABASE_URL"):
        pytest.skip("needs a PostgreSQL server in ALC_TEST_DATABASE_URL")
    import uuid
    from urllib.parse import urlsplit

    import psycopg
    from conftest import fresh_postgres

    gen = fresh_postgres(os.environ["ALC_TEST_DATABASE_URL"])
    owner_url = next(gen)
    app_user, password = f"alc_app_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    parts = urlsplit(owner_url)
    netloc = f"{app_user}:{password}@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    app_url = parts._replace(netloc=netloc).geturl()
    try:
        yield owner_url, app_url, app_user, password
    finally:
        dbmod.close_pools()
        try:
            next(gen)
        except StopIteration:
            pass
        with psycopg.connect(os.environ["ALC_TEST_DATABASE_URL"], autocommit=True) as c:
            c.execute(f"drop role if exists {app_user}")


def test_the_app_login_reads_and_writes_rows_but_not_the_schema(owner_and_app):
    import psycopg

    owner_url, app_url, app_user, password = owner_and_app
    owner = dbmod.connect(owner_url)
    done = dbmod.grant_app_login(owner.conn, app_user, password)
    assert f"created login {app_user}" in done
    assert dbmod.grant_app_login(owner.conn, app_user, password)[0] == f"set the password of {app_user}"  # rerun
    owner.close()

    cat = Catalog(app_url)  # opening finds the schema current and does not try to migrate
    cat.upsert_run(run_id="R1", path="s3://ws/r1", status="awaiting_approval", user_id="u1")
    assert cat.run("R1")["status"] == "awaiting_approval"
    assert cat.suggest("ds1", "u", "fix", "unit?")  # identity columns: the app may use their sequences
    for ddl in ("create table x (a int)", "drop table runs", "alter table runs add column y int",
                "insert into schema_migrations(version, name) values (999, 'x')", "truncate runs",
                f"create role {app_user}_2", "create schema x"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), cat.db.transaction():
            cat.db.execute(ddl)
    cat.db.close()


def test_the_app_login_says_when_the_schema_is_behind(owner_and_app):
    owner_url, app_url, app_user, password = owner_and_app
    owner = dbmod.connect(owner_url)
    dbmod.grant_app_login(owner.conn, app_user, password)
    owner.execute("delete from schema_migrations where version = ?", (dbmod.LATEST,))
    owner.close()
    with pytest.raises(dbmod.SchemaBehind, match=f"db migrate --app-user {app_user}"):
        dbmod.connect(app_url)


def test_grant_refuses_a_login_stronger_than_the_app_needs(owner_and_app):
    owner_url, _, app_user, _ = owner_and_app
    owner = dbmod.connect(owner_url)
    with pytest.raises(ValueError, match="must not be the owner"):
        dbmod.grant_app_login(owner.conn, owner.conn.info.user)
    owner.execute(f"create role {app_user} login createdb")
    with pytest.raises(ValueError, match="too powerful.*createdb"):
        dbmod.grant_app_login(owner.conn, app_user)
    owner.close()


def test_db_migrate_as_the_owner_sets_up_the_apps_own_login(owner_and_app, monkeypatch, capsys):
    """The Kubernetes and AWS migrate step: connect as --owner-user, turn ALC_DB_USER/PASSWORD into the app login."""
    from urllib.parse import urlsplit

    from alchemist import cli

    owner_url, app_url, app_user, password = owner_and_app
    owner = urlsplit(owner_url)
    app = urlsplit(app_url)
    monkeypatch.setenv("ALC_DB", app._replace(netloc=app.netloc.replace(f":{password}@", "@")).geturl())
    monkeypatch.setenv("ALC_DB_PASSWORD", password)
    monkeypatch.setenv("ALC_DB_OWNER_PASSWORD", owner.password)
    monkeypatch.delenv("ALC_DB_APP_PASSWORD", raising=False)
    cli.main(["db", "migrate", "--owner-user", owner.username])
    out = capsys.readouterr().out
    assert f"created login {app_user}" in out and f"set the password of {app_user}" in out
    dbmod.close_pools()
    cat = Catalog(os.environ["ALC_DB"])  # the app login, password from ALC_DB_PASSWORD
    assert cat.db.execute("select current_user as u").fetchone()["u"] == app_user
    cat.db.close()
