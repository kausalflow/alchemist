"""The Step Functions launcher used in AWS (ALC_LAUNCHER=alchemist.sfn_launcher:StepFunctionsLauncher)."""

import io
import json
from types import SimpleNamespace

from alchemist.sfn_launcher import StepFunctionsLauncher


class NoSuchKey(Exception):
    pass


class TaskTimedOut(Exception):
    pass


class InvalidToken(Exception):
    pass


class FakeS3:
    exceptions = SimpleNamespace(NoSuchKey=NoSuchKey)

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


class FakeSfn:
    exceptions = SimpleNamespace(TaskTimedOut=TaskTimedOut, InvalidToken=InvalidToken)

    def __init__(self, fail_send=False):
        self.started, self.sent, self.fail_send = [], [], fail_send

    def start_execution(self, **kw):
        self.started.append(kw)

    def send_task_success(self, taskToken, output):
        if self.fail_send:
            raise InvalidToken()
        self.sent.append((taskToken, json.loads(output)))


def launcher(sfn=None, s3=None):
    return StepFunctionsLauncher(None, state_machine_arn="arn:sm", approvals="s3://ws/_approvals/",
                                 sfn=sfn or FakeSfn(), s3=s3 or FakeS3())


def test_plan_and_replay_start_executions():
    l = launcher()
    l.plan("R1")
    l.replay("R2")
    assert [(s["name"], json.loads(s["input"])) for s in l.sfn.started] == [
        ("R1-run", {"run_id": "R1", "mode": "run"}), ("R2-replay", {"run_id": "R2", "mode": "replay"})]
    assert all(s["stateMachineArn"] == "arn:sm" for s in l.sfn.started)


def test_approval_releases_a_waiting_run():
    l = launcher()
    l.s3.put_object(Bucket="ws", Key="_approvals/R1/token.json", Body=json.dumps({"task_token": "T"}).encode())
    l.analyse("R1", "eng_p1")
    (token, output), = l.sfn.sent
    assert token == "T" and output["approved_by"] == "eng_p1"
    assert ("ws", "_approvals/R1/approved.json") in l.s3.objects


def test_approval_before_the_token_is_stored_is_kept_for_the_workflow():
    l = launcher()
    l.analyse("R1", "eng_p1")
    assert l.sfn.sent == [] and ("ws", "_approvals/R1/approved.json") in l.s3.objects


def test_a_token_already_released_is_not_an_error():
    l = launcher(sfn=FakeSfn(fail_send=True))
    l.s3.put_object(Bucket="ws", Key="_approvals/R1/token.json", Body=json.dumps({"task_token": "T"}).encode())
    l.analyse("R1", "eng_p1")
