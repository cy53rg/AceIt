"""Interaction pipeline helpers — friendly errors and screen gather."""

from __future__ import annotations

from atlas_interaction import friendly_error, gather_screen_frame


def test_friendly_error_kinds():
    assert "last reply" in friendly_error("busy").lower()
    assert "API" in friendly_error("api") or "service" in friendly_error("api").lower()
    assert friendly_error("ocr", "timeout")


def test_gather_screen_frame_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("no display")

    monkeypatch.setattr(
        "atlas_vision.capture_screen_b64",
        _boom,
        raising=False,
    )
    b64, summary = gather_screen_frame()
    assert b64 is None
    assert summary == ""
