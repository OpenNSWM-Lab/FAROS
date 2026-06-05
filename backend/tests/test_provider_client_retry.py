"""Tests for ProviderClient.chat retry behavior.

These tests pin down the attempt-count semantics: with MAX_RETRIES=N the
client should make at most N attempts, and the failure log should say
"attempt(s)" rather than the misleading "retries".
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Make the backend importable without installing the package.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.llm.provider_client import ProviderClient, ProviderError  # noqa: E402


class _FakeSettings:
    MAX_RETRIES = 2
    RETRY_BACKOFF = 0.0  # no sleeping in tests

    def get_active_model(self, provider):
        return "fake-model"

    def get_runtime_api_key(self, provider):
        return "test-key"

    def get_runtime_base_url(self, provider):
        return None

    def _get_default_base_url(self, provider):
        return None


class _FakeConfig:
    api_format = "openai"

    def get_api_key(self):
        return "test-key"

    def get_base_url(self):
        return None


def _make_client() -> ProviderClient:
    return ProviderClient(provider_name="openai")


def _ok_response():
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    choice = SimpleNamespace(
        message=SimpleNamespace(content="ok"),
        finish_reason="stop",
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def test_chat_makes_exactly_max_retries_attempts_then_succeeds(monkeypatch):
    client = _make_client()
    client.settings = _FakeSettings()
    client.config = _FakeConfig()
    calls = {"n": 0}

    def fake_completion(**kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return _ok_response()

    monkeypatch.setattr(client, "_get_litellm", lambda: SimpleNamespace(completion=fake_completion))
    monkeypatch.setattr(client, "_get_api_config", lambda: {"api_key": "k", "api_base": None, "timeout": 5})

    out = client.chat(messages=[SimpleNamespace(role="user", content="hi")])
    assert out.text == "ok"
    assert calls["n"] == 2  # MAX_RETRIES=2, succeeded on attempt 2


def test_chat_stops_after_max_retries_attempts(monkeypatch, caplog):
    client = _make_client()
    client.settings = _FakeSettings()
    client.config = _FakeConfig()
    calls = {"n": 0}

    def fake_completion(**kwargs):
        calls["n"] += 1
        raise RuntimeError("always fails")

    monkeypatch.setattr(client, "_get_litellm", lambda: SimpleNamespace(completion=fake_completion))
    monkeypatch.setattr(client, "_get_api_config", lambda: {"api_key": "k", "api_base": None, "timeout": 5})

    with caplog.at_level("ERROR", logger="app.llm.provider_client"):
        with pytest.raises(ProviderError) as ei:
            client.chat(messages=[SimpleNamespace(role="user", content="hi")])
    assert calls["n"] == _FakeSettings.MAX_RETRIES
    assert "attempt" in caplog.text
    assert "retries" not in caplog.text
    assert ei.value.status_code == 502
