"""Optional modules, infrastructure plugins, and app plugins installed into the image."""

import json
from pathlib import Path

import pytest

from alchemist_infra.config import EnvConfig

from .conftest import dumps, find, synth


def _cfg(dev_cfg, **sections):
    data = dev_cfg.model_dump()
    for k, v in sections.items():
        data[k] = {**data.get(k, {}), **v} if isinstance(v, dict) else v
    return EnvConfig.model_validate(data)


def test_edge_and_observability_can_be_left_out(dev_cfg):
    t = synth(_cfg(dev_cfg, modules={"edge": False, "observability": False, "indexers": False}))
    t.resource_count_is("AWS::CloudFront::Distribution", 0)
    t.resource_count_is("AWS::CloudWatch::Alarm", 0)
    t.resource_count_is("AWS::CloudTrail::Trail", 0)
    t.resource_count_is("AWS::Events::Rule", 0)
    # The core and its security rules stay.
    t.resource_count_is("AWS::StepFunctions::StateMachine", 1)
    t.resource_count_is("AWS::EC2::NatGateway", 0)


def test_infra_plugin_adds_resources_and_widens_only_the_s3_endpoint(dev_cfg):
    t = synth(_cfg(dev_cfg, plugins={"infra": ["tests.sample_infra_plugin"]}))
    r = t.to_json()["Resources"]
    assert any("PluginKilnExport" in k and v["Type"] == "AWS::S3::Bucket" for k, v in r.items())
    task = next(v for k, v in r.items() if v["Type"] == "AWS::ECS::TaskDefinition" and "KilnExport" in k)
    assert task["Properties"]["Family"] == "alchemist-dev-kiln-export"
    s3_ep = next(v for k, v in r.items() if v["Type"] == "AWS::EC2::VPCEndpoint" and k.startswith("NetworkVpcS3"))
    bucket_id = next(k for k, v in r.items() if v["Type"] == "AWS::S3::Bucket" and "PluginKilnExport" in k)
    assert bucket_id in dumps(s3_ep["Properties"]["PolicyDocument"])
    assert t.to_json()["Outputs"]["InfraPlugins"]["Value"] == "tests.sample_infra_plugin"


def test_unknown_infra_plugin_fails_synth(dev_cfg):
    with pytest.raises(ModuleNotFoundError):
        synth(_cfg(dev_cfg, plugins={"infra": ["no_such_plugin_module"]}))


def test_app_plugin_settings_reach_every_container(dev_cfg):
    t = synth(_cfg(dev_cfg, plugins={"enabled": ["acme"], "modules": ["acme_site.kiln"],
                                     "packages": ["acme-alchemist==1.2.0"], "env": {"ACME_MODE": "strict"}}))
    for td in find(t.to_json()["Resources"], "AWS::ECS::TaskDefinition").values():
        env = {e["Name"]: e["Value"] for e in td["Properties"]["ContainerDefinitions"][0]["Environment"]}
        assert env["ALC_PLUGINS"] == "acme" and env["ALC_PLUGIN_MODULES"] == "acme_site.kiln"
        assert env["ALC_EXTRA_PACKAGES"] == "acme-alchemist==1.2.0" and env["ACME_MODE"] == "strict"


def test_plugin_packages_become_an_image_build_argument(dev_cfg, tmp_path):
    import aws_cdk as cdk

    from alchemist_infra.stack import AlchemistStack

    app = cdk.App(outdir=str(tmp_path))
    AlchemistStack(app, "Alchemist-dev", cfg=_cfg(dev_cfg, plugins={"packages": ["acme-alchemist==1.2.0"]}))
    app.synth()
    assets = json.loads(Path(tmp_path, "Alchemist-dev.assets.json").read_text())["dockerImages"]
    (image,) = assets.values()
    assert image["source"]["dockerBuildArgs"] == {"EXTRA_PACKAGES": "acme-alchemist==1.2.0"}


def test_default_loads_only_built_in_app_plugins(resources):
    api = next(v for k, v in resources.items() if v["Type"] == "AWS::ECS::TaskDefinition" and "ApiTask" in k)
    env = {e["Name"]: e["Value"] for e in api["Properties"]["ContainerDefinitions"][0]["Environment"]}
    assert env["ALC_PLUGINS"] == "none" and env["ALC_ACCESS_PROVIDER"] == "alb-oidc"


@pytest.mark.parametrize("plugins,image,msg", [
    ({"packages": ["x==1"]}, {"ecr_repository_arn": "arn:aws:ecr:eu-central-1:111111111111:repository/a",
                               "digest": "sha256:" + "0" * 64}, "bake them"),
    ({"env": {"ALC_PLUGINS": "*"}}, {}, "plugins.enabled"),
])
def test_plugin_config_mistakes_are_rejected(dev_cfg, plugins, image, msg):
    with pytest.raises(ValueError, match=msg):
        _cfg(dev_cfg, plugins=plugins, image=image)
