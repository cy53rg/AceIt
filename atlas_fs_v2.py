"""
atlas_fs_v2.py — Controlled filesystem access (read user tree, scoped writes).

Read: browse under the user home directory except hardcoded deny paths.
Write/delete: default DENY; user grants per-directory write scopes in Settings.
Every mutation goes through PolicyEngine before touching disk.
"""
from __future__ import annotations

import platform
import sqlite3
import subprocess
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from atlas_fs_paths import (
    deny_message,
    is_path_denied,
    is_under_user_home,
    path_in_write_scope,
)
from atlas_logging import get_logger

log = get_logger("fs_v2")


class FSPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DELETE = "delete"


class WriteScopeStore:
    """Persist user-granted write directories in SQLite."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fs_write_scopes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL
                )
                """
            )

    def list_scopes(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path, label, created FROM fs_write_scopes ORDER BY path"
            ).fetchall()
        return [{"path": r[0], "label": r[1], "created": r[2]} for r in rows]

    def add_scope(self, path: str, *, label: str = "") -> tuple[bool, str]:
        resolved = str(Path(path).expanduser().resolve())
        if is_path_denied(resolved):
            return False, "That path is on the security deny list and cannot be granted."
        if not is_under_user_home(resolved):
            return False, "Write scopes must be inside your user profile directory."
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fs_write_scopes (path, label, created) VALUES (?, ?, ?)",
                (resolved, label or Path(resolved).name, time.time()),
            )
        return True, f"Write scope added: {resolved}"

    def remove_scope(self, path: str) -> None:
        resolved = str(Path(path).expanduser().resolve())
        with self._connect() as conn:
            conn.execute("DELETE FROM fs_write_scopes WHERE path = ?", (resolved,))

    def scope_paths(self) -> tuple[str, ...]:
        return tuple(s["path"] for s in self.list_scopes())


