"""One Step Functions standard execution per run.

    Prepare -> Plan (waits on a task token: plan approval) -> StartSandbox -> Analyse -> Report
            -> StopSandbox -> Seal -> Succeed or Fail

The API starts an execution with input
    {"run_id", "question_id", "workspace", "project", "run_prefix", "broker_token", "skip_approval"}.

The plan task gets the task token in ALC_TASK_TOKEN. It stores it with the run and exits; the execution waits at no
cost until the API calls SendTaskSuccess (approval) or SendTaskFailure (rejection). With skip_approval the plan task
sends SendTaskSuccess itself. A failure in Analyse still runs Report, StopSandbox and Seal, so every run ends with a
(possibly partial) report and a sealed folder; a failure before the sandbox exists goes straight to Seal.
"""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from .config import EnvConfig
from .network import Network
from .services import Services, _retention

J = sfn.JsonPath


def _command(base: list[str], *extra: str) -> str:
    """States.Array of the configured command, the run id, and any extra JSONPath arguments."""
    return J.array(*base, J.string_at("$.run_id"), *extra)


class RunWorkflow(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, network: Network, services: Services) -> None:
        super().__init__(scope, id)
        svc, c = services, cfg.commands
        launch = tasks.EcsFargateLaunchTarget(platform_version=ecs.FargatePlatformVersion.LATEST)

        def run_task(name: str, task_def, container, command_path: str, sg, subnets, *,
                     pattern=sfn.IntegrationPattern.RUN_JOB, env=None, timeout: Duration | None = None,
                     result_path: str | None = J.DISCARD, **kw) -> tasks.EcsRunTask:
            return tasks.EcsRunTask(
                self, name, cluster=svc.cluster, task_definition=task_def, launch_target=launch,
                integration_pattern=pattern, assign_public_ip=False, security_groups=[sg], subnets=subnets,
                propagated_tag_source=ecs.PropagatedTagSource.TASK_DEFINITION,
                container_overrides=[tasks.ContainerOverride(
                    container_definition=container, command=J.list_at(command_path),
                    environment=[tasks.TaskEnvironmentVariable(name=k, value=v) for k, v in (env or {}).items()])],
                task_timeout=sfn.Timeout.duration(timeout) if timeout else None, result_path=result_path, **kw)

        prepare = sfn.Pass(self, "Prepare", parameters={
            "plan": _command(c.plan), "report": _command(c.report), "seal": _command(c.seal),
            "sandbox": J.array(*c.sandbox)}, result_path="$.cmd")

        plan = run_task(
            "Plan", svc.orchestrator_task, svc.orchestrator_container, "$.cmd.plan", network.orchestrator_sg,
            network.app_subnets, pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            env={"ALC_RUN_ID": J.string_at("$.run_id"), "ALC_TASK_TOKEN": J.task_token,
                 "ALC_SKIP_APPROVAL": J.format("{}", J.string_at("$.skip_approval"))},
            timeout=Duration.days(cfg.workflow.approval_timeout_days), result_path="$.approval")

        start_sandbox = run_task(
            "StartSandbox", svc.sandbox_task, svc.sandbox_container, "$.cmd.sandbox", network.sandbox_sg,
            network.sandbox_subnets, pattern=sfn.IntegrationPattern.REQUEST_RESPONSE,
            env={"ALC_RUN_ID": J.string_at("$.run_id"), "ALC_BROKER_TOKEN": J.string_at("$.broker_token")},
            result_selector={"task_arn": J.string_at("$.Tasks[0].TaskArn")}, result_path="$.sandbox")

        prepare_analyse = sfn.Pass(self, "PrepareAnalyse", parameters={
            "analyse": _command(c.analyse, "--sandbox-task-arn", J.string_at("$.sandbox.task_arn"))},
            result_path="$.cmd2")

        analyse = run_task(
            "Analyse", svc.orchestrator_task, svc.orchestrator_container, "$.cmd2.analyse", network.orchestrator_sg,
            network.app_subnets, env={"ALC_RUN_ID": J.string_at("$.run_id")},
            timeout=Duration.hours(cfg.workflow.analysis_timeout_hours))

        report = run_task(
            "Report", svc.orchestrator_task, svc.orchestrator_container, "$.cmd.report", network.orchestrator_sg,
            network.app_subnets, env={"ALC_RUN_ID": J.string_at("$.run_id"),
                                      "ALC_SANDBOX_TASK_ARN": J.string_at("$.sandbox.task_arn")},
            timeout=Duration.hours(1))

        stop_sandbox = tasks.CallAwsService(
            self, "StopSandbox", service="ecs", action="stopTask",
            parameters={"Cluster": svc.cluster.cluster_arn, "Task": J.string_at("$.sandbox.task_arn"),
                        "Reason": "Alchemist run finished"},
            iam_resources=[f"arn:aws:ecs:{cfg.region}:{cfg.account}:task/{svc.cluster.cluster_name}/*"],
            iam_action="ecs:StopTask", result_path=J.DISCARD)

        seal = run_task(
            "Seal", svc.sealer_task, svc.sealer_container, "$.cmd.seal", network.indexer_sg, network.app_subnets,
            env={"ALC_RUN_ID": J.string_at("$.run_id")}, timeout=Duration.hours(1))

        outcome = sfn.Choice(self, "Outcome")
        failed = sfn.Fail(self, "RunFailed", cause="A run step failed; the sealed folder holds a partial report")
        done = sfn.Succeed(self, "RunSucceeded")
        seal_failed = sfn.Fail(self, "SealFailed", cause="The run folder could not be sealed")

        retry = dict(errors=["ECS.AmazonECSException", "ECS.ServerException", "States.TaskFailed"],
                     interval=Duration.seconds(10), max_attempts=2, backoff_rate=2)
        # Only infrastructure errors are retried for the sandbox start; the agent's own steps are not re-run.
        start_sandbox.add_retry(errors=["ECS.AmazonECSException", "ECS.ServerException"],
                                interval=Duration.seconds(10), max_attempts=3, backoff_rate=2)
        seal.add_retry(**retry)
        stop_sandbox.add_retry(interval=Duration.seconds(5), max_attempts=3)

        plan.add_catch(seal, result_path="$.error")
        start_sandbox.add_catch(seal, result_path="$.error")
        analyse.add_catch(report, result_path="$.error")
        report.add_catch(stop_sandbox, result_path="$.report_error")
        stop_sandbox.add_catch(seal, result_path="$.stop_error")
        seal.add_catch(seal_failed, result_path="$.seal_error")

        definition = (prepare.next(plan).next(start_sandbox).next(prepare_analyse).next(analyse).next(report)
                      .next(stop_sandbox).next(seal).next(
                          outcome.when(sfn.Condition.is_present("$.error"), failed).otherwise(done)))

        self.log_group = logs.LogGroup(self, "Logs", log_group_name=f"/aws/vendedlogs/states/{cfg.prefix}-runs",
                                       retention=_retention(cfg.operations.log_retention_days))
        self.state_machine = sfn.StateMachine(
            self, "Runs", state_machine_name=f"{cfg.prefix}-runs",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            state_machine_type=sfn.StateMachineType.STANDARD, tracing_enabled=True,
            timeout=Duration.hours(24 * cfg.workflow.approval_timeout_days + cfg.workflow.analysis_timeout_hours + 3),
            # Execution data holds the broker token, so it is not logged.
            logs=sfn.LogOptions(destination=self.log_group, level=sfn.LogLevel.ERROR, include_execution_data=False))

        # The API starts, stops and inspects executions; the orchestrator and API answer task tokens.
        self.state_machine.grant_start_execution(svc.api_role)
        self.state_machine.grant_read(svc.api_role)
        svc.api_role.add_to_policy(iam.PolicyStatement(
            actions=["states:StopExecution"], resources=[f"arn:aws:states:{cfg.region}:{cfg.account}:execution:"
                                                         f"{cfg.prefix}-runs:*"]))
        svc.api_container.add_environment("ALC_STATE_MACHINE_ARN", self.state_machine.state_machine_arn)
