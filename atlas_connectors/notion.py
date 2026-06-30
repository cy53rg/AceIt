"""Notion connector — OAuth or internal integration token."""
from __future__ import annotations

import base64
import os
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

import requests

from atlas_connectors.base import Connector, connector_action
from atlas_logging import get_logger
from atlas_policy import RiskClass

log = get_logger("connectors.notion")

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_NOTION_AUTH = "https://api.notion.com/v1/oauth/authorize"
_NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
_REDIRECT_URI = "http://127.0.0.1:8768/notion/callback"
_OAUTH_PORT = 8768


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

    def _client_config(self) -> tuple[str, str]:
        client_id = (os.environ.get("NOTION_CLIENT_ID") or "").strip()
        client_secret = (os.environ.get("NOTION_CLIENT_SECRET") or "").strip()
        return client_id, client_secret

    def connect(self) -> tuple[bool, str]:
        internal = (os.environ.get("NOTION_TOKEN") or "").strip()
        if internal:
            self._token_store.save_token(
                self.connector_id,
                {
                    "access_token": internal,
                    "token_type": "Bearer",
                    "workspace_name": "internal integration",
                },
                scopes=[],
            )
            return True, "Notion connected via NOTION_TOKEN."

        client_id, client_secret = self._client_config()
        if not client_id or not client_secret:
            return (
                False,
                "Set NOTION_CLIENT_ID + NOTION_CLIENT_SECRET for OAuth, "
                "or NOTION_TOKEN for an internal integration token.",
            )

        state = os.urandom(8).hex()
        params = urllib.parse.urlencode({
            "client_id": client_id,
            "response_type": "code",
            "owner": "user",
            "redirect_uri": _REDIRECT_URI,
            "state": state,
        })
        auth_url = f"{_NOTION_AUTH}?{params}"
        result: dict[str, Any] = {"code": None, "error": None}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if not parsed.path.startswith("/notion/callback"):
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                if qs.get("state", [""])[0] != state:
                    result["error"] = "OAuth state mismatch"
                else:
                    result["code"] = qs.get("code", [None])[0]
                    result["error"] = qs.get("error", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>You can close this window.</body></html>")

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", _OAUTH_PORT), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        webbrowser.open(auth_url)
        thread.join(timeout=120.0)
        server.server_close()

        code = result.get("code")
        if not code:
            return False, str(result.get("error") or "OAuth cancelled or timed out.")

        try:
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            resp = requests.post(
                _NOTION_TOKEN_URL,
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/json",
                },
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": _REDIRECT_URI,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            access = data.get("access_token")
            if not access:
                return False, "No access token returned."
            workspace = data.get("workspace_name") or data.get("workspace_id") or ""
            self._token_store.save_token(
                self.connector_id,
                {
                    "access_token": access,
                    "token_type": data.get("token_type", "Bearer"),
                    "workspace_name": workspace,
                    "bot_id": data.get("bot_id"),
                },
                scopes=[],
            )
            return True, f"Notion connected ({workspace or 'workspace'})."
        except Exception as exc:
            log.exception("Notion OAuth failed")
            return False, str(exc)

    def disconnect(self) -> tuple[bool, str]:
        self._token_store.delete_token(self.connector_id)
        return True, "Notion disconnected."

    def _access_token(self) -> str:
        tok = self._token_store.load_token(self.connector_id)
        if not tok or not tok.get("access_token"):
            raise RuntimeError("Notion not connected")
        return str(tok["access_token"])

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _api_post(self, path: str, payload: dict) -> dict:
        r = requests.post(f"{_NOTION_API}{path}", headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def _api_patch(self, path: str, payload: dict) -> dict:
        r = requests.patch(f"{_NOTION_API}{path}", headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _page_title(page: dict) -> str:
        props = page.get("properties") or {}
        for val in props.values():
            if isinstance(val, dict) and val.get("type") == "title":
                parts = val.get("title") or []
                return "".join(
                    p.get("plain_text", "") for p in parts if isinstance(p, dict)
                ).strip()
        return page.get("id", "Untitled")

    @connector_action("search_pages", RiskClass.READ_ONLY, description="Search Notion pages")
    def search_pages(self, query: str, *, max_results: int = 10) -> dict:
        payload: dict[str, Any] = {
            "page_size": max(1, min(int(max_results), 20)),
            "filter": {"property": "object", "value": "page"},
        }
        q = (query or "").strip()
        if q:
            payload["query"] = q
        data = self._api_post("/search", payload)
        pages = []
        for item in data.get("results") or []:
            if not isinstance(item, dict) or item.get("object") != "page":
                continue
            pages.append({
                "id": item.get("id"),
                "title": self._page_title(item),
                "url": item.get("url", ""),
                "last_edited": item.get("last_edited_time", ""),
            })
        return {"pages": pages}

    @connector_action("append_block", RiskClass.WRITE_SCOPED, description="Append text to a Notion page")
    def append_block(self, page_id: str, text: str) -> dict:
        pid = (page_id or "").strip()
        if not pid:
            raise ValueError("page_id is required")
        body = (text or "").strip()
        if not body:
            raise ValueError("text is required")
        created = self._api_patch(
            f"/blocks/{pid}/children",
            {
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": body[:2000]}}],
                        },
                    },
                ],
            },
        )
        return {"page_id": pid, "blocks_added": len(created.get("results") or [])}
