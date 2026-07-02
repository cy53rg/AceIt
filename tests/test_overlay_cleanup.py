"""Overlay widgets must release HWNDs after repeated show/close cycles."""
from __future__ import annotations

import gc

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _process_events(qapp, rounds: int = 3) -> None:
    for _ in range(rounds):
        qapp.processEvents()


def _overlay_count(qapp, cls) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or qapp
    return sum(1 for w in app.topLevelWidgets() if isinstance(w, cls))


def _handle_count() -> int | None:
    try:
        import psutil

        return psutil.Process().num_handles()
    except Exception:
        return None


def _drain_deleted_widgets(qapp, cls, baseline: int, *, max_rounds: int = 300) -> int:
    for _ in range(max_rounds):
        qapp.processEvents()
        count = _overlay_count(qapp, cls)
        if count <= baseline:
            return count
    gc.collect()
    qapp.processEvents()
    return _overlay_count(qapp, cls)


@pytest.mark.parametrize("overlay_cls_name", ["HoloOverlay", "AgentCursorOverlay"])
def test_overlay_show_hide_100_times_stable_handles(qapp, overlay_cls_name):
    """Long sessions toggle visibility on one overlay — handles must stay flat."""
    from atlas_overlay import AgentCursorOverlay, HoloOverlay

    cls = HoloOverlay if overlay_cls_name == "HoloOverlay" else AgentCursorOverlay
    overlay = cls()
    baseline_handles = _handle_count()

    for _ in range(100):
        overlay.show()
        _process_events(qapp)
        overlay.hide()
        _process_events(qapp)

    overlay.close()
    _process_events(qapp, rounds=5)

    after_handles = _handle_count()
    if baseline_handles is not None and after_handles is not None:
        assert after_handles - baseline_handles < 40


@pytest.mark.parametrize("overlay_cls_name", ["HoloOverlay", "AgentCursorOverlay"])
def test_overlay_create_destroy_100_times_no_widget_leak(qapp, overlay_cls_name):
    from atlas_overlay import AgentCursorOverlay, HoloOverlay

    cls = HoloOverlay if overlay_cls_name == "HoloOverlay" else AgentCursorOverlay
    baseline_handles = _handle_count()
    baseline_widgets = _overlay_count(qapp, cls)

    for _ in range(100):
        overlay = cls()
        overlay.show()
        _process_events(qapp)
        overlay.hide()
        overlay.close()
        _process_events(qapp)
        del overlay

    remaining = _drain_deleted_widgets(qapp, cls, baseline_widgets)
    assert remaining == baseline_widgets

    after_handles = _handle_count()
    if baseline_handles is not None and after_handles is not None:
        # Allow modest variance from Qt/OS bookkeeping, not unbounded growth.
        assert after_handles - baseline_handles < 80, (
            f"{overlay_cls_name} handle growth: "
            f"{baseline_handles} -> {after_handles}"
        )


def test_holo_overlay_release_idempotent(qapp):
    from atlas_overlay import HoloOverlay

    overlay = HoloOverlay()
    overlay.show()
    _process_events(qapp)
    overlay._release_overlay_resources()
    overlay._release_overlay_resources()
    assert overlay._overlay_teardown_done is True
    _process_events(qapp)
