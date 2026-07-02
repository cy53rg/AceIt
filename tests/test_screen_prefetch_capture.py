"""Regression: ATLAS_SCREEN_PREFETCH must not double-capture with wants_screen."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import atlas_core
from atlas_core import StateEngine


def _engine(**kwargs) -> StateEngine:
    events: list[tuple[str, dict]] = []
    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="test-screen-prefetch",
        )
    engine.on_event(lambda t, p: events.append((t, p)))
    engine._events = events
    engine.focus_mode = kwargs.get("focus_mode", False)
    engine.audio_watcher.mark_user_typed = MagicMock()
    engine.audio_watcher.get_audio_context = lambda *_a, **_k: ""
    engine.screen_watcher.take_and_analyze = MagicMock(return_value=None)
    return engine


@pytest.fixture(autouse=True)
def _quiet_voice(monkeypatch):
    monkeypatch.setattr(atlas_core.voice_engine, "speak", lambda *_a, **_k: None)
    monkeypatch.setattr(atlas_core.voice_engine, "skip", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _direct_chat_path(monkeypatch):
    """Avoid intent-router side paths so screen prefetch logic is exercised."""
    monkeypatch.setattr(
        "atlas_mind.router.try_route_tools",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "atlas_mind.stack_router.try_stack_answer",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "atlas_mind.provider_router.ProviderRouter.stream_chat",
        lambda *_a, **_k: iter(()),
    )


def test_prefetch_and_wants_screen_do_not_double_capture(monkeypatch):
    """Prefetch vision already captures — wants_screen must not capture again."""
    monkeypatch.setenv("ATLAS_SCREEN_PREFETCH", "1")
    capture_calls = {"n": 0}

    def _capture(*_a, **_k):
        capture_calls["n"] += 1
        return MagicMock(b64="png-bytes")

    monkeypatch.setattr(atlas_core, "capture_screen_b64", _capture)
    engine = _engine()
    engine.screen_watcher.take_and_analyze = MagicMock(return_value="looks like code")

    real_thread = threading.Thread

    def _sync_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        if kwargs.get("name") == "atlas-query":
            def _start():
                t.run()

            t.start = _start
        return t

    with patch("atlas_core.threading.Thread", side_effect=_sync_thread):
        engine.handle_input("what can you see on my screen right now?", source="user")

    assert capture_calls["n"] == 0
    engine.screen_watcher.take_and_analyze.assert_called_once()


def test_concurrent_queries_do_not_double_capture(monkeypatch):
    """Two screen queries 50ms apart must not overlap screen capture."""
    monkeypatch.setenv("ATLAS_SCREEN_PREFETCH", "1")
    capture_calls = {"n": 0}
    capture_lock = threading.Lock()
    query_started = threading.Event()
    allow_finish = threading.Event()

    def _capture(*_a, **_k):
        with capture_lock:
            capture_calls["n"] += 1
        query_started.set()
        allow_finish.wait(timeout=5.0)
        return MagicMock(b64="png-bytes")

    def _prefetch(_prompt: str) -> str:
        _capture()
        return "screen summary"

    monkeypatch.setattr(atlas_core, "capture_screen_b64", _capture)
    engine = _engine()
    engine.screen_watcher.take_and_analyze = MagicMock(side_effect=_prefetch)

    errors: list[Exception] = []

    def _run_query(text: str, source: str) -> None:
        try:
            engine.handle_input(text, source=source)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(
        target=_run_query,
        args=("what can you see on my screen?", "user"),
        name="query-1",
    )
    t1.start()
    assert query_started.wait(timeout=2.0), "first query never started capture"

    time.sleep(0.05)
    t2 = threading.Thread(
        target=_run_query,
        args=("look at my screen please", "mic"),
        name="query-2",
    )
    t2.start()
    time.sleep(0.05)

    assert not errors
    assert capture_calls["n"] == 1
    queued = [p for t, p in engine._events if t == "query_queued"]
    assert len(queued) == 1

    allow_finish.set()
    t1.join(timeout=10.0)

    deadline = time.time() + 10.0
    while time.time() < deadline and capture_calls["n"] < 2:
        time.sleep(0.05)

    assert not errors
    assert capture_calls["n"] == 2
    started = [p for t, p in engine._events if t == "query_started"]
    assert len(started) == 2
