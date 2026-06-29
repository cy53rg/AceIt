"""Teaching performance tracker and self-diagnosis."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from atlas_learning import (
    DRIFT_CHECK_EVERY,
    LearningEngine,
    TeachingPerformanceTracker,
    task_type_key,
)


def test_task_type_key_normalizes_goal():
    assert task_type_key("Guide me through Export PDF in Word") == "export pdf word"


def test_tracker_counters_and_rates():
    t = TeachingPerformanceTracker()
    t.set_task_context("export pdf word")
    t.record_guide_step()
    t.record_guide_step()
    t.record_verified_first_try()
    t.record_correction("dialog still open", "Save prompt visible")
    t.record_research()
    t.record_user_confused()

    diag = TeachingPerformanceTracker._compute_rates(t.snapshot()["counters"])
    assert diag["steps_total"] == 2
    assert diag["steps_verified_first_try"] == 1
    assert diag["steps_needed_correction"] == 1
    assert diag["first_try_rate"] == 0.5
    assert diag["correction_rate"] == 0.5
    assert diag["research_lookups_performed"] == 1


def test_is_user_confused_heuristic():
    assert TeachingPerformanceTracker.is_user_confused("I don't see that button")
    assert TeachingPerformanceTracker.is_user_confused("where?")
    assert TeachingPerformanceTracker.is_user_confused("nope")
    assert not TeachingPerformanceTracker.is_user_confused("done, next step please")


def test_format_user_summary():
    t = TeachingPerformanceTracker()
    for _ in range(14):
        t.record_guide_step()
    for _ in range(11):
        t.record_verified_first_try()
    for _ in range(3):
        t.record_correction()
    t.record_research()
    t.record_research()

    text = t.format_user_summary()
    assert "14" in text
    assert "11" in text
    assert "3" in text
    assert "documentation twice" in text or "2 times" in text


def test_learning_engine_get_self_diagnosis():
    mem = MagicMock()
    mem.recall.return_value = []
    mem._recent_sessions.return_value = []
    eng = LearningEngine(mem, user_id=1)
    eng.teaching.record_guide_step()
    eng.teaching.record_verified_first_try()
    diag = eng.get_self_diagnosis()
    assert diag["steps_total"] == 1
    assert diag["first_try_rate"] == 1.0


def test_get_correction_merges_persona_and_teaching():
    mem = MagicMock()
    mem.recall.return_value = []
    mem._recent_sessions.return_value = []
    eng = LearningEngine(mem, user_id=1)
    eng._pending_correction = "Persona fix"
    eng._pending_teaching_correction = "Teaching fix"
    merged = eng.get_correction()
    assert "Persona fix" in merged
    assert "Teaching fix" in merged
    assert eng.get_correction() is None


def test_persist_teaching_rollup_uses_skill_outcomes():
    mem = MagicMock()
    mem.recall.return_value = []
    mem._recent_sessions.return_value = []
    eng = LearningEngine(mem, user_id=7)
    eng.teaching.set_task_context("export pdf word")
    eng.teaching.record_guide_step()
    eng.persist_teaching_rollup()
    mem.log_skill_outcome.assert_called_once()
    args = mem.log_skill_outcome.call_args[0]
    assert args[0] == 7
    assert args[1] == "teach:export pdf word"


def test_teaching_self_critique_on_turn_cadence(monkeypatch):
    mem = MagicMock()
    mem.recall.return_value = []
    mem._recent_sessions.return_value = []
    mem.extract_facts_from_turn.return_value = []
    mem._get_groq.return_value = MagicMock()
    mem._get_groq.return_value.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Be more specific on targets."))],
    )
    eng = LearningEngine(mem, user_id=1)
    for i in range(DRIFT_CHECK_EVERY):
        eng.teaching.record_guide_step()
        eng.on_turn_complete(f"user {i}", f"assistant {i}")
    corr = eng.get_correction()
    assert corr is not None
    assert "Teaching self-critique" in corr
