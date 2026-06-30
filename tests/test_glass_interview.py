"""Glass interview copilot — question detection, screen watcher, highlight path."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from atlas_glass.interview import (
    build_interview_brief_block,
    looks_like_interview_question,
    parse_question_detection,
    question_fingerprint,
)
from atlas_glass.interview_watcher import InterviewScreenWatcher


def test_looks_like_interview_question():
    assert looks_like_interview_question("What is your experience with Redis?")
    assert looks_like_interview_question("implement a function to reverse a linked list")
    assert not looks_like_interview_question("ok")


def test_parse_question_detection_json():
    raw = '{"found": true, "question": "Design a URL shortener"}'
    parsed = parse_question_detection(raw)
    assert parsed["found"] is True
    assert "URL" in parsed["question"]


def test_question_fingerprint_stable():
    a = question_fingerprint("What is 2+2?")
    b = question_fingerprint("what   is  2+2?")
    assert a == b


def test_interview_brief_block():
    block = build_interview_brief_block("Senior SWE, be concise")
    assert "<InterviewBrief>" in block
    assert "Senior SWE" in block


def test_interview_watcher_fires_on_question():
    fired: list[str] = []
    watcher = InterviewScreenWatcher(on_question=lambda q: fired.append(q), poll_interval=1.5)
    fake_sw = MagicMock()
    fake_sw.available = True
    fake_sw._vision_query = MagicMock(
        return_value='{"found": true, "question": "Explain CAP theorem briefly"}'
    )
    watcher.bind_screen_watcher(fake_sw)
    with patch("atlas_vision.capture_screen_b64_str", return_value="abc"):
        watcher._poll_once()
    assert fired
    assert "CAP" in fired[0]


def test_handle_glass_interview_question(monkeypatch):
    from atlas_core import StateEngine

    monkeypatch.setattr("threading.Thread", lambda *a, **k: MagicMock(start=MagicMock()))
    with patch("atlas_core.capture_screen_b64", return_value=None):
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="glass-interview-test",
        )
    engine.glass.start()
    engine.set_user_pref("glass_interview_brief", "Answer as a senior engineer.")
    events: list[tuple[str, dict]] = []
    engine.on_event(lambda t, p: events.append((t, p)))
    engine.handle_glass_interview_question(
        "What is polymorphism?",
        source="highlight",
    )
    assert any(t == "glass_question_detected" for t, _ in events)


def test_speaker_auto_answer_trigger(monkeypatch):
    from atlas_core import StateEngine

    started: list[tuple] = []

    class _ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, **kw):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            started.append(self._args)
            if self._target:
                self._target(*self._args, **self._kwargs)

    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    engine = StateEngine(
        on_chunk=lambda _c: None,
        on_complete=lambda _t: None,
        on_error=lambda _e: None,
        on_coordinates=lambda _d: None,
        on_token_usage=lambda _u: None,
        user_name="glass-speaker-test",
    )
    engine.glass.start()
    engine.set_user_pref("glass_auto_answer", True)
    calls: list[str] = []
    engine.handle_glass_interview_question = lambda q, **kw: calls.append(q)  # type: ignore[method-assign]
    engine.ingest_glass_transcript(
        "Can you explain how you would design a rate limiter?",
        "speaker",
    )
    assert started
    assert calls
