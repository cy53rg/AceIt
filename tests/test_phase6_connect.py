"""Phase 6 — Gmail/Notion OAuth, provider router, onboarding."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from atlas_mind.provider_router import ProviderRouter, resolve_groq_api_key


@pytest.fixture
def connector_db(tmp_path, monkeypatch):
    db = tmp_path / "p6.sqlite3"
    monkeypatch.setenv("ATLAS_CONNECTOR_TEST_KEY", "test-fernet-key-for-unit-tests-only!!")
    monkeypatch.setenv("ATLAS_CONNECTOR_SKIP_DPAPI", "1")
    return db


@pytest.fixture
def registry(connector_db):
    from atlas_connectors.registry import ConnectorRegistry

    reg = ConnectorRegistry(db_path=connector_db, user_id=1)
    reg.set_permission_handler(lambda _a, _p: True)
    reg.set_typed_confirm_handler(lambda _m: True)
    return reg


def test_gmail_list_messages(registry, monkeypatch):
    from atlas_connectors.gmail import GmailConnector

    gmail = registry.get("gmail")
    assert isinstance(gmail, GmailConnector)
    gmail._token_store.save_token("gmail", {"access_token": "tok", "expires_at": 9e18})

    def _fake_get(path, *, params=None):
        if path.endswith("/messages"):
            return {"messages": [{"id": "m1"}]}
        return {
            "snippet": "Hello there",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Test"},
                    {"name": "From", "value": "a@b.com"},
                    {"name": "Date", "value": "Mon"},
                ],
            },
        }

    monkeypatch.setattr(gmail, "_api_get", _fake_get)
    result = registry.execute("gmail", "list_messages", safety_mode="off", max_results=5)
    assert result.get("ok") is True
    assert result["result"]["messages"][0]["subject"] == "Test"


def test_notion_search_pages(registry, monkeypatch):
    from atlas_connectors.notion import NotionConnector

    notion = registry.get("notion")
    assert isinstance(notion, NotionConnector)
    notion._token_store.save_token("notion", {"access_token": "secret_test"})

    monkeypatch.setattr(
        notion,
        "_api_post",
        lambda path, payload: {
            "results": [
                {
                    "object": "page",
                    "id": "p1",
                    "url": "https://notion.so/p1",
                    "properties": {
                        "Name": {"type": "title", "title": [{"plain_text": "Tasks"}]},
                    },
                },
            ],
        },
    )
    result = registry.execute(
        "notion",
        "search_pages",
        safety_mode="off",
        query="tasks",
    )
    assert result.get("ok") is True
    assert result["result"]["pages"][0]["title"] == "Tasks"


def test_notion_connect_with_token(registry, monkeypatch):
    from atlas_connectors.notion import NotionConnector

    monkeypatch.setenv("NOTION_TOKEN", "secret_internal")
    notion = registry.get("notion")
    ok, msg = notion.connect()
    assert ok is True
    assert notion.is_connected()


def test_provider_router_groq_first(monkeypatch):
    router = ProviderRouter()

    def _fake_groq(self, messages, *, model, reasoning_effort):
        yield "hello"
        yield " world"

    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setattr(ProviderRouter, "_stream_groq", _fake_groq)
    out = "".join(router.stream_chat(messages=[{"role": "user", "content": "hi"}], model="m"))
    assert out == "hello world"
    assert router.last_provider == "groq"


def test_provider_router_falls_back_to_ollama(monkeypatch):
    router = ProviderRouter()

    def _fail_groq(self, *a, **k):
        raise RuntimeError("groq down")

    def _fake_ollama(self, messages):
        yield "offline answer"

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(ProviderRouter, "_stream_groq", _fail_groq)
    monkeypatch.setattr(ProviderRouter, "_stream_ollama", _fake_ollama)
    out = "".join(router.stream_chat(messages=[{"role": "user", "content": "hi"}], model="m"))
    assert "offline" in out
    assert router.last_provider == "ollama"


def test_resolve_groq_api_key_from_prefs():
    engine = MagicMock()
    engine.get_user_prefs.return_value = {"groq_api_key": "gsk_pref"}
    with patch.dict("os.environ", {}, clear=True):
        assert resolve_groq_api_key(engine) == "gsk_pref"


def test_gmail_router_tool(registry, monkeypatch):
    from atlas_mind.router import execute_tool

    engine = MagicMock()
    engine.safety_mode = "off"
    engine._fs_access_active = False
    engine.execution_blocked = False
    engine.connectors = registry
    gmail = registry.get("gmail")
    gmail._token_store.save_token("gmail", {"access_token": "tok", "expires_at": 9e18})

    def _fake_get(path, *, params=None):
        if path.endswith("/messages"):
            return {"messages": [{"id": "m1"}]}
        return {
            "snippet": "Hi",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Hi"},
                    {"name": "From", "value": "x@y.com"},
                ],
            },
        }

    monkeypatch.setattr(gmail, "_api_get", _fake_get)
    msg = execute_tool(engine, "gmail_list_messages", {"max_results": 5})
    assert "Hi" in msg


def test_onboarding_flag():
    from atlas_onboarding import should_show_onboarding

    engine = MagicMock()
    engine.get_user_prefs.return_value = {}
    assert should_show_onboarding(engine=engine) is True
    engine.get_user_prefs.return_value = {"onboarding_complete": True}
    assert should_show_onboarding(engine=engine) is False
