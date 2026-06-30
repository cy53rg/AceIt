"""
atlas_daemon.py — Background Atlas process (no Qt).

Owns StateEngine, UserMemory, AccountManager, JobScheduler, ConnectorRegistry.
The UI (atlas_ui.py) connects via localhost HTTP + WebSocket (atlas_ipc.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from typing import Any, Callable, Optional

from dotenv import load_dotenv

load_dotenv()

from atlas_data import atlas_db_path, daemon_host, daemon_port, DEFAULT_SAFETY_MODE
from atlas_logging import get_logger

log = get_logger("daemon")

# Lazy imports for heavy modules (after env load)
_state = None
_account = None
_memory = None
_scheduler = None
_apscheduler = None
_connectors = None
_ssh_manager = None
_dispatcher = None
_playbooks = None
_file_indexer = None
_ui_bridge: Optional["UIBridge"] = None


def _sync_daemon_user(user_id: int) -> None:
    """Keep scheduler/connector subsystems aligned after account switch."""
    if _connectors is not None:
        _connectors._user_id = int(user_id)
    if _dispatcher is not None:
        _dispatcher.user_id = int(user_id)
    if _apscheduler is not None:
        _apscheduler.user_id = int(user_id)
    if _playbooks is not None:
        _playbooks.user_id = int(user_id)


def _sync_dispatcher_policy() -> None:
    if _dispatcher is None or _state is None:
        return
    _dispatcher.safety_mode = str(
        getattr(_state, "safety_mode", None) or DEFAULT_SAFETY_MODE
    )
    _dispatcher.fs_access_active = bool(getattr(_state, "_fs_access_active", False))
    _dispatcher.execution_blocked = bool(getattr(_state, "execution_blocked", False))


class UIBridge:
    """Routes daemon-side callbacks to the connected UI over WebSocket."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: list[Any] = []
        self._pending: dict[str, threading.Event] = {}
        self._responses: dict[str, dict] = {}

    def attach(self, ws: Any) -> None:
        with self._lock:
            self._connections.append(ws)

    def detach(self, ws: Any) -> None:
        with self._lock:
            try:
                self._connections.remove(ws)
            except ValueError:
                pass

    def broadcast(self, payload: dict) -> None:
        raw = json.dumps(payload)
        with self._lock:
            dead = []
            for ws in self._connections:
                try:
                    ws.send_text(raw)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try:
                    self._connections.remove(ws)
                except ValueError:
                    pass

    def request(
        self,
        payload: dict,
        *,
        timeout: float = 300.0,
    ) -> dict:
        req_id = str(uuid.uuid4())
        payload = dict(payload)
        payload["id"] = req_id
        ev = threading.Event()
        with self._lock:
            self._pending[req_id] = ev
        self.broadcast(payload)
        if not ev.wait(timeout=timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            return {"approved": False, "timeout": True}
        with self._lock:
            resp = self._responses.pop(req_id, {"approved": False})
            self._pending.pop(req_id, None)
        return resp

    def handle_client_message(self, msg: dict) -> None:
        req_id = msg.get("id")
        if not req_id:
            return
        with self._lock:
            ev = self._pending.get(str(req_id))
            if ev:
                self._responses[str(req_id)] = msg
                ev.set()

    def has_clients(self) -> bool:
        with self._lock:
            return len(self._connections) > 0


def _init_services(user_name: str = "default") -> None:
    global _state, _account, _memory, _scheduler, _apscheduler, _connectors
    global _ui_bridge, _ssh_manager, _dispatcher, _playbooks, _file_indexer

    from atlas_accounts import AccountManager
    from atlas_connectors.registry import ConnectorRegistry
    from atlas_core import StateEngine, atlas_fs, step_orchestrator
    from atlas_memory import UserMemory
    from atlas_apscheduler import AtlasScheduler, JobDispatcher
    from atlas_playbooks import PlaybookManager
    from atlas_policy import PolicyContext, set_policy_context_provider
    from atlas_scheduler import JobScheduler
    from atlas_stepevent import _PendingStep

    _ui_bridge = UIBridge()
    _memory = UserMemory(atlas_db_path())
    _account = AccountManager(memory=_memory)
    _connectors = ConnectorRegistry(db_path=atlas_db_path())
    _scheduler = JobScheduler(_memory)

    def _on_chunk(text: str) -> None:
        _ui_bridge.broadcast({"type": "chunk", "text": text})

    def _on_complete(text: str) -> None:
        _ui_bridge.broadcast({"type": "complete", "text": text})

    def _on_error(text: str) -> None:
        _ui_bridge.broadcast({"type": "error", "text": text})

    def _on_coordinates(coord: dict) -> None:
        _ui_bridge.broadcast({"type": "coordinates", "coord": coord})

    def _on_token_usage(usage: dict) -> None:
        _ui_bridge.broadcast({"type": "token_usage", "usage": usage})

    from atlas_audio import voice_engine as _voice_engine

    def _on_spoken(text: str) -> None:
        _ui_bridge.broadcast({"type": "spoken", "text": text})

    _voice_engine.on_spoken = _on_spoken

    _state = StateEngine(
        on_chunk=_on_chunk,
        on_complete=_on_complete,
        on_error=_on_error,
        on_coordinates=_on_coordinates,
        on_token_usage=_on_token_usage,
        user_name=user_name,
        memory=_memory,
    )

    def _policy_ctx() -> PolicyContext:
        scopes = tuple(
            s.get("path", "") for s in atlas_fs.list_write_scopes()
        ) if hasattr(atlas_fs, "list_write_scopes") else ()
        return PolicyContext(
            safety_mode=str(getattr(_state, "safety_mode", None) or DEFAULT_SAFETY_MODE),
            fs_access_active=bool(getattr(_state, "_fs_access_active", False)),
            execution_blocked=bool(getattr(_state, "execution_blocked", False)),
            write_scopes=scopes,
        )

    set_policy_context_provider(_policy_ctx)

    def _on_state_event(event_type: str, payload: dict) -> None:
        _ui_bridge.broadcast({
            "type": "state_event",
            "event_type": event_type,
            "payload": payload,
        })

    _state.on_event(_on_state_event)

    def _fs_permission(action: str, path: str, approve_fn: Callable, deny_fn: Callable) -> None:
        resp = _ui_bridge.request({
            "type": "permission_request",
            "action": action,
            "path": path,
        })
        if resp.get("approved"):
            approve_fn()
        else:
            deny_fn()

    def _typed_confirm(policy_result, *, path: str) -> bool:
        resp = _ui_bridge.request({
            "type": "typed_confirm_request",
            "path": path,
            "reason": getattr(policy_result, "reason", ""),
            "confirm_phrase": getattr(policy_result, "confirm_phrase", ""),
            "audit_id": getattr(policy_result, "audit_id", ""),
        })
        return bool(resp.get("approved"))

    def _safety_prompt(message: str) -> bool:
        resp = _ui_bridge.request({
            "type": "safety_prompt",
            "message": message,
        })
        return bool(resp.get("approved"))

    def _task_confirm(message: str) -> bool:
        resp = _ui_bridge.request({
            "type": "task_confirm",
            "message": message,
        })
        return bool(resp.get("approved"))

    atlas_fs.register_permission_callback(_fs_permission)
    atlas_fs.register_typed_confirm_callback(_typed_confirm)
    _state.connectors = _connectors
    _connectors._user_id = _state.user_id

    def _connector_permission(action: str, path: str) -> bool:
        resp = _ui_bridge.request({
            "type": "permission_request",
            "action": action,
            "path": path,
        })
        return bool(resp.get("approved"))

    def _connector_typed_confirm(msg: dict) -> bool:
        resp = _ui_bridge.request({
            "type": "typed_confirm_request",
            "path": msg.get("path", ""),
            "reason": msg.get("reason", ""),
            "confirm_phrase": msg.get("confirm_phrase", ""),
            "audit_id": msg.get("audit_id", ""),
        })
        return bool(resp.get("approved"))

    _connectors.set_permission_handler(_connector_permission)
    _connectors.set_typed_confirm_handler(_connector_typed_confirm)

    from atlas_shell import shell_runner

    shell_runner.set_permission_handler(_connector_permission)
    shell_runner.set_typed_confirm_handler(_connector_typed_confirm)

    from atlas_connectors.ssh_targets import SSHTargetManager

    _ssh_manager = SSHTargetManager(atlas_db_path(), shell_runner)
    _state.ssh_manager = _ssh_manager

    def _sync_runtime_policy() -> None:
        ctx = _policy_ctx()
        atlas_fs.set_policy_context(
            safety_mode=ctx.safety_mode,
            fs_access_active=ctx.fs_access_active,
            execution_blocked=ctx.execution_blocked,
        )
        shell_runner.set_policy_context(
            safety_mode=ctx.safety_mode,
            fs_access_active=ctx.fs_access_active,
            execution_blocked=ctx.execution_blocked,
            write_scopes=ctx.write_scopes,
        )
        _sync_dispatcher_policy()

    _sync_runtime_policy()
    _state._sync_fs_policy = _sync_runtime_policy  # type: ignore[attr-defined]

    _state._safety_prompt = _safety_prompt
    _state._task_confirm_cb = _task_confirm
    _state._fs_access_active = False

    def _step_handler(pending: _PendingStep) -> None:
        ev = pending.event
        resp = _ui_bridge.request({
            "type": "step_done_wait",
            "step": {
                "step_index": ev.step_index,
                "description": ev.description,
                "x": ev.x,
                "y": ev.y,
                "w": ev.w,
                "h": ev.h,
                "action": ev.action,
                "target": ev.target,
                "expected_state": ev.expected_state,
            },
        })
        pending.result_ok = bool(resp.get("ok", True))
        if pending.do_action and pending.result_ok:
            try:
                pending.do_action()
            except Exception as exc:
                pending.result_ok = False
                ev.error = str(exc)
                ev.status = "failed"
        pending.done.set()

    step_orchestrator.set_ui_handler(_step_handler)

    def _on_weekly_digest(digest: str, result: dict) -> None:
        _ui_bridge.broadcast({
            "type": "weekly_digest",
            "message": digest,
            "detail": result,
        })
        _ui_bridge.broadcast({
            "type": "user_notice",
            "message": digest[:4000],
        })

    _playbooks = PlaybookManager(_memory, _state.user_id)
    _dispatcher = JobDispatcher(
        _memory,
        _state.user_id,
        connectors=_connectors,
        learning=_state.learning,
        playbooks=_playbooks,
        is_attended=lambda: bool(_ui_bridge and _ui_bridge.has_clients()),
        on_notify=lambda msg: _ui_bridge.broadcast(msg) if _ui_bridge else None,
        safety_mode=str(getattr(_state, "safety_mode", None) or DEFAULT_SAFETY_MODE),
        fs_access_active=bool(getattr(_state, "_fs_access_active", False)),
        execution_blocked=bool(getattr(_state, "execution_blocked", False)),
        run_task=lambda goal: _state.run_task(goal),
        ssh_manager=_ssh_manager,
    )

    from atlas_data import atlas_data_dir

    _apscheduler = AtlasScheduler(
        _memory,
        _state.user_id,
        jobstore_path=str((atlas_data_dir() / "atlas_scheduler.sqlite3").as_posix()),
        dispatcher=_dispatcher,
        on_digest=_on_weekly_digest,
    )
    _apscheduler.configure_weekly_routine(day_of_week="mon", hour=8, minute=0)
    _apscheduler.start()
    _state.apscheduler = _apscheduler

    from atlas_files.indexer import FileIndexer

    _file_indexer = FileIndexer(atlas_db_path())
    _file_indexer.ensure_default_roots()
    _state.file_indexer = _file_indexer
    _file_indexer.start_background_rebuild_if_stale()

    _scheduler.start()
    log.info("Atlas daemon services ready (db=%s)", atlas_db_path())


def _account_call(method: str, args: list, kwargs: dict) -> dict:
    if _account is None:
        return {"ok": False, "message": "account not ready"}
    fn = getattr(_account, method, None)
    if fn is None and method == "cloud_send_email_otp":
        fn = _account.cloud.send_email_otp
    if fn is None:
        return {"ok": False, "message": f"unknown account method: {method}"}
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:
        log.exception("account.%s failed", method)
        return {"ok": False, "message": str(exc)}
    if method == "needs_2fa" and isinstance(result, tuple) and len(result) == 2:
        return {"needs": bool(result[0]), "method": str(result[1])}
    if method == "get_security":
        return {"ok": True, "data": dict(result or {})}
    if isinstance(result, tuple):
        if len(result) == 3:
            ok, msg, uid = result
            return {"ok": bool(ok), "message": str(msg), "user_id": int(uid or 0)}
        if len(result) == 2:
            ok, msg = result
            return {"ok": bool(ok), "message": str(msg)}
    if isinstance(result, dict):
        return {"ok": True, "data": result}
    if isinstance(result, bool):
        return {"ok": result}
    return {"ok": True, "result": result}


_INVOKE_ALLOW = frozenset({
    "list_global_context",
    "add_global_context",
    "delete_global_context",
    "get_user_prefs",
    "set_user_pref",
    "get_debug_state",
})


def _state_invoke(method: str, args: list, kwargs: dict) -> Any:
    if _state is None:
        raise RuntimeError("state not ready")
    if method in _INVOKE_ALLOW:
        fn = getattr(_state, method)
        return fn(*args, **kwargs)
    if method == "session.set_response_style":
        _state.session.response_style = str(
            args[0] if args else kwargs.get("style", "Balanced")
        )
        return True
    if method.startswith("session."):
        fn = getattr(_state.session, method.split(".", 1)[1])
        return fn(*args, **kwargs)
    if method.startswith("memory."):
        fn = getattr(_state.memory, method.split(".", 1)[1])
        return fn(*args, **kwargs)
    if method.startswith("skill_registry."):
        fn = getattr(_state.skill_registry, method.split(".", 1)[1])
        return fn(*args, **kwargs)
    if method.startswith("learning."):
        fn = getattr(_state.learning, method.split(".", 1)[1])
        return fn(*args, **kwargs)
    raise PermissionError(f"invoke not allowed: {method}")


def create_app():
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    from atlas_core import atlas_fs

    app = FastAPI(title="Atlas Daemon", docs_url=None, redoc_url=None)
    # Electron + Vite dev shell (Phase 7) — daemon is localhost-only.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class GenericBody(BaseModel):
        method: str
        args: list = []
        kwargs: dict = {}

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "state_ready": _state is not None,
            "scheduler_ready": _scheduler is not None,
            "apscheduler_ready": _apscheduler is not None,
        }

    @app.post("/api/handle_input")
    def api_handle_input(body: dict):
        if _state is None:
            raise HTTPException(503, "state not ready")
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        source = str(body.get("source") or "user")
        webcam_b64 = body.get("webcam_b64")
        screen_b64 = body.get("screen_b64")
        if screen_b64:
            _state.inject_screen_capture(screen_b64)
        if source == "highlight" and _state.glass.active:
            target = _state.handle_glass_interview_question
            thread_args: tuple = (text,)
            thread_kwargs = {"source": "highlight"}
        else:
            target = _state.handle_input
            thread_args = (text,)
            thread_kwargs = {"source": source, "webcam_b64": webcam_b64}
        threading.Thread(
            target=target,
            args=thread_args,
            kwargs=thread_kwargs,
            daemon=True,
            name="atlas-handle-input",
        ).start()
        return {"ok": True}

    @app.post("/api/cancel")
    def cancel():
        if _state:
            _state.cancel_current()
        try:
            from atlas_audio import voice_engine as _ve
            _ve.skip()
            _ve.flush()
        except Exception:
            pass
        return {"ok": True}

    @app.post("/api/voice/stop")
    def voice_stop():
        """Immediately silence TTS and drop queued speech."""
        try:
            from atlas_audio import voice_engine as _ve
            _ve.skip()
            _ve.flush()
        except Exception:
            pass
        if _state:
            _state.cancel_current()
        return {"ok": True}

    @app.post("/api/stop_task")
    def stop_task():
        if _state:
            _state.stop_task()
        return {"ok": True}

    @app.post("/api/set_user")
    def set_user(body: dict):
        if _state:
            uid = int(body.get("user_id", 0))
            _state.set_user(uid, body.get("user_name"))
            _sync_daemon_user(uid)
            if hasattr(_state, "_sync_fs_policy"):
                _state._sync_fs_policy()
        return {"ok": True}

    @app.post("/api/set_focus_mode")
    def set_focus_mode(body: dict):
        if _state:
            _state.set_focus_mode(bool(body.get("enabled")))
        return {"ok": True}

    @app.post("/api/set_copilot_mode")
    def set_copilot_mode(body: dict):
        if _state:
            _state.set_copilot_mode(bool(body.get("enabled")))
        return {"ok": True}

    @app.post("/api/inject_screen")
    def inject_screen(body: dict):
        if _state:
            _state.inject_screen_capture(body.get("screen_b64", ""))
        return {"ok": True}

    @app.post("/api/set_screen_vision")
    def set_screen_vision(body: dict):
        if _state:
            _state.set_screen_vision(bool(body.get("enabled")))
        return {"ok": True}

    @app.post("/api/start_learning")
    def start_learning():
        if _state:
            _state.start_learning()
        return {"ok": True}

    @app.post("/api/stop_learning")
    def stop_learning(body: dict):
        if _state:
            _state.stop_learning(body.get("name", ""))
        return {"ok": True}

    @app.post("/api/set_fs_access")
    def set_fs_access(body: dict):
        if _state:
            _state._fs_access_active = bool(body.get("active"))
            if hasattr(_state, "_sync_fs_policy"):
                _state._sync_fs_policy()
        return {"ok": True}

    @app.get("/api/fs/write_scopes")
    def fs_list_scopes():
        return {"scopes": atlas_fs.list_write_scopes()}

    @app.post("/api/fs/write_scopes")
    def fs_add_scope(body: dict):
        ok, msg = atlas_fs.add_write_scope(str(body.get("path", "")), label=str(body.get("label", "")))
        if hasattr(_state, "_sync_fs_policy"):
            _state._sync_fs_policy()
        return {"ok": ok, "message": msg}

    @app.post("/api/fs/write_scopes/remove")
    def fs_remove_scope_post(body: dict):
        atlas_fs.remove_write_scope(str(body.get("path", "")))
        if _state and hasattr(_state, "_sync_fs_policy"):
            _state._sync_fs_policy()
        return {"ok": True}

    @app.delete("/api/fs/write_scopes")
    def fs_remove_scope_delete(body: dict):
        atlas_fs.remove_write_scope(str(body.get("path", "")))
        if _state and hasattr(_state, "_sync_fs_policy"):
            _state._sync_fs_policy()
        return {"ok": True}

    @app.get("/api/files/index/status")
    def files_index_status():
        if _file_indexer is None:
            raise HTTPException(503, "file indexer not ready")
        return _file_indexer.status()

    @app.post("/api/files/index/rebuild")
    def files_index_rebuild():
        if _file_indexer is None:
            raise HTTPException(503, "file indexer not ready")
        return _file_indexer.rebuild(async_run=True)

    @app.get("/api/files/index/roots")
    def files_index_list_roots():
        if _file_indexer is None:
            raise HTTPException(503, "file indexer not ready")
        return {"roots": _file_indexer.list_roots()}

    @app.post("/api/files/index/roots")
    def files_index_add_root(body: dict):
        if _file_indexer is None:
            raise HTTPException(503, "file indexer not ready")
        ok, msg = _file_indexer.add_root(
            str(body.get("path", "")),
            label=str(body.get("label", "")),
        )
        return {"ok": ok, "message": msg}

    @app.post("/api/files/index/roots/remove")
    def files_index_remove_root(body: dict):
        if _file_indexer is None:
            raise HTTPException(503, "file indexer not ready")
        _file_indexer.remove_root(str(body.get("path", "")))
        return {"ok": True}

    @app.get("/api/glass/status")
    def glass_status():
        if _state is None:
            raise HTTPException(503, "state not ready")
        return {"glass": _state.glass.status(), "focus_mode": _state.focus_mode}

    @app.get("/api/glass/meetings")
    def glass_list_meetings(limit: int = 30):
        if _state is None or _memory is None:
            raise HTTPException(503, "not ready")
        return {"meetings": _memory.glass_list_meetings(_state.user_id, limit=limit)}

    @app.get("/api/glass/meetings/{meeting_id}")
    def glass_get_meeting(meeting_id: int):
        if _state is None or _memory is None:
            raise HTTPException(503, "not ready")
        meeting = _memory.glass_get_meeting(_state.user_id, int(meeting_id))
        if not meeting:
            raise HTTPException(404, "meeting not found")
        chunks = _memory.glass_recent_chunks(int(meeting_id), limit=500)
        return {"meeting": meeting, "chunks": chunks}

    @app.post("/api/glass/meetings/start")
    def glass_start_meeting(body: dict):
        if _state is None:
            raise HTTPException(503, "state not ready")
        if not _state.focus_mode:
            _state.set_focus_mode(True)
        return {"ok": True, "status": _state.glass.status()}

    @app.post("/api/glass/meetings/end")
    def glass_end_meeting():
        if _state is None:
            raise HTTPException(503, "state not ready")
        _state.set_focus_mode(False)
        return {"ok": True, "status": _state.glass.status()}

    @app.post("/api/killswitch")
    def engage_killswitch():
        if _state is None:
            raise HTTPException(503, "state not ready")
        _state.engage_killswitch()
        return {"ok": True}

    @app.get("/api/goals")
    def list_goals(limit: int = 20):
        if _memory is None or _state is None:
            raise HTTPException(503, "state not ready")
        active = _memory.goal_get_active(_state.user_id)
        goals = _memory.goal_list(_state.user_id, limit=limit)
        return {"active": active, "goals": goals}

    @app.post("/api/goals/start")
    def start_goal(body: dict):
        if _state is None:
            raise HTTPException(503, "state not ready")
        goal_text = str(body.get("goal") or "").strip()
        if not goal_text:
            raise HTTPException(400, "goal required")
        goal_id = _state.goals.start(goal_text)
        return {"ok": True, "goal_id": goal_id}

    @app.get("/api/audit")
    def list_audit(limit: int = 50):
        if _memory is None or _state is None:
            raise HTTPException(503, "state not ready")
        return {"entries": _memory.audit_list(_state.user_id, limit=limit)}

    @app.get("/api/recap")
    def weekly_recap(days: int = 7):
        if _memory is None or _state is None:
            raise HTTPException(503, "state not ready")
        from atlas_recap import build_weekly_recap

        text = build_weekly_recap(_memory, _state.user_id, days=max(1, min(days, 30)))
        return {"recap": text}

    @app.post("/api/shell/run")
    def shell_run(body: dict):
        from atlas_shell import shell_runner

        cmd = str(body.get("command", ""))
        if _state and hasattr(_state, "_sync_fs_policy"):
            _state._sync_fs_policy()
        return shell_runner.run(
            cmd,
            safety_mode=str(_state.safety_mode if _state else DEFAULT_SAFETY_MODE),
        )

    @app.get("/api/ssh/targets")
    def ssh_list_targets():
        if _ssh_manager is None:
            raise HTTPException(status_code=503, detail="SSH manager not ready")
        return {"targets": _ssh_manager.list_targets()}

    @app.post("/api/ssh/targets")
    def ssh_add_target(body: dict):
        if _ssh_manager is None:
            raise HTTPException(status_code=503, detail="SSH manager not ready")
        ok, msg = _ssh_manager.add_target(
            name=str(body.get("name", "")),
            host=str(body.get("host", "")),
            user=str(body.get("user", "")),
            key_path=str(body.get("key_path", "")),
            manageable_services=body.get("manageable_services") or [],
        )
        return {"ok": ok, "message": msg}

    @app.post("/api/ssh/targets/remove")
    def ssh_remove_target(body: dict):
        if _ssh_manager is None:
            raise HTTPException(status_code=503, detail="SSH manager not ready")
        _ssh_manager.remove_target(str(body.get("name", "")))
        return {"ok": True}

    @app.post("/api/ssh/run")
    def ssh_run_command(body: dict):
        if _ssh_manager is None:
            raise HTTPException(status_code=503, detail="SSH manager not ready")
        if _state and hasattr(_state, "_sync_fs_policy"):
            _state._sync_fs_policy()
        return _ssh_manager.run_command(
            str(body.get("target", "")),
            str(body.get("command", "")),
        )

    @app.post("/api/run_routine")
    def run_routine(body: dict):
        if _state:
            _state.run_routine(body.get("name", ""))
        return {"ok": True}

    @app.get("/api/debug_state")
    def debug_state():
        if not _state:
            return {"text": "state not ready"}
        return {"text": _state.get_debug_state()}

    @app.get("/api/session_snapshot")
    def session_snapshot():
        if not _state:
            return {}
        sess = _state.session
        return {
            "is_active": sess.is_active,
            "response_style": sess.response_style,
            "turn_count": getattr(sess, "_turn_count", 0),
            "summary": sess.summary,
            "user_id": _state.user_id,
            "safety_mode": _state.safety_mode,
            "focus_mode": _state.focus_mode,
            "is_learning": _state.is_learning,
            "mode": _state.mode.name,
        }

    @app.post("/api/invoke")
    def invoke(body: GenericBody):
        try:
            result = _state_invoke(body.method, body.args, body.kwargs)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"ok": True, "result": result}

    @app.get("/api/accounts/cloud_available")
    def cloud_available():
        return {"available": bool(_account and _account.cloud_available)}

    @app.post("/api/accounts/call")
    def accounts_call(body: GenericBody):
        return _account_call(body.method, body.args, body.kwargs)

    @app.post("/api/jobs/enqueue")
    def jobs_enqueue(body: dict):
        if not _scheduler:
            raise HTTPException(503, "scheduler not ready")
        job_id = _scheduler.enqueue_job(
            user_id=int(body.get("user_id") or 0),
            name=str(body.get("name") or ""),
            steps=list(body.get("steps") or []),
        )
        return {"ok": True, "job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def jobs_get(job_id: int):
        if not _scheduler:
            raise HTTPException(503, "scheduler not ready")
        job = _scheduler.get_job(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/scheduler/enqueue")
    def scheduler_enqueue(body: dict):
        log.warning("deprecated API: POST /api/scheduler/enqueue — use /api/jobs/enqueue")
        if not _scheduler:
            raise HTTPException(503, "scheduler not ready")
        job_id = _scheduler.enqueue_job(
            user_id=int(body.get("user_id") or 0),
            name=str(body.get("name") or ""),
            steps=list(body.get("steps") or []),
        )
        return {"ok": True, "job_id": job_id}

    @app.get("/api/scheduler/jobs/{job_id}")
    def scheduler_get(job_id: int):
        log.warning("deprecated API: GET /api/scheduler/jobs/{id} — use /api/jobs/{id}")
        if not _scheduler:
            raise HTTPException(503, "scheduler not ready")
        job = _scheduler.get_job(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.get("/api/scheduler/definitions")
    def scheduler_list_definitions():
        if not _apscheduler:
            raise HTTPException(503, "apscheduler not ready")
        return {"jobs": _apscheduler.list_jobs()}

    @app.post("/api/scheduler/weekly-routine")
    def scheduler_configure_weekly(body: dict):
        if not _apscheduler:
            raise HTTPException(503, "apscheduler not ready")
        _apscheduler.configure_weekly_routine(
            day_of_week=str(body.get("day_of_week") or "mon"),
            hour=int(body.get("hour") or 8),
            minute=int(body.get("minute") or 0),
            checklist=body.get("checklist"),
        )
        return {"ok": True, "job_id": "weekly_routine"}

    @app.post("/api/scheduler/run/{job_id}")
    def scheduler_run_now(job_id: str):
        if not _apscheduler:
            raise HTTPException(503, "apscheduler not ready")
        _apscheduler.run_job_now(job_id)
        return {"ok": True}

    @app.post("/api/scheduler/trigger")
    def scheduler_fire_trigger(body: dict):
        if not _apscheduler:
            raise HTTPException(503, "apscheduler not ready")
        event = str(body.get("event") or "")
        fired = _apscheduler.fire_trigger(event, detail=body.get("detail"))
        return {"ok": True, "fired": fired}

    @app.get("/api/scheduler/pending")
    def scheduler_list_pending():
        if not _memory or not _state:
            raise HTTPException(503, "not ready")
        return {"pending": _memory.list_scheduler_pending(_state.user_id)}

    @app.get("/api/scheduler/activity")
    def scheduler_list_activity(since_days: float = 7.0):
        if not _memory or not _state:
            raise HTTPException(503, "not ready")
        since = time.time() - (since_days * 86400)
        return {
            "activity": _memory.list_scheduler_activity(_state.user_id, since=since),
        }

    @app.post("/api/scheduler/pending/{pending_id}/resolve")
    def scheduler_resolve_pending(pending_id: int, body: dict):
        if not _memory:
            raise HTTPException(503, "not ready")
        approved = bool(body.get("approved", False))
        _memory.resolve_scheduler_pending(int(pending_id), approved=approved)
        return {"ok": True, "approved": approved}

    @app.get("/api/connectors")
    def list_connectors():
        if not _connectors:
            return {"connectors": []}
        return {"connectors": _connectors.list_connectors()}

    @app.post("/api/connectors/{connector_id}/connect")
    def connector_connect(connector_id: str):
        if not _connectors:
            raise HTTPException(503, "connectors not ready")
        ok, msg = _connectors.connect(connector_id)
        return {"ok": ok, "message": msg}

    @app.post("/api/connectors/{connector_id}/disconnect")
    def connector_disconnect(connector_id: str):
        if not _connectors:
            raise HTTPException(503, "connectors not ready")
        ok, msg = _connectors.disconnect(connector_id)
        return {"ok": ok, "message": msg}

    @app.post("/api/connectors/{connector_id}/execute")
    def connector_execute(connector_id: str, body: dict):
        if not _connectors or not _state:
            raise HTTPException(503, "connectors not ready")
        method = str(body.pop("method", ""))
        return _connectors.execute(
            connector_id,
            method,
            safety_mode=str(_state.safety_mode or DEFAULT_SAFETY_MODE),
            fs_access_active=bool(getattr(_state, "_fs_access_active", False)),
            execution_blocked=bool(_state.execution_blocked),
            **body,
        )

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        if _ui_bridge:
            _ui_bridge.attach(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if _ui_bridge:
                    _ui_bridge.handle_client_message(msg)
        except WebSocketDisconnect:
            pass
        finally:
            if _ui_bridge:
                _ui_bridge.detach(ws)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atlas background daemon")
    parser.add_argument("--host", default=daemon_host())
    parser.add_argument("--port", type=int, default=daemon_port())
    parser.add_argument("--user", default=os.environ.get("ATLAS_USER", "default"))
    args = parser.parse_args(argv)

    _init_services(user_name=args.user)

    import uvicorn

    app = create_app()
    log.info("Starting Atlas daemon on %s:%s", args.host, args.port)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
