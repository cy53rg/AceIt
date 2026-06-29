"""GitHub connector — explicit actions only (no generic API passthrough)."""
from __future__ import annotations

import json
import os
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

import requests

from atlas_connectors.base import Connector, connector_action
from atlas_logging import get_logger
from atlas_policy import RiskClass

log = get_logger("connectors.github")

_GITHUB_AUTH = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
_GITHUB_API = "https://api.github.com"


class GitHubConnector(Connector):
    connector_id = "github"
    display_name = "GitHub"

    @property
    def scopes_requested(self) -> list[str]:
        return ["repo:read", "repo:write"]

    def scope_boundary_text(self) -> str:
        return (
            "Atlas can read your GitHub repositories and issues, and create new issues. "
            "It cannot delete repositories, change billing, or access organization admin settings."
        )

    def connect(self) -> tuple[bool, str]:
        client_id = (os.environ.get("GITHUB_CLIENT_ID") or "").strip()
        client_secret = (os.environ.get("GITHUB_CLIENT_SECRET") or "").strip()
        if not client_id:
            return False, "Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in .env for OAuth."

        state = os.urandom(8).hex()
        redirect_uri = "http://127.0.0.1:8765/github/callback"
        params = urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.scopes_requested),
            "state": state,
        })
        auth_url = f"{_GITHUB_AUTH}?{params}"

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

        server = HTTPServer(("127.0.0.1", 8765), Handler)
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
                _GITHUB_TOKEN,
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            if not token:
                return False, data.get("error_description") or "No access token returned."
            self._token_store.save_token(
                self.connector_id,
                {"access_token": token, "token_type": data.get("token_type", "bearer")},
                scopes=self.scopes_requested,
            )
            return True, "GitHub connected."
        except Exception as exc:
            log.exception("GitHub OAuth failed")
            return False, str(exc)

    def disconnect(self) -> tuple[bool, str]:
        tok = self._token_store.load_token(self.connector_id)
        if tok and tok.get("access_token"):
            try:
                requests.delete(
                    f"{_GITHUB_API}/applications/{os.environ.get('GITHUB_CLIENT_ID', '')}/token",
                    auth=(os.environ.get("GITHUB_CLIENT_ID", ""), os.environ.get("GITHUB_CLIENT_SECRET", "")),
                    json={"access_token": tok["access_token"]},
                    timeout=15,
                )
            except Exception:
                pass
        self._token_store.delete_token(self.connector_id)
        return True, "GitHub disconnected."

    def _headers(self) -> dict[str, str]:
        tok = self._token_store.load_token(self.connector_id)
        if not tok or not tok.get("access_token"):
            raise RuntimeError("GitHub not connected")
        return {
            "Authorization": f"Bearer {tok['access_token']}",
            "Accept": "application/vnd.github+json",
        }

    def _api_post(self, path: str, payload: dict) -> dict:
        r = requests.post(f"{_GITHUB_API}{path}", headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def _api_get(self, path: str) -> dict:
        r = requests.get(f"{_GITHUB_API}{path}", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    @connector_action("list_issues", RiskClass.READ_ONLY, description="List open issues in a repo")
    def list_issues(self, repo: str, *, state: str = "open") -> dict:
        owner, name = repo.split("/", 1)
        data = self._api_get(f"/repos/{owner}/{name}/issues?state={state}&per_page=20")
        return {"issues": data}

    @connector_action("create_issue", RiskClass.WRITE_SCOPED, description="Create a GitHub issue")
    def create_issue(self, repo: str, title: str, body: str = "") -> dict:
        owner, name = repo.split("/", 1)
        return self._api_post(
            f"/repos/{owner}/{name}/issues",
            {"title": title, "body": body},
        )

    @connector_action("delete_repo", RiskClass.IRREVERSIBLE, description="Delete a repository")
    def delete_repo(self, repo: str) -> dict:
        owner, name = repo.split("/", 1)
        r = requests.delete(
            f"{_GITHUB_API}/repos/{owner}/{name}",
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        return {"deleted": True, "repo": repo}
