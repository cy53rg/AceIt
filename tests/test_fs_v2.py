"""Tests for atlas_fs_v2 — deny list, write scopes, policy integration."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from atlas_policy import PolicyContext, PolicyEngine, PolicyOutcome, RiskClass


@pytest.fixture
def fs_db(tmp_path, monkeypatch):
    db = tmp_path / "fs.sqlite3"
    monkeypatch.setenv("ATLAS_CONNECTOR_TEST_KEY", "test-key-for-fs")
    monkeypatch.setenv("ATLAS_CONNECTOR_SKIP_DPAPI", "1")
    return db


@pytest.fixture
def fs(fs_db):
    from atlas_fs_v2 import AtlasFileSystemV2

    return AtlasFileSystemV2(db_path=fs_db)


def test_read_ssh_private_key_denied(fs, monkeypatch):
    home = Path.home()
    ssh_key = home / ".ssh" / "id_rsa"
    if not ssh_key.parent.exists():
        pytest.skip("no ~/.ssh on this machine")
    with pytest.raises(PermissionError) as exc:
        fs.read_text(ssh_key)
    msg = str(exc.value).lower()
    assert "deny" in msg or "not permitted" in msg or "blocked" in msg


def test_read_env_file_denied(fs, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=1", encoding="utf-8")
    with pytest.raises(PermissionError):
        fs.read_text(env_file)


def test_read_pem_pattern_denied(fs, tmp_path):
    pem = tmp_path / "server.pem"
    pem.write_text("-----BEGIN PRIVATE KEY-----", encoding="utf-8")
    with pytest.raises(PermissionError):
        fs.read_text(pem)


def test_read_normal_file_in_home_allowed(fs, tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "_resolve", lambda p: Path(p).resolve())
    doc = tmp_path / "notes.txt"
    doc.write_text("hello", encoding="utf-8")
    assert fs.read_text(doc) == "hello"


def test_write_outside_scope_denied(fs, tmp_path):
    target = tmp_path / "out.txt"
    fs.set_write_scopes([str(tmp_path / "allowed")])
    with pytest.raises(PermissionError):
        fs.write_text(target, "nope")


def test_delete_in_scope_still_requires_policy_ask_or_typed(fs_db, tmp_path):
    from atlas_fs_v2 import AtlasFileSystemV2

    scope = tmp_path / "allowed"
    scope.mkdir()
    victim = scope / "old.txt"
    victim.write_text("x", encoding="utf-8")

    fs = AtlasFileSystemV2(db_path=fs_db)
    fs.set_write_scopes([str(scope)])
    fs.set_policy_context(safety_mode="trusted", fs_access_active=True)
    fs.register_typed_confirm_callback(lambda *_a, **_k: False)

    fs.delete_file(victim)
    assert victim.exists()


def test_delete_in_scope_with_typed_confirm(fs_db, tmp_path):
    from atlas_fs_v2 import AtlasFileSystemV2

    scope = tmp_path / "allowed"
    scope.mkdir()
    victim = scope / "old.txt"
    victim.write_text("x", encoding="utf-8")

    typed = {"v": False}

    def typed_cb(*_a, **_k):
        typed["v"] = True
        return True

    fs = AtlasFileSystemV2(db_path=fs_db)
    fs.set_write_scopes([str(scope)])
    fs.set_policy_context(safety_mode="trusted", fs_access_active=True)
    fs.register_typed_confirm_callback(typed_cb)

    fs.delete_file(victim)
    assert typed["v"]
    assert not victim.exists()


def test_policy_read_ssh_always_deny(fs_db):
    engine = PolicyEngine(db_path=fs_db)
    home = Path.home()
    result = engine.authorize(
        "fs.read",
        str(home / ".ssh" / "id_rsa"),
        RiskClass.READ_ONLY,
        safety_mode="off",
    )
    assert result.decision == PolicyOutcome.DENY


def test_write_scoped_denied_without_scope(fs_db, tmp_path):
    engine = PolicyEngine(db_path=fs_db, user_id=1)
    result = engine.authorize(
        "fs.write",
        str(tmp_path / "doc.txt"),
        RiskClass.WRITE_SCOPED,
        safety_mode="off",
        fs_access_active=True,
        write_scopes=(),
    )
    assert result.decision == PolicyOutcome.DENY
