"""Stealth capture exclusion must cover child QMainWindow / QDialog HWNDs."""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_apply_capture_exclusion_to_widget_includes_child_dialogs(qapp, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMainWindow

    from atlas_overlay import apply_capture_exclusion_to_widget

    calls: list[tuple[int, bool]] = []

    def _record(hwnd_int: int, enable: bool = True) -> bool:
        calls.append((hwnd_int, enable))
        return True

    monkeypatch.setattr("atlas_overlay.apply_capture_exclusion", _record)

    main = QMainWindow()
    dialog = QDialog(main)
    main.show()
    dialog.show()
    qapp.processEvents()

    main_hwnd = int(main.winId())
    dialog_hwnd = int(dialog.winId())

    apply_capture_exclusion_to_widget(main, True)

    enabled_hwnds = [hwnd for hwnd, enabled in calls if enabled]
    assert main_hwnd in enabled_hwnds
    assert dialog_hwnd in enabled_hwnds

    apply_capture_exclusion_to_widget(main, False)

    disabled = [hwnd for hwnd, enabled in calls if not enabled]
    assert main_hwnd in disabled
    assert dialog_hwnd in disabled


def test_overlay_set_stealth_applies_to_widget_tree(qapp, monkeypatch):
    from atlas_overlay import HoloOverlay

    calls: list[tuple[int, bool]] = []

    def _record(hwnd_int: int, enable: bool = True) -> bool:
        calls.append((hwnd_int, enable))
        return True

    monkeypatch.setattr("atlas_overlay.apply_capture_exclusion", _record)

    overlay = HoloOverlay()
    overlay.show()
    qapp.processEvents()
    overlay_hwnd = int(overlay.winId())

    overlay.set_stealth(True)
    assert any(hwnd == overlay_hwnd and enabled for hwnd, enabled in calls)

    overlay.set_stealth(False)
    assert any(hwnd == overlay_hwnd and not enabled for hwnd, enabled in calls)


def test_set_stealth_reapplies_on_show_event(qapp, monkeypatch):
    from atlas_overlay import HoloOverlay

    calls: list[bool] = []

    def _record(_hwnd_int: int, enable: bool = True) -> bool:
        calls.append(enable)
        return True

    monkeypatch.setattr("atlas_overlay.apply_capture_exclusion", _record)

    overlay = HoloOverlay()
    overlay.set_stealth(True)
    overlay.show()
    qapp.processEvents()

    assert calls and calls[-1] is True
