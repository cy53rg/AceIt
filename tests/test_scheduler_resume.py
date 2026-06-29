"""Crash-safe scheduler resume after simulated daemon restart."""
from __future__ import annotations

import gc
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from atlas_memory import UserMemory
from atlas_scheduler import JobScheduler


@pytest.fixture
def temp_memory():
    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "test.sqlite3"
    memory = UserMemory(db)
    yield memory
    memory = None  # noqa: F841
    gc.collect()
    shutil.rmtree(tmp, ignore_errors=True)


def test_scheduler_resumes_after_restart_mid_delay(temp_memory):
    memory = temp_memory
    steps = [
        {"kind": "log", "message": "step-0"},
        {"kind": "delay", "seconds": 1.5, "label": "wait"},
        {"kind": "log", "message": "step-2"},
    ]
    job_id = memory.create_scheduled_job(name="resume-test", steps=steps)

    scheduler = JobScheduler(memory)
    scheduler._spawn_job(job_id)

    deadline = time.time() + 3.0
    while time.time() < deadline:
        job = memory.get_scheduled_job(job_id)
        if job and job["current_step"] >= 1 and job.get("step_state"):
            break
        time.sleep(0.05)

    job = memory.get_scheduled_job(job_id)
    assert job is not None
    assert job["current_step"] == 1
    assert "delay_until" in job["step_state"]

    # Simulate daemon crash mid-delay — new scheduler resumes from SQLite.
    scheduler.stop()
    with scheduler._lock:
        t = scheduler._threads.get(job_id)
    if t and t.is_alive():
        t.join(timeout=2.0)

    scheduler2 = JobScheduler(memory)
    scheduler2.start()
    job = scheduler2.wait_for_job(job_id, timeout=8.0)
    scheduler2.stop()
    with scheduler2._lock:
        t2 = scheduler2._threads.get(job_id)
    if t2 and t2.is_alive():
        t2.join(timeout=2.0)

    assert job is not None
    assert job["status"] == "completed"
    assert job["current_step"] == 3


def test_enqueue_fake_long_job_persists_steps(temp_memory):
    memory = temp_memory
    scheduler = JobScheduler(memory)
    job_id = scheduler.enqueue_job(
        name="fake-long",
        steps=[{"kind": "delay", "seconds": 0.2, "label": "short wait"}],
    )
    assert job_id > 0
    job = scheduler.wait_for_job(job_id, timeout=5.0)
    scheduler.stop()
    assert job is not None
    assert job["status"] == "completed"
