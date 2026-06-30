"""
atlas_do/killswitch.py — Global emergency stop for goals, tasks, TTS, and queries.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger("atlas_do.killswitch")


class KillSwitch:
    """Thread-safe killswitch invoked by hotkey or /kill command."""

    def __init__(self) -> None:
        self._handlers: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._engaged_at = 0.0

    def register(self, handler: Callable[[], None]) -> None:
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def engage(self) -> None:
        import time

        with self._lock:
            self._engaged_at = time.time()
            handlers = list(self._handlers)
        log.warning("Killswitch engaged — stopping %d handlers", len(handlers))
        for fn in handlers:
            try:
                fn()
            except Exception as exc:
                log.warning("killswitch handler failed: %s", exc)

    @property
    def engaged_at(self) -> float:
        return self._engaged_at
