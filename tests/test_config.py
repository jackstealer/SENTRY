import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from sentry_ai.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Strip any real SENTRY settings from the process env so tests see only what they set."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


def test_defaults_match_docs(clean_env: pytest.MonkeyPatch) -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.env == "dev"
    assert s.user_tz == "Asia/Kolkata"
    assert s.database_url == "postgresql+asyncpg://sentry:sentry@localhost:5432/sentry"
    assert s.openai_api_key is None
    assert s.openai_model_fallbacks == []
    assert s.persona_honorific == "sir"


def test_reads_environment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENAI_MODEL_FAST", "fast-model")
    clean_env.setenv("OPENAI_MODEL_FALLBACKS", " a-model, b-model ,,")
    clean_env.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.openai_model_fast == "fast-model"
    assert s.openai_model_fallbacks == ["a-model", "b-model"]
    assert s.telegram_allowed_chat_id == 12345


def test_blank_values_mean_unset_but_blank_honorific_stays_blank(
    clean_env: pytest.MonkeyPatch,
) -> None:
    for key in ("OPENAI_API_KEY", "TELEGRAM_ALLOWED_CHAT_ID", "GOOGLE_TOKEN_FILE"):
        clean_env.setenv(key, "")
    clean_env.setenv("PERSONA_HONORIFIC", "")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.openai_api_key is None
    assert s.telegram_allowed_chat_id is None
    assert s.google_token_file is None
    assert s.persona_honorific == ""


@pytest.mark.parametrize("tz", ["Mars/Olympus", "", "IST"])
def test_unknown_timezone_rejected(clean_env: pytest.MonkeyPatch, tz: str) -> None:
    clean_env.setenv("USER_TZ", tz)
    with pytest.raises(ValidationError, match="timezone"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_secrets_never_rendered(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENAI_API_KEY", "sk-very-secret")
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "123:bot-secret")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    rendered = repr(s) + str(s) + s.model_dump_json()
    assert "very-secret" not in rendered
    assert "bot-secret" not in rendered
    assert s.openai_api_key is not None
    assert s.openai_api_key.get_secret_value() == "sk-very-secret"


def test_env_example_lists_exactly_the_settings() -> None:
    keys = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(), flags=re.M))
    assert keys == {name.upper() for name in Settings.model_fields}
