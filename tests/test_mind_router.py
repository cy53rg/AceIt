"""Tests for atlas_mind stack router and Groq tool router."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from atlas_mind import router as mind_router
from atlas_mind import stack_router
from atlas_mind.router import (
    classify_with_model,
    execute_tool,
    regex_tool_fallback,
    resolve_tool_call,
)


# ── Stack router ──────────────────────────────────────────────────────────────


def test_stack_time_answer():
    ans = stack_router.try_stack_answer("what time is it?")
    assert ans is not None
    assert "It's" in ans


def test_stack_math_answer():
    assert stack_router.try_stack_answer("what is 12 + 8?") == "20"
    assert stack_router.try_stack_answer("2 * 3 + 4") == "10"


def test_stack_returns_none_for_chat():
    assert stack_router.try_stack_answer("explain quantum computing") is None


# ── Regex fallback ──────────────────────────────────────────────────────────


def test_regex_web_search():
    picked = regex_tool_fallback("search the web for Groq API pricing")
    assert picked is not None
    assert picked[0] == "web_search"
    assert "Groq API pricing" in picked[1]["query"]


def test_regex_github_create_issue():
    picked = regex_tool_fallback(
        "create a github issue in cy53rg/AceIt titled Fix router tests"
    )
    assert picked == (
        "github_create_issue",
        {
            "repo": "cy53rg/AceIt",
            "title": "Fix router tests",
            "body": "",
        },
    )


def test_regex_github_list_issues():
    picked = regex_tool_fallback("list open issues in cy53rg/AceIt")
    assert picked[0] == "github_list_issues"
    assert picked[1]["repo"] == "cy53rg/AceIt"


# ── Model classification ───────────────────────────────────────────────────


def test_classify_with_model_tool_call():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="web_search",
                                arguments=json.dumps({"query": "latest Python release"}),
                            )
                        )
                    ]
                )
            )
        ]
    )
    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        picked = classify_with_model(client, "what's the latest Python release?")
    assert picked == ("web_search", {"query": "latest Python release"})


def test_resolve_model_wins_over_regex(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="run_task",
                                arguments=json.dumps({"task": "install updates"}),
                            )
                        )
                    ]
                )
            )
        ]
    )
    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        picked = resolve_tool_call(client, "search for cats")  # regex would pick web_search
    assert picked[0] == "run_task"


# ── Tool execution ───────────────────────────────────────────────────────────


def test_execute_web_search(monkeypatch):
    engine = MagicMock()
    with patch("atlas_research.search_task_docs") as mock_search:
        mock_search.return_value = [
            {"title": "Example", "snippet": "Snippet text", "url": "https://example.com"},
        ]
        msg = execute_tool(engine, "web_search", {"query": "Atlas AI"})
    assert "Example" in msg
    assert "Snippet" in msg


def test_execute_github_create_issue(monkeypatch, tmp_path):
    from atlas_memory import UserMemory

    memory = UserMemory(str(tmp_path / "router_test.sqlite3"))
    uid = memory.create_or_login("router-github-test")

    engine = MagicMock()
    engine.memory = memory
    engine.user_id = uid
    engine.safety_mode = "off"
    engine._fs_access_active = False
    engine.execution_blocked = False
    engine.connectors = MagicMock()
    engine.connectors.execute.return_value = {
        "ok": True,
        "result": {"number": 42, "html_url": "https://github.com/o/r/issues/42"},
    }

    msg = execute_tool(
        engine,
        "github_create_issue",
        {"repo": "owner/repo", "title": "Bug", "body": ""},
    )
    assert "42" in msg
    engine.connectors.execute.assert_called_once()


def test_execute_run_task_denied_when_blocked(tmp_path):
    from atlas_memory import UserMemory

    memory = UserMemory(str(tmp_path / "blocked.sqlite3"))
    uid = memory.create_or_login("blocked-task")

    engine = MagicMock()
    engine.memory = memory
    engine.user_id = uid
    engine.safety_mode = "off"
    engine._fs_access_active = False
    engine.execution_blocked = True
    engine.run_task = MagicMock()

    msg = execute_tool(engine, "run_task", {"task": "click install"})
    assert "Can't run" in msg
    engine.run_task.assert_not_called()


def test_try_route_tools_integration(monkeypatch):
    from atlas_core import StateEngine

    engine = MagicMock(spec=StateEngine)
    engine._finish_router_response = MagicMock()

    monkeypatch.setattr(
        mind_router,
        "resolve_tool_call",
        lambda *_a, **_k: ("web_search", {"query": "test"}),
    )
    monkeypatch.setattr(
        mind_router,
        "execute_tool",
        lambda *_a, **_k: "Search results here",
    )

    assert mind_router.try_route_tools(engine, "look something up") is True
    engine._finish_router_response.assert_called_once_with(
        "look something up",
        "Search results here",
        tool="web_search",
    )
