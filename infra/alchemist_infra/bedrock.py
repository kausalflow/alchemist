"""Bedrock settings: model allow-list, guardrail, and invocation logging to an Alchemist-owned bucket."""

from __future__ import annotations

from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from aws_cdk import custom_resources as cr
from constructs import Construct

from .config import EnvConfig
from .storage import Storage


class BedrockSetup(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, storage: Storage) -> None:
        super().__init__(scope, id)
        b = cfg.bedrock
        self.model_arns = list(b.allowed_model_arns)
        region, account = cfg.region, cfg.account
        self.embedding_arn = f"arn:aws:bedrock:{region}::foundation-model/{b.embedding_model}"

        self.guardrail_id = self.guardrail_version = None
        self.guardrail_arns: list[str] = []
        if b.guardrail:
            # Sensitive-information filter on model traffic. Plant data reaching the model is already reduced by
            # the views (hidden and pseudonymised columns); this catches personal data in documents and free text.
            g = bedrock.CfnGuardrail(
                self, "Guardrail", name=f"{cfg.prefix}-guardrail",
                blocked_input_messaging="This request was blocked by the Alchemist guardrail.",
                blocked_outputs_messaging="This response was blocked by the Alchemist guardrail.",
                kms_key_arn=storage.key.key_arn,
                sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                    pii_entities_config=[
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(type=t, action="ANONYMIZE")
                        for t in ("EMAIL", "PHONE", "US_SOCIAL_SECURITY_NUMBER", "CREDIT_DEBIT_CARD_NUMBER",
                                  "AWS_ACCESS_KEY", "AWS_SECRET_KEY", "PASSWORD")]))
            v = bedrock.CfnGuardrailVersion(self, "GuardrailVersion", guardrail_identifier=g.attr_guardrail_id)
            self.guardrail_id, self.guardrail_version = g.attr_guardrail_id, v.attr_version
            self.guardrail_arns = [g.attr_guardrail_arn]

        if b.invocation_logging:
            bucket = storage.bedrock_logs
            bucket.add_to_resource_policy(iam.PolicyStatement(
                principals=[iam.ServicePrincipal("bedrock.amazonaws.com")], actions=["s3:PutObject"],
                resources=[bucket.arn_for_objects("AWSLogs/*")],
                conditions={"StringEquals": {"aws:SourceAccount": account},
                            "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{region}:{account}:*"}}))
            storage.key.add_to_resource_policy(iam.PolicyStatement(
                principals=[iam.ServicePrincipal("bedrock.amazonaws.com")],
                actions=["kms:GenerateDataKey", "kms:Decrypt"], resources=["*"],
                conditions={"StringEquals": {"aws:SourceAccount": account}}))
            config = {"loggingConfig": {
                "s3Config": {"bucketName": bucket.bucket_name, "keyPrefix": "AWSLogs"},
                "textDataDeliveryEnabled": True, "imageDataDeliveryEnabled": True,
                "embeddingDataDeliveryEnabled": False}}
            # Account-wide for the region; see `bedrock.invocation_logging` in the config.
            res = cr.AwsCustomResource(
                self, "InvocationLogging",
                on_create=cr.AwsSdkCall(service="bedrock", action="PutModelInvocationLoggingConfiguration",
                                        parameters=config, physical_resource_id=cr.PhysicalResourceId.of(
                                            f"{cfg.prefix}-invocation-logging")),
                on_update=cr.AwsSdkCall(service="bedrock", action="PutModelInvocationLoggingConfiguration",
                                        parameters=config, physical_resource_id=cr.PhysicalResourceId.of(
                                            f"{cfg.prefix}-invocation-logging")),
                on_delete=cr.AwsSdkCall(service="bedrock", action="DeleteModelInvocationLoggingConfiguration"),
                policy=cr.AwsCustomResourcePolicy.from_statements([
                    iam.PolicyStatement(actions=["bedrock:PutModelInvocationLoggingConfiguration",
                                                 "bedrock:DeleteModelInvocationLoggingConfiguration"],
                                        resources=["*"]),
                    # Bedrock checks that the caller may write to the bucket.
                    iam.PolicyStatement(actions=["s3:GetBucketPolicy", "s3:PutObject"],
                                        resources=[bucket.bucket_arn, bucket.arn_for_objects("*")])]),
                install_latest_aws_sdk=False)
            res.node.add_dependency(bucket)

    def grant_invoke(self, role: iam.IRole, *, embeddings: bool = False) -> None:
        arns = self.model_arns + ([self.embedding_arn] if embeddings else [])
        role.add_to_principal_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], resources=arns))
        if self.guardrail_arns:
            role.add_to_principal_policy(iam.PolicyStatement(actions=["bedrock:ApplyGuardrail"],
                                                             resources=self.guardrail_arns))

    def endpoint_arns(self) -> list[str]:
        return self.model_arns + [self.embedding_arn]

    def env(self) -> dict[str, str]:
        e = {}
        if self.guardrail_id:
            e = {"ALC_BEDROCK_GUARDRAIL_ID": self.guardrail_id, "ALC_BEDROCK_GUARDRAIL_VERSION": self.guardrail_version}
        return e
