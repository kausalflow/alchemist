import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from alchemist_infra.config import load_config
from alchemist_infra.stack import AlchemistStack


def synth(cfg) -> Template:
    app = cdk.App()
    stack = AlchemistStack(app, f"Alchemist-{cfg.env_name}", cfg=cfg)
    return Template.from_stack(stack)


@pytest.fixture(scope="session")
def dev_cfg():
    return load_config("dev")


@pytest.fixture(scope="session")
def template(dev_cfg) -> Template:
    return synth(dev_cfg)


@pytest.fixture(scope="session")
def resources(template) -> dict:
    return template.to_json()["Resources"]


@pytest.fixture(scope="session")
def separate_resources(dev_cfg) -> dict:
    """The target design: a separate sandbox task with broker credentials."""
    data = dev_cfg.model_dump()
    data["workflow"]["sandbox_mode"] = "separate"
    from alchemist_infra.config import EnvConfig

    return synth(EnvConfig.model_validate(data)).to_json()["Resources"]


@pytest.fixture(scope="session")
def queue_resources(dev_cfg) -> dict:
    """Workers taking steps from the run queue instead of a Step Functions execution per run."""
    data = dev_cfg.model_dump()
    data["workflow"]["launcher"] = "queue"
    data["workflow"]["workers"] = {"min_count": 1, "max_count": 3, "concurrency": 2}
    from alchemist_infra.config import EnvConfig

    return synth(EnvConfig.model_validate(data)).to_json()["Resources"]


def find(resources: dict, type_: str, contains: str = "") -> dict[str, dict]:
    return {k: v for k, v in resources.items() if v["Type"] == type_ and contains in k}


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True)
