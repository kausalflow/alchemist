import pytest

from alchemist.catalog import index_dataset
from alchemist.config import Settings
from alchemist.demo.generate import generate


@pytest.fixture(scope="session")
def home(tmp_path_factory):
    return tmp_path_factory.mktemp("alc-home")


@pytest.fixture(scope="session")
def svc(home):
    from alchemist.run import Services

    s = Settings(workspace_root=str(home / "workspace"), db_path=str(home / "alchemist.db"),
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
    for ds_id, spec in datasets.items():
        svc.catalog.register(id=ds_id, actor="data_admin", **spec)
        res = index_dataset(svc.catalog, s, ds_id)
        if ds_id != "resin_lots":  # left as a draft until the test approves it
            svc.catalog.approve(ds_id, res["version"], reviewer="data_admin")
    return svc
