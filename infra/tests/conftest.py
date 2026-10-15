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


def find(resources: dict, type_: str, contains: str = "") -> dict[str, dict]:
    return {k: v for k, v in resources.items() if v["Type"] == type_ and contains in k}


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True)
