"""Daemon WebSocket must accept UI event stream connections."""
from __future__ import annotations

from starlette.testclient import TestClient

from atlas_daemon import _init_services, create_app


def test_daemon_websocket_accepts():
    _init_services("ws-test")
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws"):
            pass
