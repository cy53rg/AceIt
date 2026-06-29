"""
atlas_data.py — Shared data paths and daemon configuration (no Qt).
"""
from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_PORT = 17847


def atlas_data_dir() -> Path:
    """Per-user Atlas data directory (SQLite, logs, daemon token)."""
    override = (os.environ.get("ATLAS_DATA_DIR") or "").strip()
    if override:
        p = Path(override).expanduser()
    else:
        local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or "."
        p = Path(local) / "Atlas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def atlas_db_path() -> Path:
    override = (os.environ.get("ATLAS_DB_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return atlas_data_dir() / "atlas_memory.sqlite3"


def daemon_host() -> str:
    return (os.environ.get("ATLAS_DAEMON_HOST") or "127.0.0.1").strip() or "127.0.0.1"


def daemon_port() -> int:
    raw = (os.environ.get("ATLAS_DAEMON_PORT") or str(_DEFAULT_PORT)).strip()
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_PORT


def daemon_base_url() -> str:
    return f"http://{daemon_host()}:{daemon_port()}"
