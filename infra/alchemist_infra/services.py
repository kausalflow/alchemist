"""ECS cluster, the Alchemist image, IAM roles, task definitions, the API service behind an ALB with OIDC,
the credential broker, and the nightly indexers.

Roles follow the split in the technical design:

| Role         | Bedrock | Lake | Workspace bucket                                         |
| ------------ | ------- | ---- | -------------------------------------------------------- |
| api          | no      | no   | read all; write questions, projects, `_grants/`          |
| orchestrator | yes     | no   | read run folders except `inputs/` and `work/`; write runs |
| sandbox      | no      | no   | nothing; it can only call the broker                     |
| run (broker) | no      | read, narrowed per run | read and write its own run folder only  |
| sealer       | no      | no   | read run folders; write `SEALED`                         |
| indexer      | yes     | read | none (catalog cards go to Aurora)                        |
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import Duration, Token
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from .bedrock import BedrockSetup
from .config import EnvConfig, TaskSize
from .database import DB_NAME, Database
from .network import Network
from .storage import GRANTS_PREFIX, RUNS_PATTERN, SEALED_PATTERN, Storage

REPO_ROOT = Path(__file__).resolve().parents[2]
BROKER_CODE = Path(__file__).resolve().parent / "lambdas" / "broker"
ORIGIN_HEADER = "X-Alchemist-Origin-Verify"
IMAGE_EXCLUDES = [".git", ".venv", "**/.venv", "alchemist-home", "infra", "site", "web/node_modules", "**/__pycache__",
                  "**/.pytest_cache", "cdk.out"]


class Services(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, network: Network, storage: Storage,
                 database: Database, bedrock: BedrockSetup) -> None:
        super().__init__(scope, id)
        self.cfg = cfg
        self.network = network
        ws, kn = storage.workspace, storage.knowledge

        self.cluster = ecs.Cluster(self, "Cluster", vpc=network.vpc,
                                   container_insights_v2=ecs.ContainerInsights.ENHANCED)

        # ---------------------------------------------------------------- image
        if cfg.image.ecr_repository_arn:
            repo = ecr.Repository.from_repository_arn(self, "Repo", cfg.image.ecr_repository_arn)
            self.image = ecs.ContainerImage.from_registry(f"{repo.repository_uri}@{cfg.image.digest}")
            self._repo = repo
        else:
            self.image = ecs.ContainerImage.from_asset(str(REPO_ROOT), exclude=IMAGE_EXCLUDES)
            self._repo = None

        # ---------------------------------------------------------------- secrets
        self.pseudonym_key = sm.Secret(
            self, "PseudonymKey", secret_name=f"{cfg.prefix}/pseudonym-key", encryption_key=storage.key,
            description="HMAC key for pseudonymised columns; a replay needs the same key",
            generate_secret_string=sm.SecretStringGenerator(password_length=48, exclude_punctuation=True))
        self.origin_secret = sm.Secret(
            self, "OriginVerify", secret_name=f"{cfg.prefix}/origin-verify", encryption_key=storage.key,
            description="Header CloudFront adds so the ALB only serves requests that came through CloudFront",
            generate_secret_string=sm.SecretStringGenerator(password_length=40, exclude_punctuation=True))

        # ---------------------------------------------------------------- roles
        def task_role(name: str, desc: str) -> iam.Role:
            return iam.Role(self, name, description=desc,
                            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))

        self.execution_role = iam.Role(
            self, "ExecutionRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AmazonECSTaskExecutionRolePolicy")])
        if self._repo:
            self._repo.grant_pull(self.execution_role)
        self.api_role = task_role("ApiRole", "Alchemist API")
        self.orchestrator_role = task_role("OrchestratorRole", "Alchemist orchestrator: Bedrock, no lake")
        self.sandbox_role = task_role("SandboxRole", "Alchemist sandbox: may only call the credential broker")
        self.sealer_role = task_role("SealerRole", "Alchemist sealer: hashes run folders and writes SEALED")
        self.indexer_role = task_role("IndexerRole", "Alchemist catalog and knowledge indexers")

        broker_fn_role = iam.Role(
            self, "BrokerFunctionRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole")])
        self.run_role = iam.Role(
            self, "RunRole", description="Per-run data role; the broker narrows it with a session policy",
            assumed_by=iam.ArnPrincipal(broker_fn_role.role_arn).with_session_tags(),
            max_session_duration=Duration.hours(1))

        # Run role ceiling: read the lake and the knowledge bucket, read and write run folders, never SEALED.
        for st in storage.lake_read_statements():
            self.run_role.add_to_policy(st)
        kn.grant_read(self.run_role)
        self.run_role.add_to_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject", "s3:PutObjectTagging", "s3:AbortMultipartUpload"],
            resources=[ws.arn_for_objects(RUNS_PATTERN)]))
        self.run_role.add_to_policy(iam.PolicyStatement(actions=["s3:ListBucket"], resources=[ws.bucket_arn]))
        self.run_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.DENY, actions=["s3:PutObject"], resources=[ws.arn_for_objects(SEALED_PATTERN)]))
        storage.key.grant_encrypt_decrypt(self.run_role)

        # Broker function: reads grants, assumes the run role.
        ws.grant_read(broker_fn_role, f"{GRANTS_PREFIX}*")
        broker_fn_role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole", "sts:TagSession"], resources=[self.run_role.role_arn]))
        self.broker = lambda_.Function(
            self, "Broker", runtime=lambda_.Runtime.PYTHON_3_12, handler="handler.handler",
            code=lambda_.Code.from_asset(str(BROKER_CODE), exclude=["__pycache__"]), role=broker_fn_role,
            timeout=Duration.seconds(15), memory_size=256, description="Alchemist per-run credential broker",
            log_group=logs.LogGroup(self, "BrokerLogs", retention=_retention(cfg.operations.log_retention_days)),
            environment={"RUN_ROLE_ARN": self.run_role.role_arn, "WORKSPACE_BUCKET": ws.bucket_name,
                         "KNOWLEDGE_BUCKET": kn.bucket_name, "LAKE_BUCKETS": ",".join(storage.lake_bucket_names),
                         "GRANTS_PREFIX": GRANTS_PREFIX})
        self.broker.grant_invoke(self.sandbox_role)

        # API: read everything in the workspace (presigned downloads), write questions and grants.
        ws.grant_read(self.api_role)
        ws.grant_put(self.api_role, "workspaces/*")
        ws.grant_put(self.api_role, f"{GRANTS_PREFIX}*")
        kn.grant_read_write(self.api_role)  # POST /documents from approved sources

        # Orchestrator: run folders without raw rows, Bedrock, knowledge, the sandbox's address.
        ws.grant_read(self.orchestrator_role)
        self.orchestrator_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.DENY, actions=["s3:GetObject"],
            resources=[ws.arn_for_objects("workspaces/*/runs/*/inputs/*"),
                       ws.arn_for_objects("workspaces/*/runs/*/work/*"), ws.arn_for_objects(f"{GRANTS_PREFIX}*")]))
        ws.grant_put(self.orchestrator_role, RUNS_PATTERN)
        ws.grant_put(self.orchestrator_role, "workspaces/*/question.yaml")  # the drafted plan
        self.orchestrator_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.DENY, actions=["s3:PutObject"], resources=[ws.arn_for_objects(SEALED_PATTERN)]))
        kn.grant_read(self.orchestrator_role)
        bedrock.grant_invoke(self.orchestrator_role)
        self.orchestrator_role.add_to_policy(iam.PolicyStatement(
            actions=["ecs:DescribeTasks"], resources=["*"],
            conditions={"ArnEquals": {"ecs:cluster": self.cluster.cluster_arn}}))
        # The plan step hands its task token back (plan approval skipped) or reports failure.
        for r in (self.orchestrator_role, self.api_role):
            r.add_to_policy(iam.PolicyStatement(
                actions=["states:SendTaskSuccess", "states:SendTaskFailure", "states:SendTaskHeartbeat"],
                resources=["*"]))

        # Sealer: hash every file, write SEALED.
        ws.grant_read(self.sealer_role, RUNS_PATTERN)
        ws.grant_put(self.sealer_role, SEALED_PATTERN)
        self.sealer_role.add_to_policy(iam.PolicyStatement(actions=["s3:ListBucket"], resources=[ws.bucket_arn]))

        # Indexers: sample the lake to draft catalog cards, embed knowledge passages.
        for st in storage.lake_read_statements():
            self.indexer_role.add_to_policy(st)
        kn.grant_read(self.indexer_role)
        bedrock.grant_invoke(self.indexer_role, embeddings=True)

        for r in (self.api_role, self.orchestrator_role, self.sealer_role, self.indexer_role):
            storage.key.grant_encrypt_decrypt(r)

        # ---------------------------------------------------------------- environment
        db = database.cluster
        self.common_env = {
            "ALC_ENV": cfg.env_name,
            "AWS_REGION": cfg.region,
            "ALC_WORKSPACE": f"s3://{ws.bucket_name}",
            "ALC_KNOWLEDGE": f"s3://{kn.bucket_name}",
            "ALC_GRANTS_PREFIX": GRANTS_PREFIX,
            "ALC_LAKE_BUCKETS": ",".join(storage.lake_bucket_names),
            "ALC_PLANT_PREFIX_TEMPLATE": cfg.lake.plant_prefix_template,
            "ALC_ALLOWED_PROVIDERS": "bedrock",
            "ALC_IMAGE_DIGEST": cfg.image.digest or "",
        }
        self.db_env = {
            "ALC_DB_HOST": db.cluster_endpoint.hostname,
            "ALC_DB_PORT": Token.as_string(db.cluster_endpoint.port),
            "ALC_DB_NAME": DB_NAME,
            "ALC_DB": f"postgresql://{db.cluster_endpoint.socket_address}/{DB_NAME}",
        }
        self.db_secrets = {
            "ALC_DB_USER": ecs.Secret.from_secrets_manager(database.secret, "username"),
            "ALC_DB_PASSWORD": ecs.Secret.from_secrets_manager(database.secret, "password"),
        }
        self.model_env = {
            "ALC_LEAD_MODEL": cfg.bedrock.lead_model,
            "ALC_SUB_MODEL": cfg.bedrock.sub_model,
            "ALC_EMBEDDING_MODEL": cfg.bedrock.embedding_model,
            **bedrock.env(),
        }
        self.identity_env = {"ALC_IDENTITY": "alb-oidc", "ALC_GROUP_CLAIM": cfg.oidc.group_claim}
        pseudonym = {"ALC_PSEUDONYM_KEY": ecs.Secret.from_secrets_manager(self.pseudonym_key)}

        # ---------------------------------------------------------------- task definitions
        sz = cfg.sizes
        self.api_task, self.api_container = self._task(
            "Api", "api", sz.api, self.api_role, cfg.commands.api,
            {**self.common_env, **self.db_env, **self.identity_env, **self.model_env}, self.db_secrets,
            port=cfg.commands.api_port)
        self.orchestrator_task, self.orchestrator_container = self._task(
            "Orchestrator", "orchestrator", sz.orchestrator, self.orchestrator_role, cfg.commands.plan,
            {**self.common_env, **self.db_env, **self.model_env, "ALC_CLUSTER_ARN": self.cluster.cluster_arn},
            self.db_secrets)
        self.sandbox_task, self.sandbox_container = self._task(
            "Sandbox", "sandbox", sz.sandbox, self.sandbox_role, cfg.commands.sandbox,
            {**self.common_env, "ALC_BROKER_FUNCTION": self.broker.function_arn}, pseudonym,
            port=cfg.commands.sandbox_port)
        self.sealer_task, self.sealer_container = self._task(
            "Sealer", "sealer", sz.sealer, self.sealer_role, cfg.commands.seal,
            {**self.common_env, **self.db_env}, self.db_secrets)
        self.indexer_task, self.indexer_container = self._task(
            "Indexer", "indexer", sz.indexer, self.indexer_role, cfg.commands.catalog_index,
            {**self.common_env, **self.db_env, **self.model_env}, {**self.db_secrets, **pseudonym})

        # ---------------------------------------------------------------- API service and ALB
        self.api_service = ecs.FargateService(
            self, "ApiService", cluster=self.cluster, task_definition=self.api_task,
            desired_count=sz.api_count, vpc_subnets=network.app_subnets, security_groups=[network.api_sg],
            assign_public_ip=False, circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=100, enable_execute_command=False)
        self.alb = elbv2.ApplicationLoadBalancer(
            self, "Alb", vpc=network.vpc, internet_facing=True, vpc_subnets=network.public_subnets,
            security_group=network.alb_sg, drop_invalid_header_fields=True)
        self.alb.log_access_logs(storage.logs, prefix="alb")
        if cfg.domain.cloudfront_prefix_list_id:
            network.alb_sg.add_ingress_rule(ec2.Peer.prefix_list(cfg.domain.cloudfront_prefix_list_id),
                                            ec2.Port.tcp(443), "CloudFront origin-facing")
        else:
            network.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443),
                                            "HTTPS; requests without the CloudFront origin header get 403")
        listener = self.alb.add_listener(
            "Https", port=443, protocol=elbv2.ApplicationProtocol.HTTPS, open=False,
            ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
            certificates=[elbv2.ListenerCertificate.from_arn(cfg.domain.alb_certificate_arn)],
            default_action=elbv2.ListenerAction.fixed_response(403, content_type="text/plain",
                                                               message_body="Forbidden"))
        tg = elbv2.ApplicationTargetGroup(
            self, "ApiTargets", vpc=network.vpc, port=cfg.commands.api_port,
            protocol=elbv2.ApplicationProtocol.HTTP, targets=[self.api_service],
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(path=cfg.commands.api_health_path, healthy_http_codes="200"))
        o = cfg.oidc
        client_secret = sm.Secret.from_secret_name_v2(self, "OidcClientSecret", o.client_secret_name)
        listener.add_action(
            "Authenticated", priority=10,
            conditions=[elbv2.ListenerCondition.http_header(ORIGIN_HEADER, [self.origin_header_value])],
            action=elbv2.ListenerAction.authenticate_oidc(
                issuer=o.issuer, authorization_endpoint=o.authorization_endpoint, token_endpoint=o.token_endpoint,
                user_info_endpoint=o.user_info_endpoint, client_id=o.client_id,
                client_secret=client_secret.secret_value, scope=o.scope,
                session_timeout=Duration.hours(o.session_timeout_hours),
                next=elbv2.ListenerAction.forward([tg])))
        self.target_group = tg

        # ---------------------------------------------------------------- scheduled indexers
        for name, command in (("CatalogIndex", cfg.commands.catalog_index),
                              ("KnowledgeIngest", cfg.commands.knowledge_ingest)):
            events.Rule(self, f"{name}Schedule", schedule=events.Schedule.expression(cfg.workflow.index_schedule),
                        description=f"Alchemist nightly {name}",
                        targets=[targets.EcsTask(
                            cluster=self.cluster, task_definition=self.indexer_task,
                            subnet_selection=network.app_subnets, security_groups=[network.indexer_sg],
                            container_overrides=[targets.ContainerOverride(container_name="indexer",
                                                                           command=command)])])

    @property
    def origin_header_value(self) -> str:
        return self.origin_secret.secret_value.unsafe_unwrap()

    def _task(self, id: str, name: str, size: TaskSize, role: iam.IRole, command: list[str], env: dict[str, str],
              secrets: dict[str, ecs.Secret], port: int | None = None):
        td = ecs.FargateTaskDefinition(
            self, f"{id}Task", family=f"{self.cfg.prefix}-{name}", cpu=size.cpu, memory_limit_mib=size.memory_mib,
            ephemeral_storage_gib=size.ephemeral_gib if size.ephemeral_gib > 20 else None, task_role=role,
            execution_role=self.execution_role,
            runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.X86_64,
                                                 operating_system_family=ecs.OperatingSystemFamily.LINUX))
        log_group = logs.LogGroup(self, f"{id}Logs", log_group_name=f"/{self.cfg.prefix}/{name}",
                                  retention=_retention(self.cfg.operations.log_retention_days))
        c = td.add_container(
            name, container_name=name, image=self.image, command=command, environment=env, secrets=secrets,
            logging=ecs.LogDrivers.aws_logs(stream_prefix=name, log_group=log_group),
            readonly_root_filesystem=False, user="alchemist",
            port_mappings=[ecs.PortMapping(container_port=port)] if port else None)
        return td, c


_RETENTION = {30: logs.RetentionDays.ONE_MONTH, 60: logs.RetentionDays.TWO_MONTHS,
              90: logs.RetentionDays.THREE_MONTHS, 180: logs.RetentionDays.SIX_MONTHS,
              365: logs.RetentionDays.ONE_YEAR, 400: logs.RetentionDays.THIRTEEN_MONTHS,
              731: logs.RetentionDays.TWO_YEARS, 1827: logs.RetentionDays.FIVE_YEARS}


def _retention(days: int) -> logs.RetentionDays:
    if days not in _RETENTION:
        raise ValueError(f"log retention must be one of {sorted(_RETENTION)} days")
    return _RETENTION[days]

