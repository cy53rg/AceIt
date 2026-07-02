"""Account switch must wipe chat session and per-user guided state."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import atlas_core
from atlas_core import StateEngine
from atlas_memory import UserMemory


def _temp_memory() -> UserMemory:
    path = Path(tempfile.mkdtemp()) / "account_session_reset.sqlite3"
    return UserMemory(path)


def _engine(mem: UserMemory, name: str) -> StateEngine:
    events: list[tuple[str, dict]] = []
    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name=name,
            memory=mem,
        )
    engine.on_event(lambda t, p: events.append((t, p)))
    engine._events = events
    engine.audio_watcher.mark_user_typed = MagicMock()
    engine.audio_watcher.get_audio_context = lambda *_a, **_k: ""
    return engine


def test_set_user_clears_history_and_emits_session_reset():
    mem = _temp_memory()
    ok_a, _, uid_a = mem.register("Alice")
    ok_b, _, uid_b = mem.register("Bob")
    assert ok_a and ok_b

    mem.remember(uid_a, "profile", "name", "Alice", confidence=0.98, source="profile")
    mem.remember(uid_b, "profile", "name", "Bob", confidence=0.98, source="profile")

    engine = _engine(mem, "Alice")
    assert engine.user_id == uid_a

    engine.session.push_user("who am I")
    engine.session.push_assistant("You are Alice, working on a Python project.")
    engine._session_task_goal = "finish the Python project"
    engine._guide_playbook_offered = True

    engine.set_user(uid_b, "Bob")

    assert engine.user_id == uid_b
    assert engine.session.history_length == 0
    assert engine._session_task_goal == ""
    assert engine._guide_playbook_offered is False
    assert any(t == "session_reset" for t, _ in engine._events)

    messages = engine.session.build_messages(
        "who am I",
        memory_prompt=mem.build_memory_prompt(uid_b),
    )
    history_text = " ".join(
        str(m.get("content", "")) for m in messages if m.get("role") != "system"
    )
    assert "Alice" not in history_text
    assert "Python project" not in history_text

    prompt_blob = " ".join(str(m.get("content", "")) for m in messages)
    assert "Bob" in prompt_blob
    assert "Alice" not in prompt_blob
