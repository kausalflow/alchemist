"""Aurora PostgreSQL Serverless v2 for the catalog, knowledge passages (pgvector) and the run index.

The database only indexes the workspace bucket, so it can be rebuilt from the run folders. The `vector` extension is
created by the app's migration task (`alchemist db migrate`), not by CloudFormation.
"""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_rds as rds
from constructs import Construct

from .config import EnvConfig
from .network import Network
from .storage import Storage

DB_NAME = "alchemist"


class Database(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, network: Network, storage: Storage) -> None:
        super().__init__(scope, id)
        d = cfg.database
        self.cluster = rds.DatabaseCluster(
            self, "Cluster",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_17_5),
            default_database_name=DB_NAME,
            credentials=rds.Credentials.from_generated_secret("alchemist_admin", secret_name=f"{cfg.prefix}/db"),
            writer=rds.ClusterInstance.serverless_v2("Writer"),
            serverless_v2_min_capacity=d.min_acu, serverless_v2_max_capacity=d.max_acu,
            vpc=network.vpc, vpc_subnets=network.data_subnets, security_groups=[network.db_sg],
            storage_encrypted=True, storage_encryption_key=storage.key,
            backup=rds.BackupProps(retention=Duration.days(d.backup_days)),
            deletion_protection=d.deletion_protection,
            iam_authentication=True,
            cloudwatch_logs_exports=["postgresql"],
            removal_policy=RemovalPolicy.SNAPSHOT if cfg.storage.retain_data else RemovalPolicy.DESTROY,
        )
        self.secret = self.cluster.secret
