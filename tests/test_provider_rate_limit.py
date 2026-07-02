"""Regression tests for Groq rate-limit backoff in ProviderRouter."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from atlas_mind.provider_router import ProviderRouter, _is_rate_limit_error


def test_is_rate_limit_error_detects_429():
    assert _is_rate_limit_error(RuntimeError("HTTP 429 Too Many Requests"))
    assert _is_rate_limit_error(SimpleNamespace(status_code=429))


def test_groq_stream_retries_on_rate_limit(monkeypatch):
    router = ProviderRouter()
    calls = {"n": 0}

    class _FakeStream:
        def __iter__(self):
            delta = SimpleNamespace(content="hello", reasoning=None)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    def _create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 rate limit exceeded")
        return _FakeStream()

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = _create
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)

    with patch("groq.Groq", return_value=mock_client):
        out = "".join(
            router._stream_groq(
                messages=[{"role": "user", "content": "hi"}],
                model="llama-3.1-8b-instant",
                reasoning_effort=None,
            )
        )

    assert out == "hello"
    assert calls["n"] == 2
