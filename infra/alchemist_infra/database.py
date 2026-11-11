"""Aurora PostgreSQL Serverless v2 for the catalog, knowledge passages (pgvector) and the run index.

The database only indexes the workspace bucket, so it can be rebuilt from the run folders.

Two logins (PA-22). The cluster's generated admin login owns the schema and is used by one task only: the migrate
task, which a deploy trigger runs before the services update (`alchemist db migrate --owner-user alchemist_admin`). It
applies migrations and creates or updates the app login, whose password is in its own secret. Every other task (API,
orchestrator, worker, sealer, indexer) connects as the app login, which may read and write rows and nothing else.
All connections use TLS with `sslmode=verify-full` against the RDS CA bundle shipped in the image. The `vector`
extension will be created by an app migration when knowledge passages move here, not by CloudFormation.
"""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from .config import EnvConfig
from .network import Network
from .storage import Storage

DB_NAME = "alchemist"
OWNER_USER = "alchemist_admin"
APP_USER = "alchemist_app"


class Database(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, network: Network, storage: Storage) -> None:
        super().__init__(scope, id)
        d = cfg.database
        self.cluster = rds.DatabaseCluster(
            self, "Cluster",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_17_5),
            default_database_name=DB_NAME,
            credentials=rds.Credentials.from_generated_secret(OWNER_USER, secret_name=f"{cfg.prefix}/db"),
            writer=rds.ClusterInstance.serverless_v2("Writer"),
            serverless_v2_min_capacity=d.min_acu, serverless_v2_max_capacity=d.max_acu,
            vpc=network.vpc, vpc_subnets=network.data_subnets, security_groups=[network.db_sg],
            storage_encrypted=True, storage_encryption_key=storage.key,
            backup=rds.BackupProps(retention=Duration.days(d.backup_days)),
            deletion_protection=d.deletion_protection,
            iam_authentication=True,
            # Refuse connections without TLS on the server side too (the default on recent engines; stated here).
            parameters={"rds.force_ssl": "1"},
            cloudwatch_logs_exports=["postgresql"],
            removal_policy=RemovalPolicy.SNAPSHOT if cfg.storage.retain_data else RemovalPolicy.DESTROY,
        )
        self.secret = self.cluster.secret  # the owner login: only the migrate task gets it
        # The app login's password. The migrate task sets it (as a SCRAM verifier) on every run, so after rotating
        # this secret, run the migrate trigger again (docs/guides/deploy-aws.md).
        self.app_secret = sm.Secret(
            self, "AppLogin", secret_name=f"{cfg.prefix}/db-app", encryption_key=storage.key,
            description="Alchemist app database login: row access only, no schema changes",
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template=f'{{"username": "{APP_USER}"}}', generate_string_key="password",
                password_length=40, exclude_punctuation=True))
