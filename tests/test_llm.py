import pytest

from alchemist import llm
from alchemist.agent import Budget, ToolOutput, run_loop
from alchemist.config import Settings


def test_split_spec():
    assert llm.split_spec("bedrock:anthropic.claude-opus-5-5") == ("bedrock", "anthropic.claude-opus-5-5")
    assert llm.split_spec("anthropic.claude-opus-5-5") == ("bedrock", "anthropic.claude-opus-5-5")
    assert llm.split_spec("arn:aws:bedrock:eu-central-1:1:application-inference-profile/x")[0] == "bedrock"
    assert llm.split_spec("google:gemini-3-pro") == ("google", "gemini-3-pro")
    assert llm.split_spec("google-gla:gemini-3-pro") == ("google", "gemini-3-pro")  # older prefix
    assert llm.split_spec("google-vertex:gemini-3-pro") == ("google-cloud", "gemini-3-pro")
    assert llm.split_spec("us.anthropic.claude-opus-5-5-v1:0") == ("bedrock", "us.anthropic.claude-opus-5-5-v1:0")
    assert llm.split_spec("openai:gpt-5") == ("openai", "gpt-5")


def test_provider_allowlist():
    s = Settings()
    assert s.allowed_providers == ["bedrock"]
    llm.check_allowed("anthropic.claude-opus-5-5", s.allowed_providers)
    for spec in ("anthropic:claude-opus-5-5", "openai:gpt-5", "google:gemini-3-pro"):
        with pytest.raises(llm.ProviderNotAllowed):
            llm.check_allowed(spec, s.allowed_providers)
    llm.check_allowed("google-gla:gemini-3-pro", ["bedrock", "google"])
    llm.check_allowed("google-cloud:gemini-3-pro", ["google-vertex"])
    with pytest.raises(llm.ProviderNotAllowed):
        llm.check_allowed("google-cloud:gemini-3-pro", ["google"])
    llm.check_allowed("openai-chat:gpt-5", ["openai"])
    llm.check_allowed("openai:gpt-5", ["*"])


@pytest.mark.parametrize("spec,kind", [("anthropic.claude-opus-5-5", "BedrockConverseModel"),
                                       ("anthropic:claude-opus-5-5", "AnthropicModel"),
                                       ("openai:gpt-5", "OpenAIResponsesModel"),
                                       ("google:gemini-3-pro", "GoogleModel"),
                                       ("google-gla:gemini-3-pro", "GoogleModel")])
def test_each_provider_builds(spec, kind, monkeypatch):
    """Every documented spec resolves to a Pydantic AI model (built only; no request is sent)."""
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(k, "test-key")
    s = Settings()
    s.allowed_providers = ["*"]
    assert type(llm.PydanticAIModel(s, spec).model).__name__ == kind


def test_loop_through_pydantic_ai(tmp_path):
    """The tool loop works against a Pydantic AI model (FunctionModel stands in for a provider)."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel

    seen = {}

    def fn(messages, info):
        returns = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        seen["tools"] = [t.name for t in info.function_tools]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("ping", {"n": 1}, tool_call_id="c1")])
        seen["return"] = returns[-1].content
        return ModelResponse(parts=[TextPart("done")])

    s = Settings()
    m = llm.PydanticAIModel.__new__(llm.PydanticAIModel)
    m.name, m.spec, m.model, m.thinking, m.max_tokens = "test:fn", "test:fn", FunctionModel(fn), None, 1000
    hit = []

    def ping(args):
        hit.append(args)
        return ToolOutput("pong")

    tools = [{"name": "ping", "description": "p", "input_schema": {"type": "object", "properties": {"n": {"type": "integer"}}}}]
    end = run_loop(m, system="s", tools=tools, handlers={"ping": ping}, first_message="go", done=lambda: bool(hit),
                   budget=Budget(5, 1000), transcript=tmp_path / "t.jsonl")
    assert end == "done" and hit == [{"n": 1}]
    assert seen["tools"] == ["ping"]


def test_message_conversion_keeps_images_and_thinking():
    import base64

    from alchemist.agent import _result_block

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": [{"type": "thinking", "content": "hm", "signature": "sig"},
                                              {"type": "tool_use", "id": "t1", "name": "run_python", "input": {"code": "1"}}]},
            {"role": "user", "content": [_result_block("t1", ToolOutput("ok", images=[png]))]}]
    out = llm.to_pydantic_messages("sys", msgs)
    assert [type(m).__name__ for m in out] == ["ModelRequest", "ModelResponse", "ModelRequest"]
    assert out[1].parts[0].signature == "sig"
    ret = out[2].parts[0]
    assert ret.tool_name == "run_python" and len(ret.content) == 2
    back = llm.from_pydantic_response(out[1])
    assert back["stop_reason"] == "tool_use" and back["content"][1]["input"] == {"code": "1"}


def test_role_models_fall_back_to_the_shared_settings(monkeypatch):
    from alchemist import model_roles
    from alchemist.config import Settings

    for r in model_roles.ROLES.values():
        monkeypatch.delenv(r.env, raising=False)
        monkeypatch.delenv(r.legacy_env, raising=False)
    s = Settings()
    assert (s.planning_model, s.analysis_model, s.reviewer_model, s.small_model) == (
        "bedrock:anthropic.claude-opus-5-5", "bedrock:anthropic.claude-sonnet-5-5",
        "bedrock:anthropic.claude-opus-5-5", "bedrock:anthropic.claude-haiku-5-5")

    monkeypatch.setenv("ALC_LEAD_MODEL", "anthropic.lead")
    monkeypatch.setenv("ALC_SUB_MODEL", "anthropic.sub")
    monkeypatch.setenv("ALC_ANALYSIS_MODEL", "anthropic.analysis")
    s = Settings()
    assert (s.planning_model, s.analysis_model, s.reviewer_model, s.small_model) == (
        "anthropic.lead", "anthropic.analysis", "anthropic.sub", "anthropic.sub")
    assert model_roles.env_source("analysis") == "ALC_ANALYSIS_MODEL"
    assert model_roles.env_source("planning") == "ALC_LEAD_MODEL"
