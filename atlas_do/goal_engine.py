"""
atlas_do/goal_engine.py — Resumable multi-step goals with checkpoints and step limits.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Optional

from atlas_do.constants import TASK_MAX_STEPS

log = logging.getLogger("atlas_do.goal_engine")

GOAL_MAX_STEPS = min(25, int(TASK_MAX_STEPS))
_STEP_WAIT_S = 2.0
_STEP_TIMEOUT_S = 600.0


class GoalEngine:
    """Background goal runner that survives UI restarts via SQLite checkpoints."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, goal_text: str) -> str:
        goal_text = (goal_text or "").strip()
        if not goal_text:
            raise ValueError("goal text required")
        memory = self._state.memory
        user_id = self._state.user_id
        steps = self._plan_steps(goal_text)
        goal_id = memory.goal_create(
            user_id,
            goal_text,
            steps=steps,
        )
        memory.audit_log(
            user_id,
            "goal",
            f"Started goal: {goal_text[:120]}",
            {"goal_id": goal_id, "steps": len(steps)},
        )
        self._launch(goal_id)
        return goal_id

    def resume_if_needed(self) -> bool:
        active = self._state.memory.goal_get_active(self._state.user_id)
        if not active:
            return False
        if self._running:
            return True
        self._launch(str(active["id"]))
        return True

    def kill(self) -> None:
        self._stop.set()
        self._running = False
        active = self._state.memory.goal_get_active(self._state.user_id)
        if active:
            self._state.memory.goal_update_status(
                self._state.user_id,
                str(active["id"]),
                "killed",
            )
            self._state.memory.audit_log(
                self._state.user_id,
                "goal",
                f"Killed goal: {str(active.get('goal_text', ''))[:80]}",
                {"goal_id": active["id"]},
            )

    def _launch(self, goal_id: str) -> None:
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=1.0)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(goal_id,),
            daemon=True,
            name="atlas-goal",
        )
        self._thread.start()

    def _plan_steps(self, goal_text: str) -> list[str]:
        """Decompose a goal into executable steps (Groq with offline fallback)."""
        fallback = [goal_text]
        try:
            from atlas_core import GROQ_MODEL, groq_client
        except Exception:
            return fallback
        prompt = (
            "Break this user goal into 2-8 short actionable steps for a desktop assistant. "
            "Reply ONLY with a JSON array of strings, no markdown.\n\n"
            f"Goal: {goal_text}"
        )
        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=400,
            )
            raw = (resp.choices[0].message.content or "").strip()
            steps = json.loads(raw)
            if isinstance(steps, list):
                cleaned = [str(s).strip() for s in steps if str(s).strip()]
                if cleaned:
                    return cleaned[:GOAL_MAX_STEPS]
        except Exception as exc:
            log.debug("goal plan via Groq failed: %s", exc)
        return fallback

    def _run(self, goal_id: str) -> None:
        self._running = True
        memory = self._state.memory
        user_id = self._state.user_id
        try:
            row = memory.goal_get(user_id, goal_id)
            if not row:
                return
            steps = row.get("steps") or [row.get("goal_text", "")]
            if not isinstance(steps, list):
                steps = [str(steps)]
            step_index = int(row.get("current_step") or 0)
            memory.goal_update_status(user_id, goal_id, "active")
            self._state._emit("goal_running", {
                "goal_id": goal_id,
                "goal": row.get("goal_text", ""),
                "step": step_index,
                "total": len(steps),
            })
            while step_index < len(steps) and step_index < GOAL_MAX_STEPS:
                if self._stop.is_set():
                    memory.goal_update_status(user_id, goal_id, "killed")
                    return
                step_text = str(steps[step_index]).strip()
                if not step_text:
                    step_index += 1
                    continue
                memory.goal_checkpoint(user_id, goal_id, step_index, step_text)
                memory.audit_log(
                    user_id,
                    "goal_step",
                    f"Step {step_index + 1}/{len(steps)}: {step_text[:100]}",
                    {"goal_id": goal_id, "step_index": step_index},
                )
                self._state._emit("goal_step", {
                    "goal_id": goal_id,
                    "step_index": step_index,
                    "step": step_text,
                    "total": len(steps),
                })
                self._run_step(step_text)
                if self._stop.is_set():
                    memory.goal_update_status(user_id, goal_id, "killed")
                    return
                step_index += 1
                memory.goal_checkpoint(user_id, goal_id, step_index, "")
            memory.goal_update_status(user_id, goal_id, "done")
            memory.audit_log(
                user_id,
                "goal",
                f"Completed goal: {str(row.get('goal_text', ''))[:80]}",
                {"goal_id": goal_id},
            )
            self._state._emit("goal_done", {"goal_id": goal_id})
        except Exception as exc:
            log.warning("goal run failed: %s", exc)
            memory.goal_update_status(user_id, goal_id, "error")
            self._state._emit("goal_error", {"goal_id": goal_id, "error": str(exc)})
        finally:
            self._running = False

    def _run_step(self, step_text: str) -> None:
        if getattr(self._state, "_task_running", False):
            self._state.stop_task()
            time.sleep(0.3)
        self._state.run_task(step_text)
        deadline = time.time() + _STEP_TIMEOUT_S
        while time.time() < deadline:
            if self._stop.is_set():
                self._state.stop_task()
                return
            if not getattr(self._state, "_task_running", False):
                return
            time.sleep(_STEP_WAIT_S)
        self._state.stop_task()
