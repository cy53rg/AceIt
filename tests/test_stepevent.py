"""Tests for StepEvent orchestrator."""
from __future__ import annotations

import threading
import time

import pytest

from atlas_stepevent import StepEvent, StepOrchestrator, StepOrchestratorStalled


def test_step_orchestrator_no_handler_runs_action():
    orch = StepOrchestrator()
    ran = []

    def action():
        ran.append(1)

    ok = orch.run_step("test click", 10, 20, do_action=action)
    assert ok is True
    assert ran == [1]


def test_step_orchestrator_ui_handler_sync():
    orch = StepOrchestrator()
    results = []

    def ui_handler(pending):
        pending.event.status = "completed"
        time.sleep(0.05)
        pending.done.set()

    orch.set_ui_handler(ui_handler)
    ok = orch.run_step("move", 1, 2, action="guide")
    assert ok is True


def test_step_event_fields():
    evt = StepEvent(step_index=1, description="Click Save", x=100, y=200, action="click")
    assert evt.status == "started"
    assert evt.action == "click"


def test_run_step_resets_timeout_counter_on_success():
    orch = StepOrchestrator()
    orch._consecutive_timeouts = 1

    def ui_handler(pending):
        pending.done.set()

    orch.set_ui_handler(ui_handler)
    assert orch.run_step("ok", 0, 0) is True
    assert orch._consecutive_timeouts == 0


def test_two_consecutive_timeouts_raise_stalled():
    orch = StepOrchestrator()
    orch.set_ui_handler(lambda _pending: None)

    assert orch.run_step("hang", 0, 0, timeout_s=0.05) is False
    assert orch._consecutive_timeouts == 1

    with pytest.raises(StepOrchestratorStalled):
        orch.run_step("hang again", 0, 0, timeout_s=0.05)
