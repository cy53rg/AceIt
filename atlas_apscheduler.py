"""
atlas_apscheduler.py — APScheduler-backed job engine with PolicyEngine gating.

Job types: ONE_OFF, RECURRING, TRIGGERED.
All scheduled actions execute through the same policy layer as interactive chat.
CONFIRM_TYPED / ASK while unattended → queue pending approval, never auto-escalate.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from atlas_data import DEFAULT_SAFETY_MODE
from atlas_logging import get_logger
from atlas_memory import UserMemory
from atlas_policy import PolicyContext, PolicyEngine, PolicyOutcome, RiskClass

log = get_logger("apscheduler")

WEEKLY_ROUTINE_JOB_ID = "weekly_routine"

_RUNTIME: Optional["AtlasScheduler"] = None


class JobType(str, Enum):
    ONE_OFF = "ONE_OFF"
    RECURRING = "RECURRING"
    TRIGGERED = "TRIGGERED"


# Delegation: classify free-text tasks → connector/skill routes.
DISPATCH_RULES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    (re.compile(r"\b(email|gmail|inbox|mail)\b", re.I), {
        "route": "connector",
        "connector": "gmail",
        "method": "list_messages",
        "params": {"max_results": 10},
    }),
    (re.compile(r"\b(github|issue|pull request|\bpr\b)\b", re.I), {
        "route": "connector",
        "connector": "github",
        "method": "list_issues",
        "params": {},
    }),
    (re.compile(r"\b(notion|task due|tasks due)\b", re.I), {
        "route": "connector",
        "connector": "notion",
        "method": "search_pages",
        "params": {"query": "due"},
    }),
    (re.compile(r"\b(paystack|transfer|payment|payout)\b", re.I), {
        "route": "connector",
        "connector": "paystack",
        "method": "initiate_transfer",
        "params": {"amount": 0, "recipient": "scheduled-placeholder"},
    }),
    (re.compile(r"\b(ssh|remote server|remote host)\b", re.I), {
        "route": "connector",
        "connector": "ssh",
        "method": "run",
        "target": "",
        "command": "",
    }),
    (re.compile(r"\b(playbook|procedure cleanup)\b", re.I), {
        "route": "maintenance",
        "kind": "playbook_maintenance",
    }),
)


def classify_task(goal: str) -> Optional[dict[str, Any]]:
    text = (goal or "").strip()
    if not text:
        return None
    for pattern, spec in DISPATCH_RULES:
        if pattern.search(text):
            return dict(spec)
    return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def execute_scheduled_job(job_id: str) -> None:
    """APScheduler entrypoint — must be module-level for persistence."""
    if _RUNTIME is None:
        log.error("AtlasScheduler runtime not initialized (job=%s)", job_id)
        return
    _RUNTIME.dispatch(job_id)


def set_scheduler_runtime(scheduler: "AtlasScheduler") -> None:
    global _RUNTIME
    _RUNTIME = scheduler


class JobDispatcher:
    """Policy-gated execution for scheduled and delegated tasks."""

    def __init__(
        self,
        memory: UserMemory,
        user_id: int,
        *,
        connectors: Any = None,
        policy: PolicyEngine | None = None,
        learning: Any = None,
        playbooks: Any = None,
        is_attended: Callable[[], bool] | None = None,
        on_notify: Callable[[dict], None] | None = None,
        safety_mode: str = DEFAULT_SAFETY_MODE,
        fs_access_active: bool = False,
        execution_blocked: bool = False,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self.connectors = connectors
        self.policy = policy or PolicyEngine(memory.db_path, user_id=user_id)
        self.learning = learning
        self.playbooks = playbooks
        self._is_attended = is_attended or (lambda: False)
        self._on_notify = on_notify
        self.safety_mode = safety_mode
        self.fs_access_active = fs_access_active
        self.execution_blocked = execution_blocked

    def _ctx(self) -> PolicyContext:
        return PolicyContext(
            safety_mode=self.safety_mode,
            fs_access_active=self.fs_access_active,
            execution_blocked=self.execution_blocked,
        )

    def _log_activity(
        self,
        *,
        source: str,
        category: str,
        summary: str,
        detail: dict | None = None,
        status: str = "completed",
    ) -> None:
        self.memory.log_scheduler_activity(
            self.user_id,
            source=source,
            category=category,
            summary=summary,
            detail=detail,
            status=status,
        )

    def _queue_pending(
        self,
        *,
        action_type: str,
        detail: str,
        risk_class: RiskClass,
        auth: Any,
        job_id: str = "",
    ) -> dict[str, Any]:
        pid = self.memory.queue_scheduler_pending(
            self.user_id,
            action_type=action_type,
            detail=detail,
            risk_class=risk_class.value,
            confirm_phrase=getattr(auth, "confirm_phrase", "") or "",
            reason=getattr(auth, "reason", "") or "",
            audit_id=getattr(auth, "audit_id", "") or "",
            job_id=job_id,
        )
        summary = f"Pending approval: {action_type} — {detail[:120]}"
        self._log_activity(
            source="scheduled",
            category="pending",
            summary=summary,
            detail={"pending_id": pid, "action_type": action_type},
            status="pending_approval",
        )
        if self._on_notify:
            self._on_notify({
                "type": "scheduler_pending",
                "pending_id": pid,
                "action_type": action_type,
                "detail": detail,
                "reason": getattr(auth, "reason", ""),
            })
        return {
            "ok": False,
            "pending": True,
            "pending_id": pid,
            "reason": getattr(auth, "reason", ""),
        }

    def execute_action(
        self,
        action: dict[str, Any],
        *,
        job_id: str = "",
        source: str = "scheduled",
    ) -> dict[str, Any]:
        """Run one action through PolicyEngine; queue pending if unattended."""
        route = str(action.get("route") or action.get("type") or "").lower()
        attended = self._is_attended()

        if route == "maintenance":
            kind = str(action.get("kind") or "")
            if kind == "playbook_maintenance" and self.playbooks:
                result = self.playbooks.run_maintenance()
                self._log_activity(
                    source=source,
                    category="maintenance",
                    summary=f"Playbook maintenance: {result}",
                    detail=result,
                )
                return {"ok": True, "result": result}
            return {"ok": False, "error": f"unknown maintenance kind: {kind}"}

        if route == "connector" or action.get("connector"):
            return self._execute_connector(action, job_id=job_id, source=source, attended=attended)

        if route == "task" or action.get("goal"):
            goal = str(action.get("goal") or "")
            auth = self.policy.authorize(
                "task.run",
                goal,
                RiskClass.SHELL_DANGEROUS,
                context=self._ctx(),
            )
            if auth.decision in (PolicyOutcome.CONFIRM_TYPED, PolicyOutcome.ASK):
                if not attended:
                    return self._queue_pending(
                        action_type="task.run",
                        detail=goal,
                        risk_class=auth.risk_class,
                        auth=auth,
                        job_id=job_id,
                    )
                return {"ok": False, "denied": True, "reason": "Task requires confirmation."}
            if auth.decision == PolicyOutcome.DENY:
                return {"ok": False, "denied": True, "reason": auth.reason}
            self._log_activity(
                source=source,
                category="task",
                summary=f"Delegated task queued for interactive loop: {goal[:100]}",
                detail={"goal": goal},
            )
            return {"ok": True, "delegated": True, "goal": goal}

        return {"ok": False, "error": "unknown action route"}

    def _execute_connector(
        self,
        action: dict[str, Any],
        *,
        job_id: str,
        source: str,
        attended: bool,
    ) -> dict[str, Any]:
        connector_id = str(action.get("connector") or "")
        method = str(action.get("method") or "")
        params = dict(action.get("params") or {})
        if not self.connectors:
            return {"ok": False, "error": "connector registry unavailable"}

        from atlas_connectors.registry import ACTION_RISK_MAP

        key = f"{connector_id}.{method}"
        risk = ACTION_RISK_MAP.get(key)
        if risk is None and self.connectors:
            conn = self.connectors.get(connector_id)
            if conn is not None:
                for spec in conn.list_actions():
                    if spec.method == method:
                        risk = spec.risk_class
                        break
        if risk is None:
            risk = RiskClass.WRITE_SCOPED
        detail = f"connector://{connector_id}/{method}?{json.dumps(params, sort_keys=True)}"
        auth = self.policy.authorize(
            f"connector.{method}",
            detail,
            risk,
            context=self._ctx(),
            user_id=self.user_id,
        )

        if auth.decision in (PolicyOutcome.CONFIRM_TYPED, PolicyOutcome.ASK):
            if not attended:
                return self._queue_pending(
                    action_type=f"connector.{method}",
                    detail=detail,
                    risk_class=risk,
                    auth=auth,
                    job_id=job_id,
                )
            return {"ok": False, "denied": True, "reason": auth.reason or "Confirmation required."}

        if auth.decision == PolicyOutcome.DENY:
            self._log_activity(
                source=source,
                category="connector",
                summary=f"Denied: {connector_id}.{method}",
                detail={"reason": auth.reason},
                status="denied",
            )
            return {"ok": False, "denied": True, "reason": auth.reason}

        result = self.connectors.execute(
            connector_id,
            method,
            safety_mode=self.safety_mode,
            fs_access_active=self.fs_access_active,
            execution_blocked=self.execution_blocked,
            **params,
        )
        ok = bool(result.get("ok"))
        self._log_activity(
            source=source,
            category="connector",
            summary=f"{connector_id}.{method}: {'ok' if ok else result.get('reason', 'failed')}",
            detail=result,
            status="completed" if ok else "failed",
        )
        return result

    def dispatch_goal(self, goal: str, *, job_id: str = "", source: str = "scheduled") -> dict[str, Any]:
        """Classify and route a free-text goal."""
        route = classify_task(goal)
        if route:
            return self.execute_action(route, job_id=job_id, source=source)
        return self.execute_action({"route": "task", "goal": goal}, job_id=job_id, source=source)


class WeeklyRoutineRunner:
    """Configurable weekly checklist → single digest notification."""

    CHECKLIST: tuple[dict[str, Any], ...] = (
        {"route": "maintenance", "kind": "playbook_maintenance", "label": "Playbook health"},
        {"route": "connector", "connector": "gmail", "method": "list_messages", "params": {"max_results": 5}, "label": "Gmail triage"},
        {"route": "connector", "connector": "github", "method": "list_issues", "params": {}, "label": "GitHub issues/PRs"},
        {"route": "connector", "connector": "notion", "method": "search_pages", "params": {"query": "due today"}, "label": "Notion tasks due"},
    )

    def __init__(self, dispatcher: JobDispatcher, memory: UserMemory, user_id: int) -> None:
        self.dispatcher = dispatcher
        self.memory = memory
        self.user_id = user_id

    def run(self, *, checklist: tuple[dict[str, Any], ...] | None = None) -> dict[str, Any]:
        week_ago = time.time() - (7 * 86400)
        items = self._filter_checklist(checklist or self.CHECKLIST)
        checklist_results: list[dict[str, Any]] = []

        for item in items:
            label = item.get("label") or item.get("kind") or item.get("method") or "step"
            try:
                result = self.dispatcher.execute_action(
                    dict(item),
                    job_id=WEEKLY_ROUTINE_JOB_ID,
                    source="weekly_routine",
                )
                checklist_results.append({"label": label, "result": result})
            except Exception as exc:
                log.warning("weekly checklist item %s failed: %s", label, exc)
                checklist_results.append({"label": label, "error": str(exc)})

        diagnostics: dict[str, Any] = {}
        if self.dispatcher.learning is not None:
            try:
                diagnostics = self.dispatcher.learning.run_weekly_diagnostics()
            except Exception as exc:
                log.warning("weekly diagnostics failed: %s", exc)
                diagnostics = {"error": str(exc)}

        activity = self.memory.list_scheduler_activity(self.user_id, since=week_ago, limit=50)
        pending = self.memory.list_scheduler_pending(self.user_id, status="pending")

        digest = self._build_digest(activity, pending, checklist_results, diagnostics)
        self.memory.log_scheduler_activity(
            self.user_id,
            source="weekly_routine",
            category="digest",
            summary=digest[:500],
            detail={
                "checklist": checklist_results,
                "diagnostics": diagnostics,
                "pending_count": len(pending),
            },
            status="completed",
        )
        return {
            "digest": digest,
            "checklist": checklist_results,
            "diagnostics": diagnostics,
            "pending": pending,
            "activity_count": len(activity),
        }

    def _filter_checklist(
        self, items: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        """Skip stub connectors (gmail/notion) when not connected."""
        reg = getattr(self.dispatcher, "connectors", None)
        out: list[dict[str, Any]] = []
        for item in items:
            if item.get("route") != "connector":
                out.append(dict(item))
                continue
            cid = str(item.get("connector") or "")
            if cid in ("gmail", "notion") and reg is not None:
                conn = reg.get(cid)
                if conn is None or not conn.is_connected():
                    log.info("weekly routine: skipping unconfigured connector %s", cid)
                    continue
            out.append(dict(item))
        return tuple(out)

    def _build_digest(
        self,
        activity: list[dict],
        pending: list[dict],
        checklist: list[dict],
        diagnostics: dict,
    ) -> str:
        lines = ["**Weekly Atlas digest**", ""]

        auto = [a for a in activity if a.get("source") in ("scheduled", "weekly_routine") and a.get("status") == "completed"]
        interactive = [a for a in activity if a.get("source") == "interactive"]
        lines.append("**Autonomous this week**")
        if auto:
            for row in auto[:8]:
                lines.append(f"- {row.get('summary', '')}")
        else:
            lines.append("- (no scheduled actions logged yet)")

        lines.append("")
        lines.append("**Interactive highlights**")
        if interactive:
            for row in interactive[:5]:
                lines.append(f"- {row.get('summary', '')}")
        else:
            lines.append("- (none logged)")

        lines.append("")
        lines.append("**Pending your confirmation**")
        if pending:
            for row in pending[:10]:
                lines.append(f"- [{row.get('risk_class')}] {row.get('reason') or row.get('detail', '')[:100]}")
        else:
            lines.append("- Nothing waiting.")

        lines.append("")
        lines.append("**Self-diagnosis**")
        diag = diagnostics.get("teaching_diagnosis") or {}
        if diag.get("steps_total"):
            lines.append(
                f"- Teaching: {diag.get('steps_verified_first_try', 0)}/{diag.get('steps_total', 0)} "
                f"first-try ({diag.get('first_try_rate', 0):.0%})"
            )
        drift = diagnostics.get("persona_drift")
        if drift:
            lines.append(f"- Persona drift note: {drift[:200]}")
        critique = diagnostics.get("teaching_critique")
        if critique:
            lines.append(f"- {critique[:240]}")

        lines.append("")
        lines.append("**This week's checklist**")
        for item in checklist:
            label = item.get("label", "?")
            res = item.get("result") or {}
            if res.get("pending"):
                lines.append(f"- {label}: waiting for your approval")
            elif res.get("ok"):
                lines.append(f"- {label}: done")
            elif item.get("error"):
                lines.append(f"- {label}: skipped ({item['error'][:60]})")
            else:
                lines.append(f"- {label}: {res.get('reason') or res.get('error') or 'checked'}")

        return "\n".join(lines)


class AtlasScheduler:
    """APScheduler wrapper with SQLite persistence and Atlas job metadata."""

    def __init__(
        self,
        memory: UserMemory,
        user_id: int,
        *,
        jobstore_path: str | None = None,
        dispatcher: JobDispatcher | None = None,
        on_digest: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self._lock = threading.Lock()
        self._on_digest = on_digest

        if jobstore_path is None:
            from atlas_data import atlas_data_dir
            jobstore_path = str((atlas_data_dir() / "atlas_scheduler.sqlite3").as_posix())

        url = f"sqlite:///{jobstore_path}"
        jobstores = {"default": SQLAlchemyJobStore(url=url)}
        self._apscheduler = BackgroundScheduler(jobstores=jobstores, timezone="UTC")
        self.dispatcher = dispatcher or JobDispatcher(memory, user_id)
        self.weekly = WeeklyRoutineRunner(self.dispatcher, memory, user_id)

    def start(self) -> None:
        set_scheduler_runtime(self)
        if not self._apscheduler.running:
            self._apscheduler.start()
        log.info("AtlasScheduler started (APScheduler + SQLite jobstore)")

    def shutdown(self, wait: bool = False) -> None:
        if self._apscheduler.running:
            self._apscheduler.shutdown(wait=wait)

    def dispatch(self, job_id: str) -> None:
        job_def = self.memory.get_scheduler_job_def(job_id)
        if not job_def or not int(job_def.get("enabled", 1)):
            log.warning("unknown or disabled job: %s", job_id)
            return

        payload = job_def.get("payload") or {}
        job_type = str(job_def.get("job_type") or "")

        try:
            if job_id == WEEKLY_ROUTINE_JOB_ID or payload.get("kind") == "weekly_routine":
                checklist_raw = payload.get("checklist")
                checklist = tuple(checklist_raw) if checklist_raw else None
                result = self.weekly.run(checklist=checklist)
                digest = result.get("digest") or ""
                if self._on_digest and digest:
                    self._on_digest(digest, result)
                return

            if payload.get("goal"):
                self.dispatcher.dispatch_goal(str(payload["goal"]), job_id=job_id)
                return

            if payload.get("action"):
                self.dispatcher.execute_action(dict(payload["action"]), job_id=job_id)
                return

            if payload.get("actions"):
                for action in payload["actions"]:
                    self.dispatcher.execute_action(dict(action), job_id=job_id)
        except Exception as exc:
            log.exception("scheduled job %s failed", job_id)
            self.memory.log_scheduler_activity(
                self.user_id,
                source="scheduled",
                category="error",
                summary=f"Job {job_id} failed: {exc}",
                status="failed",
            )

    def _register_apscheduler_job(
        self,
        job_id: str,
        trigger: Any,
        *,
        replace_existing: bool = True,
    ) -> None:
        self._apscheduler.add_job(
            execute_scheduled_job,
            trigger=trigger,
            id=job_id,
            kwargs={"job_id": job_id},
            replace_existing=replace_existing,
            misfire_grace_time=3600,
        )

    def schedule_one_off(
        self,
        job_id: str,
        *,
        name: str,
        run_at: datetime,
        payload: dict[str, Any],
    ) -> None:
        self.memory.upsert_scheduler_job_def(
            self.user_id,
            job_id=job_id,
            job_type=JobType.ONE_OFF.value,
            name=name,
            payload=payload,
            trigger={"run_at": run_at.isoformat()},
        )
        self._register_apscheduler_job(job_id, DateTrigger(run_date=run_at))

    def schedule_recurring_cron(
        self,
        job_id: str,
        *,
        name: str,
        payload: dict[str, Any],
        day_of_week: str = "mon",
        hour: int = 8,
        minute: int = 0,
    ) -> None:
        trigger = CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute, timezone="UTC")
        self.memory.upsert_scheduler_job_def(
            self.user_id,
            job_id=job_id,
            job_type=JobType.RECURRING.value,
            name=name,
            payload=payload,
            trigger={"day_of_week": day_of_week, "hour": hour, "minute": minute},
        )
        self._register_apscheduler_job(job_id, trigger)

    def register_triggered_job(
        self,
        job_id: str,
        *,
        name: str,
        payload: dict[str, Any],
        event: str,
    ) -> None:
        """Register metadata; job is fired via fire_trigger(event)."""
        self.memory.upsert_scheduler_job_def(
            self.user_id,
            job_id=job_id,
            job_type=JobType.TRIGGERED.value,
            name=name,
            payload={**payload, "event": event},
            trigger={"event": event},
        )

    def fire_trigger(self, event: str, *, detail: dict | None = None) -> list[str]:
        """Fire all TRIGGERED jobs matching event."""
        fired: list[str] = []
        for row in self.memory.list_scheduler_job_defs(self.user_id):
            if row.get("job_type") != JobType.TRIGGERED.value:
                continue
            job_def = self.memory.get_scheduler_job_def(str(row["id"]))
            if not job_def:
                continue
            payload = job_def.get("payload") or {}
            if payload.get("event") != event:
                continue
            if detail:
                payload = {**payload, "trigger_detail": detail}
                self.memory.upsert_scheduler_job_def(
                    self.user_id,
                    job_id=str(row["id"]),
                    job_type=JobType.TRIGGERED.value,
                    name=str(row.get("name") or row["id"]),
                    payload=payload,
                    trigger=job_def.get("trigger") or {},
                )
            self.dispatch(str(row["id"]))
            fired.append(str(row["id"]))
        return fired

    def configure_weekly_routine(
        self,
        *,
        day_of_week: str = "mon",
        hour: int = 8,
        minute: int = 0,
        checklist: list[dict] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"kind": "weekly_routine"}
        if checklist:
            payload["checklist"] = checklist
        self.schedule_recurring_cron(
            WEEKLY_ROUTINE_JOB_ID,
            name="Weekly routine",
            payload=payload,
            day_of_week=day_of_week,
            hour=hour,
            minute=minute,
        )

    def run_job_now(self, job_id: str) -> None:
        """Immediate execution (tests / manual run)."""
        self.dispatch(job_id)

    def fast_forward_job(self, job_id: str, *, seconds: float = 0.05) -> None:
        """Reschedule a job to run almost immediately (APScheduler test helper)."""
        run_at = _utcnow() + timedelta(seconds=seconds)
        self._apscheduler.add_job(
            execute_scheduled_job,
            trigger=DateTrigger(run_date=run_at),
            id=f"{job_id}__fastforward",
            kwargs={"job_id": job_id},
            replace_existing=True,
        )
        self._apscheduler.wakeup()

    def list_jobs(self) -> list[dict[str, Any]]:
        defs = self.memory.list_scheduler_job_defs(self.user_id)
        out = []
        for d in defs:
            ap = self._apscheduler.get_job(str(d["id"]))
            next_run = ap.next_run_time.isoformat() if ap and ap.next_run_time else None
            out.append({**d, "next_run": next_run})
        return out