class AtlasFileSystemV2:
    """
    v2 filesystem gate — read-mostly user tree, scoped writes, policy on every op.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        permission_callback: Optional[Callable] = None,
    ) -> None:
        if db_path is None:
            try:
                from atlas_data import atlas_db_path
                db_path = atlas_db_path()
            except ImportError:
                db_path = Path("atlas_memory.sqlite3")
        self._db_path = Path(db_path)
        self._scopes = WriteScopeStore(self._db_path)
        self._permission_callback = permission_callback
        self._typed_confirm_callback: Optional[Callable] = None
        self._fs_access_active = False
        self._safety_mode = "always"
        self._execution_blocked = False

    def register_permission_callback(self, callback: Callable) -> None:
        self._permission_callback = callback

    def register_typed_confirm_callback(self, callback: Callable) -> None:
        self._typed_confirm_callback = callback

    def set_write_scopes(self, paths: list[str]) -> None:
        for p in paths:
            self._scopes.add_scope(p)

    def list_write_scopes(self) -> list[dict]:
        return self._scopes.list_scopes()

    def add_write_scope(self, path: str, *, label: str = "") -> tuple[bool, str]:
        return self._scopes.add_scope(path, label=label)

    def remove_write_scope(self, path: str) -> None:
        self._scopes.remove_scope(path)

    def set_policy_context(
        self,
        *,
        safety_mode: str | None = None,
        fs_access_active: bool | None = None,
        execution_blocked: bool | None = None,
    ) -> None:
        if safety_mode is not None:
            self._safety_mode = safety_mode
        if fs_access_active is not None:
            self._fs_access_active = fs_access_active
        if execution_blocked is not None:
            self._execution_blocked = execution_blocked

    def _resolve(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not is_under_user_home(resolved):
            raise PermissionError(
                f"Atlas can only access files under your user profile. "
                f"'{resolved}' is outside that tree."
            )
        return resolved

    def _policy_context(self):
        from atlas_policy import PolicyContext

        return PolicyContext(
            safety_mode=self._safety_mode,
            fs_access_active=self._fs_access_active,
            execution_blocked=self._execution_blocked,
            write_scopes=self._scopes.scope_paths(),
        )

    def _authorize_read(self, path: Path) -> None:
        if is_path_denied(path):
            raise PermissionError(deny_message(path))
        from atlas_policy import PolicyEngine, PolicyOutcome, RiskClass

        engine = PolicyEngine(self._db_path)
        result = engine.authorize(
            "fs.read",
            str(path),
            RiskClass.READ_ONLY,
            context=self._policy_context(),
        )
        if result.decision == PolicyOutcome.DENY:
            raise PermissionError(result.reason or deny_message(path))

    def _request_permission(
        self,
        action: FSPermission,
        path: Path,
        proceed: Callable,
    ) -> None:
        from atlas_policy import PolicyOutcome, PolicyEngine, RiskClass, build_fs_request, evaluate_action, PolicyDecision

        path_str = str(path)
        if path_str.startswith(("atlas-hands://", "atlas-task://", "atlas-routine://")):
            req = build_fs_request(action.value, path_str)
            result = evaluate_action(req, self._policy_context())

            def _run_proceed() -> None:
                try:
                    proceed()
                except Exception as exc:
                    log.error("fs approved '%s' on '%s' failed: %s", action.value, path, exc)

            if result.decision == PolicyDecision.DENY:
                return
            if result.decision == PolicyDecision.ALLOW:
                _run_proceed()
                return
            if result.decision == PolicyDecision.CONFIRM_TYPED:
                if self._typed_confirm_callback and self._typed_confirm_callback(result, path=path_str):
                    _run_proceed()
                return
            if self._permission_callback:
                self._permission_callback(action.value, path_str, _run_proceed, lambda: None)
            return

        if is_path_denied(path):
            raise PermissionError(deny_message(path))

        if action == FSPermission.DELETE:
            risk = RiskClass.IRREVERSIBLE
        elif action in (FSPermission.WRITE, FSPermission.EXECUTE):
            if not path_in_write_scope(path, self._scopes.scope_paths()):
                raise PermissionError(
                    f"Write access denied — path is outside your granted write scopes "
                    f"(Settings → Filesystem): {path}"
                )
            risk = RiskClass.WRITE_SCOPED if action == FSPermission.WRITE else RiskClass.SHELL_DANGEROUS
        else:
            risk = RiskClass.READ_ONLY

        engine = PolicyEngine(self._db_path)
        auth = engine.authorize(
            f"fs.{action.value}",
            str(path),
            risk,
            context=self._policy_context(),
        )
        result_decision = auth.decision

        def _run_proceed() -> None:
            try:
                proceed()
            except Exception as exc:
                log.error("fs approved '%s' on '%s' failed: %s", action.value, path, exc)

        if result_decision == PolicyOutcome.DENY:
            raise PermissionError(auth.reason or f"Policy denied {action.value} on {path}")

        if result_decision == PolicyOutcome.ALLOW:
            _run_proceed()
            return

        if result_decision == PolicyOutcome.CONFIRM_TYPED:
            if not self._typed_confirm_callback:
                log.warning("typed confirmation required but no handler for %s", path)
                return
            legacy = type("R", (), {
                "confirm_phrase": auth.confirm_phrase,
                "reason": auth.reason,
                "audit_id": auth.audit_id,
            })()
            if not self._typed_confirm_callback(legacy, path=str(path)):
                return
            _run_proceed()
            return

        if not self._permission_callback:
            log.warning("confirmation required but no permission callback for %s", path)
            return

        def _deny() -> None:
            log.info("fs %s on %s denied by user", action.value, path)

        self._permission_callback(action.value, str(path), _run_proceed, _deny)

    # ── READ ──────────────────────────────────────────────────────────────────

    def list_directory(self, path: str | Path = ".") -> list[dict]:
        target = self._resolve(path)
        self._authorize_read(target)
        if not target.exists():
            raise FileNotFoundError(f"Directory not found: {target}")
        if not target.is_dir():
            raise NotADirectoryError(f"Not a directory: {target}")
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if is_path_denied(item):
                entries.append({
                    "name": item.name,
                    "type": "denied",
                    "size": None,
                })
                continue
            entries.append({
                "name": item.name,
                "type": "file" if item.is_file() else "dir",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return entries

    def read_text(
        self,
        path: str | Path,
        encoding: str = "utf-8",
        max_chars: int = 50_000,
    ) -> str:
        target = self._resolve(path)
        self._authorize_read(target)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {target}")
        if not target.is_file():
            raise IsADirectoryError(f"Path is a directory: {target}")
        text = target.read_text(encoding=encoding, errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[… truncated at {max_chars:,} chars]"
        return text

    def file_info(self, path: str | Path) -> dict:
        target = self._resolve(path)
        self._authorize_read(target)
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {target}")
        stat = target.stat()
        return {
            "path": str(target),
            "name": target.name,
            "type": "file" if target.is_file() else "dir",
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "suffix": target.suffix,
            "denied": is_path_denied(target),
        }

    # ── WRITE / DELETE ────────────────────────────────────────────────────────

    def write_text(self, path: str | Path, content: str, encoding: str = "utf-8") -> None:
        target = self._resolve(path)

        def _do() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding=encoding)
            log.info("wrote %d chars → %s", len(content), target)

        self._request_permission(FSPermission.WRITE, target, _do)

    def append_text(self, path: str | Path, content: str, encoding: str = "utf-8") -> None:
        target = self._resolve(path)

        def _do() -> None:
            with open(target, "a", encoding=encoding) as fh:
                fh.write(content)

        self._request_permission(FSPermission.WRITE, target, _do)

    def create_directory(self, path: str | Path) -> None:
        target = self._resolve(path)

        def _do() -> None:
            target.mkdir(parents=True, exist_ok=True)

        self._request_permission(FSPermission.WRITE, target, _do)

    def move_file(self, src: str | Path, dst: str | Path) -> None:
        src_path = self._resolve(src)
        dst_path = self._resolve(dst)

        def _do() -> None:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            src_path.rename(dst_path)

        self._request_permission(FSPermission.WRITE, src_path, _do)

    def delete_file(self, path: str | Path) -> None:
        target = self._resolve(path)
        if not path_in_write_scope(target, self._scopes.scope_paths()):
            raise PermissionError(
                f"Delete denied — path is outside your granted write scopes "
                f"(Settings → Filesystem): {target}"
            )

        def _do() -> None:
            target.unlink(missing_ok=True)
            log.info("deleted %s", target)

        self._request_permission(FSPermission.DELETE, target, _do)

    def run_script(self, path: str | Path, args: Optional[list[str]] = None) -> None:
        target = self._resolve(path)
        cmd = [str(target)] + (args or [])

        def _do() -> None:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30, shell=False)

        self._request_permission(FSPermission.EXECUTE, target, _do)
