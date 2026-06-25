"""
atlas_stepevent.py — Single source of truth for synchronized agent steps.

StepEvent drives BOTH the animated agent cursor and TTS narration in strict order.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from atlas_logging import get_logger

log = get_logger("stepevent")


@dataclass
class StepEvent:
    step_index: int
    description: str
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    action: str = "move"       # move | click | double | type | scroll | guide
    status: str = "started"    # started | animating | executing | completed | failed
    target: str = ""
    error: str = ""


@dataclass
class _PendingStep:
    event: StepEvent
    do_action: Optional[Callable[[], None]]
    done: threading.Event = field(default_factory=threading.Event)
    result_ok: bool = True


class StepOrchestrator:
    """
    Cross-thread step coordinator.

    Worker threads call ``run_step`` which blocks until the UI handler finishes
    animate → narrate → act → confirm.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._step_counter = 0
        self._ui_handler: Optional[Callable[[_PendingStep], None]] = None
        self.narration_enabled = True
        self.min_step_ms = 400   # minimum pacing even when TTS is off

    def set_ui_handler(self, handler: Callable[[_PendingStep], None]) -> None:
        self._ui_handler = handler

    def next_index(self) -> int:
        with self._lock:
            self._step_counter += 1
            return self._step_counter

    def reset(self) -> None:
        with self._lock:
            self._step_counter = 0

    def run_step(
        self,
        description: str,
        x: int,
        y: int,
        w: int = 0,
        h: int = 0,
        action: str = "click",
        target: str = "",
        do_action: Optional[Callable[[], None]] = None,
        timeout_s: float = 45.0,
    ) -> bool:
        """
        Execute one synchronized step.  Blocks the calling thread until the UI
        handler completes animation + narration + optional physical action.
        """
        idx = self.next_index()
        cx = int(x + w / 2) if w else x
        cy = int(y + h / 2) if h else y
        evt = StepEvent(
            step_index=idx,
            description=description or f"Step {idx}",
            x=cx,
            y=cy,
            w=w,
            h=h,
            action=action,
            status="started",
            target=target,
        )
        pending = _PendingStep(event=evt, do_action=do_action)
        handler = self._ui_handler
        if handler is None:
            log.warning("No UI handler — running action without sync")
            try:
                if do_action:
                    do_action()
            except Exception as exc:
                log.error("Step %d action failed: %s", idx, exc)
                return False
            time.sleep(self.min_step_ms / 1000.0)
            return True

        t0 = time.monotonic()
        try:
            handler(pending)
        except Exception as exc:
            log.error("UI step handler failed: %s", exc, exc_info=True)
            pending.result_ok = False
            pending.done.set()
            return False

        if not pending.done.wait(timeout=timeout_s):
            log.error("Step %d timed out after %.0fs", idx, timeout_s)
            return False
        log.debug("Step %d finished in %.0fms ok=%s", idx, (time.monotonic() - t0) * 1000, pending.result_ok)
        return pending.result_ok

    def emit_only(self, description: str, x: int = 0, y: int = 0, action: str = "guide") -> None:
        """Fire a guide-only step (no physical action)."""
        self.run_step(description, x, y, action=action, do_action=None)
