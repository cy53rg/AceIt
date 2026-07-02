"""Daemon/UI protocol version negotiation."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from atlas_ipc import DaemonClient, DaemonError


def _mock_health_response(protocol_version: int | None, *, ok: bool = True) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    body = {"ok": ok}
    if protocol_version is not None:
        body["protocol_version"] = protocol_version
    response.json.return_value = body
    return response


def test_health_returns_protocol_version_from_daemon():
    with patch("requests.Session") as session_cls:
        session = session_cls.return_value
        session.get.return_value = _mock_health_response(1)
        client = DaemonClient(auto_connect=False)
        data = client.health()

    assert data is not None
    assert data["protocol_version"] == 1


def test_init_accepts_matching_protocol_version():
    with patch("requests.Session") as session_cls:
        session = session_cls.return_value
        session.get.return_value = _mock_health_response(1)
        client = DaemonClient(auto_connect=False)

    assert client.PROTOCOL_VERSION == 1


def test_init_rejects_mismatched_protocol_version():
    with patch("requests.Session") as session_cls:
        session = session_cls.return_value
        session.get.return_value = _mock_health_response(2)
        with pytest.raises(DaemonError, match="Protocol version mismatch"):
            DaemonClient(auto_connect=False)


def test_init_skips_protocol_check_when_daemon_unreachable():
    with patch("requests.Session") as session_cls:
        session = session_cls.return_value
        session.get.side_effect = OSError("connection refused")
        client = DaemonClient(auto_connect=False)

    assert client.health() is None


def test_init_rejects_old_daemon_without_protocol_version():
    """New UI against old daemon (no protocol_version field) must fail loudly."""
    with patch("requests.Session") as session_cls:
        session = session_cls.return_value
        session.get.return_value = _mock_health_response(None)
        with pytest.raises(DaemonError, match="Protocol version mismatch"):
            DaemonClient(auto_connect=False)


def test_old_ui_against_new_daemon_raises_clear_error():
    """Old UI (protocol v1) against daemon reporting v2 must not fail silently."""
    with patch("requests.Session") as session_cls:
        session = session_cls.return_value
        session.get.return_value = _mock_health_response(2)
        with pytest.raises(DaemonError, match="Protocol version mismatch"):
            DaemonClient(auto_connect=False)


def test_verify_protocol_response_raises_clear_error():
    with patch("requests.Session") as session_cls:
        session = session_cls.return_value
        session.get.side_effect = OSError("connection refused")
        client = DaemonClient(auto_connect=False)

    with pytest.raises(DaemonError, match="Protocol version mismatch"):
        client._verify_protocol_response({"ok": True, "protocol_version": 99})
