"""Tests for SSH target registry."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def ssh_db(tmp_path):
    return tmp_path / "ssh.sqlite3"


def test_ssh_unknown_target_rejected(ssh_db):
    from atlas_connectors.ssh_targets import SSHTargetManager
    from atlas_shell import ShellRunner

    mgr = SSHTargetManager(ssh_db, ShellRunner(db_path=ssh_db))
    result = mgr.run_command("prod", "uptime")
    assert not result.get("ok")
    assert "unknown" in result.get("error", "").lower()


def test_ssh_denied_command(ssh_db, tmp_path, monkeypatch):
    from atlas_connectors.ssh_targets import SSHTargetManager
    from atlas_shell import ShellRunner

    key = tmp_path / "fake_key"
    key.write_text("not-a-real-key", encoding="utf-8")
    mgr = SSHTargetManager(ssh_db, ShellRunner(db_path=ssh_db))
    ok, _ = mgr.add_target(
        name="lab",
        host="127.0.0.1",
        user="dev",
        key_path=str(key),
    )
    assert ok
    result = mgr.run_command("lab", "clean up old files using the format command")
    assert result.get("denied")
    assert "denied" in (result.get("reason") or "").lower()


def test_ssh_service_not_in_manageable_list(ssh_db, tmp_path):
    from atlas_connectors.ssh_targets import SSHTargetManager
    from atlas_shell import ShellRunner

    key = tmp_path / "fake_key"
    key.write_text("not-a-real-key", encoding="utf-8")
    mgr = SSHTargetManager(ssh_db, ShellRunner(db_path=ssh_db))
    mgr.add_target(name="web", host="10.0.0.1", user="ops", key_path=str(key))
    result = mgr.service_status("web", "nginx")
    assert result.get("denied")
    assert "manageable" in (result.get("reason") or "").lower()
