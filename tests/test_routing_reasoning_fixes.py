"""Regression tests: focus keyword router, Qwen reasoning_format, bypass routing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import atlas_core
from atlas_core import StateEngine
from atlas_mind.provider_router import ProviderRouter


def _engine_with_events(**kwargs) -> StateEngine:
    events: list[tuple[str, dict]] = []

    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="test-routing-fixes",
        )
    engine.on_event(lambda t, p: events.append((t, p)))
    engine.focus_mode = kwargs.get("focus_mode", False)
    engine._events = events
    engine.audio_watcher.mark_user_typed = MagicMock()
    engine.audio_watcher.get_audio_context = lambda *_a, **_k: ""
    return engine


@pytest.fixture(autouse=True)
def _quiet_voice(monkeypatch):
    monkeypatch.setattr(atlas_core.voice_engine, "speak", lambda *_a, **_k: None)
    monkeypatch.setattr(atlas_core.voice_engine, "skip", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _patch_ambient(monkeypatch):
    monkeypatch.setattr(atlas_core, "capture_screen_b64", lambda *_a, **_k: None)


def _run_handle_input(engine: StateEngine, text: str, source: str = "user") -> None:
    real_thread = __import__("threading").Thread

    def _sync_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)

        def _start():
            t.run()

        t.start = _start
        return t

    with patch("atlas_core.threading.Thread", side_effect=_sync_thread):
        engine.handle_input(text, source=source)


@pytest.mark.parametrize(
    "text",
    [
        "how's the interview going",
        "let's focus on this",
        "how is copilot helping today",
    ],
)
def test_focus_router_does_not_hijack_conversational_phrases(text):
    engine = _engine_with_events(focus_mode=False)
    routed = engine.route_command(text)
    assert routed["intent"] == "chat"


@pytest.mark.parametrize(
    "text",
    [
        "interview mode",
        "focus mode",
        "focus",
        "interview",
    ],
)
def test_focus_router_still_handles_explicit_mode_phrases(text):
    engine = _engine_with_events(focus_mode=False)
    routed = engine.route_command(text)
    assert routed == {"intent": "toggle_focus", "enabled": True}


def test_stream_groq_adds_reasoning_format_for_qwen(monkeypatch):
    captured: dict = {}

    class _Delta:
        content = "hello"

    class _Choice:
        delta = _Delta()

    class _Chunk:
        choices = [_Choice()]

    def _fake_create(client, **kwargs):
        captured.update(kwargs)
        return iter([_Chunk()])

    monkeypatch.setattr(
        "atlas_mind.model_health.groq_completions_create",
        _fake_create,
    )
    monkeypatch.setattr(
        "atlas_mind.provider_router.resolve_groq_api_key",
        lambda *_a, **_k: "test-key",
    )

    router = ProviderRouter()
    chunks = list(
        router._stream_groq(
            [{"role": "user", "content": "hi"}],
            model="qwen/qwen3.6-27b",
            reasoning_effort=None,
            vision=True,
        )
    )

    assert chunks == ["hello"]
    assert captured.get("reasoning_format") == "hidden"


def test_bypass_routing_skips_commands_and_reaches_llm(monkeypatch):
    monkeypatch.setenv("ATLAS_DEBUG_BYPASS_ROUTING", "1")

    def _fake_groq_stream(self, messages, *, model, reasoning_effort, vision=False, engine=None):
        yield "4"

    monkeypatch.setattr(
        "atlas_mind.provider_router.ProviderRouter._stream_groq",
        _fake_groq_stream,
    )
    engine = _engine_with_events()
    monkeypatch.setattr(engine, "try_handle_command", MagicMock(return_value=True))
    monkeypatch.setattr(
        "atlas_mind.router.try_route_tools",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "atlas_mind.stack_router.try_stack_answer",
        lambda *_a, **_k: "should not use stack",
    )
    monkeypatch.setattr(
        engine.memory,
        "build_local_context_block",
        lambda *_a, **_k: "",
    )
    monkeypatch.setattr(
        engine.memory,
        "build_context_prompt",
        lambda *_a, **_k: "",
    )
    monkeypatch.setattr(
        engine.memory,
        "build_memory_prompt",
        lambda *_a, **_k: "",
    )

    _run_handle_input(engine, "what's 2+2", source="user")

    engine.try_handle_command.assert_not_called()
    assert any(t == "query_started" for t, _ in engine._events)
    assert not any(t == "command_handled" for t, _ in engine._events)


def test_bypass_routing_disabled_restores_command_handling(monkeypatch):
    monkeypatch.delenv("ATLAS_DEBUG_BYPASS_ROUTING", raising=False)
    engine = _engine_with_events(focus_mode=False)

    engine.handle_input("focus mode", source="user")

    assert engine.focus_mode is True
    assert any(t == "command_handled" for t, _ in engine._events)
