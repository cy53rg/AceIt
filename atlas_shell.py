"""
atlas_shell.py — Policy-gated subprocess execution.

Maintains explicit SHELL_SAFE allowlist and hardcoded DENY patterns.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Callable, Optional

from atlas_data import DEFAULT_SAFETY_MODE
from atlas_fs_paths import normalize_path
from atlas_logging import get_logger
from atlas_policy import PolicyEngine, PolicyOutcome, RiskClass

log = get_logger("shell")

PermissionHandler = Callable[[str, str], bool]
TypedConfirmHandler = Callable[[dict], bool]

_SHELL_SAFE_VERBS: frozenset[str] = frozenset({
    "dir", "type", "more", "where", "echo", "cd", "pwd",
    "ls", "cat", "head", "tail", "systeminfo", "tasklist", "ipconfig",
    "hostname", "whoami", "ver", "git",
})

_SHELL_SAFE_GIT: frozenset[str] = frozenset({
    "status", "log", "diff", "show", "branch", "remote",
})

# Hardcoded deny — regardless of Safety Mode or model phrasing.
_DENY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bformat\b", re.I),
    re.compile(r"\bdiskpart\b", re.I),
    re.compile(r"\breg\s+(add|delete|import|export)\b", re.I),
    re.compile(r"\bnet\s+(user|localgroup)\b", re.I),
    re.compile(r"\bshutdown\b", re.I),
    re.compile(r"\brestart-computer\b", re.I),
    re.compile(r"\bwmic\s+.*delete\b", re.I),
    re.compile(r"\b(rm|rmdir|del|erase)\b[^\n]*(\~|/s\s+/q|%userprofile%|\\\\users\\\\)", re.I),
    re.compile(r"\b(rm\s+-rf|rm\s+-r)\s+(/|~|\$home)", re.I),
)


def _tokens(command: str) -> list[str]:
    cmd = (command or "").strip()
    if not cmd:
        return []
    try:
        return shlex.split(cmd, posix=os.name != "nt")
    except ValueError:
        return cmd.split()


def _executable(command: str) -> str:
    tokens = _tokens(command)
    if not tokens:
        return ""
    exe = tokens[0].strip("\"'").rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
    return re.sub(r"\.(exe|cmd|bat|ps1)$", "", exe)


def is_shell_denied(command: str) -> bool:
    """Structural deny check — scans full command text for blocked verbs/patterns."""
    text = command or ""
    for pat in _DENY_PATTERNS:
        if pat.search(text):
            return True
    tokens = _tokens(text)
    if not tokens:
        return False
    exe = _executable(text)
    home = normalize_path(Path.home())
    joined = " ".join(tokens).lower()
    if exe in ("del", "erase", "rm", "rmdir"):
        if home in joined or " c:\\ " in f" {joined} " or joined.strip() in ("rm -rf /", "rm -rf ~"):
            return True
    return False


def classify_shell_command(command: str) -> RiskClass:
    if is_shell_denied(command):
        return RiskClass.SHELL_DANGEROUS
    tokens = _tokens(command)
    if not tokens:
        return RiskClass.SHELL_DANGEROUS
    exe = _executable(command)
    if exe == "git" and len(tokens) > 1 and tokens[1].lower() in _SHELL_SAFE_GIT:
        return RiskClass.SHELL_SAFE
    if exe in _SHELL_SAFE_VERBS and exe != "git":
        return RiskClass.SHELL_SAFE
    return RiskClass.SHELL_DANGEROUS


class ShellRunner:
    """Execute shell commands only after PolicyEngine authorization."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            try:
                from atlas_data import atlas_db_path
                db_path = atlas_db_path()
            except ImportError:
                db_path = Path("atlas_memory.sqlite3")
        self._db_path = Path(db_path)
        self._policy = PolicyEngine(self._db_path)
        self._permission_handler: Optional[PermissionHandler] = None
        self._typed_confirm_handler: Optional[TypedConfirmHandler] = None
        self._safety_mode = DEFAULT_SAFETY_MODE
        self._fs_access_active = False
        self._execution_blocked = False
        self._write_scopes: tuple[str, ...] = ()

    def set_permission_handler(self, handler: PermissionHandler) -> None:
        self._permission_handler = handler

    def set_typed_confirm_handler(self, handler: TypedConfirmHandler) -> None:
        self._typed_confirm_handler = handler

    def set_policy_context(self, **kwargs) -> None:
        if "safety_mode" in kwargs:
            self._safety_mode = str(kwargs["safety_mode"])
        if "fs_access_active" in kwargs:
            self._fs_access_active = bool(kwargs["fs_access_active"])
        if "execution_blocked" in kwargs:
            self._execution_blocked = bool(kwargs["execution_blocked"])
        if "write_scopes" in kwargs:
            self._write_scopes = tuple(kwargs["write_scopes"])

    def run(
        self,
        command: str,
        *,
        safety_mode: str | None = None,
        timeout: int = 30,
        cwd: str | None = None,
    ) -> dict:
        from atlas_policy import PolicyContext

        if is_shell_denied(command):
            return {
                "ok": False,
                "denied": True,
                "reason": "Command matches Atlas shell deny list (format/diskpart/account/shutdown/home wipe).",
            }

        risk = classify_shell_command(command)
        if risk == RiskClass.SHELL_SAFE:
            risk = RiskClass.SHELL_SAFE
        ctx = PolicyContext(
            safety_mode=safety_mode or self._safety_mode,
            fs_access_active=self._fs_access_active,
            execution_blocked=self._execution_blocked,
            write_scopes=self._write_scopes,
        )
        auth = self._policy.authorize(
            "shell.exec",
            command,
            risk,
            context=ctx,
        )
        if auth.decision == PolicyOutcome.DENY:
            return {"ok": False, "denied": True, "reason": auth.reason}
        if auth.decision == PolicyOutcome.CONFIRM_TYPED:
            if not self._typed_confirm_handler or not self._typed_confirm_handler({
                "confirm_phrase": auth.confirm_phrase,
                "reason": auth.reason,
                "path": f"shell://{command[:120]}",
                "audit_id": auth.audit_id,
            }):
                return {"ok": False, "denied": True, "decision": auth.decision.value}
        elif auth.decision == PolicyOutcome.ASK:
            if not self._permission_handler or not self._permission_handler(
                "shell.exec", f"shell://{command[:120]}"
            ):
                return {"ok": False, "denied": True, "decision": auth.decision.value}

        try:
            if os.name == "nt":
                proc = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=cwd,
                )
            else:
                proc = subprocess.run(
                    shlex.split(command),
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=cwd,
                )
            return {
                "ok": True,
                "returncode": proc.returncode,
                "stdout": proc.stdout[:8000],
                "stderr": proc.stderr[:4000],
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Command timed out after {timeout}s"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


shell_runner = ShellRunner()
