"""App lock PIN hashing — per-user salt (matches UserMemory._hash_password)."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from atlas_accounts import AccountManager
from atlas_memory import UserMemory


def _temp_memory() -> UserMemory:
    path = Path(tempfile.mkdtemp()) / "test_accounts_lock.sqlite3"
    return UserMemory(path)


def test_app_lock_pin_uses_per_user_salt():
    mem = _temp_memory()
    acct = AccountManager(mem)
    ok, _msg, uid = mem.register("locktest")
    assert ok

    ok, _ = acct.set_app_lock_pin(uid, "1234")
    assert ok

    sec = acct._sec(uid, mem)
    stored = sec["app_lock_hash"]
    assert stored.startswith("pbkdf2_sha256$")
    parts = stored.split("$", 3)
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert len(parts[2]) == 32  # 16-byte salt as hex

    assert acct.verify_app_lock_pin(uid, "1234") is True
    assert acct.verify_app_lock_pin(uid, "9999") is False


def test_app_lock_pin_legacy_constant_salt_still_verifies():
    mem = _temp_memory()
    acct = AccountManager(mem)
    ok, _msg, uid = mem.register("legacylock")
    assert ok

    legacy_hash = hashlib.pbkdf2_hmac(
        "sha256", b"5678", b"atlas-lock", 120_000,
    ).hex()
    sec = acct._sec(uid, mem)
    sec["app_lock_hash"] = legacy_hash
    acct._save_sec(uid, mem, sec)

    assert acct.verify_app_lock_pin(uid, "5678") is True
    assert acct.verify_app_lock_pin(uid, "1234") is False
    migrated = acct._sec(uid, mem)["app_lock_hash"]
    assert migrated.startswith("pbkdf2_sha256$")


def test_change_password_rejects_cloud_only_account():
    mem = _temp_memory()
    acct = AccountManager(mem)
    ok, _msg, uid = mem.register("clouduser", email="cloud@example.com")
    assert ok
    mem.set_pref(uid, "cloud_id", "supabase-user-uuid")

    ok, msg = acct.change_password_local(uid, "any-old-password", "new-password")

    assert ok is False
    assert "cloud sign-in" in msg.lower()


def test_change_password_updates_local_account():
    mem = _temp_memory()
    acct = AccountManager(mem)
    ok, _msg, uid = mem.register("localuser", password="old-secret")
    assert ok

    ok, msg = acct.change_password_local(uid, "old-secret", "new-secret")

    assert ok is True
    auth_ok, _, _ = mem.authenticate("localuser", "new-secret")
    assert auth_ok is True
