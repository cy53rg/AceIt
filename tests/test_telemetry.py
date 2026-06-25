"""Tests for telemetry client."""
from __future__ import annotations

from atlas_telemetry import TelemetryClient, device_fingerprint, APP_VERSION


def test_device_fingerprint_stable():
    a = device_fingerprint()
    b = device_fingerprint()
    assert a == b
    assert len(a) == 32


def test_license_check_fail_open():
    client = TelemetryClient()
    st = client.check_license("test@example.com")
    assert st.allowed is True
    assert client.execution_allowed is True


def test_feedback_stub_saves_locally(monkeypatch):
    client = TelemetryClient()

    def boom(*a, **k):
        raise OSError("network down")

    import atlas_telemetry as tel
    monkeypatch.setattr(tel, "_post_json", boom)
    ok, msg = client.send_feedback("hello world", email="t@t.com")
    assert ok is False
    assert "saved locally" in msg.lower()
