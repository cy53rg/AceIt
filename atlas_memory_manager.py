"""
atlas_memory_manager.py — Deprecated shim; session takeaways live in UserMemory SQLite.

Use ``UserMemory.build_local_context_block`` and ``record_takeaway_from_turn``.
"""
from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any, Callable, Optional

from atlas_memory import UserMemory

log = logging.getLogger("atlas.memory_manager")

ATLAS_DATA_DIR = Path.home() / ".atlas-data"
MEMORY_STORE_PATH = ATLAS_DATA_DIR / "memory_store.json"


class MemoryManager:
    """Delegates to UserMemory.session_takeaways (legacy API compatibility)."""

    def __init__(
        self,
        store_path: Path | str | None = None,
        *,
        memory: UserMemory | None = None,
        user_id: int = 0,
    ) -> None:
        if store_path:
            warnings.warn(
                "MemoryManager store_path is ignored; use UserMemory SQLite",
                DeprecationWarning,
                stacklevel=2,
            )
        self._memory = memory or UserMemory()
        self._user_id = int(user_id or 0)

    def ensure_session(self, user_goal_hint: str = "") -> str:
        if not self._user_id:
            return ""
        return self._memory.ensure_local_session(self._user_id, user_goal_hint)

    def on_turn_complete(
        self,
        user_text: str,
        ai_text: str,
        *,
        screen_context_summary: str = "",
        user_goal_hint: str = "",
        on_done: Optional[Callable[[str], None]] = None,
    ):
        if not self._user_id:
            return None
        return self._memory.record_takeaway_from_turn(
            self._user_id,
            user_text,
            ai_text,
            user_goal_hint=user_goal_hint or screen_context_summary,
            on_done=on_done,
        )

    def build_local_context_block(self, current_query: str, top_k: int = 5) -> str:
        if not self._user_id:
            return ""
        return self._memory.build_local_context_block(
            self._user_id, current_query, top_k=top_k,
        )
