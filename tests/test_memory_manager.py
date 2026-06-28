"""Tests for local-first MemoryManager (Skales-style ~/.atlas-data)."""

from __future__ import annotations

import json
from pathlib import Path

from atlas_memory_manager import MemoryManager


def test_memory_manager_persists_learning(tmp_path: Path):
    store = tmp_path / "memory_store.json"
    mgr = MemoryManager(store_path=store)
    mgr._record_learning(
        "User prefers dark interfaces",
        user_text="I like dark mode",
        ai_text="Noted.",
        screen_context_summary="Settings app open",
        user_goal_hint="Customize UI",
    )
    assert store.is_file()
    data = json.loads(store.read_text(encoding="utf-8"))
    assert len(data["learnings"]) == 1
    assert data["sessions"][0]["user_goal"] == "Customize UI"
    assert data["sessions"][0]["screen_context_summary"]


def test_build_local_context_block_xml():
    mgr = MemoryManager(store_path=Path("/nonexistent/test_store.json"))
    mgr._store["learnings"] = [
        {
            "id": "1",
            "timestamp": 1_700_000_000.0,
            "takeaway": "Project directory is C:\\dev\\AceIt",
            "keywords": ["project", "directory", "aceit"],
        },
        {
            "id": "2",
            "timestamp": 1_700_000_100.0,
            "takeaway": "User prefers terse answers",
            "keywords": ["terse", "answers"],
        },
    ]
    block = mgr.build_local_context_block("Where is my AceIt project?", top_k=5)
    assert block.startswith("<LocalContextMemory>")
    assert block.endswith("</LocalContextMemory>")
    assert "Project directory" in block
    assert "terse" not in block.lower() or "AceIt" in block
