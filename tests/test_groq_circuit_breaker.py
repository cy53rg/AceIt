"""Groq API circuit breaker on StateEngine."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from atlas_core import StateEngine


def _engine() -> StateEngine:
    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="test-groq-circuit",
        )
    return engine


def _groq_kwargs() -> dict:
    return {
        "model": "openai/gpt-oss-120b",
        "fallback": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": "ping"}],
    }


def test_circuit_opens_after_three_failures(monkeypatch):
    engine = _engine()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("groq down")

    monkeypatch.setattr("atlas_core.groq_completions_create", _boom)

    for _ in range(3):
        with pytest.raises(RuntimeError, match="groq down"):
            engine._groq_completions_create(**_groq_kwargs())

    assert engine._groq_circuit_open_at is not None

    with pytest.raises(RuntimeError, match="circuit breaker is open"):
        engine._groq_completions_create(**_groq_kwargs())


def test_circuit_rejects_calls_for_thirty_seconds(monkeypatch):
    engine = _engine()
    now = {"t": 1_000.0}
    monkeypatch.setattr("atlas_core.time.time", lambda: now["t"])

    def _boom(*_args, **_kwargs):
        raise RuntimeError("groq down")

    monkeypatch.setattr("atlas_core.groq_completions_create", _boom)

    for _ in range(3):
        with pytest.raises(RuntimeError, match="groq down"):
            engine._groq_completions_create(**_groq_kwargs())

    now["t"] = 1_010.0
    with pytest.raises(RuntimeError, match="retry in 20 seconds"):
        engine._groq_completions_create(**_groq_kwargs())

    now["t"] = 1_020.0
    with pytest.raises(RuntimeError, match="retry in 10 seconds"):
        engine._groq_completions_create(**_groq_kwargs())

    now["t"] = 1_025.0
    with pytest.raises(RuntimeError, match="retry in 5 seconds"):
        engine._groq_completions_create(**_groq_kwargs())


def test_circuit_half_open_after_cooldown(monkeypatch):
    engine = _engine()
    now = {"t": 2_000.0}
    monkeypatch.setattr("atlas_core.time.time", lambda: now["t"])

    engine._groq_failures = 3
    engine._groq_circuit_open_at = 2_000.0

    with pytest.raises(RuntimeError, match="circuit breaker is open"):
        engine._check_groq_circuit()

    now["t"] = 2_031.0
    engine._check_groq_circuit()
    assert engine._groq_circuit_open_at is None


def test_success_resets_failure_count(monkeypatch):
    engine = _engine()
    engine._groq_failures = 2

    monkeypatch.setattr(
        "atlas_core.groq_completions_create",
        lambda *_a, **_k: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        ),
    )

    engine._groq_completions_create(**_groq_kwargs())

    assert engine._groq_failures == 0
    assert engine._groq_circuit_open_at is None
