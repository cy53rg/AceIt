"""Adaptive IPC timeouts for large daemon payloads."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from atlas_ipc import DaemonClient, _compute_ipc_timeout


def _mock_health_session(session_cls):
    session = session_cls.return_value
    health_resp = MagicMock()
    health_resp.status_code = 200
    health_resp.json.return_value = {"ok": True, "protocol_version": 1}
    session.get.return_value = health_resp
    return session


def test_compute_ipc_timeout_scales_with_payload_size():
    small = {"text": "hello"}
    large = {"screen_b64": "A" * (6 * 1024 * 1024)}

    small_timeout, small_mb = _compute_ipc_timeout(small)
    large_timeout, large_mb = _compute_ipc_timeout(large)

    assert small_timeout >= 30.0
    assert large_mb > 5.0
    assert large_timeout >= 80.0
    assert large_timeout > small_timeout


def test_post_uses_adaptive_timeout_for_large_payload():
    with patch("requests.Session") as session_cls:
        session = _mock_health_session(session_cls)
        client = DaemonClient(auto_connect=False)
        body = {
            "text": "describe my screen",
            "source": "user",
            "screen_b64": "B" * (6 * 1024 * 1024),
        }
        expected_timeout, _ = _compute_ipc_timeout(body)
        captured: dict[str, float] = {}

        response = MagicMock()
        response.status_code = 200
        response.content = b'{"ok": true}'
        response.json.return_value = {"ok": True}

        with patch.object(session, "post", return_value=response) as mock_post:
            client._post("/api/handle_input", body)
            captured["timeout"] = mock_post.call_args.kwargs["timeout"]

    assert captured["timeout"] == expected_timeout
    assert captured["timeout"] >= 80.0


def test_request_waits_within_adaptive_timeout():
    with patch("requests.Session") as session_cls:
        _mock_health_session(session_cls)
        client = DaemonClient(auto_connect=False)
        payload = {"type": "permission_request", "blob": "C" * (6 * 1024 * 1024)}
        expected_timeout, _ = _compute_ipc_timeout({**payload, "id": "test-id"})
        assert expected_timeout >= 80.0

        with patch.object(client, "send_ws") as mock_send:
            def _respond(*_args, **_kwargs):
                for req_id, ev in list(client._pending.items()):
                    client._responses[req_id] = {
                        "type": "permission_response",
                        "id": req_id,
                        "approved": True,
                    }
                    ev.set()

            mock_send.side_effect = _respond
            resp = client.request(payload)

    assert resp.get("approved") is True
    assert not resp.get("timeout")
