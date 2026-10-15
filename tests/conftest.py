import os
import uuid

import pytest

from alchemist.catalog import index_dataset
from alchemist.config import Settings
from alchemist.demo.generate import generate


@pytest.fixture(scope="session")
def home(tmp_path_factory):
    return tmp_path_factory.mktemp("alc-home")


@pytest.fixture(scope="session")
def db_target(home):
    """The shared app database: SQLite in the test home, or a fresh PostgreSQL database when
    ALC_TEST_DATABASE_URL points at a server (CI runs the suite both ways)."""
    url = os.environ.get("ALC_TEST_DATABASE_URL")
    if not url:
        yield str(home / "alchemist.db")
        return
    yield from fresh_postgres(url)


def fresh_postgres(url: str):
    from urllib.parse import urlsplit

    import psycopg

    from alchemist.db import close_pools

    name = f"alc_test_{uuid.uuid4().hex[:12]}"
    admin = urlsplit(url)._replace(path="/postgres").geturl()
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"create database {name}")
    try:
        yield urlsplit(url)._replace(path=f"/{name}").geturl()
    finally:
        close_pools()
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f"drop database if exists {name} with (force)")


@pytest.fixture(scope="session")
def svc(home, db_target):
    from alchemist.run import Services

    s = Settings(workspace_root=str(home / "workspace"), db_path=db_target,
                 access_file=str(home / "access.yaml"), knowledge_root=str(home / "knowledge"))
    s.ensure_local_dirs()
    datasets = generate(home)
    svc = Services.__new__(Services)
    svc.settings = s
    from alchemist.access import AccessModel
    from alchemist.catalog import Catalog
    from alchemist.knowledge import Knowledge

    svc.catalog = Catalog(s.db_path)
    svc.access = AccessModel.load(s.access_file)
    svc.knowledge = Knowledge(s.knowledge_root)
    from alchemist.credentials import CredentialBroker

    svc.broker = CredentialBroker(None)
    for ds_id, spec in datasets.items():
        svc.catalog.register(id=ds_id, actor="data_admin", **spec)
        res = index_dataset(svc.catalog, s, ds_id)
        if ds_id != "resin_lots":  # left as a draft until the test approves it
            svc.catalog.approve(ds_id, res["version"], reviewer="data_admin")
    return svc
