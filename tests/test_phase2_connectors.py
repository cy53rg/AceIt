"""Phase 2 — Google Calendar + extended GitHub connector tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from atlas_mind.router import (
    _parse_reminder_calendar,
    execute_tool,
    regex_tool_fallback,
)


@pytest.fixture
def connector_db(tmp_path, monkeypatch):
    db = tmp_path / "connectors.sqlite3"
    monkeypatch.setenv("ATLAS_CONNECTOR_TEST_KEY", "test-fernet-key-for-unit-tests-only!!")
    monkeypatch.setenv("ATLAS_CONNECTOR_SKIP_DPAPI", "1")
    return db


@pytest.fixture
def registry(connector_db):
    from atlas_connectors.registry import ConnectorRegistry

    reg = ConnectorRegistry(db_path=connector_db, user_id=1)
    reg.set_permission_handler(lambda _a, _p: True)
    reg.set_typed_confirm_handler(lambda _m: True)
    return reg


def test_action_risk_map_phase2(registry):
    from atlas_connectors.registry import ACTION_RISK_MAP
    from atlas_policy import RiskClass

    assert ACTION_RISK_MAP["google_calendar.create_event"] == RiskClass.WRITE_SCOPED
    assert ACTION_RISK_MAP["google_calendar.list_events"] == RiskClass.READ_ONLY
    assert ACTION_RISK_MAP["github.create_repo"] == RiskClass.WRITE_SCOPED
    assert ACTION_RISK_MAP["github.push_folder"] == RiskClass.SHELL_DANGEROUS


def test_calendar_create_event(registry, monkeypatch):
    from atlas_connectors.google_calendar import GoogleCalendarConnector

    cal = registry.get("google_calendar")
    assert isinstance(cal, GoogleCalendarConnector)
    cal._token_store.save_token("google_calendar", {"access_token": "tok", "expires_at": 9e18})
    monkeypatch.setattr(
        cal,
        "_api_post",
        lambda path, payload: {
            "id": "evt1",
            "summary": payload.get("summary"),
            "start": payload.get("start"),
            "htmlLink": "https://calendar.google.com/event?eid=evt1",
        },
    )

    start = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0).isoformat()
    result = registry.execute(
        "google_calendar",
        "create_event",
        safety_mode="off",
        title="Call John",
        start=start,
    )
    assert result.get("ok") is True
    assert result["result"]["summary"] == "Call John"


def test_github_create_repo(registry, monkeypatch):
    from atlas_connectors.github import GitHubConnector

    gh = registry.get("github")
    assert isinstance(gh, GitHubConnector)
    gh._token_store.save_token("github", {"access_token": "gho_test"})
    monkeypatch.setattr(
        gh,
        "_api_post",
        lambda path, payload: {
            "full_name": f"me/{payload['name']}",
            "html_url": f"https://github.com/me/{payload['name']}",
        },
    )

    result = registry.execute(
        "github",
        "create_repo",
        safety_mode="off",
        name="atlas-demo",
        private=False,
    )
    assert result.get("ok") is True
    assert "atlas-demo" in result["result"]["full_name"]


def test_github_push_folder_policy_gated(registry, monkeypatch, tmp_path):
    from atlas_connectors.github import GitHubConnector

    gh = registry.get("github")
    gh._token_store.save_token("github", {"access_token": "gho_test"})
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "readme.txt").write_text("hello", encoding="utf-8")

    calls: list[str] = []

    def _fake_run(cmd, **kwargs):
        calls.append(kwargs.get("audit_detail") or cmd)
        if "push" in cmd:
            return {"ok": True, "returncode": 0, "stdout": "pushed"}
        return {"ok": True, "returncode": 0, "stdout": "ok"}

    monkeypatch.setattr("atlas_shell.shell_runner.run", _fake_run)

    result = registry.execute(
        "github",
        "push_folder",
        safety_mode="off",
        folder_path=str(folder),
        repo="me/demo",
    )
    assert result.get("ok") is True
    assert any("git push" in c for c in calls)
    assert not any("gho_test" in c for c in calls)


def test_parse_reminder_calendar():
    parsed = _parse_reminder_calendar("remind me to call John tomorrow at 9am")
    assert parsed is not None
    assert parsed["title"] == "call John"
    assert "09:00" in parsed["start"] or "9:00" in parsed["start"]


def test_regex_calendar_reminder():
    picked = regex_tool_fallback("remind me to call John tomorrow at 9am")
    assert picked is not None
    assert picked[0] == "calendar_create_event"
    assert picked[1]["title"] == "call John"


def test_router_calendar_create_integration(monkeypatch):
    engine = MagicMock()
    engine.get_user_prefs.return_value = {}
    engine.safety_mode = "off"
    engine._fs_access_active = False
    engine.execution_blocked = False
    engine.connectors = MagicMock()
    engine.connectors.execute.return_value = {
        "ok": True,
        "result": {
            "summary": "Call John",
            "start": "2026-06-02T09:00:00",
            "htmlLink": "https://calendar.google.com/event?eid=x",
        },
    }

    msg = execute_tool(
        engine,
        "calendar_create_event",
        {"title": "Call John", "start": "2026-06-02T09:00:00"},
    )
    assert "Call John" in msg
    assert "created" in msg.lower()
    engine.connectors.execute.assert_called_once()
