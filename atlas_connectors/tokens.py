"""Encrypted connector token storage (Fernet + Windows DPAPI).

All SQL in this module uses ``?`` placeholders — never interpolate connector IDs
or other user-controlled values into query strings.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from atlas_logging import get_logger

log = get_logger("connectors.tokens")

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore


def _use_test_key() -> bool:
    return bool((os.environ.get("ATLAS_CONNECTOR_TEST_KEY") or "").strip())


def _dpapi_protect(data: bytes) -> bytes:
    if os.environ.get("ATLAS_CONNECTOR_SKIP_DPAPI", "").strip().lower() in ("1", "true", "yes"):
        return data
    try:
        import win32crypt  # type: ignore

        return win32crypt.CryptProtectData(
            data, "Atlas connector key", None, None, None, 0,
        )
    except Exception as exc:
        log.warning("DPAPI protect unavailable (%s); using raw key file (dev only)", exc)
        return data


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.environ.get("ATLAS_CONNECTOR_SKIP_DPAPI", "").strip().lower() in ("1", "true", "yes"):
        return data
    try:
        import win32crypt  # type: ignore

        return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]
    except Exception as exc:
        log.warning("DPAPI unprotect unavailable (%s)", exc)
        return data


class TokenStore:
    """Persist OAuth/API tokens encrypted — never plaintext in SQLite."""

    _KEY_FILE = "connector_fernet.key"

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._fernet = self._load_fernet()
        self._init_db()

    def _key_path(self) -> Path:
        return self.db_path.parent / self._KEY_FILE

    def _load_fernet(self) -> "Fernet":
        if Fernet is None:
            raise RuntimeError("cryptography package required for connector tokens")
        if _use_test_key():
            import base64
            import hashlib

            digest = hashlib.sha256(os.environ["ATLAS_CONNECTOR_TEST_KEY"].encode()).digest()
            key = base64.urlsafe_b64encode(digest)
            return Fernet(key)

        kp = self._key_path()
        if kp.is_file():
            protected = kp.read_bytes()
            key = _dpapi_unprotect(protected)
        else:
            key = Fernet.generate_key()
            kp.parent.mkdir(parents=True, exist_ok=True)
            kp.write_bytes(_dpapi_protect(key))
        return Fernet(key)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS connector_tokens (
                    connector_id TEXT PRIMARY KEY,
                    token_blob BLOB NOT NULL,
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    granted_at REAL NOT NULL,
                    updated REAL NOT NULL
                )
                """
            )

    def save_token(
        self,
        connector_id: str,
        token_data: dict[str, Any],
        *,
        scopes: list[str] | None = None,
    ) -> None:
        payload = json.dumps(token_data).encode("utf-8")
        blob = self._fernet.encrypt(payload)
        now = time.time()
        scopes_json = json.dumps(scopes or token_data.get("scopes") or [])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO connector_tokens (connector_id, token_blob, scopes_json, granted_at, updated)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(connector_id) DO UPDATE SET
                    token_blob = excluded.token_blob,
                    scopes_json = excluded.scopes_json,
                    updated = excluded.updated
                """,
                (connector_id, blob, scopes_json, now, now),
            )

    def load_token(self, connector_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT token_blob, scopes_json FROM connector_tokens WHERE connector_id = ?",
                (connector_id,),
            ).fetchone()
        if not row:
            return None
        try:
            raw = self._fernet.decrypt(row[0])
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict):
                scopes = json.loads(row[1] or "[]")
                data.setdefault("scopes", scopes)
            return data
        except (InvalidToken, json.JSONDecodeError) as exc:
            log.error("failed to decrypt token for %s: %s", connector_id, exc)
            return None

    def delete_token(self, connector_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM connector_tokens WHERE connector_id = ?",
                (connector_id,),
            )

    def list_connected(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT connector_id FROM connector_tokens ORDER BY connector_id"
            ).fetchall()
        return [str(r[0]) for r in rows]

    def get_scopes(self, connector_id: str) -> list[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT scopes_json FROM connector_tokens WHERE connector_id = ?",
                (connector_id,),
            ).fetchone()
        if not row:
            return []
        try:
            return list(json.loads(row[0] or "[]"))
        except json.JSONDecodeError:
            return []
