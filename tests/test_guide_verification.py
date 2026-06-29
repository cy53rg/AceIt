"""Guided step verification — token schema, advance detection, verify wiring."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from atlas_core import StateEngine, verify_step_completion, _GUIDE_ADVANCE_RE
from atlas_recorder import ActionTokenPatterns
from atlas_stepevent import StepEvent


def test_guide_token_optional_expected_state():
    raw = "[[GUIDE: Save button | Click Save | Save dialog is open]]"
    m = ActionTokenPatterns._ACTION_TOKEN_RE.match(raw)
    assert m is not None
    assert m.group(1).upper() == "GUIDE"
    assert m.group(2).strip() == "Save button"
    assert m.group(3).strip() == "Click Save"
    assert m.group(4).strip() == "Save dialog is open"


def test_guide_token_two_segments_still_parses():
    raw = "[[GUIDE: Export menu | Open the File menu]]"
    m = ActionTokenPatterns._ACTION_TOKEN_RE.match(raw)
    assert m is not None
    assert m.group(4) is None


def test_do_token_ignores_extra_segment():
    raw = "[[DO: Submit | click]]"
    m = ActionTokenPatterns._ACTION_TOKEN_RE.match(raw)
    assert m.group(3).strip() == "click"


@pytest.mark.parametrize(
    "text",
    ["done", "OK", "next", "got it", "I'm ready", "move on", "yes."],
)
def test_guide_advance_phrases(text):
    assert _GUIDE_ADVANCE_RE.match(text.strip())


def test_step_event_verification_fields():
    evt = StepEvent(
        step_index=2,
        description="Click Save",
        expected_state="Save dialog visible",
        verified=None,
    )
    assert evt.expected_state == "Save dialog visible"
    assert evt.verified is None


def test_verify_step_completion_delegates_to_spatial():
    spatial = MagicMock()
    spatial.verify.return_value = {
        "completed": True,
        "confidence": 0.9,
        "observed": "Dialog open",
        "discrepancy": None,
    }
    out = verify_step_completion("Save dialog open", "b64data", spatial)
    spatial.verify.assert_called_once_with("Save dialog open", screen_b64="b64data")
    assert out["completed"] is True


def test_try_verify_guide_step_passes_and_clears_pending(monkeypatch):
    engine = StateEngine.__new__(StateEngine)
    engine.mode = engine.mode if hasattr(engine, "mode") else None
    from atlas_core import ModeState
    engine.mode = ModeState.GUIDED
    evt = StepEvent(step_index=1, description="Click Save", expected_state="Dialog open")
    engine._pending_guide = {
        "step_index": 1,
        "instruction": "Click Save",
        "expected_state": "Dialog open",
        "event": evt,
    }
    engine.spatial = MagicMock()
    engine.spatial.verify = MagicMock(return_value={
        "completed": True,
        "confidence": 0.88,
        "observed": "Save dialog is visible",
        "discrepancy": None,
    })
    engine._emit = MagicMock()
    engine._finish_direct_response = MagicMock()
    engine.learning = MagicMock()
    engine.learning.teaching = MagicMock()

    frame = MagicMock()
    frame.b64 = "screen"
    monkeypatch.setattr("atlas_core.capture_screen_b64", lambda: frame)

    mode, prefix = engine._try_verify_guide_step("done")
    assert mode == "passed"
    assert "STEP VERIFIED" in prefix
    assert engine._pending_guide is None
    assert evt.verified is True
    assert engine._emit.call_args_list[-1][0][0] == "step_verified"
    assert engine._emit.call_args_list[-1][0][1]["passed"] is True


def test_try_verify_guide_step_mismatch_responds(monkeypatch):
    engine = StateEngine.__new__(StateEngine)
    from atlas_core import ModeState
    engine.mode = ModeState.GUIDED
    evt = StepEvent(step_index=3, description="Select PDF", expected_state="PDF selected")
    engine._pending_guide = {
        "step_index": 3,
        "instruction": "Select PDF",
        "expected_state": "PDF selected",
        "event": evt,
    }
    engine.spatial = MagicMock()
    engine.spatial.verify = MagicMock(return_value={
        "completed": False,
        "confidence": 0.8,
        "observed": "Word format still selected",
        "discrepancy": "Export type is still DOCX",
    })
    engine._emit = MagicMock()
    engine._finish_direct_response = MagicMock()
    engine.learning = MagicMock()
    engine.learning.teaching = MagicMock()
    monkeypatch.setattr("atlas_core.capture_screen_b64", lambda: MagicMock(b64="x"))

    mode, _ = engine._try_verify_guide_step("next")
    assert mode == "handled"
    engine._finish_direct_response.assert_called_once()
    assert evt.verified is False
