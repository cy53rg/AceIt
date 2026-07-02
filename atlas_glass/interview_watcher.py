"""
atlas_glass/interview_watcher.py — Poll screen during Glass for new interview questions.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from atlas_glass.interview import (
    QUESTION_DETECT_PROMPT,
    parse_question_detection,
    question_fingerprint,
)

log = logging.getLogger("atlas_glass.interview_watcher")

_DEFAULT_INTERVAL_S = float(
    __import__("os").environ.get("ATLAS_GLASS_POLL_INTERVAL") or 2.5
)
_COOLDOWN_S = 45.0
_MIN_VISION_INTERVAL_S = float(
    __import__("os").environ.get("ATLAS_GLASS_VISION_MIN_INTERVAL") or 8.0
)


class InterviewScreenWatcher:
    """Vision poll loop active only during Glass / Focus sessions."""

    def __init__(
        self,
        *,
        on_question: Callable[[str], None],
        poll_interval: float = _DEFAULT_INTERVAL_S,
    ) -> None:
        self._on_question = on_question
        self._interval = max(1.5, float(poll_interval))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_hash = ""
        self._last_fire_at = 0.0
        self._last_frame_hash: tuple | str = ()
        self._last_vision_at = 0.0
        self._screen_watcher: Any = None

    def bind_screen_watcher(self, watcher: Any) -> None:
        self._screen_watcher = watcher

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="atlas-glass-interview-watch",
        )
        self._thread.start()
        log.info("Interview screen watcher started (interval=%.1fs)", self._interval)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                log.debug("interview watcher poll failed: %s", exc)
            if self._stop.wait(self._interval):
                break

    def _poll_once(self) -> None:
        watcher = self._screen_watcher
        if watcher is None or not getattr(watcher, "available", True):
            return
        try:
            from atlas_vision import capture_screen_b64_str

            b64 = capture_screen_b64_str()
        except Exception:
            b64 = None
        if not b64:
            return
        frame_hash: tuple | str = b64[:256]
        if watcher is not None and hasattr(watcher, "_compute_hash"):
            frame_hash = watcher._compute_hash(b64)
        if frame_hash == self._last_frame_hash:
            return
        now = time.time()
        if (now - self._last_vision_at) < _MIN_VISION_INTERVAL_S:
            return
        self._last_frame_hash = frame_hash
        self._last_vision_at = now
        raw = ""
        try:
            if hasattr(watcher, "_vision_query"):
                raw = watcher._vision_query(b64, QUESTION_DETECT_PROMPT)
            else:
                raw = watcher.take_and_analyze(QUESTION_DETECT_PROMPT) or ""
        except Exception as exc:
            log.debug("interview vision query failed: %s", exc)
            return
        parsed = parse_question_detection(raw)
        if not parsed.get("found"):
            return
        question = str(parsed.get("question") or "").strip()
        if len(question) < 10:
            return
        fp = question_fingerprint(question)
        now = time.time()
        if fp == self._last_hash and (now - self._last_fire_at) < _COOLDOWN_S:
            return
        self._last_hash = fp
        self._last_fire_at = now
        log.info("Interview question detected on screen (%d chars)", len(question))
        self._on_question(question)
