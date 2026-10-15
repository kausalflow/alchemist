"""KMS key and buckets: workspace (run folders, sealed), knowledge, web (SPA), logs, Bedrock invocation logs.

The data lake buckets are not created here; they are referenced by name from the config and only read.
"""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct

from .config import EnvConfig

# Paths inside the workspace bucket (see docs/guides/s3.md).
RUNS_PATTERN = "workspaces/*/runs/*"  # `*` crosses `/`, so this is every run folder
SEALED_PATTERN = "workspaces/*/runs/*/SEALED"
GRANTS_PREFIX = "_grants/"
APPROVALS_PREFIX = "_approvals/"  # plan-approval task tokens and approvals (see workflow.py)


class Storage(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig) -> None:
        super().__init__(scope, id)
        st = cfg.storage
        removal = RemovalPolicy.RETAIN if st.retain_data else RemovalPolicy.DESTROY
        self.removal_policy = removal
        auto_delete = not st.retain_data

        self.key = kms.Key(self, "Key", alias=f"alias/{cfg.prefix}", enable_key_rotation=True,
                           description="Alchemist workspace, knowledge, database and logs", removal_policy=removal)

        self.logs = s3.Bucket(
            self, "Logs", encryption=s3.BucketEncryption.S3_MANAGED,  # S3 access-log targets cannot use SSE-KMS
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(cfg.bedrock.invocation_log_retention_days))],
            removal_policy=removal, auto_delete_objects=auto_delete)

        def bucket(name: str, bucket_name: str | None, **kw) -> s3.Bucket:
            return s3.Bucket(
                self, name, bucket_name=bucket_name, encryption=s3.BucketEncryption.KMS, encryption_key=self.key,
                bucket_key_enabled=True, block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED, server_access_logs_bucket=self.logs,
                server_access_logs_prefix=f"s3/{name.lower()}/", removal_policy=removal,
                auto_delete_objects=auto_delete, **kw)

        lock = {}
        if st.object_lock_governance_days:
            lock = dict(object_lock_enabled=True, object_lock_default_retention=s3.ObjectLockRetention.governance(
                Duration.days(st.object_lock_governance_days)))
        self.workspace = bucket(
            "Workspace", st.workspace_bucket_name, versioned=st.workspace_versioning,
            lifecycle_rules=[
                # work/ holds intermediates the notebook can regenerate.
                s3.LifecycleRule(id="expire-work", prefix="workspaces/", expiration=Duration.days(st.work_expiry_days),
                                 tag_filters={"alchemist:tier": "work"}),
                s3.LifecycleRule(id="tiering", transitions=[s3.Transition(
                    storage_class=s3.StorageClass.INTELLIGENT_TIERING, transition_after=Duration.days(0))]),
                s3.LifecycleRule(id="abort-mpu", abort_incomplete_multipart_upload_after=Duration.days(7)),
            ],
            cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.GET], allowed_origins=[f"https://{cfg.domain.name}"],
                              allowed_headers=["*"], max_age=3600)],
            **lock)
        self.knowledge = bucket("Knowledge", st.knowledge_bucket_name)
        self.web = s3.Bucket(
            self, "Web", encryption=s3.BucketEncryption.S3_MANAGED, block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True, removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True)
        self.bedrock_logs = bucket(
            "BedrockLogs", None,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(cfg.bedrock.invocation_log_retention_days))])

        self._auto_delete = auto_delete
        self._account = cfg.account
        self.lake_bucket_names = [b.name for b in cfg.lake.buckets]
        self.lake_key_arns = [b.kms_key_arn for b in cfg.lake.buckets if b.kms_key_arn]

    def lake_read_statements(self) -> list[iam.PolicyStatement]:
        out = [
            iam.PolicyStatement(actions=["s3:GetObject"],
                                resources=[f"arn:aws:s3:::{b}/*" for b in self.lake_bucket_names]),
            iam.PolicyStatement(actions=["s3:ListBucket", "s3:GetBucketLocation"],
                                resources=[f"arn:aws:s3:::{b}" for b in self.lake_bucket_names]),
        ]
        if self.lake_key_arns:
            out.append(iam.PolicyStatement(actions=["kms:Decrypt"], resources=self.lake_key_arns))
        return out

    def seal_workspace(self, writer_role_arns: list[str]) -> None:
        """Bucket policy for sealing: only the listed roles may write or delete inside run folders.

        Together with expiring run credentials this means no writer remains once a run is sealed.
        Lifecycle expiry is done by S3 itself and is not affected.
        """
        # Environments without retain_data empty their buckets on stack deletion through CDK's provider role.
        exempt = [f"arn:aws:iam::{self._account}:role/*CustomS3AutoDeleteObjects*"] if self._auto_delete else []
        self.workspace.add_to_resource_policy(iam.PolicyStatement(
            sid="OnlyRunWritersInRunFolders", effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
            actions=["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectTagging",
                     "s3:PutObjectRetention", "s3:BypassGovernanceRetention"],
            resources=[self.workspace.arn_for_objects(RUNS_PATTERN)],
            conditions={"ArnNotLike": {"aws:PrincipalArn": writer_role_arns + exempt}}))
