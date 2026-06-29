"""Session takeaways in UserMemory SQLite (replaces JSON MemoryManager)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from atlas_memory import UserMemory


def _temp_memory() -> UserMemory:
    path = Path(tempfile.mkdtemp()) / "session_memory.sqlite3"
    return UserMemory(path)


def test_record_and_build_local_context_block():
    mem = _temp_memory()
    uid = mem.create_or_login("session_user")
    mem.record_takeaway(uid, "User prefers dark mode interfaces")
    mem.record_takeaway(uid, "Project lives in AceIt_Project folder")
    block = mem.build_local_context_block(uid, "dark theme settings", top_k=2)
    assert "<LocalContextMemory>" in block
    assert "dark mode" in block.lower()


def test_migrate_legacy_json_store():
    mem = _temp_memory()
    uid = mem.create_or_login("migrate_user")
    legacy_dir = Path(tempfile.mkdtemp())
    legacy = legacy_dir / "memory_store.json"
    legacy.write_text(
        json.dumps({
            "version": 1,
            "learnings": [
                {
                    "takeaway": "Legacy learning one",
                    "timestamp": 1_700_000_000.0,
                    "keywords": ["legacy", "learning"],
                }
            ],
        }),
        encoding="utf-8",
    )
    import atlas_memory as am

    old_path = am._LEGACY_JSON_STORE
    am._LEGACY_JSON_STORE = legacy
    try:
        n = mem.migrate_legacy_json_store(uid)
        assert n == 1
        block = mem.build_local_context_block(uid, "legacy", top_k=3)
        assert "Legacy learning" in block
        assert legacy.with_suffix(".json.migrated").is_file()
    finally:
        am._LEGACY_JSON_STORE = old_path


def test_memory_manager_shim_delegates():
    from atlas_memory_manager import MemoryManager

    mem = _temp_memory()
    uid = mem.create_or_login("shim_user")
    mgr = MemoryManager(memory=mem, user_id=uid)
    mgr.ensure_session("test goal")
    mgr.on_turn_complete("I prefer terse answers", "Got it — I'll keep replies short.")
    block = mgr.build_local_context_block("terse", top_k=3)
    assert isinstance(block, str)
