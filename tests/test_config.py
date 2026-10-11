from __future__ import annotations

import pytest

from verimind.config import Settings


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERIMIND_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")
    monkeypatch.setenv("VERIMIND_MAX_UPLOAD_MB", "10")
    settings = Settings(_env_file=None)
    assert settings.database_url.endswith("@db:5432/x")
    assert settings.max_upload_mb == 10


def test_ai_summaries_disabled_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert Settings(_env_file=None).ai_summaries_enabled is False


def test_ai_summaries_enabled_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert Settings(_env_file=None).ai_summaries_enabled is True
