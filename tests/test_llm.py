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


def test_models_check_reports_each_role_and_hints_at_bedrock_fixes(monkeypatch, tmp_path, capsys):
    from alchemist import cli

    monkeypatch.setenv("ALC_HOME", str(tmp_path))
    monkeypatch.setenv("ALC_SMALL_MODEL", "eu.anthropic.claude-haiku-5-5")

    def fake_complete(settings, spec, **kw):
        if "opus" in spec:
            raise RuntimeError("ValidationException: Invocation of model ID anthropic.claude-opus-5-5 with on-demand "
                               "throughput isn't supported. Retry your request with the ID or ARN of an inference profile")
        return "OK"

    monkeypatch.setattr("alchemist.llm.complete", fake_complete)
    with pytest.raises(SystemExit) as e:
        cli.main(["models", "check"])
    out = capsys.readouterr().out
    assert "2 of 4 models did not answer" in str(e.value)
    assert "small     eu.anthropic.claude-haiku-5-5: answered 'OK'" in out
    assert "inference profile id" in out and "Bedrock region: eu-central-1 (default)" in out

    cli.main(["models", "--role", "analysis"])
    assert "analysis  bedrock:anthropic.claude-sonnet-5-5" in capsys.readouterr().out


def test_bedrock_hints():
    from alchemist.cli import _model_error_hint

    assert "aws sso login" in _model_error_hint("bedrock", "NoCredentialsError: Unable to locate credentials")
    assert "AWS_PROFILE" in _model_error_hint("bedrock", "The security token included in the request is invalid.")
    assert "model access" in _model_error_hint("bedrock", "AccessDeniedException: You don't have access to the model")
    assert _model_error_hint("openai", "Unable to locate credentials") == ""


def test_bedrock_can_use_its_own_profile_and_region(monkeypatch, tmp_path):
    """Model calls may use a different AWS identity and region than storage (S3), which keeps AWS_PROFILE."""
    import configparser

    cfg = configparser.ConfigParser()
    cfg["profile models"] = {"aws_access_key_id": "AKIAMODELS", "aws_secret_access_key": "x"}
    cfg["profile storage"] = {"aws_access_key_id": "AKIASTORAGE", "aws_secret_access_key": "y"}
    path = tmp_path / "config"
    with open(path, "w") as f:
        cfg.write(f)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(path))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_PROFILE", "storage")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    def creds(model):
        return model.client._request_signer._credentials.access_key, model.client.meta.region_name

    assert creds(llm.build_model("eu.anthropic.claude-sonnet-5-5", Settings())) == ("AKIASTORAGE", "eu-west-1")
    monkeypatch.setenv("ALC_BEDROCK_PROFILE", "models")
    monkeypatch.setenv("ALC_BEDROCK_REGION", "eu-central-1")
    assert creds(llm.build_model("eu.anthropic.claude-sonnet-5-5", Settings())) == ("AKIAMODELS", "eu-central-1")
    assert Settings().aws_region == "eu-west-1"  # storage and run credentials keep AWS_REGION
