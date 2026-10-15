"""One stack per environment: network, storage, database, Bedrock, services, run workflow, edge, observability."""

from __future__ import annotations

from aws_cdk import CfnOutput, Environment, Stack, Tags
from constructs import Construct

from .bedrock import BedrockSetup
from .config import EnvConfig
from .database import Database
from .edge import Edge
from .network import Network
from .observability import Observability
from .services import Services
from .storage import Storage
from .workflow import RunWorkflow


class AlchemistStack(Stack):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, **kw) -> None:
        self._azs = (cfg.network.existing.availability_zones if cfg.network.existing
                     else cfg.network.availability_zones or [f"{cfg.region}{z}" for z in "ab"])
        super().__init__(scope, id, env=Environment(account=cfg.account, region=cfg.region),
                         description=f"Alchemist ({cfg.env_name})", **kw)
        for k, v in {"alchemist:env": cfg.env_name, **cfg.tags}.items():
            Tags.of(self).add(k, v)

        self.network = Network(self, "Network", cfg=cfg)
        self.storage = Storage(self, "Storage", cfg=cfg)
        self.database = Database(self, "Database", cfg=cfg, network=self.network, storage=self.storage)
        self.bedrock = BedrockSetup(self, "Bedrock", cfg=cfg, storage=self.storage)
        self.services = Services(self, "Services", cfg=cfg, network=self.network, storage=self.storage,
                                 database=self.database, bedrock=self.bedrock)
        self.workflow = RunWorkflow(self, "Workflow", cfg=cfg, network=self.network, services=self.services)
        self.edge = Edge(self, "Edge", cfg=cfg, storage=self.storage, services=self.services)
        self.observability = Observability(self, "Observability", cfg=cfg, storage=self.storage,
                                           services=self.services, workflow=self.workflow)

        # Endpoint policies: S3 only to Alchemist's buckets and the lake; Bedrock only to allow-listed models.
        s = self.storage
        self.network.limit_s3_endpoint(
            [s.workspace.bucket_name, s.knowledge.bucket_name, s.web.bucket_name, *s.lake_bucket_names], cfg.region)
        self.network.limit_bedrock_endpoint(self.bedrock.endpoint_arns(), self.bedrock.guardrail_arns)

        # Sealing: only run, orchestrator and sealer roles (and break-glass) may write in run folders.
        writers = [self.services.run_role.role_arn, self.services.orchestrator_role.role_arn,
                   self.services.sealer_role.role_arn]
        if self.observability.break_glass_role:
            writers.append(self.observability.break_glass_role.role_arn)
        s.seal_workspace(writers)

        for name, value in {
            "Url": f"https://{cfg.domain.name}",
            "DistributionDomain": self.edge.distribution.distribution_domain_name,
            "AlbDns": self.services.alb.load_balancer_dns_name,
            "WorkspaceBucket": s.workspace.bucket_name,
            "KnowledgeBucket": s.knowledge.bucket_name,
            "WebBucket": s.web.bucket_name,
            "StateMachineArn": self.workflow.state_machine.state_machine_arn,
            "ClusterName": self.services.cluster.cluster_name,
            "IndexerTaskDefinition": self.services.indexer_task.task_definition_arn,
            "DatabaseEndpoint": self.database.cluster.cluster_endpoint.hostname,
            "RunRoleArn": self.services.run_role.role_arn,
            "BrokerFunction": self.services.broker.function_name,
        }.items():
            CfnOutput(self, name, value=value)

    @property
    def availability_zones(self) -> list[str]:
        # Named in the config, so synth never needs an AWS lookup.
        return self._azs
