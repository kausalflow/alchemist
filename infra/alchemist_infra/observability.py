"""Alarms, CloudTrail with S3 data events on the workspace bucket, and the alarmed break-glass role."""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from .config import EnvConfig
from .services import Services
from .storage import RUNS_PATTERN, Storage
from .workflow import RunWorkflow


class Observability(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, storage: Storage, services: Services,
                 workflow: RunWorkflow) -> None:
        super().__init__(scope, id)
        ops = cfg.operations
        self.topic = sns.Topic(self, "Alarms", display_name=f"{cfg.prefix} alarms", enforce_ssl=True)
        if ops.alarm_email:
            self.topic.add_subscription(subs.EmailSubscription(ops.alarm_email))
        action = cw_actions.SnsAction(self.topic)

        sm = workflow.state_machine
        five = Duration.minutes(5)
        alarms = {
            "RunsFailed": sm.metric_failed(period=five),
            "RunsTimedOut": sm.metric_timed_out(period=five),
            "BrokerErrors": services.broker.metric_errors(period=five),
            "ApiTarget5xx": services.target_group.metrics.http_code_target(
                code=elbv2.HttpCodeTarget.TARGET_5XX_COUNT, period=five),
        }
        for name, metric in alarms.items():
            cw.Alarm(self, name, metric=metric, threshold=1, evaluation_periods=1,
                     comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                     treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                     alarm_description=f"{cfg.prefix}: {name}").add_alarm_action(action)

        # Break-glass: the only human principal that may write in sealed run folders. Every assumption alarms.
        self.break_glass_role = None
        if ops.break_glass_principal_arns:
            principals = [iam.ArnPrincipal(a) for a in ops.break_glass_principal_arns]
            self.break_glass_role = iam.Role(
                self, "BreakGlass", role_name=f"{cfg.prefix}-break-glass",
                assumed_by=iam.CompositePrincipal(*principals), max_session_duration=Duration.hours(1),
                description="Alchemist break-glass: may change sealed run folders; every use alarms")
            storage.workspace.grant_read_write(self.break_glass_role, RUNS_PATTERN)
            storage.workspace.grant_delete(self.break_glass_role, RUNS_PATTERN)
            events.Rule(self, "BreakGlassUsed", description="Alarm on any use of the break-glass role",
                        event_pattern=events.EventPattern(
                            source=["aws.sts"], detail_type=["AWS API Call via CloudTrail"],
                            detail={"eventName": ["AssumeRole"],
                                    "requestParameters": {"roleArn": [self.break_glass_role.role_arn]}}),
                        targets=[targets.SnsTopic(self.topic)])

        if ops.create_trail:
            trail = cloudtrail.Trail(self, "Trail", bucket=storage.logs, s3_key_prefix="cloudtrail",
                                     is_multi_region_trail=False, include_global_service_events=True,
                                     enable_file_validation=True)
            trail.add_s3_event_selector([cloudtrail.S3EventSelector(bucket=storage.workspace)],
                                        read_write_type=cloudtrail.ReadWriteType.ALL)
        self.topic.grant_publish(iam.ServicePrincipal("events.amazonaws.com"))

