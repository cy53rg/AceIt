"""GUIDED mode must survive corrupted playbook data."""
from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import atlas_core
from atlas_core import ModeState, StateEngine


def _engine_with_events() -> StateEngine:
    events: list[tuple[str, dict]] = []

    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="test-playbook-corrupt",
        )
    engine.on_event(lambda t, p: events.append((t, p)))
    engine._events = events
    engine.audio_watcher.mark_user_typed = MagicMock()
    engine.audio_watcher.get_audio_context = lambda *_a, **_k: ""
    return engine


def _patch_chat_path(monkeypatch) -> None:
    monkeypatch.setattr(atlas_core, "capture_screen_b64", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "atlas_mind.router.try_route_tools",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "atlas_mind.stack_router.try_stack_answer",
        lambda *_a, **_k: None,
    )


def _run_handle_input(engine: StateEngine, text: str, source: str = "user") -> None:
    real_thread = threading.Thread

    def _sync_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        if kwargs.get("name") == "atlas-query":
            def _start():
                t.run()

            t.start = _start
        return t

    with patch("atlas_core.threading.Thread", side_effect=_sync_thread):
        engine.handle_input(text, source=source)


def test_corrupt_playbook_check_continues_guided_flow(monkeypatch):
    stream_calls = {"n": 0}

    def fake_stream_chat(
        _self,
        *,
        messages,
        model,
        vision,
        engine,
        reasoning_effort=None,
    ):
        stream_calls["n"] += 1
        yield "Step 1: open the settings panel."

    _patch_chat_path(monkeypatch)
    monkeypatch.setattr(
        "atlas_mind.provider_router.ProviderRouter.stream_chat",
        fake_stream_chat,
    )
    monkeypatch.setattr(atlas_core.voice_engine, "speak", lambda *_a, **_k: None)
    monkeypatch.setattr(atlas_core.voice_engine, "skip", lambda *_a, **_k: None)

    engine = _engine_with_events()
    engine.mode = ModeState.GUIDED

    def _broken_check(_goal: str):
        raise json.JSONDecodeError("corrupt playbooks.json", "", 0)

    engine.playbooks.check_proposal = _broken_check

    _run_handle_input(engine, "install docker on windows", source="user")

    assert not any(t == "query_failed" for t, _ in engine._events)
    assert stream_calls["n"] == 1
    assert engine._guide_playbook_offered is True
    assert engine._playbook_proposal is None
    assert engine._query_semaphore.acquire(blocking=False)
    engine._query_semaphore.release()


def test_skip_playbook_clears_pending_proposal(monkeypatch):
    engine = _engine_with_events()
    engine._playbook_proposal = {
        "goal": "install docker",
        "message": "Reuse saved steps?",
        "steps": [{"action": "click", "target": "Install"}],
    }
    events: list[str] = []
    engine.on_event(lambda t, _p: events.append(t))

    engine.skip_playbook()

    assert engine._playbook_proposal is None
    assert engine._playbook_force_fresh is True
    assert "playbook_skipped" in events


def test_skip_playbook_voice_intent(monkeypatch):
    engine = _engine_with_events()
    engine._playbook_proposal = {
        "goal": "deploy staging",
        "message": "Reuse saved steps?",
        "steps": [],
    }

    handled = engine.try_handle_command("skip playbook")

    assert handled is True
    assert engine._playbook_proposal is None
    assert engine._playbook_force_fresh is True
