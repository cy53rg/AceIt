"""Post-plan additions — OpenRouter, pre-meeting brief, weekly recap, audit API."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from atlas_glass.pre_meeting import fetch_pre_meeting_context
from atlas_mind.provider_router import ProviderRouter, resolve_openrouter_api_key
from atlas_recap import build_weekly_recap


@pytest.fixture
def memory(tmp_path):
    from atlas_memory import UserMemory

    return UserMemory(str(tmp_path / "post_plan.sqlite3"))


@pytest.fixture
def user_id(memory):
    return memory.create_or_login("post-plan-user")


def test_openrouter_key_env():
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "or-test"}):
        assert resolve_openrouter_api_key() == "or-test"


def test_provider_router_openrouter_fallback(monkeypatch):
    router = ProviderRouter()

    def _fail_groq(self, *a, **k):
        raise RuntimeError("rate limited")

    def _fake_openrouter(self, messages, *, model):
        yield "openrouter "

    def _fail_gemini(self, *a, **k):
        raise RuntimeError("no gemini")

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(ProviderRouter, "_stream_groq", _fail_groq)
    monkeypatch.setattr(ProviderRouter, "_stream_openrouter", _fake_openrouter)
    monkeypatch.setattr(ProviderRouter, "_stream_gemini", _fail_gemini)
    out = "".join(router.stream_chat(messages=[{"role": "user", "content": "hi"}], model="m"))
    assert "openrouter" in out
    assert router.last_provider == "openrouter"


def test_pre_meeting_context_without_calendar():
    engine = MagicMock()
    engine.user_id = 1
    engine.connectors = None
    engine.get_glass_interview_brief.return_value = "Be concise"
    ctx = fetch_pre_meeting_context(engine)
    assert ctx["title"] == "Focus session"
    assert "Be concise" in ctx["brief"]


def test_pre_meeting_context_with_calendar_event(monkeypatch):
    engine = MagicMock()
    engine.user_id = 1
    engine.safety_mode = "off"
    engine.get_glass_interview_brief.return_value = ""
    engine.memory.glass_list_meetings.return_value = []

    cal = MagicMock()
    cal.is_connected.return_value = True
    reg = MagicMock()
    reg.get.return_value = cal
    now = time.time()
    reg.execute.return_value = {
        "ok": True,
        "result": {
            "events": [{
                "summary": "Standup with team",
                "start": "2099-01-01T10:00:00+00:00",
            }],
        },
    }
    engine.connectors = reg

    from datetime import datetime, timezone

    fake_now = datetime(2099, 1, 1, 9, 50, tzinfo=timezone.utc)
    with patch("atlas_glass.pre_meeting.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.fromisoformat = datetime.fromisoformat
        ctx = fetch_pre_meeting_context(engine, window_minutes=45)
    assert ctx["event_title"] == "Standup with team"
    assert ctx["title"] == "Standup with team"


def test_weekly_recap(memory, user_id):
    memory.audit_log(user_id, "goal", "Backed up photos", {})
    memory.goal_create(user_id, "Organize desktop")
    text = build_weekly_recap(memory, user_id)
    assert "Backed up photos" in text
    assert "Organize desktop" in text


def test_recap_route_command():
    from atlas_core import StateEngine

    engine = MagicMock(spec=StateEngine)
    engine.route_command = StateEngine.route_command.__get__(engine, StateEngine)
    assert engine.route_command("/recap")["intent"] == "weekly_recap"
    assert engine.route_command("summarize my week")["intent"] == "weekly_recap"


def test_glass_session_pre_meeting_brief(memory, user_id):
    from atlas_glass.session import GlassSession

    session = GlassSession(memory, user_id)
    session.start(title="Call")
    session.set_pre_meeting_brief("<PreMeetingBrief>Agenda: Q4 review</PreMeetingBrief>")
    block = session.build_context_block("hello")
    assert "Q4 review" in block
