"""Fast highlight path skips vision and uses Groq text model only."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import atlas_core
from atlas_core import StateEngine


def _engine(**kwargs) -> StateEngine:
    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="test-highlight-fast",
        )
    engine.on_event(lambda t, p: engine._events.append((t, p)))
    engine._events = []
    engine.focus_mode = kwargs.get("focus_mode", True)
    engine.audio_watcher.mark_user_typed = MagicMock()
    engine.audio_watcher.get_audio_context = lambda *_a, **_k: ""
    return engine


def test_highlight_uses_fast_text_dispatch(monkeypatch):
    engine = _engine()
    captured: dict = {}

    def _fake_ai_query(messages, raw_user_text, had_screen=False, *, fast_fail=False):
        captured["fast_fail"] = fast_fail
        captured["had_screen"] = had_screen
        captured["messages"] = messages

    monkeypatch.setattr(engine, "_on_ai_query", _fake_ai_query)
    monkeypatch.setattr(engine, "try_handle_command", lambda _t: False)

    engine.handle_input("What is 2+2?", source="highlight")

    assert captured.get("fast_fail") is True
    assert captured.get("had_screen") is False
    assert any(t == "query_started" for t, _ in engine._events)


def test_highlight_skips_stack_router(monkeypatch):
    engine = _engine()
    stack = MagicMock(return_value="stack answer")
    monkeypatch.setattr("atlas_mind.stack_router.try_stack_answer", stack)
    monkeypatch.setattr(engine, "_dispatch_fast_text_query", MagicMock())
    monkeypatch.setattr(engine, "try_handle_command", lambda _t: False)

    engine.handle_input("A can finish in 18 days", source="highlight")

    stack.assert_not_called()
    engine._dispatch_fast_text_query.assert_called_once()
