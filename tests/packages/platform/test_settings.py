from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from packages.platform.settings import Settings, load_settings

SECRET_A = "a" * 40
SECRET_B = "b" * 40
ENV_EXAMPLE = Path(__file__).parents[3] / ".env.example"


@pytest.fixture
def base() -> dict[str, str]:
    return {
        "MONGO_URL_APP": "mongodb://app:pw@localhost:27017/?replicaSet=rs0&authSource=admin",
        "MONGO_URL_OWNER": "mongodb://owner:pw@localhost:27017/?replicaSet=rs0&authSource=admin",
        "SESSION_SECRET": SECRET_A,
        "SYSTEM_CREDENTIAL_SECRET": SECRET_B,
    }


def test_FR_000_04_missing_setting_refuses_start_names_setting(
    base: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    del base["SESSION_SECRET"]

    with pytest.raises(SystemExit) as exit_info:
        load_settings(_env_file=None, **base)

    err = capsys.readouterr().err
    assert exit_info.value.code == 2
    assert "setting SESSION_SECRET" in err
    assert SECRET_B not in err


def test_FR_000_12_unknown_llm_provider_lists_allowed_values(
    base: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        load_settings(_env_file=None, **base, LLM_PROVIDER="openai")

    err = capsys.readouterr().err
    assert exit_info.value.code == 2
    assert "setting LLM_PROVIDER" in err
    assert "allowed: anthropic, bedrock" in err
    assert "openai" not in err


def test_FR_000_03_env_example_lists_every_setting() -> None:
    keys = {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert keys == set(Settings.model_fields)


def test_settings_defaults_match_the_contract(base: dict[str, str]) -> None:
    settings = Settings(_env_file=None, **base)

    assert settings.SESSION_TTL_HOURS == 8
    assert settings.LLM_PROVIDER == "anthropic"
    assert settings.MONGO_DB == "benefit"
    assert isinstance(settings.SESSION_SECRET, SecretStr)
    assert SECRET_A not in repr(settings)


@pytest.mark.parametrize(
    ("overrides", "setting"),
    [
        ({"SYSTEM_CREDENTIAL_SECRET": SECRET_A}, "SYSTEM_CREDENTIAL_SECRET"),
        ({"SESSION_SECRET": "short"}, "SESSION_SECRET"),
        (
            {"APP_ENV": "dev", "LLM_MODE": "live", "LLM_PROVIDER": "anthropic"},
            "ANTHROPIC_API_KEY",
        ),
        (
            {"APP_ENV": "dev", "LLM_MODE": "live", "LLM_PROVIDER": "bedrock"},
            "AWS_REGION",
        ),
        ({"APP_ENV": "test", "LLM_MODE": "live"}, "LLM_MODE"),
        ({"APP_ENV": "test", "TRACING_ENABLED": "true"}, "TRACING_ENABLED"),
        ({"SESSION_TTL_HOURS": "99"}, "SESSION_TTL_HOURS"),
    ],
)
def test_FR_000_04_cross_field_rules_refuse_start(
    base: dict[str, str],
    overrides: dict[str, str],
    setting: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        load_settings(_env_file=None, **{**base, **overrides})

    assert exit_info.value.code == 2
    assert f"setting {setting}" in capsys.readouterr().err


def test_FR_010_02_review_settings_default_2_and_4_and_escalation_after_reminder(
    base: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(_env_file=None, **base)
    assert (settings.REVIEW_REMINDER_DAYS, settings.REVIEW_ESCALATION_DAYS) == (2, 4)

    for escalation in ("2", "1"):
        with pytest.raises(SystemExit) as exit_info:
            load_settings(
                _env_file=None, **base, REVIEW_REMINDER_DAYS="2", REVIEW_ESCALATION_DAYS=escalation
            )
        assert exit_info.value.code == 2
        assert "setting REVIEW_ESCALATION_DAYS" in capsys.readouterr().err


def test_environment_picks_defaults_for_models_and_tracing(
    base: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("EMBEDDER", "RERANKER", "TRACING_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    dev = Settings(_env_file=None, **base, APP_ENV="dev")
    test = Settings(_env_file=None, **base, APP_ENV="test")

    assert (dev.EMBEDDER, dev.TRACING_ENABLED) == ("qwen", True)
    assert (test.EMBEDDER, test.TRACING_ENABLED) == ("fake", False)
