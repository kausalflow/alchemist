"""Deploy-time trigger: run the migrate task (owner login) once and fail the deploy unless it exits 0.

CloudFormation runs this after the database and before the services that connect with the app login, so new tasks
never find the schema behind. Environment: CLUSTER, TASK_DEFINITION, CONTAINER, SUBNETS, SECURITY_GROUPS,
LOG_GROUP. The function only starts and watches the task; it never sees a database credential.
"""

import os

import boto3


def handler(event, context):
    ecs = boto3.client("ecs")
    cluster = os.environ["CLUSTER"]
    started = ecs.run_task(
        cluster=cluster, taskDefinition=os.environ["TASK_DEFINITION"], launchType="FARGATE", count=1,
        startedBy="alchemist-deploy",
        networkConfiguration={"awsvpcConfiguration": {
            "subnets": os.environ["SUBNETS"].split(","), "securityGroups": os.environ["SECURITY_GROUPS"].split(","),
            "assignPublicIp": "DISABLED"}})
    if started.get("failures") or not started.get("tasks"):
        raise RuntimeError(f"could not start the migrate task: {started.get('failures')}")
    arn = started["tasks"][0]["taskArn"]
    # The Lambda limit is 15 minutes; leave a minute to report.
    ecs.get_waiter("tasks_stopped").wait(cluster=cluster, tasks=[arn], WaiterConfig={"Delay": 10, "MaxAttempts": 80})
    task = ecs.describe_tasks(cluster=cluster, tasks=[arn])["tasks"][0]
    container = next(c for c in task["containers"] if c["name"] == os.environ["CONTAINER"])
    if container.get("exitCode") != 0:
        raise RuntimeError(f"migrate task {arn} failed (exit code {container.get('exitCode')}, "
                           f"{task.get('stoppedReason', '')}); see log group {os.environ['LOG_GROUP']}")
    return {"task": arn}
