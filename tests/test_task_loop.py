"""StateEngine task loop: stuck detection, step ceiling, safety mode."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import atlas_core
from atlas_core import StateEngine, _TASK_MAX_STEPS
from atlas_vision import ScreenCapture


def _mock_capture(b64: str) -> ScreenCapture:
    return ScreenCapture(b64, 1.0, (100, 100), (1920, 1080))


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

    monkeypatch.setattr(
        atlas_core,
        "capture_screen_b64",
        lambda *_a, **_k: _mock_capture(same_frame),
    )
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

    monkeypatch.setattr(
        atlas_core,
        "capture_screen_b64",
        lambda *_a, **_k: _mock_capture(f"frame-{decide_calls['n']}"),
    )
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


def test_run_task_cancelled_when_confirm_denied(monkeypatch):
    engine = _minimal_engine(safety_mode="always")
    engine._task_confirm_cb = lambda _task: False
    loop_started = {"n": 0}
    monkeypatch.setattr(engine, "_task_loop", lambda *_a: loop_started.__setitem__("n", 1))

    engine.run_task("type hello in notepad")

    assert loop_started["n"] == 0
    assert not getattr(engine, "_task_running", False)
    status = [p.get("text", "") for t, p in engine._events if t == "task_status"]
    assert any("cancelled" in text.lower() for text in status)


def test_run_task_starts_when_confirm_allowed(monkeypatch):
    engine = _minimal_engine(safety_mode="always")
    engine._task_confirm_cb = lambda _task: True
    loop_started = {"n": 0}
    monkeypatch.setattr(engine, "_task_loop", lambda *_a: loop_started.__setitem__("n", 1))

    engine.run_task("type hello in notepad")

    assert loop_started["n"] == 1


def test_execute_step_type_respects_safety_mode(monkeypatch):
    prompts: list[str] = []
    engine = _minimal_engine(
        safety_mode="always",
        safety_prompt=lambda desc: prompts.append(desc) or False,
    )
    type_mock = MagicMock()
    monkeypatch.setattr(atlas_core.atlas_hands, "type_text", type_mock)

    result = engine._execute_step("type", {"text": "hello"})

    assert "denied" in result.lower()
    assert prompts == ["type: {'text': 'hello'}"]
    type_mock.assert_not_called()


def test_execute_step_hotkey_respects_safety_mode(monkeypatch):
    prompts: list[str] = []
    engine = _minimal_engine(
        safety_mode="always",
        safety_prompt=lambda desc: prompts.append(desc) or False,
    )
    hotkey_mock = MagicMock()
    monkeypatch.setattr(atlas_core.atlas_hands, "hotkey", hotkey_mock)

    result = engine._execute_step("hotkey", {"keys": ["ctrl", "s"]})

    assert "denied" in result.lower()
    assert len(prompts) == 1
    hotkey_mock.assert_not_called()


def test_execute_step_type_allowed_when_safety_off(monkeypatch):
    engine = _minimal_engine(safety_mode="off")
    type_mock = MagicMock()
    monkeypatch.setattr(atlas_core.atlas_hands, "type_text", type_mock)
    monkeypatch.setattr(atlas_core.time, "sleep", lambda *_a: None)

    result = engine._execute_step("type", {"text": "hello"})

    assert "typed" in result.lower()
    type_mock.assert_called_once_with("hello")


def test_task_done_requires_verification(monkeypatch):
    engine = _minimal_engine()
    decide_calls = {"n": 0}

    def decide(*_a, **_k):
        decide_calls["n"] += 1
        return {"action": "done", "summary": "Saved the file."}

    monkeypatch.setattr(
        atlas_core,
        "capture_screen_b64",
        lambda *_a, **_k: _mock_capture("frame"),
    )
    monkeypatch.setattr(engine, "_decide_next_step", decide)
    monkeypatch.setattr(
        engine,
        "_verify_task_completion",
        lambda *_a, **_k: {"completed": True, "reason": "Save dialog closed."},
    )

    engine._task_loop("save the document")

    status_texts = [p["text"] for t, p in engine._events if t == "task_status"]
    assert decide_calls["n"] == 1
    assert any("Saved the file." in text for text in status_texts)


def test_false_done_continues_with_verification_reason(monkeypatch):
    engine = _minimal_engine()
    decide_calls = {"n": 0}

    def decide(_task, steps, *_a, **_k):
        decide_calls["n"] += 1
        if decide_calls["n"] == 1:
            return {"action": "done", "summary": "All set."}
        if decide_calls["n"] == 2:
            return {"action": "click", "target": "Save button"}
        return {"action": "done", "summary": "All set."}

    verify_calls = {"n": 0}

    def verify(*_a, **_k):
        verify_calls["n"] += 1
        if verify_calls["n"] == 1:
            return {"completed": False, "reason": "Save dialog still open."}
        return {"completed": True, "reason": "Dialog closed."}

    monkeypatch.setattr(
        atlas_core,
        "capture_screen_b64",
        lambda *_a, **_k: _mock_capture(f"frame-{decide_calls['n']}"),
    )
    monkeypatch.setattr(engine, "_decide_next_step", decide)
    monkeypatch.setattr(engine, "_verify_task_completion", verify)
    monkeypatch.setattr(engine, "_execute_step", lambda *_a, **_k: "clicked")

    engine._task_loop("save the document")

    status_texts = [p["text"] for t, p in engine._events if t == "task_status"]
    assert verify_calls["n"] == 2
    assert decide_calls["n"] == 3
    assert any("not done yet" in text.lower() for text in status_texts)
    assert any("All set." in text for text in status_texts)


def test_false_done_gives_up_after_one_retry(monkeypatch):
    engine = _minimal_engine()

    monkeypatch.setattr(
        atlas_core,
        "capture_screen_b64",
        lambda *_a, **_k: _mock_capture("frame"),
    )
    monkeypatch.setattr(
        engine,
        "_decide_next_step",
        lambda *_a, **_k: {"action": "done", "summary": "Done."},
    )
    monkeypatch.setattr(
        engine,
        "_verify_task_completion",
        lambda *_a, **_k: {
            "completed": False,
            "reason": "Target window still visible.",
        },
    )

    engine._task_loop("close the settings window")

    status_texts = [p["text"] for t, p in engine._events if t == "task_status"]
    assert any("not done yet" in text.lower() for text in status_texts)
    assert any("couldn't confirm" in text.lower() for text in status_texts)
    assert not any(text.startswith("✓ Done.") for text in status_texts)
