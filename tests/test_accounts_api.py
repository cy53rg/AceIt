"""Regression: account RPC must accept JSON bodies (PEP 563 + FastAPI)."""
from __future__ import annotations

import importlib

import atlas_daemon
from fastapi.testclient import TestClient


def test_accounts_call_login_local():
    importlib.reload(atlas_daemon)
    atlas_daemon._init_services("test_accounts_call")
    client = TestClient(atlas_daemon.create_app())

    resp = client.post(
        "/api/accounts/call",
        json={"method": "login_local", "args": ["default"], "kwargs": {}},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("ok") is True
    assert int(data.get("user_id") or 0) > 0


def test_accounts_call_unknown_method():
    importlib.reload(atlas_daemon)
    atlas_daemon._init_services("test_accounts_unknown")
    client = TestClient(atlas_daemon.create_app())

    resp = client.post(
        "/api/accounts/call",
        json={"method": "not_a_real_method", "args": [], "kwargs": {}},
    )
    assert resp.status_code == 200
    assert resp.json().get("ok") is False
