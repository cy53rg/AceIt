"""Phase 3 — self-verification for DO/TASK actions."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import atlas_core
from atlas_core import StateEngine, _TASK_ACTION_MAX_RETRIES
from atlas_vision import ScreenCapture


def _mock_capture(b64: str = "frame") -> ScreenCapture:
    return ScreenCapture(b64, 1.0, (100, 100), (1920, 1080))


def _minimal_engine(**kwargs) -> StateEngine:
    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="test-self-verify",
        )
    engine._task_running = kwargs.get("task_running", True)
    engine.safety_mode = kwargs.get("safety_mode", "off")
    engine._events: list[tuple[str, dict]] = []
    engine.on_event(lambda t, p: engine._events.append((t, p)))
    return engine


@pytest.fixture(autouse=True)
def _quiet_voice(monkeypatch):
    monkeypatch.setattr(atlas_core.voice_engine, "speak", lambda *_a, **_k: None)


def test_action_expect_prefers_planner_field():
    engine = _minimal_engine()
    exp = engine._action_expect(
        {"target": "Save", "expect": "Save dialog closes"},
        "click",
    )
    assert exp == "Save dialog closes"


def test_execute_task_action_verify_passes(monkeypatch):
    engine = _minimal_engine()
    monkeypatch.setattr(engine, "_execute_step", lambda *_a, **_k: "clicked Save")
    monkeypatch.setattr(
        engine,
        "_verify_action_outcome",
        lambda *_a, **_k: {
            "completed": True,
            "confidence": 0.9,
            "observed": "Dialog closed",
            "discrepancy": None,
        },
    )
    result, cont = engine._execute_task_action_with_verify(
        "click",
        {"target": "Save", "expect": "Save dialog closes"},
    )
    assert cont is True
    assert "verified" in result


def test_execute_task_action_verify_retries_then_halts(monkeypatch):
    engine = _minimal_engine()
    exec_calls = {"n": 0}

    def fake_exec(*_a, **_k):
        exec_calls["n"] += 1
        return "clicked Save"

    verify_calls = {"n": 0}

    def fake_verify(*_a, **_k):
        verify_calls["n"] += 1
        return {
            "completed": False,
            "confidence": 0.85,
            "observed": "Dialog still open",
            "discrepancy": "Save prompt visible",
        }

    undo = {"n": 0}
    monkeypatch.setattr(engine, "_execute_step", fake_exec)
    monkeypatch.setattr(engine, "_verify_action_outcome", fake_verify)
    monkeypatch.setattr(
        engine,
        "_attempt_undo",
        lambda: undo.__setitem__("n", undo["n"] + 1) or "undo attempted",
    )
    monkeypatch.setattr(atlas_core.time, "sleep", lambda *_a: None)

    result, cont = engine._execute_task_action_with_verify(
        "click",
        {"target": "Save", "expect": "Save dialog closes"},
    )

    assert cont is False
    assert exec_calls["n"] == _TASK_ACTION_MAX_RETRIES + 1
    assert verify_calls["n"] == _TASK_ACTION_MAX_RETRIES + 1
    assert undo["n"] == 1
    status = [p["text"] for t, p in engine._events if t == "task_status"]
    assert any("Last observed: Dialog still open" in text for text in status)


def test_run_do_with_verify_retries(monkeypatch):
    engine = _minimal_engine(task_running=False)
    locate_calls = {"n": 0}

    def fake_act(*_a, **_k):
        locate_calls["n"] += 1
        return {"found": True, "x": 1, "y": 2, "w": 10, "h": 10}

    verify_n = {"n": 0}

    def fake_verify(*_a, **_k):
        verify_n["n"] += 1
        if verify_n["n"] <= 2:
            return {
                "completed": False,
                "confidence": 0.8,
                "observed": "Button still inactive",
                "discrepancy": "No state change",
            }
        return {
            "completed": True,
            "confidence": 0.9,
            "observed": "Button activated",
            "discrepancy": None,
        }

    monkeypatch.setattr(
        atlas_core,
        "capture_screen_b64",
        lambda *_a, **_k: _mock_capture(),
    )
    monkeypatch.setattr(engine, "act_on_target", fake_act)
    monkeypatch.setattr(engine, "_verify_action_outcome", fake_verify)
    monkeypatch.setattr(atlas_core.time, "sleep", lambda *_a: None)

    engine._run_do_with_verify("Submit", "click", "Submit button becomes enabled")

    assert locate_calls["n"] == 3
    assert verify_n["n"] == 3


def test_verify_task_completion_uses_spatial_verify(monkeypatch):
    engine = _minimal_engine()
    monkeypatch.setattr(
        atlas_core,
        "verify_step_completion",
        lambda task, b64, spatial: {
            "completed": False,
            "confidence": 0.7,
            "observed": "Wrong file tab active",
            "discrepancy": "report.pdf not open",
        },
    )
    out = engine._verify_task_completion("open report.pdf", "screenb64")
    assert out["completed"] is False
    assert out["observed"] == "Wrong file tab active"
    assert "report.pdf" in out["reason"]
