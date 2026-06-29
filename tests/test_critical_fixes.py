"""Tier 1–2 critical fixes — unit coverage for safety, memory, accounts."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas_accounts import AccountManager
from atlas_memory import UserMemory, _MEMORY_PROMPT_TOKEN_BUDGET
from atlas_recorder import StreamBracketFilter
from atlas_task_safety import check_task_goal_allowed


def _temp_memory() -> UserMemory:
    path = Path(tempfile.mkdtemp()) / "critical_fixes.sqlite3"
    return UserMemory(path)


def test_task_goal_delete_all_files_blocked():
    allowed, reason = check_task_goal_allowed("delete all files in Downloads")
    assert allowed is False
    assert reason


def test_task_goal_benign_allowed():
    allowed, _ = check_task_goal_allowed("open notepad and type hello")
    assert allowed is True


def test_dispatch_action_token_blocks_destructive_task():
    from tests.test_task_loop import _minimal_engine

    engine = _minimal_engine()
    ran: list[str] = []
    engine.run_task = lambda goal, **kw: ran.append(goal)  # type: ignore[method-assign]

    engine._dispatch_action_token("[[TASK: delete all files in Downloads]]")

    assert not ran
    assert any(t == "task_status" for t, _ in engine._events)


def test_build_memory_prompt_respects_token_budget(monkeypatch):
    monkeypatch.setattr(
        "atlas_memory._MEMORY_PROMPT_TOKEN_BUDGET",
        80,
        raising=False,
    )
    mem = _temp_memory()
    ok, _msg, uid = mem.register("budgetuser")
    assert ok
    for i in range(200):
        mem.remember(
            uid,
            "general",
            f"fact_{i}",
            f"value number {i} with extra padding text",
            confidence=0.9 - (i * 0.0001),
            source="test",
        )
    prompt = mem.build_memory_prompt(uid)
    est_tokens = len(prompt) // 4
    assert est_tokens <= 120  # budget 80 + header slack


def test_set_pref_invalidates_memory_cache():
    mem = _temp_memory()
    ok, _msg, uid = mem.register("cacheuser")
    assert ok
    mem.remember(uid, "profile", "color", "blue", confidence=0.9, source="test")
    mem.build_memory_prompt(uid)
    assert mem._memory_cache is not None
    mem.set_pref(uid, "response_style", "Terse")
    assert mem._memory_cache is None


def test_create_or_login_concurrent_same_name():
    mem = _temp_memory()
    results: list[int] = []
    errors: list[Exception] = []

    def _login() -> None:
        try:
            uid = mem.create_or_login("ConcurrentUser")
            results.append(uid)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_login) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(set(results)) == 1


def test_app_lock_legacy_hash_migrates_on_unlock():
    mem = _temp_memory()
    acct = AccountManager(mem)
    ok, _msg, uid = mem.register("migrateuser")
    assert ok

    legacy_hash = hashlib.pbkdf2_hmac(
        "sha256", b"4321", b"atlas-lock", 120_000,
    ).hex()
    sec = acct._sec(uid, mem)
    sec["app_lock_hash"] = legacy_hash
    acct._save_sec(uid, mem, sec)

    assert acct.verify_app_lock_pin(uid, "4321") is True
    migrated = acct._sec(uid, mem)["app_lock_hash"]
    assert migrated.startswith("pbkdf2_sha256$")
    assert migrated != legacy_hash


def test_totp_pending_secret_not_in_sqlite():
    mem = _temp_memory()
    acct = AccountManager(mem)
    ok, _msg, uid = mem.register("totpuser")
    assert ok

    secret, _uri = acct.setup_totp_secret(uid)
    assert secret
    sec = acct._sec(uid, mem)
    assert "totp_pending_secret" not in sec
    assert uid in acct._totp_pending


def test_stream_bracket_filter_incomplete_token_on_flush():
    filt = StreamBracketFilter()
    filt.feed("Here is [[TASK: open notepad")
    _visible, tokens = filt.flush()
    assert not tokens
    assert filt.incomplete_action_token is not None
    assert "TASK" in filt.incomplete_action_token


def test_decide_next_step_json_retry(monkeypatch):
    from tests.test_task_loop import _minimal_engine

    engine = _minimal_engine()
    calls: list[str] = []

    def _fake_create(**kwargs):
        calls.append(kwargs["messages"][-1]["content"][-1]["text"])
        raw = "not json" if len(calls) == 1 else '{"action":"done","summary":"ok"}'
        msg = MagicMock()
        msg.content = raw
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    monkeypatch.setattr(
        "atlas_core.groq_client.chat.completions.create",
        _fake_create,
    )
    result = engine._decide_next_step("test task", [], "fakeb64")
    assert result is not None
    assert result.get("action") == "done"
    assert len(calls) == 2
    assert "valid JSON" in calls[1]


def test_persona_drift_skipped_for_terse_style():
    from atlas_learning import LearningEngine

    mem = _temp_memory()
    ok, _msg, uid = mem.register("terseuser")
    assert ok
    mem.set_pref(uid, "response_style", "Terse")
    eng = LearningEngine(mem, uid)

    with patch.object(eng, "_get_groq", return_value=MagicMock()):
        assert eng.check_persona_drift(["ok", "yes", "done"]) is None


def test_two_users_same_pin_different_hashes():
    mem = _temp_memory()
    acct = AccountManager(mem)
    ok, _msg, u1 = mem.register("user_a")
    assert ok
    ok, _msg, u2 = mem.register("user_b")
    assert ok
    acct.set_app_lock_pin(u1, "1234")
    acct.set_app_lock_pin(u2, "1234")
    h1 = acct._sec(u1, mem)["app_lock_hash"]
    h2 = acct._sec(u2, mem)["app_lock_hash"]
    assert h1 != h2
