"""Gmail connector — OAuth with minimal send/read scopes."""
from __future__ import annotations

import base64
import os
import time
from email.mime.text import MIMEText
from typing import Any, Optional

import requests

from atlas_connectors.base import Connector, connector_action
from atlas_connectors.google_oauth import (
    client_config,
    ensure_access_token,
    exchange_code,
    run_oauth_flow,
)
from atlas_logging import get_logger
from atlas_policy import RiskClass

log = get_logger("connectors.gmail")

_GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
_REDIRECT_URI = "http://127.0.0.1:8767/gmail/callback"
_OAUTH_PORT = 8767
_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)


class GmailConnector(Connector):
    connector_id = "gmail"
    display_name = "Gmail"

    @property
    def scopes_requested(self) -> list[str]:
        return list(_SCOPES)

    def scope_boundary_text(self) -> str:
        return (
            "Atlas can read and send email on your behalf. "
            "It cannot change account settings, delete your mailbox, or access other Google services."
        )

    def connect(self) -> tuple[bool, str]:
        client_id, client_secret = client_config()
        if not client_id or not client_secret:
            return (
                False,
                "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env "
                "(Google Cloud OAuth client with Gmail API enabled).",
            )
        code, err = run_oauth_flow(
            scopes=list(_SCOPES),
            redirect_uri=_REDIRECT_URI,
            port=_OAUTH_PORT,
            path_prefix="/gmail/callback",
        )
        if not code:
            return False, str(err or "OAuth cancelled or timed out.")
        try:
            data = exchange_code(code, redirect_uri=_REDIRECT_URI)
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
            return True, "Gmail connected."
        except Exception as exc:
            log.exception("Gmail OAuth failed")
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
        return True, "Gmail disconnected."

    def _access_token(self) -> str:
        return ensure_access_token(
            self._token_store,
            self.connector_id,
            scopes=self.scopes_requested,
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _api_get(self, path: str, *, params: Optional[dict] = None) -> Any:
        r = requests.get(
            f"{_GMAIL_API}{path}",
            headers=self._headers(),
            params=params or {},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _api_post(self, path: str, payload: dict) -> dict:
        r = requests.post(
            f"{_GMAIL_API}{path}",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    @connector_action("list_messages", RiskClass.READ_ONLY, description="List recent Gmail messages")
    def list_messages(self, *, max_results: int = 10, query: str = "") -> dict:
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(max_results), 20)),
        }
        q = (query or "").strip()
        if q:
            params["q"] = q
        data = self._api_get("/users/me/messages", params=params)
        messages = []
        for item in data.get("messages") or []:
            mid = item.get("id")
            if not mid:
                continue
            detail = self._api_get(
                f"/users/me/messages/{mid}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                },
            )
            headers = {
                h.get("name", ""): h.get("value", "")
                for h in (detail.get("payload") or {}).get("headers") or []
            }
            messages.append({
                "id": mid,
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": detail.get("snippet", ""),
            })
        return {"messages": messages}

    @connector_action("send_message", RiskClass.WRITE_SENSITIVE, description="Send a Gmail message")
    def send_message(self, to: str, subject: str, body: str) -> dict:
        recipient = (to or "").strip()
        if not recipient:
            raise ValueError("to is required")
        msg = MIMEText((body or "").strip())
        msg["to"] = recipient
        msg["subject"] = (subject or "").strip()[:500]
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        created = self._api_post("/users/me/messages/send", {"raw": raw})
        return {
            "id": created.get("id"),
            "to": recipient,
            "subject": msg["subject"],
        }
