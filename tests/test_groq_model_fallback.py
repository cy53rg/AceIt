"""Deprecated ATLAS_CHAT_MODEL should fall back instead of crashing."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import atlas_core
from atlas_mind.model_health import get_available_groq_models, groq_completions_create, resolve_groq_model


_AVAILABLE = frozenset(
    {
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
        "qwen/qwen3.6-27b",
    }
)
_DEPRECATED = "mixtral-8x7b-32768"


def test_startup_replaces_deprecated_chat_model(monkeypatch):
    monkeypatch.setenv("ATLAS_CHAT_MODEL", _DEPRECATED)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    atlas_core.GROQ_MODEL = _DEPRECATED

    monkeypatch.setattr(
        atlas_core,
        "get_available_groq_models",
        lambda *_a, **_k: _AVAILABLE,
    )
    monkeypatch.setattr(
        atlas_core,
        "resolve_groq_model",
        lambda model, *, fallback, groq_client: (
            fallback if model not in _AVAILABLE else model
        ),
    )

    atlas_core.validate_groq_models_at_startup(groq_client_override=MagicMock())

    assert atlas_core.GROQ_MODEL == atlas_core.GROQ_DEFAULT_MODEL


def test_resolve_groq_model_falls_back_when_missing():
    client = MagicMock()
    with patch(
        "atlas_mind.model_health.get_available_groq_models",
        return_value=_AVAILABLE,
    ):
        resolved = resolve_groq_model(
            _DEPRECATED,
            fallback=atlas_core.GROQ_DEFAULT_MODEL,
            groq_client=client,
        )

    assert resolved == atlas_core.GROQ_DEFAULT_MODEL


def test_groq_completions_create_uses_fallback_model(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock()

    monkeypatch.setattr(
        "atlas_mind.model_health.get_available_groq_models",
        lambda *_a, **_k: _AVAILABLE,
    )

    groq_completions_create(
        client,
        model=_DEPRECATED,
        fallback=atlas_core.GROQ_DEFAULT_MODEL,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=8,
    )

    client.chat.completions.create.assert_called_once()
    assert (
        client.chat.completions.create.call_args.kwargs["model"]
        == atlas_core.GROQ_DEFAULT_MODEL
    )


def test_get_available_groq_models_reads_models_list():
    client = MagicMock()
    client.models.list.return_value = MagicMock(
        data=[
            MagicMock(id="openai/gpt-oss-120b"),
            MagicMock(id="llama-3.3-70b-versatile"),
        ]
    )

    import atlas_mind.model_health as mh

    mh._MODEL_LIST_CACHE = None
    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False):
        ids = get_available_groq_models(client, force_refresh=True)

    assert ids == frozenset({"openai/gpt-oss-120b", "llama-3.3-70b-versatile"})
