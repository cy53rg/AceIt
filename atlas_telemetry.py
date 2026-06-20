"""
atlas_telemetry.py — Account status check + user feedback (client-side only).

Endpoint URLs come from environment / config — never hardcoded production URLs.
Fails OPEN on network errors for license checks (never lock out offline users).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from atlas_logging import get_logger

log = get_logger("telemetry")

APP_VERSION = "0.9.0-mvp"
DEVICE_ID_PATH = Path.home() / ".atlas" / "device_id"


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


LICENSE_URL = _env("ATLAS_LICENSE_URL", "https://api.example.com/atlas/v1/access")
FEEDBACK_URL = _env("ATLAS_FEEDBACK_URL", "https://api.example.com/atlas/v1/feedback")
LICENSE_CHECK_INTERVAL_S = int(_env("ATLAS_LICENSE_INTERVAL_S", "86400"))


def device_fingerprint() -> str:
    """Stable, non-PII device id persisted locally."""
    if DEVICE_ID_PATH.is_file():
        return DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
    raw = f"{platform.node()}|{platform.system()}|{uuid.getnode()}"
    fid = hashlib.sha256(raw.encode()).hexdigest()[:32]
    DEVICE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEVICE_ID_PATH.write_text(fid, encoding="utf-8")
    return fid


@dataclass
class LicenseStatus:
    allowed: bool = True
    message: str = ""
    checked_at: float = 0.0
    source: str = "default"   # default | network | cache | error


class TelemetryClient:
    """License/access check + feedback submission."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = LicenseStatus(allowed=True, message="", source="default")
        self._timer: Optional[threading.Timer] = None
        self.execution_allowed = True

    @property
    def status(self) -> LicenseStatus:
        with self._lock:
            return LicenseStatus(
                allowed=self._status.allowed,
                message=self._status.message,
                checked_at=self._status.checked_at,
                source=self._status.source,
            )

    def check_license(self, email: str = "") -> LicenseStatus:
        """
        POST {email, device_id, app_version} → {allowed, message}.
        Fail OPEN on any network/parse error.
        """
        payload = {
            "email": email or "local@atlas",
            "device_id": device_fingerprint(),
            "app_version": APP_VERSION,
            "platform": platform.platform(),
        }
        try:
            data = _post_json(LICENSE_URL, payload, timeout=8.0)
            allowed = bool(data.get("allowed", True))
            message = str(data.get("message", "") or "")
            st = LicenseStatus(
                allowed=allowed,
                message=message,
                checked_at=time.time(),
                source="network",
            )
        except Exception as exc:
            log.warning("License check failed (fail-open): %s", exc)
            st = LicenseStatus(
                allowed=True,
                message="",
                checked_at=time.time(),
                source="error",
            )
        with self._lock:
            self._status = st
            self.execution_allowed = st.allowed
        return st

    def start_daily_check(self, email: str = "", on_update=None) -> None:
        def _tick() -> None:
            st = self.check_license(email)
            if on_update:
                try:
                    on_update(st)
                except Exception:
                    pass
            self._schedule_next(email, on_update)

        self._schedule_next = lambda e, cb: None  # placeholder overwritten below

        def _schedule(email_: str, cb) -> None:
            t = threading.Timer(LICENSE_CHECK_INTERVAL_S, _tick)
            t.daemon = True
            t.start()
            self._timer = t

        self._schedule_next = _schedule
        _schedule(email, on_update)

    def send_feedback(
        self,
        message: str,
        *,
        email: str = "",
        screenshot_b64: Optional[str] = None,
        include_context: bool = False,
        extra: Optional[dict] = None,
    ) -> tuple[bool, str]:
        body: dict[str, Any] = {
            "message": message.strip(),
            "email": email or "",
            "device_id": device_fingerprint(),
            "app_version": APP_VERSION,
            "platform": platform.platform(),
            "timestamp": time.time(),
        }
        if screenshot_b64:
            body["screenshot_b64"] = screenshot_b64
        if include_context:
            body["context"] = extra or {}
        try:
            _post_json(FEEDBACK_URL, body, timeout=12.0)
            return True, "Thanks — your feedback was sent."
        except Exception as exc:
            log.warning("Feedback send failed: %s", exc)
            _save_feedback_local(body)
            return False, "Couldn't reach the server — feedback saved locally."

    def apply_execution_gate(self, state_engine) -> None:
        """Disable agent automation when license blocks execution."""
        if self.execution_allowed:
            return
        if state_engine is not None:
            state_engine.execution_blocked = True


def _post_json(url: str, payload: dict, timeout: float = 10.0) -> dict:
    if not url or "example.com" in url:
        log.debug("Telemetry stub URL — treating as allowed/no-op: %s", url)
        return {"allowed": True, "ok": True}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": f"Atlas/{APP_VERSION}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def _save_feedback_local(body: dict) -> None:
    p = Path.home() / ".atlas" / "feedback_queue"
    p.mkdir(parents=True, exist_ok=True)
    fn = p / f"feedback_{int(time.time())}.json"
    fn.write_text(json.dumps(body, indent=2), encoding="utf-8")
