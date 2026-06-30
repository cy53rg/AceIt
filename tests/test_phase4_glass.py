"""Phase 4 — Atlas Glass session, profile router, meeting memory."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from atlas_glass.profile_router import ProfileRouter
from atlas_glass.session import GlassSession
from atlas_memory import UserMemory


@pytest.fixture
def memory(tmp_path):
    return UserMemory(str(tmp_path / "glass.sqlite3"))


def test_profile_router_coding():
    router = ProfileRouter()
    assert router.classify("explain binary search complexity") == "coding"
    assert "Technical" in router.prompt_suffix("coding")


def test_profile_router_behavioral():
    router = ProfileRouter()
    assert router.classify("tell me about a time you had a conflict") == "behavioral"


def test_glass_meeting_lifecycle(memory):
    uid = memory.create_or_login("glass-test")
    session = GlassSession(memory, uid)
    mid = session.start(title="Interview practice")
    assert session.active
    session.ingest("Let's discuss system design", "speaker")
    session.ingest("How would you scale this API?", "mic")
    assert session.profile in ("coding", "meeting", "general")
    memory.glass_add_chunk(mid, "user", "extra line")
    chunks = memory.glass_recent_chunks(mid)
    assert len(chunks) >= 2
    ended = session.end()
    assert ended.get("meeting_id") == mid
    assert not session.active
    meeting = memory.glass_get_meeting(uid, mid)
    assert meeting["status"] == "ended"
    assert meeting.get("summary")


def test_glass_search_chunks(memory):
    uid = memory.create_or_login("glass-search")
    mid = memory.glass_start_meeting(uid, title="Tax review")
    memory.glass_add_chunk(mid, "user", "We discussed the 2023 tax return PDF")
    memory.glass_add_chunk(mid, "speaker", "Please send the document by Friday")
    hits = memory.glass_search_chunks(uid, "tax return 2023", top_k=3)
    assert hits
    assert "tax" in hits[0]["text"].lower()


def test_glass_context_block(memory):
    from atlas_glass.rag import build_glass_context_block

    uid = memory.create_or_login("glass-ctx")
    mid = memory.glass_start_meeting(uid, title="Standup")
    memory.glass_add_chunk(mid, "speaker", "What is the status on the API migration?")
    block = build_glass_context_block(
        memory, uid, meeting_id=mid, query="API migration", speaker_context="deadline Friday"
    )
    assert "GlassSession" in block
    assert "API migration" in block or "migration" in block.lower()


def test_state_engine_ingest_glass_transcript(monkeypatch):
    from atlas_core import StateEngine

    events: list[tuple[str, dict]] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("threading.Thread", lambda *a, **k: MagicMock(start=MagicMock()))
        engine = StateEngine(
            on_chunk=lambda _c: None,
            on_complete=lambda _t: None,
            on_error=lambda _e: None,
            on_coordinates=lambda _d: None,
            on_token_usage=lambda _u: None,
            user_name="glass-ingest-test",
        )
    engine.on_event(lambda t, p: events.append((t, p)))
    engine.glass.start()
    engine.ingest_glass_transcript("hello from the call", "speaker")
    chunks = engine.memory.glass_recent_chunks(engine.glass.meeting_id)
    assert any("hello" in c["text"] for c in chunks)
