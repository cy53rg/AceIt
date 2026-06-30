"""Phase 3 — file indexer, search, and policy-gated open/read."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from atlas_files.access import find_file, is_executable_path, open_path, read_file_path
from atlas_files.indexer import FileIndexer
from atlas_mind.router import execute_tool, regex_tool_fallback


@pytest.fixture
def indexer(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "tax_return_2023.pdf").write_text("pdf", encoding="utf-8")
    (root / "resume.pdf").write_text("resume", encoding="utf-8")
    (subdir := root / "work").mkdir()
    (subdir / "notes.txt").write_text("hello atlas", encoding="utf-8")

    idx = FileIndexer(tmp_path / "files.sqlite3")
    idx.add_root(str(root), label="docs")
    idx.rebuild(async_run=False)
    return idx, root


def test_indexer_search_by_keywords(indexer):
    idx, root = indexer
    hits = idx.search("tax return 2023")
    assert hits
    assert any("tax_return_2023.pdf" in h["name"] for h in hits)


def test_find_file_helper(indexer):
    idx, _root = indexer
    matches = find_file(idx, "resume")
    assert len(matches) >= 1
    assert matches[0]["name"] == "resume.pdf"


def test_read_file_path(indexer, tmp_path):
    idx, root = indexer
    target = root / "work" / "notes.txt"
    engine = MagicMock()
    engine.memory.db_path = tmp_path / "policy.sqlite3"
    engine.user_id = 1
    engine.safety_mode = "off"
    engine._fs_access_active = False
    engine.execution_blocked = False

    ok, text = read_file_path(str(target), engine=engine)
    assert ok is True
    assert "hello atlas" in text


def test_open_path_by_query(indexer, monkeypatch):
    idx, _root = indexer
    opened: list[str] = []
    monkeypatch.setattr(
        "atlas_files.access.os.startfile",
        lambda p: opened.append(p),
    )
    engine = MagicMock()
    engine.memory.db_path = idx.db_path
    engine.user_id = 1
    engine.safety_mode = "off"
    engine._fs_access_active = False
    engine.execution_blocked = False

    ok, msg = open_path(query="resume pdf", indexer=idx, engine=engine)
    assert ok is True
    assert opened
    assert opened[0].endswith("resume.pdf")


def test_executable_requires_sensitive_policy(tmp_path):
    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"MZ")
    assert is_executable_path(exe) is True

    engine = MagicMock()
    engine.memory.db_path = tmp_path / "policy.sqlite3"
    engine.user_id = 1
    engine.safety_mode = "always"
    engine._fs_access_active = False
    engine.execution_blocked = False

    ok, _msg = open_path(str(exe), engine=engine)
    assert ok is False


def test_router_regex_open_file():
    picked = regex_tool_fallback("open my tax return 2023")
    assert picked is not None
    assert picked[0] == "open_path"
    assert "tax return 2023" in picked[1]["query"]


def test_router_find_file_tool(indexer, tmp_path):
    idx, _root = indexer
    engine = MagicMock()
    engine.file_indexer = idx
    msg = execute_tool(engine, "find_file", {"query": "notes"})
    assert "notes.txt" in msg


def test_router_read_file_tool(indexer, tmp_path):
    idx, root = indexer
    engine = MagicMock()
    engine.file_indexer = idx
    engine.memory.db_path = tmp_path / "policy.sqlite3"
    engine.user_id = 1
    engine.safety_mode = "off"
    engine._fs_access_active = False
    engine.execution_blocked = False
    msg = execute_tool(engine, "read_file", {"path": str(root / "work" / "notes.txt")})
    assert "hello atlas" in msg
