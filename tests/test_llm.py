import pytest

from alchemist import llm
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


def test_allowed_spec_becomes_penguineers_adapter(monkeypatch):
    """The allow-list and provider building stay here; the adapter is penguineer's."""
    from penguineer.agent.models import PydanticAIModel

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    s = Settings()
    s.allowed_providers = ["anthropic"]
    m = llm.PydanticAIModel(s, "anthropic:claude-opus-5-5", effort="max")
    assert isinstance(m, PydanticAIModel) and m.name == "anthropic:claude-opus-5-5" and m.thinking == "xhigh"
    assert m.retry_seconds == s.model_retry_seconds


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
