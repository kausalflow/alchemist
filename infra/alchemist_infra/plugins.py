"""Infrastructure plugins: extra AWS resources installed alongside the core stack.

A plugin is an importable module (named in `plugins.infra` in the environment file, or found through the entry-point
group `alchemist.infra`) with a function

    def register(ctx: InfraContext) -> None: ...

It runs after the core constructs and before the endpoint and sealing policies are finalised, so it can add a task
or a service, a bucket the S3 endpoint must allow, or a role that may write in run folders. Example:

    from aws_cdk import aws_iam as iam, aws_s3 as s3
    from alchemist_infra.config import TaskSize

    def register(ctx):
        scope = ctx.scope("KilnReports")
        bucket = s3.Bucket(scope, "Exports", encryption_key=ctx.storage.key, ...)
        ctx.allow_s3_bucket(bucket.bucket_name)
        role = iam.Role(scope, "Role", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))
        ctx.services.add_task("KilnExport", "kiln-export", TaskSize(cpu=512, memory_mib=1024), role,
                              ["kiln", "export"], ctx.services.common_env, {})
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Callable

from constructs import Construct

if TYPE_CHECKING:
    from .bedrock import BedrockSetup
    from .config import EnvConfig
    from .database import Database
    from .network import Network
    from .services import Services
    from .storage import Storage
    from .workflow import RunWorkflow

ENTRY_POINT_GROUP = "alchemist.infra"


@dataclass
class InfraContext:
    cfg: "EnvConfig"
    stack: Construct
    network: "Network"
    storage: "Storage"
    database: "Database"
    bedrock: "BedrockSetup"
    services: "Services"
    workflow: "RunWorkflow"
    extra_s3_buckets: list[str] = field(default_factory=list)
    extra_run_writer_arns: list[str] = field(default_factory=list)
    loaded: list[str] = field(default_factory=list)

    def scope(self, name: str) -> Construct:
        """A construct scope of its own for a plugin, so its logical ids never clash with the core."""
        return Construct(self.stack, f"Plugin{name}")

    def allow_s3_bucket(self, bucket_name: str) -> None:
        """Let tasks reach this bucket through the S3 gateway endpoint (which otherwise allows only Alchemist's)."""
        self.extra_s3_buckets.append(bucket_name)

    def allow_run_writer(self, role_arn: str) -> None:
        """Let this role write inside run folders. Use sparingly: every writer weakens sealing."""
        self.extra_run_writer_arns.append(role_arn)


def _resolve(spec: str) -> Callable[[InfraContext], None]:
    module, _, attr = spec.partition(":")
    fn = getattr(importlib.import_module(module), attr or "register", None)
    if not callable(fn):
        raise ValueError(f"infrastructure plugin {spec!r} has no register(ctx) function")
    return fn


def load_plugins(ctx: InfraContext) -> list[str]:
    specs = [(s, _resolve(s)) for s in ctx.cfg.plugins.infra]
    if ctx.cfg.plugins.discover_infra_entry_points:
        for ep in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
            specs.append((f"{ep.name} ({ep.value})", ep.load()))
    for name, fn in specs:
        fn(ctx)
        ctx.loaded.append(name)
    return ctx.loaded
