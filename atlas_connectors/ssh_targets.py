"""SSH target registry — explicit hosts only, key-based auth, shared shell policy."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from atlas_logging import get_logger
from atlas_policy import RiskClass
from atlas_shell import ShellRunner, is_shell_denied

log = get_logger("connectors.ssh")

# Per-action risk map entries (review like firewall rules).
SSH_ACTION_RISKS = {
    "ssh.run_command": RiskClass.SHELL_DANGEROUS,
    "ssh.service_status": RiskClass.SHELL_SAFE,
}


class SSHTargetStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ssh_targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    host TEXT NOT NULL,
                    user TEXT NOT NULL,
                    key_path TEXT NOT NULL,
                    manageable_services_json TEXT NOT NULL DEFAULT '[]',
                    created REAL NOT NULL
                )
                """
            )

    def list_targets(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, host, user, key_path, manageable_services_json FROM ssh_targets ORDER BY name"
            ).fetchall()
        return [
            {
                "name": r[0],
                "host": r[1],
                "user": r[2],
                "key_path": r[3],
                "manageable_services": json.loads(r[4] or "[]"),
            }
            for r in rows
        ]

    def add_target(
        self,
        *,
        name: str,
        host: str,
        user: str,
        key_path: str,
        manageable_services: list[str] | None = None,
    ) -> tuple[bool, str]:
        kp = str(Path(key_path).expanduser().resolve())
        if not Path(kp).is_file():
            return False, "Key file not found."
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ssh_targets (name, host, user, key_path, manageable_services_json, created)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, host, user, kp, json.dumps(manageable_services or []), time.time()),
            )
        return True, f"Registered SSH target '{name}'."

    def get(self, name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, host, user, key_path, manageable_services_json FROM ssh_targets WHERE name = ?",
                (name,),
            ).fetchone()
        if not row:
            return None
        return {
            "name": row[0],
            "host": row[1],
            "user": row[2],
            "key_path": row[3],
            "manageable_services": json.loads(row[4] or "[]"),
        }

    def remove(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ssh_targets WHERE name = ?", (name,))


class SSHTargetManager:
    """Connector-like SSH host runner — hosts must be registered explicitly."""

    display_name = "SSH Targets"

    def __init__(self, db_path: Path | str, shell: ShellRunner) -> None:
        self._store = SSHTargetStore(db_path)
        self._shell = shell

    def list_targets(self) -> list[dict[str, Any]]:
        return self._store.list_targets()

    def add_target(self, **kwargs: Any) -> tuple[bool, str]:
        return self._store.add_target(**kwargs)

    def remove_target(self, name: str) -> None:
        self._store.remove(name)

    def scope_boundary_text(self) -> str:
        return (
            "Atlas runs allowlisted commands only on SSH hosts you register with a key file. "
            "No password storage. Shell deny rules apply to remote commands too."
        )

    def run_command(self, target: str, command: str) -> dict:
        info = self._store.get(target)
        if not info:
            return {"ok": False, "error": f"Unknown SSH target: {target}"}
        if is_shell_denied(command):
            return {"ok": False, "denied": True, "reason": "Command denied by Atlas shell policy."}
        remote = (
            f'ssh -i "{info["key_path"]}" -o BatchMode=yes '
            f'{info["user"]}@{info["host"]} {command!r}'
        )
        return self._shell.run(remote)

    def service_status(self, target: str, service: str) -> dict:
        info = self._store.get(target)
        if not info:
            return {"ok": False, "error": f"Unknown SSH target: {target}"}
        allowed = info.get("manageable_services") or []
        if service not in allowed:
            return {
                "ok": False,
                "denied": True,
                "reason": f"Service '{service}' not in manageable list for {target}.",
            }
        cmd = f"systemctl status {service}"
        remote = (
            f'ssh -i "{info["key_path"]}" -o BatchMode=yes '
            f'{info["user"]}@{info["host"]} {cmd!r}'
        )
        return self._shell.run(remote)
