"""Groq model health-check banner tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import atlas_core
from atlas_core import StateEngine


def _engine_with_events() -> tuple[StateEngine, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []

    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="test-model-health",
        )
    engine.on_event(lambda t, p: events.append((t, p)))
    return engine, events


def test_model_health_emits_banner_on_decommissioned():
    engine, events = _engine_with_events()

    with patch.object(atlas_core, "_probe_groq_model", side_effect=[True, False]):
        engine._model_health_check()

    retired = [p for t, p in events if t == "model_retired"]
    assert len(retired) == 1
    assert retired[0]["role"] == "chat"
    assert "retired" in retired[0]["message"].lower() or "⚠" in retired[0]["message"]


def test_model_health_silent_when_healthy():
    engine, events = _engine_with_events()

    with patch.object(atlas_core, "_probe_groq_model", return_value=False):
        engine._model_health_check()

    assert not any(t == "model_retired" for t, _ in events)
