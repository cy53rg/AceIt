"""
atlas_task_safety.py — Task goal allowlist / deny patterns (defense in depth).

System-prompt guardrails alone are insufficient: injected screen/OCR/clipboard
content can coerce [[TASK:…]] tokens with destructive goals.
"""
from __future__ import annotations

import re
from pathlib import Path

from atlas_logging import get_logger

log = get_logger("task_safety")

# Destructive verbs / patterns — reject or force confirmation path.
_TASK_GOAL_DENY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(delete|remove|wipe|erase|uninstall|format)\b", re.I),
    re.compile(r"\b(rm\s+-rf|rm\s+-r|del\s+/|rmdir\s+/s)\b", re.I),
    re.compile(r"\b(reg\s+(add|delete|import|export)|diskpart|shutdown)\b", re.I),
    re.compile(r"\b(all files|entire drive|whole disk|system32)\b", re.I),
    re.compile(r"[\"']?C:\\\\(Windows|Program Files)", re.I),
    re.compile(r"\b(downloads|documents|desktop)\b.*\b(all|every|entire)\b", re.I),
)

# Trusted-mode allowlist: benign task shapes only (no destructive verbs).
_TASK_TRUSTED_ALLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(open|launch|start|play|find|search|navigate|go to|click|show|read|check|"
        r"type|export|save|copy|paste|fill|submit|send|email|create|add|update|deploy)\b",
        re.I,
    ),
)


def check_task_goal_allowed(
    goal: str,
    *,
    trusted_only: bool = False,
) -> tuple[bool, str]:
    """
    Return (allowed, reason). Denied goals must not reach run_task() silently.
    """
    text = (goal or "").strip()
    if not text:
        return False, "Empty task goal."
    if len(text) > 2000:
        return False, "Task goal too long."

    for pat in _TASK_GOAL_DENY_PATTERNS:
        if pat.search(text):
            reason = f"Task goal matches blocked pattern: {pat.pattern[:60]}"
            log.warning("task goal rejected: %r — %s", text[:120], reason)
            return False, reason

    if trusted_only:
        if not any(p.search(text) for p in _TASK_TRUSTED_ALLOW_PATTERNS):
            reason = "Task goal not on trusted allowlist — confirmation required."
            log.warning("task goal not trusted-allowlisted: %r", text[:120])
            return False, reason

    return True, ""
