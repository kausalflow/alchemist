"""The approval function stores the task token and releases a run whose approval arrived first."""

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "approval_handler", Path(__file__).resolve().parents[1] / "alchemist_infra" / "lambdas" / "approval" / "handler.py")
approval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(approval)


class NoSuchKey(Exception):
    pass


class InvalidToken(Exception):
    pass


class FakeS3:
    exceptions = SimpleNamespace(NoSuchKey=NoSuchKey)

    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


class FakeSfn:
    exceptions = SimpleNamespace(TaskTimedOut=InvalidToken, InvalidToken=InvalidToken)

    def __init__(self):
        self.sent = []

    def send_task_success(self, taskToken, output):
        self.sent.append((taskToken, json.loads(output)))


def run(s3, sfn):
    return approval.store_and_maybe_release("R1", "T", s3=s3, sfn=sfn, bucket="ws", prefix="_approvals/")


def test_token_is_stored_and_the_run_waits():
    s3, sfn = FakeS3(), FakeSfn()
    assert run(s3, sfn) == {"stored": True, "released": False}
    assert json.loads(s3.objects["_approvals/R1/token.json"])["task_token"] == "T" and sfn.sent == []


def test_an_earlier_approval_releases_the_run_at_once():
    s3 = FakeS3({"_approvals/R1/approved.json": json.dumps({"approved_by": "eng_p1"}).encode()})
    sfn = FakeSfn()
    assert run(s3, sfn)["released"] is True
    assert sfn.sent == [("T", {"approved_by": "eng_p1"})]
