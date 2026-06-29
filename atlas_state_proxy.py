"""
atlas_state_proxy.py — UI-side proxy for daemon-hosted StateEngine.

Presents the same surface area atlas_ui.py and atlas_settings_ui.py expect,
forwarding mutations over IPC and mirroring session fields locally.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Optional

from atlas_ipc import DaemonClient
from atlas_data import DEFAULT_SAFETY_MODE
from atlas_logging import get_logger


class _SessionProxy:
    def __init__(self, owner: "AtlasStateProxy") -> None:
        self._owner = owner
        self.is_active = True
        self.response_style = "Balanced"
        self._turn_count = 0
        self._start_time = time.time()

    @property
    def summary(self) -> str:
        elapsed = int(time.time() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        return f"Session {mins:02d}:{secs:02d} · {self._turn_count} turns"

    def add_pinned_context(self, content: str, source: str = "context") -> None:
        self._owner.invoke("add_global_context", content)

    def end(self) -> None:
        self.is_active = False
        self._turn_count = 0

    def refresh(self, snap: dict) -> None:
        self.is_active = bool(snap.get("is_active", self.is_active))
        self.response_style = str(snap.get("response_style", self.response_style))
        self._turn_count = int(snap.get("turn_count", self._turn_count))


class _MemoryProxy:
    def __init__(self, owner: "AtlasStateProxy") -> None:
        self._owner = owner

    def list_routines(self, user_id: int) -> list:
        return self._owner.invoke("memory.list_routines", user_id) or []

    def recall(self, user_id: int, min_confidence: float = 0.5) -> list:
        return self._owner.invoke("memory.recall", user_id, min_confidence) or []

    def forget(self, user_id: int, category: str, key: str) -> None:
        self._owner.invoke("memory.forget", user_id, category, key)

    def get_profile(self, user_id: int) -> dict:
        return dict(self._owner.invoke("memory.get_profile", user_id) or {})

    def list_context(self, user_id: int) -> list:
        return self._owner.invoke("memory.list_context", user_id) or []

    def get_prefs(self, user_id: int) -> dict:
        return dict(self._owner.invoke("memory.get_prefs", user_id) or {})


class _SkillRegistryProxy:
    def __init__(self, owner: "AtlasStateProxy") -> None:
        self._owner = owner

    def list_skills(self) -> list:
        return self._owner.invoke("skill_registry.list_skills") or []

    def install_from_file(self, path: str) -> tuple[bool, str]:
        result = self._owner.invoke("skill_registry.install_from_file", path)
        if isinstance(result, (list, tuple)) and len(result) >= 2:
            return bool(result[0]), str(result[1])
        return bool(result), str(result)

    def uninstall(self, name: str) -> None:
        self._owner.invoke("skill_registry.uninstall", name)


class _LearningProxy:
    def __init__(self, owner: "AtlasStateProxy") -> None:
        self._owner = owner

    def get_learning_report(self) -> str:
        return str(self._owner.invoke("learning.get_learning_report") or "")


class _LocalAudioBridge:
    """Mic routing stays in the UI process (AudioEngine is local)."""

    _USER_TYPED_GRACE_S = 5.0

    def __init__(self) -> None:
        self.voice_listening = False
        self._user_typed_at = 0.0

    def bind_audio(self, audio) -> None:
        pass

    def note_user_typed(self) -> None:
        self._user_typed_at = time.time()

    def route_transcript(self, text: str, source: str) -> str:
        src = (source or "").lower()
        if src == "mic" and not self.voice_listening:
            return "drop"
        if time.time() - self._user_typed_at < self._USER_TYPED_GRACE_S:
            return "drop"
        return "forward"


@dataclass
class _RemoteStepPending:
    event: Any
    do_action: None = None
    result_ok: bool = True
    done: threading.Event = None  # type: ignore

    def __post_init__(self) -> None:
        if self.done is None:
            self.done = threading.Event()


class AtlasStateProxy:
    """Daemon-backed stand-in for StateEngine in the Qt UI."""

    def __init__(
        self,
        client: DaemonClient,
        *,
        on_chunk: Callable[[str], None],
        on_complete: Callable[[str], None],
        on_error: Callable[[str], None],
        on_coordinates: Callable[[dict], None],
        on_token_usage: Callable[[dict], None],
        step_ui_handler: Callable[[_RemoteStepPending], None] | None = None,
    ) -> None:
        self._client = client
        self._on_chunk = on_chunk
        self._on_complete = on_complete
        self._on_error = on_error
        self._on_coordinates = on_coordinates
        self._on_token_usage = on_token_usage
        self._step_ui_handler = step_ui_handler
        self._listeners: list[Callable] = []
        self._permission_handler: Callable[[str, str], bool] | None = None
        self._typed_confirm_handler: Callable[[dict], bool] | None = None
        self.session = _SessionProxy(self)
        self.memory = _MemoryProxy(self)
        self.skill_registry = _SkillRegistryProxy(self)
        self.learning = _LearningProxy(self)
        self.audio_watcher = _LocalAudioBridge()
        self.user_id = 0
        self.safety_mode = DEFAULT_SAFETY_MODE
        self.focus_mode = False
        self.execution_blocked = False
        self.is_learning = False
        self.screen_vision = False
        self._task_running = False
        self._safety_session_ok = False
        self._safety_prompt: Callable[[str], bool] | None = None
        self._task_confirm_cb: Callable[[str], bool] | None = None
        client.on_message(self._on_ws_message)
        self._refresh_session()

    def _refresh_session(self) -> None:
        try:
            snap = self._client.get_session_snapshot()
            self.session.refresh(snap)
            self.user_id = int(snap.get("user_id") or 0)
            self.safety_mode = str(snap.get("safety_mode") or self.safety_mode)
            self.focus_mode = bool(snap.get("focus_mode"))
            self.is_learning = bool(snap.get("is_learning"))
        except Exception:
            log.debug("session snapshot unavailable", exc_info=True)

    def on_event(self, fn: Callable) -> None:
        self._listeners.append(fn)

    def _emit_local(self, event_type: str, payload: dict) -> None:
        for fn in list(self._listeners):
            try:
                fn(event_type, payload)
            except Exception:
                log.exception("state event listener error")

    def _on_ws_message(self, msg: dict) -> None:
        msg_type = msg.get("type", "")
        if msg_type == "chunk":
            self._on_chunk(str(msg.get("text", "")))
        elif msg_type == "complete":
            self._on_complete(str(msg.get("text", "")))
            self._refresh_session()
        elif msg_type == "error":
            self._on_error(str(msg.get("text", "")))
        elif msg_type == "coordinates":
            self._on_coordinates(dict(msg.get("coord") or {}))
        elif msg_type == "token_usage":
            self._on_token_usage(dict(msg.get("usage") or {}))
        elif msg_type == "state_event":
            self._emit_local(str(msg.get("event_type", "")), dict(msg.get("payload") or {}))
            if msg.get("event_type") == "task_running":
                self._task_running = bool((msg.get("payload") or {}).get("running"))
        elif msg_type == "permission_request":
            self._handle_permission(msg)
        elif msg_type == "typed_confirm_request":
            self._handle_typed_confirm(msg)
        elif msg_type == "safety_prompt":
            self._handle_safety_prompt(msg)
        elif msg_type == "task_confirm":
            self._handle_task_confirm(msg)
        elif msg_type == "step_done_wait":
            self._handle_step_done_wait(msg)

    def _handle_permission(self, msg: dict) -> None:
        approved = False
        if self._permission_handler:
            approved = bool(self._permission_handler(
                str(msg.get("action", "")),
                str(msg.get("path", "")),
            ))
        self._client.respond(str(msg["id"]), {
            "type": "permission_response",
            "approved": approved,
        })

    def _handle_typed_confirm(self, msg: dict) -> None:
        approved = False
        if self._typed_confirm_handler:
            approved = bool(self._typed_confirm_handler(msg))
        self._client.respond(str(msg["id"]), {
            "type": "typed_confirm_response",
            "approved": approved,
        })

    def _handle_safety_prompt(self, msg: dict) -> None:
        approved = True
        if self._safety_prompt:
            approved = bool(self._safety_prompt(str(msg.get("message", ""))))
        self._client.respond(str(msg["id"]), {
            "type": "safety_prompt_response",
            "approved": approved,
        })

    def _handle_task_confirm(self, msg: dict) -> None:
        approved = True
        if self._task_confirm_cb:
            approved = bool(self._task_confirm_cb(str(msg.get("message", ""))))
        self._client.respond(str(msg["id"]), {
            "type": "task_confirm_response",
            "approved": approved,
        })

    def _handle_step_done_wait(self, msg: dict) -> None:
        step = dict(msg.get("step") or {})
        evt = SimpleNamespace(
            step_index=int(step.get("step_index", 0)),
            description=str(step.get("description", "")),
            x=int(step.get("x", 0)),
            y=int(step.get("y", 0)),
            w=int(step.get("w", 0)),
            h=int(step.get("h", 0)),
            action=str(step.get("action", "move")),
            target=str(step.get("target", "")),
            expected_state=str(step.get("expected_state", "")),
            status="started",
            error="",
            verified=None,
        )
        pending = _RemoteStepPending(event=evt)
        ok = True
        if self._step_ui_handler:
            try:
                self._step_ui_handler(pending)
            except Exception:
                log.exception("step ui handler failed")
                ok = False
        else:
            pending.done.wait(timeout=30.0)
            ok = pending.result_ok
        self._client.respond(str(msg["id"]), {
            "type": "step_done_response",
            "ok": ok and pending.result_ok,
        })

    def register_permission_handler(
        self,
        handler: Callable[[str, str], bool],
    ) -> None:
        self._permission_handler = handler

    def register_typed_confirm_handler(
        self,
        handler: Callable[[dict], bool],
    ) -> None:
        self._typed_confirm_handler = handler

    def invoke(self, method: str, *args: Any, **kwargs: Any) -> Any:
        data = self._client.invoke(method, *args, **kwargs)
        return data.get("result") if isinstance(data, dict) else data

    def handle_input(
        self,
        text: str,
        source: str = "user",
        *,
        webcam_b64: str | None = None,
    ) -> None:
        self.audio_watcher.note_user_typed()
        self._client.handle_input(text, source, webcam_b64=webcam_b64)

    def cancel_current(self) -> None:
        self._client.cancel_current()

    def stop_task(self) -> None:
        self._client.stop_task()

    def set_user(self, user_id: int, user_name: str | None = None) -> None:
        self._client.set_user(user_id, user_name)
        self.user_id = user_id
        self._refresh_session()

    def set_focus_mode(self, enabled: bool) -> None:
        self.focus_mode = bool(enabled)
        self._client.set_focus_mode(enabled)

    def set_copilot_mode(self, enabled: bool) -> None:
        self._client.set_copilot_mode(enabled)

    def inject_screen_capture(self, screen_b64: str) -> None:
        self._client.inject_screen_capture(screen_b64)

    def set_screen_vision(self, enabled: bool) -> None:
        self.screen_vision = bool(enabled)
        self._client.set_screen_vision(enabled)

    def start_learning(self) -> None:
        self._client.start_learning()
        self.is_learning = True

    def stop_learning(self, name: str) -> None:
        self._client.stop_learning(name)
        self.is_learning = False

    def run_routine(self, name: str) -> None:
        self._client.run_routine(name)

    def get_debug_state(self) -> str:
        return self._client.get_debug_state()

    def get_user_prefs(self) -> dict:
        return dict(self.invoke("get_user_prefs") or {})

    def set_user_pref(self, key: str, value: Any) -> None:
        self.invoke("set_user_pref", key, value)
        if key == "safety_mode":
            self.safety_mode = str(value)

    def list_global_context(self) -> list:
        return self.invoke("list_global_context") or []

    def add_global_context(self, text: str) -> None:
        self.invoke("add_global_context", text)

    def delete_global_context(self, context_id: int) -> None:
        self.invoke("delete_global_context", context_id)

    def set_fs_access_active(self, active: bool) -> None:
        self._client._post("/api/set_fs_access", {"active": bool(active)})
