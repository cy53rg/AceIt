"""StateEngine task loop: stuck detection, step ceiling, safety mode."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import atlas_core
from atlas_core import StateEngine, _TASK_MAX_STEPS


def _minimal_engine(**kwargs) -> StateEngine:
    events: list[tuple[str, dict]] = []

    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="test-task-loop",
        )
    engine.on_event(lambda t, p: events.append((t, p)))
    engine._task_running = False
    engine._task_stop = __import__("threading").Event()
    engine.safety_mode = kwargs.get("safety_mode", "off")
    engine._safety_prompt = kwargs.get("safety_prompt")
    engine._events = events
    return engine


@pytest.fixture(autouse=True)
def _quiet_voice(monkeypatch):
    monkeypatch.setattr(atlas_core.voice_engine, "speak", lambda *_a, **_k: None)


def test_task_loop_stuck_on_identical_screen_hashes(monkeypatch):
    engine = _minimal_engine()
    same_frame = "identical-screen-bytes"
    hashes = []

    monkeypatch.setattr(atlas_core, "capture_screen_b64", lambda *_a, **_k: same_frame)
    monkeypatch.setattr(
        engine,
        "_decide_next_step",
        lambda *_a, **_k: {"action": "wait", "seconds": 0.01},
    )

    engine._task_loop("click save three times")

    status_texts = [p["text"] for t, p in engine._events if t == "task_status"]
    assert any("stuck" in text.lower() for text in status_texts)


def test_task_loop_step_ceiling(monkeypatch):
    limit = 3
    monkeypatch.setattr(StateEngine, "_TASK_MAX_STEPS", limit)
    engine = _minimal_engine()
    decide_calls = {"n": 0}

    def never_done(*_a, **_k):
        decide_calls["n"] += 1
        return {"action": "wait", "seconds": 0.01}

    monkeypatch.setattr(atlas_core, "capture_screen_b64", lambda *_a, **_k: f"frame-{decide_calls['n']}")
    monkeypatch.setattr(engine, "_decide_next_step", never_done)
    monkeypatch.setattr(engine, "_execute_step", lambda *_a, **_k: "waited")

    engine._task_loop("never finish")

    status_texts = [p["text"] for t, p in engine._events if t == "task_status"]
    assert any("step limit" in text.lower() for text in status_texts)
    assert decide_calls["n"] == limit


def test_safety_always_blocks_until_confirmed(monkeypatch):
    prompts: list[str] = []
    clicks: list[tuple[int, int]] = []

    def prompt(desc: str) -> bool:
        prompts.append(desc)
        return len(prompts) >= 2

    engine = _minimal_engine(safety_mode="always", safety_prompt=prompt)
    engine.spatial.locate = MagicMock(
        return_value={"found": True, "x": 100, "y": 200, "w": 40, "h": 20},
    )

    def fake_click(x, y):
        clicks.append((x, y))

    monkeypatch.setattr(atlas_core.atlas_hands, "click", fake_click)
    monkeypatch.setattr(
        atlas_core.step_orchestrator,
        "run_step",
        lambda desc, x, y, w, h, **kw: kw["do_action"]() or True,
    )

    first = engine._locate_and_click("Save", double=False)
    assert prompts == ["Clicking Save"]
    assert clicks == []
    assert "denied" in first.lower()

    second = engine._locate_and_click("Save", double=False)
    assert len(prompts) == 2
    assert clicks == [(120, 210)]
