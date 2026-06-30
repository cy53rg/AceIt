"""Shared Google OAuth helpers for Calendar, Gmail, etc."""
from __future__ import annotations

import os
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Optional

import requests

from atlas_logging import get_logger

log = get_logger("connectors.google_oauth")

_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


def client_config() -> tuple[str, str]:
    client_id = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    return client_id, client_secret


def run_oauth_flow(
    *,
    scopes: list[str],
    redirect_uri: str,
    port: int,
    path_prefix: str = "/callback",
) -> tuple[Optional[str], Optional[str]]:
    """Open browser OAuth; return (code, error)."""
    client_id, _ = client_config()
    if not client_id:
        return None, "GOOGLE_CLIENT_ID not configured"

    state = os.urandom(8).hex()
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    auth_url = f"{_GOOGLE_AUTH}?{params}"
    result: dict[str, Any] = {"code": None, "error": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if not parsed.path.startswith(path_prefix):
                self.send_response(404)
                self.end_headers()
                return
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

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    webbrowser.open(auth_url)
    thread.join(timeout=120.0)
    server.server_close()
    return result.get("code"), result.get("error")


def exchange_code(code: str, *, redirect_uri: str) -> dict[str, Any]:
    client_id, client_secret = client_config()
    resp = requests.post(
        _GOOGLE_TOKEN,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(tok: dict[str, Any]) -> dict[str, Any]:
    refresh = tok.get("refresh_token")
    if not refresh:
        return tok
    client_id, client_secret = client_config()
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
        return {
            **tok,
            "access_token": access,
            "expires_at": time.time() + expires_in - 60,
        }
    except Exception as exc:
        log.warning("Google token refresh failed: %s", exc)
        return tok


def ensure_access_token(
    token_store: Any,
    connector_id: str,
    *,
    scopes: list[str],
) -> str:
    tok = token_store.load_token(connector_id)
    if not tok or not tok.get("access_token"):
        raise RuntimeError(f"{connector_id} not connected")
    expires_at = float(tok.get("expires_at") or 0)
    if expires_at and time.time() >= expires_at:
        tok = refresh_access_token(tok)
        token_store.save_token(connector_id, tok, scopes=scopes)
    token = tok.get("access_token")
    if not token:
        raise RuntimeError(f"{connector_id} not connected")
    return str(token)
