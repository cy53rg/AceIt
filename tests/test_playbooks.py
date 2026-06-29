"""Playbook distillation — 3-run scenario and size cap."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas_playbooks import (
    PLAYBOOK_MAX_BYTES,
    PlaybookManager,
    enforce_playbook_size,
    keyword_signature,
    sanitize_raw_steps,
)


@pytest.fixture
def mem_db(tmp_path):
    return tmp_path / "playbooks.sqlite3"


@pytest.fixture
def memory(mem_db):
    from atlas_memory import UserMemory

    return UserMemory(mem_db)


@pytest.fixture
def user_id(memory):
    return memory.create_or_login("playbook_tester")


@pytest.fixture
def mgr(memory, user_id):
    return PlaybookManager(
        memory,
        user_id,
        signature_fn=_fixed_sig,
        distill_fn=_fixed_distill,
    )


def _fixed_sig(_goal: str) -> str:
    return "deploy-staging"


def _fixed_distill(goal: str, steps: list[dict]) -> list[dict]:
    return [
        {
            "target": s.get("target") or "element",
            "action": s.get("action") or "click",
            "instruction": s.get("instruction") or f"step for {goal}",
            "expected_state": s.get("expected_state") or "done",
        }
        for s in steps[:4]
    ]


def _sample_steps(variant: int) -> list[dict]:
    return [
        {
            "action": "click",
            "detail": {
                "target": f"Deploy button v{variant}",
                "expected_state": "Build pipeline started",
            },
        },
        {
            "action": "type",
            "detail": {
                "target": "version field",
                "text": f"1.0.{variant}",
                "expected_state": "Version entered",
            },
        },
        {"action": "done", "detail": {}},
    ]


def test_playbook_created_on_second_successful_run(mgr, user_id):
    goal_a = "deploy our React app to staging"
    goal_b = "push the latest build to staging server please"

    r1 = mgr.on_sequence_completed(goal_a, _sample_steps(1), source="task")
    assert r1 is None

    r2 = mgr.on_sequence_completed(goal_b, _sample_steps(2), source="task")
    assert r2 is not None
    assert r2["task_signature"] == "deploy-staging"
    assert len(r2["steps"]) >= 2

    pb = mgr.memory.get_playbook(user_id, "deploy-staging")
    assert pb is not None
    size = len((pb.get("steps_json") or "[]").encode("utf-8"))
    assert size < PLAYBOOK_MAX_BYTES
    assert size < 10_000, "playbook should stay in low-KB range, not grow unbounded"


def test_third_run_proposes_reuse(mgr, user_id):
    mgr.on_sequence_completed("deploy to staging", _sample_steps(1), source="task")
    mgr.on_sequence_completed("deploy app to staging env", _sample_steps(2), source="task")
    mgr.memory.record_playbook_outcome(user_id, "deploy-staging", success=True)

    proposal = mgr.check_proposal("deploy the service to staging again")
    assert proposal is not None
    assert "done this before" in proposal["message"].lower()
    assert proposal["task_signature"] == "deploy-staging"
    assert len(proposal["steps"]) >= 2


def test_playbook_size_stays_bounded_across_runs(mgr, user_id):
    sizes = []
    for i in range(1, 5):
        mgr.on_sequence_completed(f"deploy staging run {i}", _sample_steps(i), source="task")
        pb = mgr.memory.get_playbook(user_id, "deploy-staging")
        if pb:
            sizes.append(len(pb.get("steps_json") or ""))
    assert sizes, "playbook should exist after multiple runs"
    assert max(sizes) < 10_000
    assert sizes[-1] <= sizes[0] + 500, "stored playbook should not balloon with each run"


def test_oversized_playbook_truncated_with_warning(caplog):
    huge = [{"target": "x" * 8000, "action": "click", "instruction": "y" * 8000}] * 20
    trimmed, size = enforce_playbook_size(huge)
    assert size <= PLAYBOOK_MAX_BYTES
    assert len(trimmed) < len(huge)


def test_sanitize_strips_non_procedure_fields():
    raw = [
        {
            "action": "click",
            "detail": {"target": "Save", "screen_b64": "AAA", "expected_state": "saved"},
        },
        {"action": "done", "detail": {}},
    ]
    out = sanitize_raw_steps(raw)
    assert len(out) == 1
    assert "screen_b64" not in json.dumps(out)


def test_failure_increments_playbook_failure_count(mgr, user_id):
    mgr.on_sequence_completed("deploy staging", _sample_steps(1), source="task")
    mgr.on_sequence_completed("deploy staging again", _sample_steps(2), source="task")
    mgr.on_sequence_failed("deploy staging", task_signature="deploy-staging", used_playbook=True)
    pb = mgr.memory.get_playbook(user_id, "deploy-staging")
    assert int(pb.get("failure_count") or 0) >= 1


def test_prune_bad_playbooks(memory, user_id):
    memory.upsert_playbook(
        user_id,
        task_signature="bad-proc",
        goal="bad",
        steps_json='[{"action":"click","target":"x"}]',
    )
    with memory._write_lock, memory._connect() as conn:
        conn.execute(
            "UPDATE playbooks SET success_count = 0, failure_count = 5 WHERE task_signature = ?",
            ("bad-proc",),
        )
    pruned = memory.prune_bad_playbooks()
    assert pruned >= 1
    assert memory.get_playbook(user_id, "bad-proc") is None


def test_keyword_signature_fallback():
    a = keyword_signature("Deploy to staging")
    b = keyword_signature("deploy to staging!")
    assert a == b
