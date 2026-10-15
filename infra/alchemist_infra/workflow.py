"""One Step Functions standard execution per run (or replay).

The API's launcher (`alchemist.sfn_launcher`) starts an execution with input {"run_id": ..., "mode": "run"} after
creating a run, or {"run_id": ..., "mode": "replay"} after creating a replay run. With a separate sandbox the input
also carries "broker_token".

    mode = replay:  Replay                                                               (sandbox_mode: in_process)
                    StartReplaySandbox -> Replay -> StopReplaySandbox -> SealReplay      (sandbox_mode: separate)
    mode = run:     Plan -> WaitForApproval -> Analyse                                   (sandbox_mode: in_process)
                    Plan -> WaitForApproval -> StartSandbox -> Analyse -> Report
                         -> StopSandbox -> Seal                                          (sandbox_mode: separate)
    mode = resume:  the same, from Analyse (in_process) or StartSandbox (separate)

The analysis writes a checkpoint after every step. A paused run's execution ends (Report and Seal leave a paused run
alone); resuming it starts a new execution in resume mode. Analyse is retried up to workflow.analysis_retries times
when its task fails or times out, and each attempt resumes from the checkpoint.

WaitForApproval invokes the approval function with the task token, which stores it in the workspace bucket under
`_approvals/<run_id>/`; the execution then waits at no cost until the launcher sends the token on approval (see
lambdas/approval). In-process, the analyse step builds the report and seals the run itself and a model failure still
ends in a sealed partial run. With a separate sandbox, Report, StopSandbox and Seal run even if Analyse fails, and
the orchestrator reaches the kernel at the sandbox task's address (`--sandbox-task-arn`); only the sandbox task gets
the broker token.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import Duration
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from .config import EnvConfig
from .network import Network
from .services import Services, _retention
from .storage import APPROVALS_PREFIX

J = sfn.JsonPath
APPROVAL_CODE = Path(__file__).resolve().parent / "lambdas" / "approval"


def _command(base: list[str], *extra: str) -> str:
    """States.Array of the configured command, the run id, and any extra JSONPath arguments."""
    return J.array(*base, J.string_at("$.run_id"), *extra)


class RunWorkflow(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, network: Network, services: Services,
                 storage) -> None:
        super().__init__(scope, id)
        svc, c, wf = services, cfg.commands, cfg.workflow
        launch = tasks.EcsFargateLaunchTarget(platform_version=ecs.FargatePlatformVersion.LATEST)
        ws = storage.workspace

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

        run_env = {"ALC_RUN_ID": J.string_at("$.run_id")}
        orch = dict(task_def=svc.orchestrator_task, container=svc.orchestrator_container,
                    sg=network.orchestrator_sg, subnets=network.app_subnets)

        # ---------------------------------------------------------------- approval token store
        fn_role = iam.Role(self, "ApprovalFunctionRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                           managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                               "service-role/AWSLambdaBasicExecutionRole")])
        ws.grant_read_write(fn_role, f"{APPROVALS_PREFIX}*")
        fn_role.add_to_policy(iam.PolicyStatement(actions=["states:SendTaskSuccess"], resources=["*"]))
        self.approval_function = lambda_.Function(
            self, "Approval", runtime=lambda_.Runtime.PYTHON_3_12, handler="handler.handler",
            code=lambda_.Code.from_asset(str(APPROVAL_CODE), exclude=["__pycache__"]), role=fn_role,
            timeout=Duration.seconds(15), memory_size=128, description="Alchemist plan-approval token store",
            log_group=logs.LogGroup(self, "ApprovalLogs", retention=_retention(cfg.operations.log_retention_days)),
            environment={"WORKSPACE_BUCKET": ws.bucket_name, "APPROVALS_PREFIX": APPROVALS_PREFIX})

        # ---------------------------------------------------------------- states
        failed = sfn.Fail(self, "RunFailed", cause="A run step failed; see the run's status and logs")
        done = sfn.Succeed(self, "RunSucceeded")

        cmds = {"plan": _command(c.plan), "analyse": _command(c.analyse), "replay": _command(c.replay)}
        if not svc.in_process:
            cmds |= {"report": _command(c.report), "seal": _command(c.seal), "sandbox": J.array(*c.sandbox)}
        prepare = sfn.Pass(self, "Prepare", parameters=cmds, result_path="$.cmd")

        if svc.in_process:
            replay = run_task("Replay", command_path="$.cmd.replay", env=run_env,
                              timeout=Duration.hours(wf.analysis_timeout_hours), **orch)
            replay.add_catch(failed, result_path="$.error")
            replay.next(done)
        else:
            replay = self._separate(cfg, network, svc, run_task, orch, run_env, failed, done, replay=True)

        plan = run_task("Plan", command_path="$.cmd.plan", env=run_env, timeout=Duration.hours(2), **orch)
        wait = tasks.LambdaInvoke(
            self, "WaitForApproval", lambda_function=self.approval_function,
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload=sfn.TaskInput.from_object({"run_id": J.string_at("$.run_id"), "token": J.task_token}),
            task_timeout=sfn.Timeout.duration(Duration.days(wf.approval_timeout_days)), result_path="$.approval")
        plan.add_catch(failed, result_path="$.error")
        wait.add_catch(failed, result_path="$.error")

        if svc.in_process:
            analyse = run_task("Analyse", command_path="$.cmd.analyse", env=run_env,
                               timeout=Duration.hours(wf.analysis_timeout_hours), **orch)
            self._retry_analysis(analyse, wf)
            analyse.add_catch(failed, result_path="$.error")
            body = plan.next(wait).next(analyse).next(done)
            analysis = analyse
        else:
            body = plan.next(wait).next(self._separate(cfg, network, svc, run_task, orch, run_env, failed, done))
            analysis = self.analysis_entry

        def mode_is(m: str):
            return sfn.Condition.and_(sfn.Condition.is_present("$.mode"), sfn.Condition.string_equals("$.mode", m))

        # resume: a paused run goes straight to its analysis, which continues from the run's checkpoint.
        mode = sfn.Choice(self, "Mode").when(mode_is("replay"), replay).when(mode_is("resume"), analysis) \
            .otherwise(body)
        definition = prepare.next(mode)

        self.log_group = logs.LogGroup(self, "Logs", log_group_name=f"/aws/vendedlogs/states/{cfg.prefix}-runs",
                                       retention=_retention(cfg.operations.log_retention_days))
        self.state_machine = sfn.StateMachine(
            self, "Runs", state_machine_name=f"{cfg.prefix}-runs",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            state_machine_type=sfn.StateMachineType.STANDARD, tracing_enabled=True,
            timeout=Duration.hours(24 * wf.approval_timeout_days
                                   + wf.analysis_timeout_hours * (1 + wf.analysis_retries) + 4),
            # Execution data can hold the broker token, so it is not logged.
            logs=sfn.LogOptions(destination=self.log_group, level=sfn.LogLevel.ERROR, include_execution_data=False))

        # The API starts, stops and inspects executions; it answers the stored task token on approval.
        self.state_machine.grant_start_execution(svc.api_role)
        self.state_machine.grant_read(svc.api_role)
        svc.api_role.add_to_policy(iam.PolicyStatement(
            actions=["states:StopExecution"], resources=[f"arn:aws:states:{cfg.region}:{cfg.account}:execution:"
                                                         f"{cfg.prefix}-runs:*"]))
        svc.api_container.add_environment("ALC_STATE_MACHINE_ARN", self.state_machine.state_machine_arn)

    @staticmethod
    def _retry_analysis(analyse, wf) -> None:
        """A killed or timed-out analysis task is started again; the step resumes from the run's checkpoint, so a
        run survives a lost task and can go past analysis_timeout_hours (each attempt gets that much)."""
        if wf.analysis_retries:
            analyse.add_retry(errors=["States.TaskFailed", "States.Timeout", "ECS.AmazonECSException"],
                              interval=Duration.seconds(30), max_attempts=wf.analysis_retries, backoff_rate=2)

    def _separate(self, cfg, network, svc, run_task, orch, run_env, failed, done, *,
                  replay: bool = False) -> sfn.IChainable:
        """Sandbox task with broker credentials, then (for a run) report, then stop and seal.

        A run's chain is StartSandbox, Analyse, Report, StopSandbox, Seal; a replay's is StartReplaySandbox, Replay,
        StopReplaySandbox, SealReplay. The analysing step gets the sandbox task's ARN, never the broker token.
        """
        wf = cfg.workflow
        n = "Replay" if replay else ""
        step = "replay" if replay else "analyse"
        start_sandbox = run_task(
            f"Start{n}Sandbox", svc.sandbox_task, svc.sandbox_container, "$.cmd.sandbox", network.sandbox_sg,
            network.sandbox_subnets, pattern=sfn.IntegrationPattern.REQUEST_RESPONSE,
            env={**run_env, "ALC_BROKER_TOKEN": J.string_at("$.broker_token")},
            result_selector={"task_arn": J.string_at("$.Tasks[0].TaskArn")}, result_path="$.sandbox")
        prepare = sfn.Pass(self, f"Prepare{n or 'Analyse'}", parameters={
            step: J.array(*getattr(cfg.commands, step), J.string_at("$.run_id"), "--sandbox-task-arn",
                          J.string_at("$.sandbox.task_arn"))}, result_path="$.cmd2")
        analyse = run_task(n or "Analyse", command_path=f"$.cmd2.{step}", env=run_env,
                           timeout=Duration.hours(wf.analysis_timeout_hours), **orch)
        stop_sandbox = tasks.CallAwsService(
            self, f"Stop{n}Sandbox", service="ecs", action="stopTask",
            parameters={"Cluster": svc.cluster.cluster_arn, "Task": J.string_at("$.sandbox.task_arn"),
                        "Reason": "Alchemist run finished"},
            iam_resources=[f"arn:aws:ecs:{cfg.region}:{cfg.account}:task/{svc.cluster.cluster_name}/*"],
            iam_action="ecs:StopTask", result_path=J.DISCARD)
        seal = run_task(f"Seal{n}", svc.sealer_task, svc.sealer_container, "$.cmd.seal", network.indexer_sg,
                        network.app_subnets, env=run_env, timeout=Duration.hours(1))
        outcome = sfn.Choice(self, f"{n}Outcome")
        seal_failed = sfn.Fail(self, f"{n}SealFailed", cause="The run folder could not be sealed")

        start_sandbox.add_retry(errors=["ECS.AmazonECSException", "ECS.ServerException"],
                                interval=Duration.seconds(10), max_attempts=3, backoff_rate=2)
        seal.add_retry(errors=["ECS.AmazonECSException", "ECS.ServerException", "States.TaskFailed"],
                       interval=Duration.seconds(10), max_attempts=2, backoff_rate=2)
        stop_sandbox.add_retry(interval=Duration.seconds(5), max_attempts=3)
        start_sandbox.add_catch(failed, result_path="$.error")
        stop_sandbox.add_catch(seal, result_path="$.stop_error")
        seal.add_catch(seal_failed, result_path="$.seal_error")
        if not replay:
            self.analysis_entry = start_sandbox
            self._retry_analysis(analyse, wf)
        if replay:
            analyse.add_catch(stop_sandbox, result_path="$.error")
            chain = start_sandbox.next(prepare).next(analyse).next(stop_sandbox)
        else:
            report = run_task("Report", command_path="$.cmd.report", env=run_env, timeout=Duration.hours(1), **orch)
            analyse.add_catch(report, result_path="$.error")
            report.add_catch(stop_sandbox, result_path="$.report_error")
            chain = start_sandbox.next(prepare).next(analyse).next(report).next(stop_sandbox)
        return chain.next(seal).next(outcome.when(sfn.Condition.is_present("$.error"), failed).otherwise(done))
