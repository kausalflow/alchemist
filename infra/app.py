#!/usr/bin/env python3
"""CDK entry point: `cdk synth -c env=dev` reads config/dev.yaml (or `-c config=path/to/file.yaml`)."""

import aws_cdk as cdk

from alchemist_infra.config import load_config
from alchemist_infra.stack import AlchemistStack

app = cdk.App()
env = app.node.try_get_context("env") or "dev"
cfg = load_config(env, app.node.try_get_context("config"))
AlchemistStack(app, f"Alchemist-{cfg.env_name}", cfg=cfg)
app.synth()
