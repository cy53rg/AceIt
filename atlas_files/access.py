"""
atlas_files/access.py — Policy-gated find, open, and read for indexed paths.

Opening executables requires WRITE_SENSITIVE confirmation (policy ASK by default).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from atlas_fs_paths import deny_message, is_path_denied
from atlas_logging import get_logger
from atlas_policy import PolicyEngine, PolicyOutcome, RiskClass

log = get_logger("atlas_files.access")

_EXECUTE_EXTS = frozenset({
    ".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".scr", ".vbs", ".js", ".jar",
})


def is_executable_path(path: str | Path) -> bool:
    p = Path(path)
    return p.suffix.lower() in _EXECUTE_EXTS


def _policy_context(engine: Any | None) -> dict[str, Any]:
    if engine is None:
        return {
            "safety_mode": "off",
            "fs_access_active": False,
            "execution_blocked": False,
        }
    return {
        "safety_mode": str(getattr(engine, "safety_mode", "off")),
        "fs_access_active": bool(getattr(engine, "_fs_access_active", False)),
        "execution_blocked": bool(getattr(engine, "execution_blocked", False)),
    }


def _policy_engine(engine: Any | None) -> PolicyEngine:
    if engine is not None and getattr(engine, "memory", None) is not None:
        return PolicyEngine(engine.memory.db_path, user_id=getattr(engine, "user_id", 0))
    from atlas_data import atlas_db_path
    return PolicyEngine(atlas_db_path())


def _permission_handlers() -> tuple[Optional[Callable], Optional[Callable]]:
    try:
        from atlas_core import atlas_fs
        return atlas_fs._permission_callback, atlas_fs._typed_confirm_callback
    except Exception:
        return None, None


def _authorize_path(
    engine: Any | None,
    path: str,
    *,
    execute: bool = False,
) -> tuple[bool, str]:
    if is_path_denied(path):
        return False, deny_message(path)
    ctx = _policy_context(engine)
    if ctx["execution_blocked"]:
        return False, "Execution is blocked for this account."
    risk = RiskClass.WRITE_SENSITIVE if execute else RiskClass.READ_ONLY
    action = "fs.execute" if execute else "fs.read"
    auth = _policy_engine(engine).authorize(
        action,
        path,
        risk,
        **ctx,
    )
    if auth.decision == PolicyOutcome.DENY:
        return False, auth.reason or "Access denied by policy."
    perm_cb, typed_cb = _permission_handlers()
    if auth.decision == PolicyOutcome.CONFIRM_TYPED:
        if not typed_cb:
            return False, "Typed confirmation required but UI is unavailable."
        ok = typed_cb({
            "confirm_phrase": auth.confirm_phrase,
            "reason": auth.reason,
            "path": path,
            "audit_id": auth.audit_id,
        })
        return (True, "") if ok else (False, "Typed confirmation denied.")
    if auth.decision == PolicyOutcome.ASK:
        if not perm_cb:
            return False, "Confirmation required but UI is unavailable."
        approved = {"v": False}

        def _approve() -> None:
            approved["v"] = True

        perm_cb("execute" if execute else "read", path, _approve, lambda: None)
        return (True, "") if approved["v"] else (False, "User denied file access.")
    return True, ""


def find_file(
    indexer: Any,
    query: str,
    *,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    if indexer is None:
        return []
    return indexer.search(query, top_k=top_k)


def read_file_path(
    path: str,
    *,
    engine: Any | None = None,
    max_chars: int = 8000,
) -> tuple[bool, str]:
    raw = (path or "").strip()
    if not raw:
        return False, "Path is required."
    try:
        resolved = str(Path(raw).expanduser().resolve())
    except OSError as exc:
        return False, str(exc)
    if not Path(resolved).is_file():
        return False, f"Not a file: {resolved}"
    ok, reason = _authorize_path(engine, resolved, execute=False)
    if not ok:
        return False, reason
    try:
        from atlas_core import atlas_fs
        if hasattr(atlas_fs, "read_text"):
            try:
                text = atlas_fs.read_text(resolved, max_chars=max_chars)
                return True, text
            except PermissionError:
                pass
    except ImportError:
        pass
    try:
        text = Path(resolved).read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[… truncated at {max_chars:,} chars]"
        return True, text
    except OSError as exc:
        return False, str(exc)


def open_path(
    path: str = "",
    *,
    query: str = "",
    indexer: Any | None = None,
    engine: Any | None = None,
    reveal_only: bool = False,
) -> tuple[bool, str]:
    """
    Open a file with the default app, or reveal in Explorer/Finder.
    If ``query`` is set, search the index and open the best match.
    """
    target = (path or "").strip()
    if not target and query and indexer is not None:
        matches = find_file(indexer, query, top_k=1)
        if not matches:
            return False, f"I couldn't find a file matching \"{query}\". Try rebuilding the index in Settings."
        target = matches[0]["path"]

    if not target:
        return False, "I need a file path or search query."

    try:
        resolved = str(Path(target).expanduser().resolve())
    except OSError as exc:
        return False, str(exc)

    p = Path(resolved)
    if not p.exists():
        return False, f"Path not found: {resolved}"

    execute = p.is_file() and is_executable_path(p)
    ok, reason = _authorize_path(engine, resolved, execute=execute)
    if not ok:
        return False, reason

    try:
        if reveal_only or p.is_dir():
            _reveal_in_explorer(p)
            return True, f"Opened folder: {resolved}"
        if os.name == "nt":
            os.startfile(resolved)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", resolved])  # noqa: S603
        return True, f"Opened {p.name}"
    except OSError as exc:
        log.warning("open_path failed for %s: %s", resolved, exc)
        return False, str(exc)


def _reveal_in_explorer(path: Path) -> None:
    if os.name == "nt":
        if path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603
        else:
            os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])  # noqa: S603
