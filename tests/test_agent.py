from alchemist.agent import Budget, ScriptedModel, ToolOutput, run_loop


def test_budget_stops_the_loop(tmp_path):
    calls = []
    model = ScriptedModel([[("ping", {})]] * 10)
    ended = run_loop(model, system="", tools=[], handlers={"ping": lambda i: calls.append(1) or ToolOutput("pong")},
                     first_message="go", done=lambda: False, budget=Budget(3, 10**6), transcript=tmp_path / "t.jsonl")
    assert ended.startswith("budget") and len(calls) == 3


def test_tool_errors_go_back_to_the_model(tmp_path):
    def boom(_):
        raise RuntimeError("bad input")

    model = ScriptedModel([[("boom", {})]])
    ended = run_loop(model, system="", tools=[], handlers={"boom": boom}, first_message="go", done=lambda: False,
                     budget=Budget(10, 10**6), transcript=tmp_path / "t.jsonl", max_nudges=0)
    assert "bad input" in (tmp_path / "t.jsonl").read_text()
    assert ended.startswith("stopped")
