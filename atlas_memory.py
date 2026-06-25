"""
atlas_memory.py — Atlas persistent user memory (SQLite).

No Groq client at import/init time; API calls use lazy initialization only.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("atlas_memory")

_default_fast_model = "llama-3.1-8b-instant"
ATLAS_FAST_MODEL = (
    os.environ.get("ATLAS_FAST_MODEL") or _default_fast_model
).strip() or _default_fast_model

_MEMORY_CACHE_TTL_S = 30.0


# ── Optional Qt signal bridge ─────────────────────────────────────────────────
# Memory cognition (Groq summarisation / fact extraction) and SQLite writes must
# never block the PySide6 event loop, so they run on background threads.  When Qt
# is available we expose Signals so the worker results can be marshalled back to
# the UI thread safely.  Without PySide6 the module still works via plain
# callbacks, keeping atlas_memory importable in headless contexts.
try:
    from PySide6.QtCore import QObject, Signal

    class MemorySignals(QObject):
        """Thread-safe channel for delivering memory-worker results to the UI."""

        facts_extracted = Signal(int, object)   # (user_id, list[dict])
        summary_saved   = Signal(int, str)      # (user_id, summary_text)
        error           = Signal(str)           # (message)

    _HAS_QT = True
except Exception:  # pragma: no cover - headless / no PySide6
    QObject = object  # type: ignore
    Signal = None     # type: ignore
    MemorySignals = None  # type: ignore
    _HAS_QT = False


class UserMemory:
    """
    SQLite persistence for users, facts, session summaries, and skill outcomes.

    Threading model
    ----------------
    All Groq network calls (summary generation, fact extraction) and the SQLite
    writes they trigger run on daemon worker threads via the ``*_async`` helpers,
    so the PySide6 UI event loop is never blocked.  Results are marshalled back
    through ``self.signals`` (Qt Signals) when PySide6 is present, or via an
    optional ``on_done`` callback otherwise.

    SQLite is configured for Write-Ahead Logging (WAL); each call opens its own
    short-lived connection (safe across threads), a ``busy_timeout`` absorbs
    concurrent-writer contention, and a process-level write lock serialises
    mutations to eliminate "database is locked" races between workers.
    """

    def __init__(self, db_path: str | Path = "atlas_memory.sqlite3") -> None:
        self.db_path = Path(db_path)
        self._groq_client: Any = None
        self._memory_cache: Optional[tuple[float, str]] = None
        self._cache_lock = threading.Lock()
        # Serialises writes across worker threads; readers stay concurrent (WAL).
        self._write_lock = threading.Lock()
        self.signals = MemorySignals() if _HAS_QT else None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # timeout + busy_timeout let concurrent writers wait instead of erroring.
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    email TEXT,
                    created REAL NOT NULL,
                    last_seen REAL NOT NULL
                )
                """
            )
            # Per-user free-form notes/instructions that apply to ALL features
            # (the "global context" the user can add to). scope is reserved for
            # future per-feature scoping; "global" applies everywhere.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS global_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'global',
                    pinned INTEGER NOT NULL DEFAULT 1,
                    created REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            # Learn-and-Execute: reusable procedures generalised from a recorded
            # demonstration. steps_json is the ordered, UI-agnostic step plan.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS routines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    goal TEXT NOT NULL DEFAULT '',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    runs INTEGER NOT NULL DEFAULT 0,
                    successes INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL,
                    last_run REAL,
                    UNIQUE(user_id, name),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.75,
                    source TEXT NOT NULL DEFAULT 'inferred',
                    created REAL NOT NULL,
                    UNIQUE(user_id, category, key),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    mode TEXT,
                    summary TEXT NOT NULL,
                    tags TEXT,
                    created REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    skill_name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    feedback TEXT,
                    created REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            self._migrate(conn)

    # Additive, idempotent column migrations on the users table — keeps existing
    # databases working while adding account (password), per-user settings, and
    # cloud-sync metadata for the local-first + sync-seam design.
    _USER_MIGRATIONS = (
        ("password_hash", "TEXT"),       # set only when password auth is used
        ("prefs",         "TEXT"),       # JSON blob: per-user settings (voice, mode…)
        ("cloud_id",      "TEXT"),       # remote account id once cloud sync is wired
        ("updated",       "REAL"),       # last-modified clock for sync conflict checks
    )

    def _migrate(self, conn: sqlite3.Connection) -> None:
        have = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for col, decl in self._USER_MIGRATIONS:
            if col not in have:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")

    def _invalidate_cache(self) -> None:
        with self._cache_lock:
            self._memory_cache = None

    def _get_groq(self) -> Any:
        if self._groq_client is not None:
            return self._groq_client
        import os

        api_key = (os.environ.get("GROQ_API_KEY") or "").strip()
        if not api_key:
            return None
        try:
            from groq import Groq

            self._groq_client = Groq(api_key=api_key)
        except Exception:
            self._groq_client = None
        return self._groq_client

    def create_or_login(self, name: str, email: str | None = None) -> int:
        """Create a user if needed, update last_seen, return user id."""
        clean_name = (name or "default").strip() or "default"
        now = time.time()
        with self._write_lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE lower(name) = lower(?)",
                (clean_name,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE users SET last_seen = ?, email = COALESCE(?, email) WHERE id = ?",
                    (now, email, row["id"]),
                )
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO users (name, email, created, last_seen, updated) "
                "VALUES (?, ?, ?, ?, ?)",
                (clean_name, email, now, now, now),
            )
            return int(cur.lastrowid or 0)

    # ── Accounts / profiles ───────────────────────────────────────────────────

    @staticmethod
    def _hash_password(password: str, *, iterations: int = 200_000,
                       salt: bytes | None = None) -> str:
        import hashlib
        import os as _os
        salt = salt or _os.urandom(16)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"

    @staticmethod
    def _verify_password(password: str, stored: str) -> bool:
        import hashlib
        import hmac
        try:
            algo, iters, salt_hex, hash_hex = stored.split("$", 3)
            if algo != "pbkdf2_sha256":
                return False
            dk = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"),
                bytes.fromhex(salt_hex), int(iters),
            )
            return hmac.compare_digest(dk.hex(), hash_hex)
        except Exception:
            return False

    def register(self, name: str, email: str | None = None,
                 password: str | None = None) -> tuple[bool, str, int]:
        """
        Create a new account. Returns (ok, message, user_id).

        Password is optional (local profiles); when given it is PBKDF2-hashed.
        """
        clean_name = (name or "").strip()
        if not clean_name:
            return (False, "Name is required.", 0)
        now = time.time()
        pw_hash = self._hash_password(password) if password else None
        with self._write_lock, self._connect() as conn:
            exists = conn.execute(
                "SELECT id FROM users WHERE lower(name) = lower(?)", (clean_name,)
            ).fetchone()
            if exists:
                return (False, "That name is already taken.", 0)
            cur = conn.execute(
                "INSERT INTO users (name, email, password_hash, created, last_seen, updated) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (clean_name, email, pw_hash, now, now, now),
            )
            return (True, "Account created.", int(cur.lastrowid))

    def authenticate(self, name: str, password: str | None = None
                     ) -> tuple[bool, str, int]:
        """Verify credentials. Returns (ok, message, user_id)."""
        clean_name = (name or "").strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM users WHERE lower(name) = lower(?)",
                (clean_name,),
            ).fetchone()
        if not row:
            return (False, "No such user.", 0)
        stored = row["password_hash"]
        if stored:
            if not password or not self._verify_password(password, stored):
                return (False, "Incorrect password.", 0)
        # Touch last_seen on successful login.
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_seen = ? WHERE id = ?", (time.time(), row["id"])
            )
        return (True, "Signed in.", int(row["id"]))

    def list_users(self) -> list[dict[str, Any]]:
        """All profiles, most-recently-seen first (for a profile picker)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, email, last_seen, "
                "       (password_hash IS NOT NULL) AS has_password "
                "FROM users ORDER BY last_seen DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_profile(self, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, email, created, last_seen FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else {}

    def set_display_name(self, user_id: int, name: str) -> None:
        """Update the user's display name (drives 'Known about <name>')."""
        clean = (name or "").strip()
        if not clean:
            return
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "UPDATE users SET name = ?, updated = ? WHERE id = ?",
                (clean, time.time(), user_id),
            )
        self._invalidate_cache()

    def update_profile(self, user_id: int, *, email: str | None = None,
                       password: str | None = None) -> None:
        sets, args = ["updated = ?"], [time.time()]
        if email is not None:
            sets.append("email = ?"); args.append(email)
        if password:
            sets.append("password_hash = ?"); args.append(self._hash_password(password))
        args.append(user_id)
        with self._write_lock, self._connect() as conn:
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", args)

    # ── Per-user settings (prefs JSON) ────────────────────────────────────────

    def get_prefs(self, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT prefs FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if not row or not row["prefs"]:
            return {}
        try:
            return json.loads(row["prefs"])
        except Exception:
            return {}

    def set_pref(self, user_id: int, key: str, value: Any) -> None:
        prefs = self.get_prefs(user_id)
        prefs[str(key)] = value
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "UPDATE users SET prefs = ?, updated = ? WHERE id = ?",
                (json.dumps(prefs), time.time(), user_id),
            )

    # ── Global context (per-user notes applied to ALL features) ───────────────

    def add_context(self, user_id: int, content: str, scope: str = "global") -> int:
        content = (content or "").strip()
        if not content:
            return 0
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO global_context (user_id, content, scope, created) "
                "VALUES (?, ?, ?, ?)",
                (user_id, content, scope, time.time()),
            )
        self._invalidate_cache()
        return int(cur.lastrowid or 0)

    def list_context(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, content, scope, pinned, created FROM global_context "
                "WHERE user_id = ? ORDER BY created DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_context(self, user_id: int, context_id: int) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM global_context WHERE user_id = ? AND id = ?",
                (user_id, context_id),
            )
        self._invalidate_cache()

    def build_context_prompt(self, user_id: int) -> str:
        """Markdown block of the user's standing instructions for all prompts."""
        rows = self.list_context(user_id)
        if not rows:
            return ""
        lines = ["## Standing instructions from the user (always apply)"]
        for r in rows:
            lines.append(f"- {r['content']}")
        return "\n".join(lines)

    # ── Routines (Learn-and-Execute) ──────────────────────────────────────────

    def save_routine(self, user_id: int, name: str, goal: str,
                     steps: list[dict[str, Any]], notes: str = "") -> int:
        name = (name or "").strip() or "untitled routine"
        now = time.time()
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO routines (user_id, name, goal, steps_json, notes, created)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, name) DO UPDATE SET
                    goal = excluded.goal,
                    steps_json = excluded.steps_json,
                    notes = excluded.notes
                """,
                (user_id, name, goal, json.dumps(steps), notes, now),
            )
            rid = cur.lastrowid or conn.execute(
                "SELECT id FROM routines WHERE user_id = ? AND name = ?",
                (user_id, name),
            ).fetchone()["id"]
        return int(rid)

    def list_routines(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, goal, runs, successes, created, last_run "
                "FROM routines WHERE user_id = ? ORDER BY last_run DESC, created DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_routine(self, user_id: int, name_or_id: str | int) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            if isinstance(name_or_id, int) or str(name_or_id).isdigit():
                row = conn.execute(
                    "SELECT * FROM routines WHERE user_id = ? AND id = ?",
                    (user_id, int(name_or_id)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM routines WHERE user_id = ? AND lower(name) = lower(?)",
                    (user_id, str(name_or_id).strip()),
                ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["steps"] = json.loads(data.get("steps_json") or "[]")
        except Exception:
            data["steps"] = []
        return data

    def record_routine_run(self, user_id: int, name_or_id: str | int,
                           success: bool) -> None:
        with self._write_lock, self._connect() as conn:
            field = "id" if (isinstance(name_or_id, int) or str(name_or_id).isdigit()) else "name"
            key = int(name_or_id) if field == "id" else str(name_or_id).strip()
            comp = "id = ?" if field == "id" else "lower(name) = lower(?)"
            conn.execute(
                f"UPDATE routines SET runs = runs + 1, "
                f"successes = successes + ?, last_run = ? "
                f"WHERE user_id = ? AND {comp}",
                (1 if success else 0, time.time(), user_id, key),
            )

    def remember(
        self,
        user_id: int,
        category: str,
        key: str,
        value: str,
        confidence: float = 0.75,
        source: str = "inferred",
    ) -> None:
        """Upsert one durable user fact."""
        clean_category = (category or "general").strip().lower()
        clean_key = (key or "note").strip().lower().replace(" ", "_")
        clean_value = (value or "").strip()
        if not clean_value:
            return
        conf = max(0.0, min(1.0, float(confidence)))
        now = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_facts
                    (user_id, category, key, value, confidence, source, created)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, category, key)
                DO UPDATE SET
                    value = excluded.value,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    created = excluded.created
                """,
                (user_id, clean_category, clean_key, clean_value, conf, source, now),
            )
        self._invalidate_cache()

    def reinforce(self, user_id: int, category: str, key: str) -> None:
        """Bump confidence +0.05 capped at 1.0."""
        clean_category = (category or "general").strip().lower()
        clean_key = (key or "note").strip().lower().replace(" ", "_")
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE user_facts
                SET confidence = MIN(1.0, confidence + 0.05),
                    created = ?
                WHERE user_id = ? AND category = ? AND key = ?
                """,
                (time.time(), user_id, clean_category, clean_key),
            )
        self._invalidate_cache()

    def decay_stale_facts(self, days_threshold: int = 30, decay: float = 0.02) -> int:
        """Reduce confidence for stale facts; floor at 0.1. Returns rows updated."""
        cutoff = time.time() - (days_threshold * 86400)
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE user_facts
                SET confidence = MAX(0.1, confidence - ?)
                WHERE created < ? AND confidence > 0.1
                """,
                (decay, cutoff),
            )
            count = cur.rowcount or 0
        if count:
            self._invalidate_cache()
        return count

    def recall(self, user_id: int, min_confidence: float = 0.0) -> list[dict[str, Any]]:
        """Return facts for a user above min_confidence."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, category, key, value, confidence, source, created
                FROM user_facts
                WHERE user_id = ? AND confidence >= ?
                ORDER BY category ASC, confidence DESC, created DESC
                """,
                (user_id, min_confidence),
            ).fetchall()
        return [dict(row) for row in rows]

    def forget(self, user_id: int, category: str, key: str) -> None:
        """Delete one fact."""
        clean_category = (category or "general").strip().lower()
        clean_key = (key or "note").strip().lower().replace(" ", "_")
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM user_facts WHERE user_id = ? AND category = ? AND key = ?",
                (user_id, clean_category, clean_key),
            )
        self._invalidate_cache()

    def _user_name(self, user_id: int) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
        return str(row["name"]) if row else "User"

    def _recent_sessions(self, user_id: int, limit: int = 3) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT mode, summary, tags, created
                FROM session_summaries
                WHERE user_id = ?
                ORDER BY created DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_session_summary(
        self,
        user_id: int,
        mode: str,
        history_turns: list[dict[str, Any]],
        summary_text: str | None = None,
    ) -> str:
        """
        Persist a session summary; optionally summarize via Groq.

        Blocking — call ``save_session_summary_async`` from the UI thread.
        Returns the stored summary text.
        """
        tags = ""
        summary = (summary_text or "").strip()
        if not summary and history_turns:
            client = self._get_groq()
            if client is not None:
                last = history_turns[-10:]
                lines = []
                for turn in last:
                    role = turn.get("role", "user")
                    content = str(turn.get("content", ""))[:500]
                    lines.append(f"{role}: {content}")
                prompt = (
                    "Summarize this mentoring session in 2-3 sentences. "
                    "Return JSON: {\"summary\":\"...\",\"tags\":[\"tag1\",\"tag2\"]}"
                    f"\n\n{chr(10).join(lines)}"
                )
                try:
                    resp = client.chat.completions.create(
                        model=ATLAS_FAST_MODEL,
                        messages=[
                            {"role": "system", "content": "Return valid JSON only."},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0,
                        max_tokens=400,
                        response_format={"type": "json_object"},
                    )
                    data = json.loads(resp.choices[0].message.content or "{}")
                    summary = str(data.get("summary", "")).strip()
                    tag_list = data.get("tags", [])
                    if isinstance(tag_list, list):
                        tags = ",".join(str(t) for t in tag_list)
                except Exception:
                    summary = "Session ended."
        if not summary:
            summary = "Session ended."
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO session_summaries (user_id, mode, summary, tags, created)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, mode, summary, tags, time.time()),
            )
        self._invalidate_cache()
        return summary

    # ── Native multi-threaded cognition (never blocks the UI loop) ────────────

    def save_session_summary_async(
        self,
        user_id: int,
        mode: str,
        history_turns: list[dict[str, Any]],
        summary_text: str | None = None,
        on_done: Optional[Callable[[str], None]] = None,
    ) -> threading.Thread:
        """
        Summarise + persist a session on a daemon worker thread.

        The blocking Groq call and SQLite write happen off the UI thread; the
        resulting summary is delivered via ``signals.summary_saved`` (Qt) and/or
        the optional ``on_done`` callback.  Returns the worker thread.
        """
        # Snapshot history so the caller can mutate its own copy freely.
        history_snapshot = list(history_turns)

        def _work() -> None:
            try:
                summary = self.save_session_summary(
                    user_id, mode, history_snapshot, summary_text
                )
                if self.signals is not None:
                    self.signals.summary_saved.emit(user_id, summary)
                if on_done is not None:
                    on_done(summary)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("save_session_summary_async failed: %s", exc)
                if self.signals is not None:
                    self.signals.error.emit(f"summary: {exc}")

        worker = threading.Thread(
            target=_work, daemon=True, name="atlas-mem-summary"
        )
        worker.start()
        return worker

    def extract_and_store_facts_async(
        self,
        user_id: int,
        user_message: str,
        ai_response: str,
        model: str = ATLAS_FAST_MODEL,
        on_done: Optional[Callable[[list[dict[str, Any]]], None]] = None,
    ) -> threading.Thread:
        """
        Extract durable facts via Groq, persist them, all on a daemon thread.

        Network + DB work runs off the UI loop; extracted facts are marshalled
        back through ``signals.facts_extracted`` (Qt) and/or ``on_done``.
        Returns the worker thread.
        """

        def _work() -> None:
            try:
                facts = self.extract_facts_from_turn(user_message, ai_response, model)
                for fact in facts:
                    self.remember(
                        user_id,
                        str(fact.get("category", "general")),
                        str(fact.get("key", "note")),
                        str(fact.get("value", "")),
                        float(fact.get("confidence", 0.75)),
                        source="inferred",
                    )
                if self.signals is not None:
                    self.signals.facts_extracted.emit(user_id, facts)
                if on_done is not None:
                    on_done(facts)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("extract_and_store_facts_async failed: %s", exc)
                if self.signals is not None:
                    self.signals.error.emit(f"facts: {exc}")

        worker = threading.Thread(
            target=_work, daemon=True, name="atlas-mem-facts"
        )
        worker.start()
        return worker

    def extract_facts_from_turn(
        self,
        user_message: str,
        ai_response: str,
        model: str = ATLAS_FAST_MODEL,
    ) -> list[dict[str, Any]]:
        """Lazy Groq fact extraction; returns [] if unavailable. Blocking."""
        client = self._get_groq()
        if client is None:
            return []
        prompt = (
            "Extract durable user facts for future mentoring. Ignore secrets and transient requests. "
            "Return JSON: {\"facts\":[{\"category\":\"profile|preference|project|goal|constraint\","
            "\"key\":\"snake_case\",\"value\":\"short\",\"confidence\":0.0-1.0}]}\n\n"
            f"USER:\n{user_message}\n\nAI:\n{ai_response}"
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=600,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            facts = data.get("facts", [])
            if not isinstance(facts, list):
                return []
            cleaned: list[dict[str, Any]] = []
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                key = str(fact.get("key", "")).strip()
                value = str(fact.get("value", "")).strip()
                if not key or not value:
                    continue
                try:
                    confidence = float(fact.get("confidence", 0.75))
                except (TypeError, ValueError):
                    confidence = 0.75
                cleaned.append(
                    {
                        "category": str(fact.get("category", "general")).strip(),
                        "key": key,
                        "value": value,
                        "confidence": max(0.0, min(1.0, confidence)),
                    }
                )
            return cleaned
        except Exception:
            return []

    def build_memory_prompt(self, user_id: int, min_confidence: float = 0.5) -> str:
        """Cached markdown block for system prompt injection."""
        now = time.time()
        with self._cache_lock:
            if self._memory_cache and (now - self._memory_cache[0]) < _MEMORY_CACHE_TTL_S:
                return self._memory_cache[1]

        facts = self.recall(user_id, min_confidence=min_confidence)
        name = self._user_name(user_id)
        lines = [
            f"## Known about {name}",
            "Use quietly to personalize; do not mention memory unless relevant.",
        ]
        if facts:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for fact in facts:
                grouped.setdefault(str(fact["category"]), []).append(fact)
            for category, items in grouped.items():
                lines.append(f"\n### {category.title()}")
                for item in items:
                    key = str(item["key"]).replace("_", " ")
                    value = str(item["value"])
                    conf = float(item["confidence"])
                    lines.append(f"- {key}: {value} (confidence {conf:.2f})")

        sessions = self._recent_sessions(user_id, limit=3)
        if sessions:
            lines.append("\n### Recent sessions")
            for sess in sessions:
                mode = sess.get("mode") or "unknown"
                summary = str(sess.get("summary", ""))[:200]
                lines.append(f"- [{mode}] {summary}")

        result = "\n".join(lines) if len(lines) > 2 else ""
        with self._cache_lock:
            self._memory_cache = (now, result)
        return result

    def log_skill_outcome(
        self,
        user_id: int,
        skill_name: str,
        success: bool,
        feedback: str = "",
    ) -> None:
        """Record skill execution outcome."""
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_outcomes (user_id, skill_name, success, feedback, created)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, skill_name, 1 if success else 0, feedback, time.time()),
            )

    def get_skill_performance(self, user_id: int, skill_name: str) -> dict[str, Any]:
        """Return success_rate, uses, last_used for a skill."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT success, created FROM skill_outcomes
                WHERE user_id = ? AND skill_name = ?
                ORDER BY created DESC
                """,
                (user_id, skill_name),
            ).fetchall()
        if not rows:
            return {"success_rate": 1.0, "uses": 0, "last_used": None}
        uses = len(rows)
        successes = sum(int(r["success"]) for r in rows)
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(rows[0]["created"])))
        return {
            "success_rate": successes / uses if uses else 1.0,
            "uses": uses,
            "last_used": last,
        }
