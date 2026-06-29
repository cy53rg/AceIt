"""
atlas_scheduler.py — Crash-safe background job scheduler (Phase E).

Persists job state to SQLite (via UserMemory) after every step so a daemon
restart resumes from the last completed step instead of losing work.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Optional

from atlas_logging import get_logger
from atlas_memory import UserMemory

log = get_logger("scheduler")

StepHandler = Callable[[dict[str, Any], dict[str, Any]], None]


class JobScheduler:
    """
    Executes multi-step jobs on background threads.

    Step kinds (built-in):
      - delay: {"kind": "delay", "seconds": N, "label": "..."}
      - log:   {"kind": "log", "message": "..."}
    """

    def __init__(self, memory: UserMemory) -> None:
        self.memory = memory
        self._lock = threading.Lock()
        self._threads: dict[int, threading.Thread] = {}
        self._stop = threading.Event()
        self._custom_handlers: dict[str, StepHandler] = {}

    def register_step_handler(self, kind: str, handler: StepHandler) -> None:
        self._custom_handlers[kind] = handler

    def start(self) -> None:
        """Resume any incomplete jobs after daemon startup."""
        for job in self.memory.list_resumable_jobs():
            self._spawn_job(job["id"])

    def stop(self) -> None:
        self._stop.set()

    def enqueue_job(
        self,
        *,
        user_id: int = 0,
        name: str = "",
        steps: list[dict] | None = None,
        job_type: str = "generic",
    ) -> int:
        job_id = self.memory.create_scheduled_job(
            user_id=user_id,
            name=name,
            job_type=job_type,
            steps=steps or [],
        )
        self._spawn_job(job_id)
        return job_id

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        return self.memory.get_scheduled_job(job_id)

    def wait_for_job(
        self,
        job_id: int,
        *,
        timeout: float = 30.0,
        poll: float = 0.05,
    ) -> dict[str, Any] | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.get_job(job_id)
            if job and job["status"] in ("completed", "failed", "cancelled"):
                with self._lock:
                    t = self._threads.get(job_id)
                if t and t.is_alive():
                    t.join(timeout=1.0)
                return job
            time.sleep(poll)
        with self._lock:
            t = self._threads.get(job_id)
        if t and t.is_alive():
            t.join(timeout=1.0)
        return self.get_job(job_id)

    def _spawn_job(self, job_id: int) -> None:
        with self._lock:
            if job_id in self._threads and self._threads[job_id].is_alive():
                return
            t = threading.Thread(
                target=self._run_job,
                args=(job_id,),
                daemon=True,
                name=f"atlas-job-{job_id}",
            )
            self._threads[job_id] = t
            t.start()

    def _run_job(self, job_id: int) -> None:
        job = self.memory.get_scheduled_job(job_id)
        if not job:
            return
        if job["status"] in ("completed", "failed", "cancelled"):
            return

        if job["status"] == "pending":
            self.memory.update_scheduled_job(
                job_id,
                status="running",
                started_at=time.time(),
            )

        steps: list[dict] = job["steps"]
        idx = int(job["current_step"])
        step_state = dict(job.get("step_state") or {})

        while idx < len(steps):
            if self._stop.is_set():
                return
            step = steps[idx]
            try:
                self._execute_step(job_id, job, idx, step, step_state)
            except Exception as exc:
                log.exception("job %s step %s failed", job_id, idx)
                self.memory.update_scheduled_job(
                    job_id,
                    status="failed",
                    error=str(exc),
                    step_state={},
                )
                return

            idx += 1
            step_state = {}
            self.memory.update_scheduled_job(
                job_id,
                current_step=idx,
                step_state={},
            )

        self.memory.update_scheduled_job(
            job_id,
            status="completed",
            completed_at=time.time(),
            step_state={},
        )
        log.info("job %s completed (%s)", job_id, job.get("name"))

    def _execute_step(
        self,
        job_id: int,
        job: dict[str, Any],
        idx: int,
        step: dict[str, Any],
        step_state: dict[str, Any],
    ) -> None:
        kind = str(step.get("kind") or "log").lower()
        log.info(
            "job %s step %s/%s kind=%s",
            job_id,
            idx + 1,
            len(job["steps"]),
            kind,
        )

        if kind == "delay":
            seconds = float(step.get("seconds") or 0)
            delay_until = step_state.get("delay_until")
            now = time.time()
            if delay_until is None:
                delay_until = now + seconds
                self.memory.update_scheduled_job(
                    job_id,
                    step_state={"delay_until": delay_until, "seconds": seconds},
                )
                step_state["delay_until"] = delay_until
            remaining = float(delay_until) - time.time()
            if remaining > 0:
                end = time.time() + remaining
                while time.time() < end:
                    if self._stop.is_set():
                        return
                    time.sleep(min(0.25, remaining))
                    remaining = float(delay_until) - time.time()
            return

        if kind == "log":
            log.info("scheduled job %s: %s", job_id, step.get("message", ""))
            return

        handler = self._custom_handlers.get(kind)
        if handler:
            handler(step, job)
            return

        raise ValueError(f"unknown step kind: {kind}")
