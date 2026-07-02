"""Goal risk model must fail closed when unavailable."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from atlas_policy import PolicyEngine, reset_goal_risk_model_cache


@pytest.fixture(autouse=True)
def _clear_risk_model_cache(monkeypatch):
    monkeypatch.delenv("ATLAS_ADMIN_PASSWORD", raising=False)
    reset_goal_risk_model_cache()
    yield
    reset_goal_risk_model_cache()


def _write_model(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "deny_substrings": ["delete all files"],
                "deny_regex": [],
            }
        ),
        encoding="utf-8",
    )


def test_delete_all_files_rejected_when_model_loaded(tmp_path, monkeypatch):
    model_path = tmp_path / "goal_risk_model.json"
    _write_model(model_path)
    monkeypatch.setenv("ATLAS_GOAL_RISK_MODEL", str(model_path))

    engine = PolicyEngine(db_path=tmp_path / "policy.sqlite3", user_id=1)
    result = engine.can_execute_goal("delete all files on my desktop")

    assert result["approved"] is False
    assert result["reason"] == "goal_denied_by_risk_model"


def test_missing_model_rejects_without_admin_override(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setenv("ATLAS_GOAL_RISK_MODEL", str(missing))

    engine = PolicyEngine(db_path=tmp_path / "policy.sqlite3", user_id=1)
    result = engine.can_execute_goal("delete all files")

    assert result["approved"] is False
    assert result["reason"] == "risk_model_unavailable"
    assert result["error"]


def test_corrupt_model_rejects_without_admin_override(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("ATLAS_GOAL_RISK_MODEL", str(bad))

    engine = PolicyEngine(db_path=tmp_path / "policy.sqlite3", user_id=1)
    result = engine.can_execute_goal("delete all files")

    assert result["approved"] is False
    assert result["reason"] == "risk_model_unavailable"


def test_admin_password_allows_override_when_model_missing(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setenv("ATLAS_GOAL_RISK_MODEL", str(missing))
    monkeypatch.setenv("ATLAS_ADMIN_PASSWORD", "break-glass-secret")

    engine = PolicyEngine(db_path=tmp_path / "policy.sqlite3", user_id=1)
    result = engine.can_execute_goal("delete all files")

    assert result["approved"] is True
    assert result["reason"] == "admin_override"


def test_prepare_task_start_blocks_when_model_unavailable(tmp_path, monkeypatch):
    from tests.test_intent_router_wiring import _engine_with_events

    missing = tmp_path / "missing.json"
    monkeypatch.setenv("ATLAS_GOAL_RISK_MODEL", str(missing))
    engine = _engine_with_events()
    engine.memory.db_path = tmp_path / "mem.sqlite3"

    assert engine._prepare_task_start("delete all files") is False
