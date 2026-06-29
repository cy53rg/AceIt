"""Test harness for in-process StateEngine (unit tests only)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from atlas_core import StateEngine


def make_state_engine(**kwargs) -> StateEngine:
    """Construct StateEngine without starting background threads."""
    events: list[tuple[str, dict]] = []

    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name=kwargs.get("user_name", "test-harness"),
        )
    engine.on_event(lambda t, p: events.append((t, p)))
    engine._task_running = False
    engine._task_stop = __import__("threading").Event()
    engine.safety_mode = kwargs.get("safety_mode", "off")
    engine._safety_prompt = kwargs.get("safety_prompt")
    engine._task_confirm_cb = kwargs.get("task_confirm_cb")
    engine._events = events
    return engine
