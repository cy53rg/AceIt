"""Regression tests: control commands handled inside handle_input before Groq."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import atlas_core
from atlas_core import StateEngine


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
            user_name="test-intent-router",
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
    """Run handle_input with Thread targets executed synchronously."""
    real_thread = __import__("threading").Thread

    def _sync_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        original_start = t.start

        def _start():
            t.run()

        t.start = _start
        return t

    with patch("atlas_core.threading.Thread", side_effect=_sync_thread):
        engine.handle_input(text, source=source)


def test_focus_command_skips_groq(monkeypatch):
    groq_create = MagicMock()
    monkeypatch.setattr(atlas_core.groq_client.chat.completions, "create", groq_create)
    engine = _engine_with_events(focus_mode=False)

    engine.handle_input("switch to focus mode", source="mic")

    assert engine.focus_mode is True
    groq_create.assert_not_called()
    assert ("command_handled", {"text": "switch to focus mode", "source": "mic"}) in [
        (t, p) for t, p in engine._events
    ]
    assert not any(t == "query_started" for t, _ in engine._events)


def test_remember_command_calls_global_context(monkeypatch):
    groq_create = MagicMock()
    monkeypatch.setattr(atlas_core.groq_client.chat.completions, "create", groq_create)
    engine = _engine_with_events()
    added: list[str] = []
    monkeypatch.setattr(
        engine,
        "add_global_context",
        lambda note: added.append(note) or 1,
    )

    engine.handle_input("remember that I prefer dark backgrounds", source="user")

    assert added == ["remember that I prefer dark backgrounds"]
    groq_create.assert_not_called()
    assert any(t == "command_handled" for t, _ in engine._events)


def test_stop_command_skips_groq(monkeypatch):
    groq_create = MagicMock()
    monkeypatch.setattr(atlas_core.groq_client.chat.completions, "create", groq_create)
    engine = _engine_with_events()
    stop_called = {"n": 0}
    monkeypatch.setattr(engine, "stop_task", lambda: stop_called.__setitem__("n", stop_called["n"] + 1))

    engine.handle_input("stop", source="speaker")

    assert stop_called["n"] == 1
    groq_create.assert_not_called()
    assert any(t == "command_handled" for t, _ in engine._events)


def test_watch_source_never_routes_commands(monkeypatch):
    groq_create = MagicMock()
    monkeypatch.setattr(atlas_core.groq_client.chat.completions, "create", groq_create)
    engine = _engine_with_events()
    monkeypatch.setattr(engine, "try_handle_command", MagicMock(return_value=True))

    engine.handle_input("stop", source="watch")

    engine.try_handle_command.assert_not_called()
    groq_create.assert_not_called()
    assert not any(t == "command_handled" for t, _ in engine._events)


def test_ordinary_chat_reaches_groq(monkeypatch):
    groq_create = MagicMock(
        return_value=iter([
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="Paris"))]
            ),
            SimpleNamespace(choices=[]),
        ])
    )
    monkeypatch.setattr(atlas_core.groq_client.chat.completions, "create", groq_create)
    monkeypatch.setattr(
        engine := _engine_with_events(),
        "skill_registry",
        SimpleNamespace(
            get=lambda *_a, **_k: None,
            match_triggers=lambda *_a, **_k: None,
            execute=MagicMock(),
        ),
    )
    monkeypatch.setattr(
        engine.local_memory,
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

    _run_handle_input(engine, "what's the capital of France", source="user")

    groq_create.assert_called_once()
    engine.skill_registry.execute.assert_not_called()
    assert any(t == "query_started" for t, _ in engine._events)
    assert not any(t == "command_handled" for t, _ in engine._events)
