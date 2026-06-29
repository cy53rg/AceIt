"""Regression tests for AtlasStateProxy daemon parity fixes."""
from __future__ import annotations

from unittest.mock import MagicMock

from atlas_audio import AudioWatcher
from atlas_state_proxy import AtlasStateProxy, _LearningProxy, _SessionProxy


def test_mic_routes_forward_when_not_copilot_voice_always_on():
    watcher = AudioWatcher(on_voice_input=lambda _t: None)
    assert watcher.route_transcript("hello", "mic") == "forward"


def test_learning_report_returns_dict():
    owner = MagicMock()
    owner.invoke.return_value = {"total_facts": 3, "turn_count": 2}
    report = _LearningProxy(owner).get_learning_report()
    assert isinstance(report, dict)
    assert report.get("total_facts") == 3


def test_pinned_context_invokes_session_not_global():
    owner = MagicMock()
    sp = _SessionProxy(owner)
    sp.add_pinned_context("doc body", source="context")
    owner.invoke.assert_called_with("session.add_pinned_context", "doc body", "context")


def test_task_running_uses_active_key():
    client = MagicMock()
    proxy = AtlasStateProxy(
        client,
        on_chunk=lambda _t: None,
        on_complete=lambda _t: None,
        on_error=lambda _t: None,
        on_coordinates=lambda _c: None,
        on_token_usage=lambda _u: None,
    )
    proxy._on_ws_message({
        "type": "state_event",
        "event_type": "task_running",
        "payload": {"active": True},
    })
    assert proxy._task_running is True


def test_ssh_manager_assigned_after_creation():
    import pathlib
    lines = pathlib.Path("atlas_daemon.py").read_text(encoding="utf-8").splitlines()
    assign = next(i for i, l in enumerate(lines) if "_state.ssh_manager = _ssh_manager" in l)
    create = next(i for i, l in enumerate(lines) if "_ssh_manager = SSHTargetManager" in l)
    assert assign > create
