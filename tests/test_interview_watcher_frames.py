"""Interview watcher frame buffer must not grow without bound."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from atlas_glass.interview_watcher import InterviewScreenWatcher, _FRAME_MAXLEN, _FRAME_TTL_S


def _frame_bytes(watcher: InterviewScreenWatcher) -> int:
    return sum(len(frame) for _, frame in watcher.frames)


def test_store_frame_prunes_by_ttl():
    watcher = InterviewScreenWatcher(on_question=lambda _q: None)
    base = 1_000_000.0

    with patch("time.time", return_value=base):
        watcher._store_frame("old-frame")

    with patch("time.time", return_value=base + _FRAME_TTL_S + 1):
        watcher._store_frame("new-frame")

    assert len(watcher.frames) == 1
    assert watcher.frames[-1][1] == "new-frame"


def test_store_frame_respects_maxlen():
    watcher = InterviewScreenWatcher(on_question=lambda _q: None)
    base = 2_000_000.0

    for i in range(_FRAME_MAXLEN + 50):
        with patch("time.time", return_value=base + i * 0.01):
            watcher._store_frame(f"frame-{i}")

    assert len(watcher.frames) <= _FRAME_MAXLEN


def test_simulated_long_interview_memory_plateau():
    pytest.importorskip("psutil")
    import psutil

    watcher = InterviewScreenWatcher(on_question=lambda _q: None)
    frame = "X" * (256 * 1024)  # ~256 KB per capture
    process = psutil.Process()
    base_rss = process.memory_info().rss
    peak_rss = base_rss
    peak_bytes = 0

    base = time.time()
    # Simulate 1 hour of polling every 10 seconds (360 samples).
    for step in range(360):
        now = base + step * 10
        with patch("time.time", return_value=now):
            watcher._store_frame(frame)
        peak_bytes = max(peak_bytes, _frame_bytes(watcher))
        peak_rss = max(peak_rss, process.memory_info().rss)

    assert len(watcher.frames) <= _FRAME_MAXLEN
    assert peak_bytes <= 55 * 1024 * 1024
    assert (peak_rss - base_rss) <= 80 * 1024 * 1024


def test_poll_once_stores_captured_frame():
    watcher = InterviewScreenWatcher(on_question=lambda _q: None, poll_interval=1.5)
    fake_sw = MagicMock()
    fake_sw.available = True
    fake_sw._vision_query = MagicMock(return_value='{"found": false}')
    watcher.bind_screen_watcher(fake_sw)

    with patch("atlas_vision.capture_screen_b64_str", return_value="screen-bytes"):
        watcher._poll_once()

    assert len(watcher.frames) == 1
    assert watcher.frames[0][1] == "screen-bytes"
