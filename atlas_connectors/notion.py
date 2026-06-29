"""Notion connector — OAuth with minimal page read/write scopes."""
from __future__ import annotations

import os

from atlas_connectors.base import Connector, connector_action
from atlas_policy import RiskClass


class NotionConnector(Connector):
    connector_id = "notion"
    display_name = "Notion"

    @property
    def scopes_requested(self) -> list[str]:
        return []

    def scope_boundary_text(self) -> str:
        return (
            "Atlas can read and update pages you explicitly share with the integration. "
            "It cannot access your full workspace or admin settings without sharing."
        )

    def connect(self) -> tuple[bool, str]:
        if not (os.environ.get("NOTION_CLIENT_ID") or "").strip():
            return False, "Set NOTION_CLIENT_ID / NOTION_CLIENT_SECRET for OAuth."
        return False, "Notion OAuth wiring pending — create a Notion integration first."

    def disconnect(self) -> tuple[bool, str]:
        self._token_store.delete_token(self.connector_id)
        return True, "Notion disconnected."

    @connector_action("search_pages", RiskClass.READ_ONLY)
    def search_pages(self, query: str) -> dict:
        raise RuntimeError("Notion not connected")

    @connector_action("append_block", RiskClass.WRITE_SCOPED)
    def append_block(self, page_id: str, text: str) -> dict:
        raise RuntimeError("Notion not connected")
