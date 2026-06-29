"""Tests for MemoryManager shim delegating to UserMemory."""

from __future__ import annotations

import tempfile
from pathlib import Path

from atlas_memory import UserMemory
from atlas_memory_manager import MemoryManager


def test_memory_manager_shim_records_via_sqlite():
    path = Path(tempfile.mkdtemp()) / "mm_shim.sqlite3"
    mem = UserMemory(path)
    uid = mem.create_or_login("mm_user")
    mgr = MemoryManager(memory=mem, user_id=uid)
    mgr.ensure_session("Customize UI")
    mgr.on_turn_complete("I like dark mode", "Noted.")
    block = mgr.build_local_context_block("dark mode", top_k=5)
    assert "<LocalContextMemory>" in block or block == ""


def test_build_local_context_block_xml():
    path = Path(tempfile.mkdtemp()) / "mm_block.sqlite3"
    mem = UserMemory(path)
    uid = mem.create_or_login("block_user")
    mem.record_takeaway(uid, "Project directory is C:\\dev\\AceIt")
    mem.record_takeaway(uid, "User prefers terse answers")
    mgr = MemoryManager(memory=mem, user_id=uid)
    block = mgr.build_local_context_block("Where is my AceIt project?", top_k=5)
    assert block.startswith("<LocalContextMemory>")
    assert block.endswith("</LocalContextMemory>")
    assert "Project directory" in block
