"""Groq stream must tolerate empty choices arrays without crashing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from atlas_mind.provider_router import ProviderRouter


def _make_chunk(*, content: str = "", choices=None):
    if choices is None:
        delta = SimpleNamespace(content=content, reasoning=None)
        choices = [SimpleNamespace(delta=delta)]
    return SimpleNamespace(choices=choices)


def test_stream_groq_skips_empty_choices_and_continues(monkeypatch):
    router = ProviderRouter()
    stream = [
        _make_chunk(choices=[]),
        {"choices": []},
        _make_chunk(content="hello "),
        _make_chunk(content="world"),
    ]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = iter(stream)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    with patch("groq.Groq", return_value=mock_client):
        out = "".join(
            router._stream_groq(
                messages=[{"role": "user", "content": "hi"}],
                model="llama-3.1-8b-instant",
                reasoning_effort=None,
            )
        )

    assert out == "hello world"


def test_stream_groq_empty_choices_only_does_not_raise(monkeypatch):
    router = ProviderRouter()
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = iter(
        [_make_chunk(choices=[]), {"choices": []}]
    )
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    with patch("groq.Groq", return_value=mock_client):
        out = list(
            router._stream_groq(
                messages=[{"role": "user", "content": "hi"}],
                model="llama-3.1-8b-instant",
                reasoning_effort=None,
            )
        )

    assert out == []
