import json
import logging
from pathlib import Path

import pytest

from invoice_to_excel.config import Settings
from invoice_to_excel.logging_config import JsonFormatter, TextFormatter, configure_logging


def record(**extra: object) -> logging.LogRecord:
    rec = logging.LogRecord(
        "invoice_to_excel.x", logging.INFO, __file__, 1, "hello %s", ("you",), None
    )
    rec.__dict__.update(extra)
    return rec


def test_defaults_are_conservative() -> None:
    settings = Settings(_env_file=None)
    assert settings.extractor == "rules"
    assert settings.llm_enabled is False
    assert settings.llm_model == "claude-sonnet-5-5"
    assert settings.web_host == "127.0.0.1"
    assert settings.anthropic_api_key is None


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVOICE_LLM_MODEL", "claude-test")
    monkeypatch.setenv("INVOICE_WEB_MAX_UPLOAD_MB", "3")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    settings = Settings(_env_file=None)
    assert settings.llm_model == "claude-test"
    assert settings.web_max_upload_mb == 3
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-secret"
    assert "sk-secret" not in repr(settings)


def test_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("INVOICE_EXTRACTOR=auto\nANTHROPIC_API_KEY=sk-from-file\nUNRELATED=1\n")
    settings = Settings(_env_file=env)
    assert settings.extractor == "auto"
    assert settings.anthropic_api_key is not None


def test_invalid_values_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVOICE_CONFIDENCE_THRESHOLD", "1.5")
    with pytest.raises(ValueError, match="confidence_threshold"):
        Settings(_env_file=None)


def test_text_formatter_appends_fields() -> None:
    line = TextFormatter().format(record(file="a b.pdf", status="ok"))
    assert "INFO" in line
    assert 'hello you file="a b.pdf" status=ok' in line


def test_json_formatter_emits_one_object() -> None:
    payload = json.loads(JsonFormatter().format(record(file="a.pdf", confidence=0.9)))
    assert payload["message"] == "hello you"
    assert payload["file"] == "a.pdf"
    assert payload["confidence"] == 0.9
    assert payload["level"] == "INFO"


def test_exceptions_are_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = record()
        rec.exc_info = sys.exc_info()
    assert "ValueError: boom" in TextFormatter().format(rec)
    assert "ValueError: boom" in json.loads(JsonFormatter().format(rec))["exception"]


def test_configure_logging_replaces_handlers() -> None:
    configure_logging("DEBUG", "json")
    configure_logging("WARNING", "text")
    logger = logging.getLogger("invoice_to_excel")
    assert len(logger.handlers) == 1
    assert logger.level == logging.WARNING


def test_log_level_is_case_insensitive_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVOICE_LOG_LEVEL", "debug")
    assert Settings(_env_file=None).log_level == "DEBUG"
    monkeypatch.setenv("INVOICE_LOG_LEVEL", "chatty")
    with pytest.raises(ValueError, match="log_level"):
        Settings(_env_file=None)
