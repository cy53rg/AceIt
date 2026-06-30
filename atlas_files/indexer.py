"""
atlas_files/indexer.py — Background file index for natural-language find/open.

Indexes user-configured roots (default: Desktop, Documents, Downloads).
Skips hardcoded deny paths and common huge directories.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from atlas_fs_paths import is_path_denied, normalize_path
from atlas_logging import get_logger

log = get_logger("atlas_files.indexer")

_SKIP_DIR_NAMES = frozenset({
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".cursor",
    "$recycle.bin",
    "system volume information",
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
})

_MAX_DEPTH = 12
_INDEX_STALE_S = 24 * 3600


def default_index_roots() -> list[str]:
    """Sensible first-run roots under the user profile."""
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "OneDrive",
        home / "Pictures",
    ]
    roots: list[str] = []
    for path in candidates:
        try:
            resolved = str(path.expanduser().resolve())
            if path.exists() and resolved not in roots:
                roots.append(resolved)
        except OSError:
            continue
    return roots


class FileIndexer:
    """SQLite-backed filename index with configurable roots."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._rebuild_thread: Optional[threading.Thread] = None
        self._rebuilding = False
        self._last_error = ""
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS file_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    ext TEXT NOT NULL DEFAULT '',
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL DEFAULT '',
                    root TEXT NOT NULL DEFAULT '',
                    indexed_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_file_index_name ON file_index(name);
                CREATE INDEX IF NOT EXISTS idx_file_index_ext ON file_index(ext);
                CREATE TABLE IF NOT EXISTS file_index_roots (
                    path TEXT PRIMARY KEY,
                    label TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS file_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def ensure_default_roots(self) -> None:
        if self.list_roots():
            return
        for root in default_index_roots():
            self.add_root(root, label=Path(root).name)

    def list_roots(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path, label, created FROM file_index_roots ORDER BY path"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_root(self, path: str, *, label: str = "") -> tuple[bool, str]:
        raw = (path or "").strip()
        if not raw:
            return False, "Path is required"
        try:
            resolved = str(Path(raw).expanduser().resolve())
        except OSError as exc:
            return False, str(exc)
        if is_path_denied(resolved):
            return False, "That path is on the security deny list."
        if not Path(resolved).exists():
            return False, f"Path does not exist: {resolved}"
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO file_index_roots (path, label, created) VALUES (?, ?, ?)",
                (resolved, label or Path(resolved).name, time.time()),
            )
        return True, f"Index root added: {resolved}"

    def remove_root(self, path: str) -> None:
        try:
            resolved = str(Path(path).expanduser().resolve())
        except OSError:
            resolved = (path or "").strip()
        with self._connect() as conn:
            conn.execute("DELETE FROM file_index_roots WHERE path = ?", (resolved,))
            conn.execute("DELETE FROM file_index WHERE root = ?", (resolved,))

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
            meta = {
                row[0]: row[1]
                for row in conn.execute("SELECT key, value FROM file_index_meta").fetchall()
            }
        return {
            "indexed_files": int(count),
            "roots": self.list_roots(),
            "rebuilding": self._rebuilding,
            "last_rebuild": meta.get("last_rebuild", ""),
            "last_error": self._last_error or meta.get("last_error", ""),
        }

    def start_background_rebuild_if_stale(self) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM file_index_meta WHERE key = 'last_rebuild'"
            ).fetchone()
        last = float(row[0]) if row else 0.0
        if time.time() - last > _INDEX_STALE_S:
            self.rebuild(async_run=True)

    def rebuild(self, *, async_run: bool = True) -> dict[str, Any]:
        if self._rebuilding:
            return {"ok": True, "status": "already_running"}
        if async_run:
            self._rebuild_thread = threading.Thread(
                target=self._rebuild_worker,
                daemon=True,
                name="atlas-file-index",
            )
            self._rebuild_thread.start()
            return {"ok": True, "status": "started"}
        self._rebuild_worker()
        return {"ok": True, "status": "completed", **self.status()}

    def _rebuild_worker(self) -> None:
        with self._lock:
            if self._rebuilding:
                return
            self._rebuilding = True
        self._last_error = ""
        indexed = 0
        try:
            roots = self.list_roots()
            if not roots:
                self.ensure_default_roots()
                roots = self.list_roots()
            with self._connect() as conn:
                conn.execute("DELETE FROM file_index")
            for root_info in roots:
                root = root_info["path"]
                indexed += self._index_root(root)
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO file_index_meta (key, value) VALUES (?, ?)",
                    ("last_rebuild", str(time.time())),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO file_index_meta (key, value) VALUES (?, ?)",
                    ("last_error", ""),
                )
            log.info("file index rebuild complete (%s files)", indexed)
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("file index rebuild failed: %s", exc)
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO file_index_meta (key, value) VALUES (?, ?)",
                    ("last_error", self._last_error),
                )
        finally:
            self._rebuilding = False

    def _should_skip_dir(self, path: Path) -> bool:
        name = path.name.lower()
        if name in _SKIP_DIR_NAMES:
            return True
        if is_path_denied(path):
            return True
        return False

    def _index_root(self, root: str) -> int:
        root_path = Path(root)
        if not root_path.exists():
            return 0
        count = 0
        batch: list[tuple] = []
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            current = Path(dirpath)
            depth = len(current.relative_to(root_path).parts) if current != root_path else 0
            if depth > _MAX_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [
                d for d in dirnames
                if not self._should_skip_dir(current / d)
            ]
            if self._should_skip_dir(current):
                continue
            for fname in filenames:
                fpath = current / fname
                if is_path_denied(fpath):
                    continue
                try:
                    stat = fpath.stat()
                except OSError:
                    continue
                if not fpath.is_file():
                    continue
                batch.append((
                    str(fpath.resolve()),
                    fpath.name,
                    fpath.suffix.lower(),
                    float(stat.st_mtime),
                    int(stat.st_size),
                    "",
                    root,
                    time.time(),
                ))
                if len(batch) >= 500:
                    self._insert_batch(batch)
                    count += len(batch)
                    batch.clear()
        if batch:
            self._insert_batch(batch)
            count += len(batch)
        return count

    def _insert_batch(self, rows: list[tuple]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO file_index
                (path, name, ext, mtime, size, content_hash, root, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def search(self, query: str, *, top_k: int = 10) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        terms = [t for t in re_split_terms(q) if t]
        if not terms:
            return []

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path, name, ext, mtime, size FROM file_index"
            ).fetchall()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            name = str(row["name"]).lower()
            path = str(row["path"])
            hay = f"{name} {path.lower()}"
            if not all(term in hay for term in terms):
                continue
            score = 0.0
            if q in name:
                score += 10.0
            for term in terms:
                if term in name:
                    score += 3.0
                if name.startswith(term):
                    score += 2.0
            score += min(float(row["mtime"]) / 1e10, 2.0)
            scored.append((score, {
                "path": path,
                "name": row["name"],
                "ext": row["ext"],
                "mtime": row["mtime"],
                "size": row["size"],
            }))
        scored.sort(key=lambda x: (-x[0], -x[1]["mtime"]))
        return [item for _, item in scored[: max(1, min(int(top_k), 25))]]


def re_split_terms(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())
