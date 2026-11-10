import os

import pytest

from alchemist import cli, config


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """apply_config_file and load_settings write os.environ directly; restore it all afterwards."""
    saved = dict(os.environ)
    for k in ("ALC_CONFIG", "ALC_BEDROCK_PROFILE", "ALC_BEDROCK_REGION", "ALC_AWS_PROFILE", "ALC_AWS_REGION",
              "ALC_ALLOWED_PROVIDERS", "ALC_MAX_STEPS", "AWS_PROFILE", "AWS_REGION", "ALC_SMALL_MODEL", "ALC_SUB_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALC_HOME", str(tmp_path / "home"))
    yield monkeypatch
    os.environ.clear()
    os.environ.update(saved)
    config._applied_config.update(path=None, keys=[])


def test_file_fills_settings_and_the_environment_wins(clean_env, tmp_path):
    f = tmp_path / "bedrock.yaml"
    f.write_text("ALC_BEDROCK_PROFILE: alchemist-models\nAWS_PROFILE: alchemist-data\n"
                 "ALC_ALLOWED_PROVIDERS: [bedrock, anthropic]\nALC_MAX_STEPS: 80\nALC_SMALL_MODEL:\n")
    clean_env.setenv("AWS_PROFILE", "from-shell")
    assert config.apply_config_file(str(f)) == ["ALC_BEDROCK_PROFILE", "ALC_ALLOWED_PROVIDERS", "ALC_MAX_STEPS"]
    s = config.Settings()
    assert s.bedrock_profile == "alchemist-models" and s.allowed_providers == ["bedrock", "anthropic"]
    assert s.max_steps == 80 and os.environ["AWS_PROFILE"] == "from-shell"
    assert "ALC_SMALL_MODEL" not in os.environ  # empty values are skipped


def test_bad_files_are_refused(clean_env, tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("alc_home: x\n")
    with pytest.raises(ValueError, match="not a setting name"):
        config.apply_config_file(str(f))
    f.write_text("ALC_MAX_STEPS: {a: 1}\n")
    with pytest.raises(ValueError, match="string, number"):
        config.apply_config_file(str(f))
    with pytest.raises(FileNotFoundError):
        config.apply_config_file(str(tmp_path / "missing.yaml"))


def test_cli_finds_the_file(clean_env, tmp_path, capsys):
    (tmp_path / "alchemist.yaml").write_text("ALC_SMALL_MODEL: eu.anthropic.claude-haiku-5-5\n")
    cli.main(["models", "--role", "small"])
    out = capsys.readouterr().out
    assert "small     eu.anthropic.claude-haiku-5-5" in out and "Settings file: " in out
    os.environ.pop("ALC_SMALL_MODEL")

    other = tmp_path / "other.yaml"
    other.write_text("ALC_SMALL_MODEL: local:qwen3:32b\n")
    cli.main(["--config", str(other), "models", "--role", "small"])
    assert "small     local:qwen3:32b" in capsys.readouterr().out
    os.environ.pop("ALC_SMALL_MODEL")

    clean_env.setenv("ALC_CONFIG", str(tmp_path / "nope.yaml"))
    with pytest.raises(SystemExit, match="settings file not found"):
        cli.main(["models"])


def test_alchemist_aws_names_apply_to_alchemist_only(clean_env, tmp_path):
    clean_env.setenv("AWS_PROFILE", "from-shell")
    clean_env.setenv("ALC_AWS_PROFILE", "alchemist-data")
    clean_env.setenv("ALC_AWS_REGION", "eu-west-1")
    clean_env.delenv("ALC_BEDROCK_REGION", raising=False)
    s = config.load_settings()
    assert os.environ["AWS_PROFILE"] == "alchemist-data" and os.environ["AWS_REGION"] == "eu-west-1"
    assert s.aws_region == "eu-west-1" and s.bedrock_region == "eu-west-1"
    clean_env.setenv("ALC_BEDROCK_REGION", "eu-central-1")
    assert config.Settings().bedrock_region == "eu-central-1"


def test_scoped_kernels_never_inherit_alchemist_profiles(tmp_path):
    from alchemist.credentials import AMBIENT_AWS_VARS, BrokerDenied, credential_process, process_profile_env

    env = process_profile_env(str(tmp_path), ["alchemist", "broker", "credentials"])
    for k in ("ALC_AWS_PROFILE", "ALC_BEDROCK_PROFILE", "ALC_CONFIG"):
        assert env[k] is None and k in AMBIENT_AWS_VARS
    kernel = {"ALC_AWS_PROFILE": "orchestrator", "ALC_BEDROCK_PROFILE": "models", "ALC_CONFIG": "x.yaml"}
    with pytest.raises(BrokerDenied):
        credential_process(kernel)
    assert kernel == {}  # the broker never resolves through the caller's own profile


def test_double_quoted_windows_path_gets_a_hint(clean_env, tmp_path):
    f = tmp_path / "win.yaml"
    f.write_text('ALC_MAX_STEPS: 3\nALC_KNOWLEDGE: "C:\\Users\\L\\alchemist-home"\n')
    with pytest.raises(ValueError, match="single quotes") as e:
        config.apply_config_file(str(f))
    assert "not valid YAML" in str(e.value)
    f.write_text("ALC_KNOWLEDGE: 'C:\\Users\\L\\alchemist-home'\n")
    config.apply_config_file(str(f))
    assert os.environ["ALC_KNOWLEDGE"] == "C:\\Users\\L\\alchemist-home"
