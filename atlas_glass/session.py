"""
atlas_glass/session.py — Live Glass session state (meetings + dual-audio transcript).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from atlas_glass.profile_router import GlassProfile, ProfileRouter
from atlas_glass.rag import build_glass_context_block, summarize_transcript, summarize_with_groq

log = logging.getLogger("atlas_glass.session")


class GlassSession:
    """One active meeting/interview session per user."""

    def __init__(self, memory: Any, user_id: int) -> None:
        self.memory = memory
        self.user_id = int(user_id)
        self._meeting_id: Optional[int] = None
        self._profile: GlassProfile = "general"
        self._router = ProfileRouter()
        self._started_at = 0.0
        self._pre_meeting_brief = ""

    def set_user(self, user_id: int) -> None:
        self.user_id = int(user_id)
        self._meeting_id = None
        self._profile = "general"

    @property
    def active(self) -> bool:
        return self._meeting_id is not None

    @property
    def meeting_id(self) -> Optional[int]:
        return self._meeting_id

    @property
    def profile(self) -> GlassProfile:
        return self._profile

    def set_pre_meeting_brief(self, text: str) -> None:
        self._pre_meeting_brief = (text or "").strip()

    def start(self, *, title: str = "") -> int:
        if self._meeting_id:
            return self._meeting_id
        self._profile = "general"
        self._started_at = time.time()
        self._meeting_id = self.memory.glass_start_meeting(
            self.user_id,
            title=title or "Glass session",
            profile=self._profile,
        )
        log.info("Glass session started meeting_id=%s user_id=%s", self._meeting_id, self.user_id)
        return self._meeting_id

    def end(self) -> dict[str, Any]:
        if not self._meeting_id:
            return {}
        mid = self._meeting_id
        chunks = self.memory.glass_recent_chunks(mid, limit=300)
        transcript = summarize_transcript(chunks)
        summary = summarize_with_groq(transcript)
        self.memory.glass_end_meeting(self.user_id, mid, summary=summary)
        duration = max(0.0, time.time() - self._started_at)
        self._meeting_id = None
        self._profile = "general"
        log.info("Glass session ended meeting_id=%s duration=%.0fs", mid, duration)
        return {
            "meeting_id": mid,
            "summary": summary,
            "duration_s": duration,
            "chunk_count": len(chunks),
        }

    def ingest(self, text: str, source: str) -> None:
        if not self._meeting_id:
            return
        body = (text or "").strip()
        if not body:
            return
        src = (source or "user").lower()
        if src in ("highlight", "capture"):
            src = "user"
        self.memory.glass_add_chunk(self._meeting_id, src, body)
        new_profile = self._router.classify(body, self._profile)
        if new_profile != self._profile:
            self._profile = new_profile
            self.memory.glass_update_profile(self._meeting_id, self._profile)

    def build_context_block(
        self,
        query: str = "",
        *,
        speaker_context: str = "",
    ) -> str:
        if not self._meeting_id:
            return ""
        block = build_glass_context_block(
            self.memory,
            self.user_id,
            meeting_id=self._meeting_id,
            query=query,
            speaker_context=speaker_context,
        )
        if self._pre_meeting_brief:
            prefix = self._pre_meeting_brief
            block = f"{prefix}\n\n{block}".strip() if block else prefix
        suffix = self._router.prompt_suffix(self._profile)
        if block:
            return f"{block}\n\n{suffix}"
        return suffix

    def status(self) -> dict[str, Any]:
        if not self._meeting_id:
            return {"active": False}
        meeting = self.memory.glass_get_meeting(self.user_id, self._meeting_id) or {}
        chunks = self.memory.glass_recent_chunks(self._meeting_id, limit=500)
        return {
            "active": True,
            "meeting_id": self._meeting_id,
            "profile": self._profile,
            "title": meeting.get("title", ""),
            "started_at": meeting.get("started_at", self._started_at),
            "chunk_count": len(chunks),
            "duration_s": max(0.0, time.time() - float(meeting.get("started_at") or self._started_at)),
        }
