"""APScheduler weekly routine, policy gating, and digest tests."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from atlas_apscheduler import (
    WEEKLY_ROUTINE_JOB_ID,
    AtlasScheduler,
    JobDispatcher,
    JobType,
    WeeklyRoutineRunner,
    classify_task,
    execute_scheduled_job,
    set_scheduler_runtime,
)
from atlas_memory import UserMemory
from atlas_policy import PolicyOutcome, RiskClass


@pytest.fixture
def mem_db(tmp_path):
    return tmp_path / "atlas.sqlite3"


@pytest.fixture
def jobstore_db(tmp_path):
    return tmp_path / "scheduler.sqlite3"


@pytest.fixture
def memory(mem_db):
    return UserMemory(mem_db)


@pytest.fixture
def user_id(memory):
    return memory.create_or_login("scheduler_tester")


@pytest.fixture
def mock_connectors():
    from atlas_connectors.gmail import GmailConnector
    from atlas_connectors.paystack import PaystackConnector

    reg = MagicMock()
    reg.execute.return_value = {"ok": True, "result": {"items": []}}
    reg.get.side_effect = lambda cid: {
        "gmail": GmailConnector(MagicMock()),
        "paystack": PaystackConnector(MagicMock()),
    }.get(cid)
    return reg


@pytest.fixture
def dispatcher(memory, user_id, mock_connectors):
    return JobDispatcher(
        memory,
        user_id,
        connectors=mock_connectors,
        is_attended=lambda: False,
    )


def test_classify_task_routes_email_to_gmail():
    route = classify_task("check my inbox for urgent email")
    assert route is not None
    assert route["connector"] == "gmail"


def test_classify_task_routes_github():
    route = classify_task("review new GitHub issues")
    assert route["connector"] == "github"


def test_unattended_financial_action_queues_pending(memory, user_id, dispatcher):
    result = dispatcher.execute_action({
        "route": "connector",
        "connector": "paystack",
        "method": "initiate_transfer",
        "params": {"amount": 5000, "recipient": "acct_123"},
    }, job_id="test-job")
    assert result.get("pending") is True
    pending = memory.list_scheduler_pending(user_id)
    assert len(pending) == 1
    assert pending[0]["risk_class"] == RiskClass.FINANCIAL.value


def test_read_only_connector_runs_unattended(memory, user_id, dispatcher, mock_connectors):
    result = dispatcher.execute_action({
        "route": "connector",
        "connector": "gmail",
        "method": "list_messages",
        "params": {"max_results": 3},
    })
    assert result.get("ok") is True
    mock_connectors.execute.assert_called_once()


def test_weekly_digest_reflects_mixed_activity_and_pending(memory, user_id, dispatcher):
    memory.log_scheduler_activity(
        user_id,
        source="scheduled",
        category="maintenance",
        summary="Playbook maintenance completed",
        status="completed",
    )
    memory.log_scheduler_activity(
        user_id,
        source="interactive",
        category="task",
        summary="User completed deploy walkthrough",
        status="completed",
    )
    memory.queue_scheduler_pending(
        user_id,
        action_type="connector.initiate_transfer",
        detail="connector://paystack/initiate_transfer",
        risk_class=RiskClass.FINANCIAL.value,
        reason="Financial actions require typed confirmation every time.",
        confirm_phrase="CONFIRM TRANSFER",
        job_id="weekly",
    )

    learning = MagicMock()
    learning.run_weekly_diagnostics.return_value = {
        "persona_drift": "Tone was slightly too formal.",
        "teaching_diagnosis": {"steps_total": 5, "steps_verified_first_try": 4, "first_try_rate": 0.8},
        "teaching_critique": "Weekly teaching note: Use clearer target names.",
    }
    dispatcher.learning = learning

    checklist = (
        {"route": "maintenance", "kind": "playbook_maintenance", "label": "Playbooks"},
        {"route": "connector", "connector": "paystack", "method": "initiate_transfer",
         "params": {"amount": 100, "recipient": "x"}, "label": "Paystack payout"},
    )
    runner = WeeklyRoutineRunner(dispatcher, memory, user_id)
    result = runner.run(checklist=checklist)
    digest = result["digest"]

    assert "Autonomous this week" in digest
    assert "Playbook maintenance" in digest
    assert "Interactive highlights" in digest
    assert "deploy walkthrough" in digest
    assert "Pending your confirmation" in digest
    assert "CONFIRM" in digest or "Financial" in digest or "paystack" in digest.lower()
    assert "Self-diagnosis" in digest
    assert "Weekly teaching note" in digest or "Persona drift" in digest
    assert result["pending"]


def test_apscheduler_fast_forward_runs_weekly_job(memory, user_id, jobstore_db, mock_connectors):
    digests: list[str] = []

    dispatcher = JobDispatcher(
        memory,
        user_id,
        connectors=mock_connectors,
        is_attended=lambda: False,
    )
    sched = AtlasScheduler(
        memory,
        user_id,
        jobstore_path=str(jobstore_db),
        dispatcher=dispatcher,
        on_digest=lambda d, _r: digests.append(d),
    )
    set_scheduler_runtime(sched)
    sched.start()
    try:
        sched.configure_weekly_routine(day_of_week="mon", hour=8)
        sched.fast_forward_job(WEEKLY_ROUTINE_JOB_ID, seconds=0.05)
        deadline = time.time() + 3.0
        while time.time() < deadline and not digests:
            time.sleep(0.05)
    finally:
        sched.shutdown(wait=False)

    assert digests, "weekly routine should fire via fast-forwarded APScheduler job"
    assert "Weekly Atlas digest" in digests[0]


def test_execute_scheduled_job_module_entry(memory, user_id, jobstore_db, mock_connectors):
    from atlas_connectors.registry import ConnectorRegistry

    ConnectorRegistry(db_path=memory.db_path, user_id=user_id)
    dispatcher = JobDispatcher(
        memory, user_id, connectors=mock_connectors, is_attended=lambda: False,
    )
    sched = AtlasScheduler(
        memory,
        user_id,
        jobstore_path=str(jobstore_db),
        dispatcher=dispatcher,
    )
    set_scheduler_runtime(sched)
    memory.upsert_scheduler_job_def(
        user_id,
        job_id="one_off_test",
        job_type=JobType.ONE_OFF.value,
        name="test",
        payload={"goal": "check github issues"},
        trigger={},
    )
    execute_scheduled_job("one_off_test")
    activity = memory.list_scheduler_activity(user_id, since=time.time() - 60)
    assert any("github" in (a.get("summary") or "").lower() or "connector" in (a.get("category") or "")
               for a in activity)


def test_triggered_job_fires_on_event(memory, user_id, jobstore_db):
    fired: list[str] = []
    dispatcher = JobDispatcher(
        memory,
        user_id,
        is_attended=lambda: False,
    )
    original = dispatcher.execute_action
    def _track(action, **kw):
        fired.append(str(action.get("goal") or action.get("method") or action))
        return {"ok": True}
    dispatcher.execute_action = _track  # type: ignore[method-assign]

    sched = AtlasScheduler(memory, user_id, jobstore_path=str(jobstore_db), dispatcher=dispatcher)
    sched.register_triggered_job(
        "gh_assigned",
        name="GitHub assigned",
        payload={"goal": "review assigned GitHub issue"},
        event="github.issue_assigned",
    )
    ids = sched.fire_trigger("github.issue_assigned", detail={"issue": 42})
    assert ids == ["gh_assigned"]
    assert fired
