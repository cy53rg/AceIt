"""
atlas_ipc.py — Localhost IPC client for atlas_daemon (no Qt).

The UI connects here; StateEngine / UserMemory / AccountManager live in the daemon.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from atlas_data import daemon_base_url
from atlas_logging import get_logger

log = get_logger("ipc")

try:
    import websocket  # type: ignore

    _HAS_WS = True
except ImportError:
    websocket = None  # type: ignore
    _HAS_WS = False


class DaemonError(RuntimeError):
    pass


class DaemonClient:
    """HTTP + WebSocket client for atlas_daemon."""

    def __init__(self, base_url: str | None = None, *, auto_connect: bool = True) -> None:
        self.base_url = (base_url or daemon_base_url()).rstrip("/")
        self._session = requests.Session()
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_stop = threading.Event()
        self._listeners: list[Callable[[dict], None]] = []
        self._pending: dict[str, threading.Event] = {}
        self._responses: dict[str, Any] = {}
        self._lock = threading.Lock()
        if auto_connect:
            self.start_event_stream()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def wait_for_health(self, timeout: float = 15.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.health():
                return True
            time.sleep(0.35)
        return False

    def health(self) -> bool:
        try:
            r = self._session.get(self._url("/health"), timeout=2.0)
            return r.status_code == 200 and r.json().get("ok")
        except Exception:
            return False

    def on_message(self, fn: Callable[[dict], None]) -> None:
        self._listeners.append(fn)

    def _dispatch(self, msg: dict) -> None:
        msg_type = msg.get("type", "")
        if msg_type.endswith("_response") and msg.get("id"):
            req_id = str(msg["id"])
            with self._lock:
                self._responses[req_id] = msg
                ev = self._pending.get(req_id)
                if ev:
                    ev.set()
        for fn in list(self._listeners):
            try:
                fn(msg)
            except Exception:
                log.exception("ipc listener error")

    def send_ws(self, payload: dict) -> None:
        """Best-effort WebSocket send (no-op if disconnected)."""
        # Stored on ws thread via module-level ref set by _ws_loop
        ws = getattr(self, "_ws", None)
        if ws is None:
            return
        try:
            ws.send(json.dumps(payload))
        except Exception:
            log.debug("ws send failed", exc_info=True)

    def request_ui(self, payload: dict, *, timeout: float = 300.0) -> dict:
        """Send a request to daemon that expects a UI response round-trip."""
        req_id = str(uuid.uuid4())
        payload = dict(payload)
        payload["id"] = req_id
        ev = threading.Event()
        with self._lock:
            self._pending[req_id] = ev
        self.send_ws(payload)
        if not ev.wait(timeout=timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            return {"approved": False, "timeout": True}
        with self._lock:
            resp = self._responses.pop(req_id, {})
            self._pending.pop(req_id, None)
        return resp

    def respond(self, req_id: str, payload: dict) -> None:
        out = dict(payload)
        out["id"] = req_id
        self.send_ws(out)

    def start_event_stream(self) -> None:
        if not _HAS_WS:
            log.warning("websocket-client not installed; live events disabled")
            return
        if self._ws_thread and self._ws_thread.is_alive():
            return
        self._ws_stop.clear()
        self._ws_thread = threading.Thread(
            target=self._ws_loop,
            daemon=True,
            name="atlas-ipc-ws",
        )
        self._ws_thread.start()

    def stop(self) -> None:
        self._ws_stop.set()
        ws = getattr(self, "_ws", None)
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    def _ws_loop(self) -> None:
        url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        while not self._ws_stop.is_set():
            try:
                ws = websocket.create_connection(url, timeout=5)
                self._ws = ws
                while not self._ws_stop.is_set():
                    raw = ws.recv()
                    if not raw:
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    self._dispatch(msg)
            except Exception:
                self._ws = None
                if not self._ws_stop.is_set():
                    time.sleep(1.0)
            finally:
                self._ws = None

    # ── HTTP RPC ──────────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict | None = None) -> Any:
        r = self._session.post(
            self._url(path),
            json=body or {},
            timeout=120.0,
        )
        if r.status_code >= 400:
            raise DaemonError(r.text or f"HTTP {r.status_code}")
        if not r.content:
            return {}
        return r.json()

    def _get(self, path: str, *, params: dict | None = None) -> Any:
        url = self._url(path)
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"
        r = self._session.get(url, timeout=30.0)
        if r.status_code >= 400:
            raise DaemonError(r.text or f"HTTP {r.status_code}")
        return r.json()

    def handle_input(
        self,
        text: str,
        source: str = "user",
        *,
        webcam_b64: str | None = None,
        screen_b64: str | None = None,
    ) -> None:
        self._post(
            "/api/handle_input",
            {
                "text": text,
                "source": source,
                "webcam_b64": webcam_b64,
                "screen_b64": screen_b64,
            },
        )

    def cancel_current(self) -> None:
        self._post("/api/cancel")

    def stop_task(self) -> None:
        self._post("/api/stop_task")

    def set_user(self, user_id: int, user_name: str | None = None) -> None:
        self._post("/api/set_user", {"user_id": user_id, "user_name": user_name})

    def set_focus_mode(self, enabled: bool) -> None:
        self._post("/api/set_focus_mode", {"enabled": bool(enabled)})

    def set_copilot_mode(self, enabled: bool) -> None:
        self._post("/api/set_copilot_mode", {"enabled": bool(enabled)})

    def inject_screen_capture(self, screen_b64: str) -> None:
        self._post("/api/inject_screen", {"screen_b64": screen_b64})

    def set_screen_vision(self, enabled: bool) -> None:
        self._post("/api/set_screen_vision", {"enabled": bool(enabled)})

    def start_learning(self) -> None:
        self._post("/api/start_learning")

    def stop_learning(self, name: str) -> None:
        self._post("/api/stop_learning", {"name": name})

    def run_routine(self, name: str) -> None:
        self._post("/api/run_routine", {"name": name})

    def get_debug_state(self) -> str:
        data = self._get("/api/debug_state")
        return str(data.get("text", ""))

    def get_session_snapshot(self) -> dict:
        return self._get("/api/session_snapshot")

    def invoke(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._post(
            "/api/invoke",
            {"method": method, "args": list(args), "kwargs": kwargs},
        )

    def enqueue_job(
        self,
        *,
        user_id: int = 0,
        name: str = "",
        steps: list[dict] | None = None,
    ) -> int:
        data = self._post(
            "/api/jobs/enqueue",
            {"user_id": user_id, "name": name, "steps": steps or []},
        )
        return int(data.get("job_id", 0))

    def get_job(self, job_id: int) -> dict:
        return self._get(f"/api/jobs/{job_id}")

    def list_scheduler_pending(self) -> list[dict]:
        return list(self._get("/api/scheduler/pending").get("pending") or [])

    def resolve_scheduler_pending(self, pending_id: int, *, approved: bool) -> None:
        self._post(
            f"/api/scheduler/pending/{int(pending_id)}/resolve",
            {"approved": bool(approved)},
        )

    def list_scheduler_definitions(self) -> list[dict]:
        return list(self._get("/api/scheduler/definitions").get("jobs") or [])

    def configure_weekly_routine(
        self,
        *,
        day_of_week: str = "mon",
        hour: int = 8,
        minute: int = 0,
        checklist: list | None = None,
    ) -> None:
        self._post(
            "/api/scheduler/weekly-routine",
            {
                "day_of_week": day_of_week,
                "hour": hour,
                "minute": minute,
                "checklist": checklist,
            },
        )

    def list_scheduler_activity(self, since_days: float = 7.0) -> list[dict]:
        return list(
            self._get("/api/scheduler/activity", params={"since_days": since_days}).get(
                "activity"
            )
            or []
        )

    # ── Account RPC ───────────────────────────────────────────────────────────

    def account_cloud_available(self) -> bool:
        return bool(self._get("/api/accounts/cloud_available").get("available"))

    def account_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._post(
            "/api/accounts/call",
            {"method": method, "args": list(args), "kwargs": kwargs},
        )


class AccountClient:
    """Thin façade mirroring AccountManager for LoginDialog / Settings."""

    def __init__(self, client: DaemonClient) -> None:
        self._client = client
        self.user_id: int | None = None
        self.email: str | None = None
        self.cloud = _RemoteCloud(self._client)

    @property
    def cloud_available(self) -> bool:
        return self._client.account_cloud_available()

    def _unpack(self, data: dict) -> tuple[bool, str, int]:
        return (
            bool(data.get("ok")),
            str(data.get("message", "")),
            int(data.get("user_id") or 0),
        )

    def register_local(self, name: str, email: str | None = None,
                       password: str | None = None) -> tuple[bool, str, int]:
        data = self._client.account_call(
            "register_local", name, email=email, password=password,
        )
        ok, msg, uid = self._unpack(data)
        if ok:
            self.user_id = uid
            self.email = email
        return ok, msg, uid

    def login_local(self, name: str, password: str | None = None) -> tuple[bool, str, int]:
        data = self._client.account_call("login_local", name, password=password)
        ok, msg, uid = self._unpack(data)
        if ok:
            self.user_id = uid
        return ok, msg, uid

    def register_cloud(self, name: str, email: str, password: str) -> tuple[bool, str, int]:
        data = self._client.account_call(
            "register_cloud", name, email, password,
        )
        ok, msg, uid = self._unpack(data)
        if ok:
            self.user_id, self.email = uid, email
        return ok, msg, uid

    def login_cloud(self, email: str, password: str, name: str | None = None
                    ) -> tuple[bool, str, int]:
        data = self._client.account_call(
            "login_cloud", email, password, name=name,
        )
        ok, msg, uid = self._unpack(data)
        if ok:
            self.user_id, self.email = uid, email
        elif msg == "2FA_REQUIRED":
            self.user_id = uid
        return ok, msg, uid

    def complete_pending_2fa(self, code: str) -> tuple[bool, str, int]:
        data = self._client.account_call("complete_pending_2fa", code)
        return self._unpack(data)

    def start_2fa_challenge(self, user_id: int, email: str) -> tuple[bool, str]:
        data = self._client.account_call("start_2fa_challenge", user_id, email)
        return bool(data.get("ok")), str(data.get("message", ""))

    def needs_2fa(self, user_id: int) -> tuple[bool, str]:
        data = self._client.account_call("needs_2fa", user_id)
        return bool(data.get("needs")), str(data.get("method", ""))

    def login_otp_cloud(self, email: str, code: str) -> tuple[bool, str, int]:
        data = self._client.account_call("login_otp_cloud", email, code)
        ok, msg, uid = self._unpack(data)
        if ok:
            self.user_id, self.email = uid, email
        return ok, msg, uid

    def sync_up(self, user_id: int) -> None:
        self._client.account_call("sync_up", user_id)

    def sync_down(self, user_id: int) -> None:
        self._client.account_call("sync_down", user_id)

    def get_security(self, user_id: int) -> dict:
        return dict(self._client.account_call("get_security", user_id).get("data") or {})

    def set_security(self, user_id: int, **kwargs: Any) -> None:
        self._client.account_call("set_security", user_id, **kwargs)

    def change_password(self, user_id: int, old_pw: str, new_pw: str) -> tuple[bool, str]:
        data = self._client.account_call(
            "change_password", user_id, old_pw, new_pw,
        )
        return bool(data.get("ok")), str(data.get("message", ""))

    def set_app_lock_pin(self, user_id: int, pin: str) -> tuple[bool, str]:
        data = self._client.account_call("set_app_lock_pin", user_id, pin)
        return bool(data.get("ok")), str(data.get("message", ""))


class _RemoteCloud:
    def __init__(self, client: DaemonClient) -> None:
        self._client = client

    def send_email_otp(self, email: str) -> tuple[bool, str]:
        data = self._client.account_call("cloud_send_email_otp", email)
        return bool(data.get("ok")), str(data.get("message", ""))


def ensure_daemon_running(timeout: float = 20.0) -> DaemonClient:
    """Connect to atlas_daemon, spawning it if necessary."""
    import subprocess
    import sys

    client = DaemonClient(auto_connect=False)
    if client.health():
        client.start_event_stream()
        return client
    subprocess.Popen(
        [sys.executable, "-m", "atlas_daemon"],
        cwd=str(Path(__file__).resolve().parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not client.wait_for_health(timeout):
        raise DaemonError(
            "Atlas daemon did not start — run: python -m atlas_daemon"
        )
    client.start_event_stream()
    return client
