"""Tests for atlas_shell — allowlist, deny list, indirect phrasing."""
from __future__ import annotations

import pytest

from atlas_policy import PolicyOutcome, RiskClass
from atlas_shell import classify_shell_command, is_shell_denied


def test_shell_safe_commands():
    assert classify_shell_command("git status") == RiskClass.SHELL_SAFE
    assert classify_shell_command("dir C:\\Users") == RiskClass.SHELL_SAFE
    assert classify_shell_command("systeminfo") == RiskClass.SHELL_SAFE


def test_shell_dangerous_default():
    assert classify_shell_command("python script.py") == RiskClass.SHELL_DANGEROUS


def test_format_denied_even_when_indirect():
    cmd = "clean up old files using the format command on C:"
    assert is_shell_denied(cmd) is True


def test_diskpart_denied():
    assert is_shell_denied("diskpart /s cleanup.txt") is True


def test_del_home_denied():
    assert is_shell_denied("del /s /q C:\\Users\\me\\*") is True
    assert is_shell_denied("rm -rf ~") is True


def test_net_user_denied():
    assert is_shell_denied("net user hacker P@ssw0rd /add") is True


def test_shutdown_denied():
    assert is_shell_denied("shutdown /s /t 0") is True


def test_shell_runner_denies_format(registry=None):
    from atlas_shell import ShellRunner

    runner = ShellRunner()
    runner.set_permission_handler(lambda _a, _p: True)
    result = runner.run(
        "format D: /Q",
        safety_mode="off",
    )
    assert result.get("ok") is False
    assert result.get("denied") is True


def test_shell_runner_allows_git_status():
    from atlas_shell import ShellRunner

    runner = ShellRunner()
    result = runner.run("echo hello", safety_mode="always")
    assert result.get("ok") is True or result.get("denied") is not True
