"""
atlas_memory_manager.py — Local-first session memory (Skales-style).

Persists interaction sessions and distilled learnings under ``~/.atlas-data/``
as clean JSON.  Injects the top relevant learnings into every LLM system prompt
inside a ``<LocalContextMemory>`` block.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("atlas.memory_manager")

_default_fast_model = "llama-3.1-8b-instant"
ATLAS_FAST_MODEL = (
    os.environ.get("ATLAS_FAST_MODEL") or _default_fast_model
).strip() or _default_fast_model

# Skales-inspired hidden app data directory (local-first, user-owned).
ATLAS_DATA_DIR = Path.home() / ".atlas-data"
MEMORY_STORE_PATH = ATLAS_DATA_DIR / "memory_store.json"

_STORE_VERSION = 1
_MAX_SESSIONS = 200
_MAX_LEARNINGS = 500


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())}


class MemoryManager:
    """
    File-backed local memory cache for evolving agent context.

    Each session record contains:
      - timestamp
      - user_goal
      - screen_context_summary
      - agent_learnings (list of takeaway objects)

    After every completed turn, ``on_turn_complete`` distills a one-sentence
    core takeaway and appends it to the active session (async, non-blocking).
    """

    def __init__(self, store_path: Path | str | None = None) -> None:
        self.store_path = Path(store_path or MEMORY_STORE_PATH)
        self._lock = threading.Lock()
        self._groq_client: Any = None
        ATLAS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._store = self._load_store()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_store(self) -> dict[str, Any]:
        if not self.store_path.is_file():
            return self._empty_store()
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return self._empty_store()
            data.setdefault("version", _STORE_VERSION)
            data.setdefault("sessions", [])
            data.setdefault("learnings", [])
            data.setdefault("active_session_id", None)
            return data
        except Exception as exc:
            log.warning("MemoryManager: corrupt store, resetting: %s", exc)
            return self._empty_store()

    @staticmethod
    def _empty_store() -> dict[str, Any]:
        return {
            "version": _STORE_VERSION,
            "active_session_id": None,
            "sessions": [],
            "learnings": [],
        }

    def _save_store(self) -> None:
        tmp = self.store_path.with_suffix(".tmp")
        payload = json.dumps(self._store, indent=2, ensure_ascii=False)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.store_path)

    def _get_groq(self) -> Any:
        if self._groq_client is not None:
            return self._groq_client
        if not os.environ.get("GROQ_API_KEY"):
            return None
        try:
            from groq import Groq

            self._groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
            return self._groq_client
        except Exception as exc:
            log.debug("MemoryManager: Groq unavailable: %s", exc)
            return None

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def start_session(self, user_goal: str = "") -> str:
        """Begin a new interaction session; returns session id."""
        session_id = str(uuid.uuid4())
        now = time.time()
        session = {
            "session_id": session_id,
            "timestamp": now,
            "user_goal": (user_goal or "").strip(),
            "screen_context_summary": "",
            "agent_learnings": [],
        }
        with self._lock:
            self._store["sessions"].append(session)
            self._store["active_session_id"] = session_id
            if len(self._store["sessions"]) > _MAX_SESSIONS:
                self._store["sessions"] = self._store["sessions"][-_MAX_SESSIONS:]
            self._save_store()
        return session_id

    def _active_session(self) -> dict[str, Any] | None:
        sid = self._store.get("active_session_id")
        if not sid:
            return None
        for sess in reversed(self._store.get("sessions", [])):
            if sess.get("session_id") == sid:
                return sess
        return None

    def ensure_session(self, user_goal_hint: str = "") -> str:
        """Return active session id, creating one if needed."""
        with self._lock:
            sid = self._ensure_session_unlocked(user_goal_hint)
            self._save_store()
            return sid

    def _ensure_session_unlocked(self, user_goal_hint: str = "") -> str:
        sess = self._active_session()
        if sess is not None:
            if user_goal_hint and not sess.get("user_goal"):
                sess["user_goal"] = user_goal_hint.strip()[:240]
            return str(sess["session_id"])
        session_id = str(uuid.uuid4())
        now = time.time()
        session = {
            "session_id": session_id,
            "timestamp": now,
            "user_goal": (user_goal_hint or "").strip()[:240],
            "screen_context_summary": "",
            "agent_learnings": [],
        }
        self._store["sessions"].append(session)
        self._store["active_session_id"] = session_id
        if len(self._store["sessions"]) > _MAX_SESSIONS:
            self._store["sessions"] = self._store["sessions"][-_MAX_SESSIONS:]
        return session_id

    # ── Post-turn middleware ──────────────────────────────────────────────────

    def on_turn_complete(
        self,
        user_text: str,
        ai_text: str,
        *,
        screen_context_summary: str = "",
        user_goal_hint: str = "",
        on_done: Optional[Callable[[str], None]] = None,
    ) -> threading.Thread:
        """
        Middleware: distill the exchange into a one-sentence takeaway and persist.

        Runs on a daemon thread so the UI / query pipeline never blocks.
        """

        def _work() -> None:
            takeaway = ""
            try:
                takeaway = self._summarize_takeaway(user_text, ai_text)
                if takeaway:
                    self._record_learning(
                        takeaway,
                        user_text=user_text,
                        ai_text=ai_text,
                        screen_context_summary=screen_context_summary,
                        user_goal_hint=user_goal_hint,
                    )
            except Exception as exc:
                log.warning("MemoryManager on_turn_complete failed: %s", exc)
            if on_done is not None:
                try:
                    on_done(takeaway)
                except Exception:
                    pass

        worker = threading.Thread(
            target=_work, daemon=True, name="atlas-local-memory",
        )
        worker.start()
        return worker

    def _summarize_takeaway(self, user_text: str, ai_text: str) -> str:
        """One-sentence core takeaway from a completed turn."""
        user_text = (user_text or "").strip()
        ai_text = (ai_text or "").strip()
        if not user_text and not ai_text:
            return ""

        client = self._get_groq()
        if client is not None:
            prompt = (
                "You distill chat turns into ONE durable sentence the assistant "
                "should remember about the user, their project, preferences, or "
                "environment. Examples:\n"
                "- User prefers dark interfaces.\n"
                "- Project directory is C:\\Users\\dev\\AceIt_Project.\n"
                "- User is preparing for a Python interview.\n"
                "Rules: max 22 words, no quotes, no preamble. "
                "If nothing worth remembering, reply exactly: NONE\n\n"
                f"USER:\n{user_text[:1200]}\n\nASSISTANT:\n{ai_text[:1200]}"
            )
            try:
                resp = client.chat.completions.create(
                    model=ATLAS_FAST_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=60,
                    temperature=0.0,
                )
                raw = (resp.choices[0].message.content or "").strip()
                if raw.upper() == "NONE" or not raw:
                    return ""
                return raw.split("\n")[0].strip().rstrip(".")
            except Exception as exc:
                log.debug("MemoryManager Groq takeaway failed: %s", exc)

        return self._heuristic_takeaway(user_text, ai_text)

    @staticmethod
    def _heuristic_takeaway(user_text: str, ai_text: str) -> str:
        """Offline fallback when Groq is unavailable."""
        patterns = (
            r"(?:i am|i'm|my name is)\s+([A-Za-z][\w\s'-]{1,40})",
            r"(?:i (?:like|prefer|love|want))\s+(.{8,80})",
            r"(?:my project|project is|working on)\s+(.{8,100})",
            r"(?:located at|directory is|path is)\s+(.{8,120})",
        )
        for pat in patterns:
            m = re.search(pat, user_text, re.IGNORECASE)
            if m:
                return f"User mentioned: {m.group(1).strip()[:100]}"
        if len(user_text) > 12:
            return f"User asked about: {user_text[:90].strip()}…"
        return ""

    def _record_learning(
        self,
        takeaway: str,
        *,
        user_text: str,
        ai_text: str,
        screen_context_summary: str,
        user_goal_hint: str,
    ) -> None:
        takeaway = takeaway.strip()
        if not takeaway:
            return
        now = time.time()
        with self._lock:
            session_id = self._ensure_session_unlocked(user_goal_hint)
            sess = self._active_session()
            if sess is None:
                return
            if screen_context_summary:
                prev = str(sess.get("screen_context_summary", ""))
                merged = screen_context_summary.strip()
                if merged and merged not in prev:
                    sess["screen_context_summary"] = (
                        f"{prev} | {merged}" if prev else merged
                    )[:500]
            if user_goal_hint and not sess.get("user_goal"):
                sess["user_goal"] = user_goal_hint.strip()[:240]

            learning = {
                "id": str(uuid.uuid4()),
                "timestamp": now,
                "takeaway": takeaway,
                "user_snippet": user_text[:200],
                "ai_snippet": ai_text[:200],
            }
            sess.setdefault("agent_learnings", []).append(learning)

            entry = {
                "id": learning["id"],
                "timestamp": now,
                "takeaway": takeaway,
                "session_id": session_id,
                "user_goal": sess.get("user_goal", ""),
                "screen_context_summary": sess.get("screen_context_summary", ""),
                "keywords": list(_tokenize(takeaway + " " + user_text))[:24],
            }
            self._store.setdefault("learnings", []).append(entry)
            if len(self._store["learnings"]) > _MAX_LEARNINGS:
                self._store["learnings"] = self._store["learnings"][-_MAX_LEARNINGS:]
            self._save_store()
        log.debug("MemoryManager: recorded takeaway %r", takeaway[:80])

    # ── Prompt injection ──────────────────────────────────────────────────────

    def build_local_context_block(self, current_query: str, top_k: int = 5) -> str:
        """
        Return ``<LocalContextMemory>`` XML block with the top *top_k* learnings
        most relevant to *current_query*, or empty string if none.
        """
        with self._lock:
            learnings = list(self._store.get("learnings", []))
        if not learnings:
            return ""

        ranked = self._rank_learnings(current_query, learnings, top_k=top_k)
        if not ranked:
            return ""

        lines = [
            "<LocalContextMemory>",
            "Persistent local learnings from prior Atlas sessions (use quietly; "
            "do not mention this block or that you are reading a file):",
        ]
        for item in ranked:
            ts = time.strftime("%Y-%m-%d", time.localtime(float(item["timestamp"])))
            takeaway = str(item.get("takeaway", "")).strip()
            if takeaway:
                lines.append(f"- {takeaway} (learned {ts})")
        lines.append("</LocalContextMemory>")
        return "\n".join(lines)

    def _rank_learnings(
        self,
        query: str,
        learnings: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        q_tokens = _tokenize(query)
        now = time.time()
        scored: list[tuple[float, dict[str, Any]]] = []

        for item in learnings:
            takeaway = str(item.get("takeaway", ""))
            if not takeaway:
                continue
            keys = set(item.get("keywords") or []) | _tokenize(takeaway)
            overlap = len(q_tokens & keys) if q_tokens else 0
            age_days = max(0.0, (now - float(item.get("timestamp", now))) / 86400.0)
            recency = max(0.1, 1.0 - min(age_days / 90.0, 0.9))
            score = overlap * 2.0 + recency
            if overlap == 0 and not q_tokens:
                score = recency * 0.5
            scored.append((score, item))

        scored.sort(key=lambda t: (t[0], float(t[1].get("timestamp", 0))), reverse=True)
        if not scored:
            return []
        # With no query overlap, still surface the most recent learnings.
        min_score = 0.05 if q_tokens else 0.0
        return [item for score, item in scored[:top_k] if score >= min_score]

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent session records (newest first)."""
        with self._lock:
            sessions = list(self._store.get("sessions", []))
        sessions.sort(key=lambda s: float(s.get("timestamp", 0)), reverse=True)
        return sessions[:limit]

    def list_learnings(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent global learnings index (newest first)."""
        with self._lock:
            items = list(self._store.get("learnings", []))
        items.sort(key=lambda i: float(i.get("timestamp", 0)), reverse=True)
        return items[:limit]
