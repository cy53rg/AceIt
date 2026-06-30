"""Regression tests for /api/handle_input daemon route."""
from __future__ import annotations

import importlib

import atlas_daemon
from fastapi.testclient import TestClient


def test_handle_input_accepts_json_text_field():
    importlib.reload(atlas_daemon)
    atlas_daemon._init_services("test_handle_input")
    app = atlas_daemon.create_app()
    client = TestClient(app)

    route = next(
        r for r in app.routes if getattr(r, "path", None) == "/api/handle_input"
    )
    assert route.methods == {"POST"}

    resp = client.post(
        "/api/handle_input",
        json={"text": "hello", "source": "user"},
    )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


def test_handle_input_rejects_empty_text():
    importlib.reload(atlas_daemon)
    atlas_daemon._init_services("test_handle_input_empty")
    client = TestClient(atlas_daemon.create_app())

    resp = client.post(
        "/api/handle_input",
        json={"text": "   ", "source": "user"},
    )
    assert resp.status_code == 400
