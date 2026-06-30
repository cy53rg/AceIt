"""Optional Composio bridge — opt-in when COMPOSIO_API_KEY is set."""
from __future__ import annotations

import os
from typing import Any

import requests

from atlas_connectors.base import Connector, connector_action
from atlas_logging import get_logger
from atlas_policy import RiskClass

log = get_logger("connectors.composio")

_COMPOSIO_API = "https://backend.composio.dev/api/v3"


class ComposioBridgeConnector(Connector):
    connector_id = "composio"
    display_name = "Composio (optional)"

    @property
    def scopes_requested(self) -> list[str]:
        return []

    def scope_boundary_text(self) -> str:
        return (
            "Optional Composio bridge for third-party app actions. "
            "Only enabled when COMPOSIO_API_KEY is set in .env."
        )

    def connect(self) -> tuple[bool, str]:
        key = (os.environ.get("COMPOSIO_API_KEY") or "").strip()
        if not key:
            return False, "Set COMPOSIO_API_KEY in .env to enable Composio."
        self._token_store.save_token(
            self.connector_id,
            {"api_key": key},
            scopes=[],
        )
        return True, "Composio bridge enabled."

    def disconnect(self) -> tuple[bool, str]:
        self._token_store.delete_token(self.connector_id)
        return True, "Composio disconnected."

    def _api_key(self) -> str:
        tok = self._token_store.load_token(self.connector_id) or {}
        key = str(tok.get("api_key") or os.environ.get("COMPOSIO_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("Composio not connected")
        return key

    @connector_action("list_toolkits", RiskClass.READ_ONLY, description="List Composio toolkits")
    def list_toolkits(self) -> dict:
        r = requests.get(
            f"{_COMPOSIO_API}/toolkits",
            headers={"x-api-key": self._api_key()},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("items") or data.get("toolkits") or []
        names = []
        for item in items[:20]:
            if isinstance(item, dict):
                names.append(str(item.get("name") or item.get("slug") or ""))
        return {"toolkits": [n for n in names if n]}
