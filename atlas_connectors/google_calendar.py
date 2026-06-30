"""Google Calendar connector — OAuth + list/create/delete events."""
from __future__ import annotations

import os
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

import requests

from atlas_connectors.base import Connector, connector_action
from atlas_logging import get_logger
from atlas_policy import RiskClass

log = get_logger("connectors.google_calendar")

_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
_REDIRECT_URI = "http://127.0.0.1:8766/google/calendar/callback"
_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)


class GoogleCalendarConnector(Connector):
    connector_id = "google_calendar"
    display_name = "Google Calendar"

    @property
    def scopes_requested(self) -> list[str]:
        return list(_SCOPES)

    def scope_boundary_text(self) -> str:
        return (
            "Atlas can list, create, and delete events on your primary Google Calendar. "
            "It cannot change calendar settings, share calendars, or access Gmail or Drive."
        )

    def _client_config(self) -> tuple[str, str]:
        client_id = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
        client_secret = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
        return client_id, client_secret

    def connect(self) -> tuple[bool, str]:
        client_id, client_secret = self._client_config()
        if not client_id or not client_secret:
            return (
                False,
                "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env "
                "(Google Cloud OAuth client with Calendar API enabled).",
            )

        state = os.urandom(8).hex()
        params = urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        })
        auth_url = f"{_GOOGLE_AUTH}?{params}"
        result: dict[str, Any] = {"code": None, "error": None}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                if qs.get("state", [""])[0] != state:
                    result["error"] = "OAuth state mismatch"
                else:
                    result["code"] = qs.get("code", [None])[0]
                    result["error"] = qs.get("error_description", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>You can close this window.</body></html>")

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 8766), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        webbrowser.open(auth_url)
        thread.join(timeout=120.0)
        server.server_close()

        code = result.get("code")
        if not code:
            return False, str(result.get("error") or "OAuth cancelled or timed out.")

        try:
            resp = requests.post(
                _GOOGLE_TOKEN,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": _REDIRECT_URI,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            access = data.get("access_token")
            if not access:
                return False, data.get("error_description") or "No access token returned."
            expires_in = int(data.get("expires_in") or 3600)
            self._token_store.save_token(
                self.connector_id,
                {
                    "access_token": access,
                    "refresh_token": data.get("refresh_token"),
                    "expires_at": time.time() + expires_in - 60,
                    "token_type": data.get("token_type", "Bearer"),
                },
                scopes=self.scopes_requested,
            )
            return True, "Google Calendar connected."
        except Exception as exc:
            log.exception("Google Calendar OAuth failed")
            return False, str(exc)

    def disconnect(self) -> tuple[bool, str]:
        tok = self._token_store.load_token(self.connector_id) or {}
        token = tok.get("access_token")
        if token:
            try:
                requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": token},
                    timeout=15,
                )
            except Exception:
                pass
        self._token_store.delete_token(self.connector_id)
        return True, "Google Calendar disconnected."

    def _refresh_access_token(self, tok: dict[str, Any]) -> dict[str, Any]:
        refresh = tok.get("refresh_token")
        if not refresh:
            return tok
        client_id, client_secret = self._client_config()
        try:
            resp = requests.post(
                _GOOGLE_TOKEN,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            access = data.get("access_token")
            if not access:
                return tok
            expires_in = int(data.get("expires_in") or 3600)
            updated = {
                **tok,
                "access_token": access,
                "expires_at": time.time() + expires_in - 60,
            }
            self._token_store.save_token(
                self.connector_id,
                updated,
                scopes=self.scopes_requested,
            )
            return updated
        except Exception as exc:
            log.warning("Calendar token refresh failed: %s", exc)
            return tok

    def _access_token(self) -> str:
        tok = self._token_store.load_token(self.connector_id)
        if not tok or not tok.get("access_token"):
            raise RuntimeError("Google Calendar not connected")
        expires_at = float(tok.get("expires_at") or 0)
        if expires_at and time.time() >= expires_at:
            tok = self._refresh_access_token(tok)
        token = tok.get("access_token")
        if not token:
            raise RuntimeError("Google Calendar not connected")
        return str(token)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _api_get(self, path: str, *, params: Optional[dict] = None) -> Any:
        r = requests.get(
            f"{_CALENDAR_API}{path}",
            headers=self._headers(),
            params=params or {},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _api_post(self, path: str, payload: dict) -> dict:
        r = requests.post(
            f"{_CALENDAR_API}{path}",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _api_delete(self, path: str) -> None:
        r = requests.delete(f"{_CALENDAR_API}{path}", headers=self._headers(), timeout=30)
        r.raise_for_status()

    @staticmethod
    def _parse_event_time(value: str, *, default_minutes: int = 30) -> tuple[str, str]:
        """Parse ISO or date+time into Calendar API dateTime + end."""
        raw = (value or "").strip()
        if not raw:
            start = datetime.now(timezone.utc) + timedelta(minutes=5)
        else:
            try:
                start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=datetime.now().astimezone().tzinfo)
            except ValueError:
                start = datetime.now(timezone.utc) + timedelta(minutes=5)
        end = start + timedelta(minutes=default_minutes)
        tz = str(start.tzinfo) if start.tzinfo else "UTC"
        return (
            start.isoformat(),
            end.isoformat(),
        )

    @connector_action("list_events", RiskClass.READ_ONLY, description="List upcoming calendar events")
    def list_events(
        self,
        *,
        calendar_id: str = "primary",
        time_min: str = "",
        time_max: str = "",
        max_results: int = 10,
    ) -> dict:
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(max_results), 50)),
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_min:
            params["timeMin"] = time_min
        else:
            params["timeMin"] = datetime.now(timezone.utc).isoformat()
        if time_max:
            params["timeMax"] = time_max
        data = self._api_get(f"/calendars/{calendar_id}/events", params=params)
        events = []
        for ev in data.get("items") or []:
            start = ev.get("start") or {}
            events.append({
                "id": ev.get("id"),
                "summary": ev.get("summary", "(no title)"),
                "start": start.get("dateTime") or start.get("date"),
                "htmlLink": ev.get("htmlLink"),
            })
        return {"events": events}

    @connector_action("create_event", RiskClass.WRITE_SCOPED, description="Create a calendar event")
    def create_event(
        self,
        title: str,
        start: str,
        *,
        end: str = "",
        description: str = "",
        calendar_id: str = "primary",
    ) -> dict:
        start_iso, end_iso = self._parse_event_time(start)
        if end:
            try:
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                end_iso = end_dt.isoformat()
            except ValueError:
                pass
        tz = datetime.now().astimezone().tzinfo
        tz_name = datetime.now().astimezone().tzname() or "UTC"
        payload = {
            "summary": (title or "Reminder").strip()[:200],
            "description": (description or "")[:4000],
            "start": {"dateTime": start_iso, "timeZone": tz_name},
            "end": {"dateTime": end_iso, "timeZone": tz_name},
        }
        created = self._api_post(f"/calendars/{calendar_id}/events", payload)
        return {
            "id": created.get("id"),
            "summary": created.get("summary"),
            "start": (created.get("start") or {}).get("dateTime"),
            "htmlLink": created.get("htmlLink"),
        }

    @connector_action("delete_event", RiskClass.WRITE_SENSITIVE, description="Delete a calendar event by ID")
    def delete_event(self, event_id: str, *, calendar_id: str = "primary") -> dict:
        eid = (event_id or "").strip()
        if not eid:
            raise ValueError("event_id is required")
        self._api_delete(f"/calendars/{calendar_id}/events/{eid}")
        return {"deleted": True, "event_id": eid}
