"""Gmail connector — OAuth with minimal send/read scopes."""
from __future__ import annotations

import os

from atlas_connectors.base import Connector, connector_action
from atlas_policy import RiskClass


class GmailConnector(Connector):
    connector_id = "gmail"
    display_name = "Gmail"

    @property
    def scopes_requested(self) -> list[str]:
        return [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ]

    def scope_boundary_text(self) -> str:
        return (
            "Atlas can read and send email on your behalf. "
            "It cannot change account settings, delete your mailbox, or access other Google services."
        )

    def connect(self) -> tuple[bool, str]:
        if not (os.environ.get("GOOGLE_CLIENT_ID") or "").strip():
            return False, "Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET for Gmail OAuth (coming soon)."
        return False, "Gmail OAuth wiring pending — configure Google Cloud OAuth client."

    def disconnect(self) -> tuple[bool, str]:
        self._token_store.delete_token(self.connector_id)
        return True, "Gmail disconnected."

    @connector_action("list_messages", RiskClass.READ_ONLY)
    def list_messages(self, *, max_results: int = 10) -> dict:
        raise RuntimeError("Gmail not connected")

    @connector_action("send_message", RiskClass.WRITE_SENSITIVE)
    def send_message(self, to: str, subject: str, body: str) -> dict:
        raise RuntimeError("Gmail not connected")
