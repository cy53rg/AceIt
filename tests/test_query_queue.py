"""Overlapping handle_input calls should queue, not drop."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import atlas_core
from atlas_core import StateEngine


def _engine_with_events() -> StateEngine:
    events: list[tuple[str, dict]] = []
    engine = StateEngine(
        on_chunk=lambda _c: None,
        on_complete=lambda _t: None,
        on_error=lambda _e: None,
        on_coordinates=lambda _d: None,
        on_token_usage=lambda _u: None,
        user_name="test-query-queue",
    )
    engine.on_event(lambda t, p: events.append((t, p)))
    engine._events = events
    engine.audio_watcher.mark_user_typed = MagicMock()
    engine.audio_watcher.get_audio_context = lambda *_a, **_k: ""
    return engine


def _patch_chat_path(monkeypatch) -> None:
    """Keep handle_input on the Groq streaming path (no stack/router/screen)."""
    monkeypatch.setattr(atlas_core, "capture_screen_b64", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "atlas_mind.router.try_route_tools",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "atlas_mind.stack_router.try_stack_answer",
        lambda *_a, **_k: None,
    )


def test_second_query_queues_and_runs_after_first(monkeypatch):
    blocker = threading.Event()
    stream_calls = {"n": 0}
    completed: list[str] = []

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
        if stream_calls["n"] == 1:
            blocker.wait(timeout=2.0)
        yield f"reply-{stream_calls['n']}"

    _patch_chat_path(monkeypatch)
    monkeypatch.setattr(
        "atlas_mind.provider_router.ProviderRouter.stream_chat",
        fake_stream_chat,
    )
    monkeypatch.setattr(atlas_core.voice_engine, "speak", lambda *_a, **_k: None)
    monkeypatch.setattr(atlas_core.voice_engine, "skip", lambda *_a, **_k: None)

    engine = _engine_with_events()

    def on_complete(text: str) -> None:
        completed.append(text)

    engine._on_complete = on_complete

    first = threading.Thread(
        target=lambda: engine.handle_input("first question", source="user"),
        daemon=True,
        name="first-query",
    )
    first.start()
    time.sleep(0.05)
    engine.handle_input("second question", source="mic")

    queued = [p for t, p in engine._events if t == "query_queued"]
    assert len(queued) == 1
    assert queued[0]["text"] == "second question"

    blocker.set()

    deadline = time.time() + 20.0
    while time.time() < deadline:
        if stream_calls["n"] >= 2 and len(completed) >= 2:
            break
        time.sleep(0.05)
    else:
        raise AssertionError(
            f"timed out waiting for queued query; streams={stream_calls['n']} "
            f"completed={completed!r} events={[t for t, _ in engine._events]}"
        )

    first.join(timeout=5.0)

    started = [p for t, p in engine._events if t == "query_started"]
    assert len(started) == 2
    assert not any(t == "query_rejected" for t, _ in engine._events)
    assert not any(t == "router_handled" for t, _ in engine._events)
    assert stream_calls["n"] == 2
    assert completed == ["reply-1", "reply-2"]


def test_third_query_replaces_pending_slot(monkeypatch):
    hold = threading.Event()

    def fake_stream_chat(
        _self,
        *,
        messages,
        model,
        vision,
        engine,
        reasoning_effort=None,
    ):
        hold.wait(timeout=2.0)
        yield "ok"

    _patch_chat_path(monkeypatch)
    monkeypatch.setattr(
        "atlas_mind.provider_router.ProviderRouter.stream_chat",
        fake_stream_chat,
    )
    monkeypatch.setattr(atlas_core.voice_engine, "speak", lambda *_a, **_k: None)
    monkeypatch.setattr(atlas_core.voice_engine, "skip", lambda *_a, **_k: None)

    engine = _engine_with_events()

    first = threading.Thread(
        target=lambda: engine.handle_input("running", source="user"),
        daemon=True,
    )
    first.start()
    time.sleep(0.02)
    engine.handle_input("queued-a", source="mic")
    engine.handle_input("queued-b", source="highlight")

    assert engine._pending_query == ("queued-b", "highlight", None)
    hold.set()
    first.join(timeout=5.0)
