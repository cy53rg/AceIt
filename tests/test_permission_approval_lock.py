"""Regression: permission approval must not race across concurrent sources."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_permission_dialog_approve_runs_once(qapp):
    from atlas_ui import PermissionDialog

    calls: list[str] = []
    done = threading.Event()

    def approve() -> None:
        calls.append("approve")
        done.set()

    def deny() -> None:
        calls.append("deny")

    with patch.object(PermissionDialog, "accept", lambda self: None), patch.object(
        PermissionDialog, "reject", lambda self: None
    ):
        dlg = PermissionDialog("WRITE", "/tmp/test.txt", approve, deny)
        threads = [
            threading.Thread(target=dlg._on_approve, daemon=True),
            threading.Thread(target=dlg._on_approve, daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)
        done.wait(timeout=2.0)

    assert calls == ["approve"]


def test_concurrent_permission_requests_serialize(qapp):
    from atlas_ui import AtlasWindow

    gate = threading.Barrier(2)
    approve_count = {"n": 0}
    deny_count = {"n": 0}
    entered = {"n": 0}

    win = MagicMock(spec=AtlasWindow)
    win._approval_lock = threading.Lock()
    win._permission_dialog_open = False
    win._show_permission_dialog = AtlasWindow._show_permission_dialog.__get__(win, AtlasWindow)

    def approve() -> None:
        approve_count["n"] += 1

    def deny() -> None:
        deny_count["n"] += 1

    def slow_show(action, path, approve_fn, deny_fn):
        with win._approval_lock:
            if win._permission_dialog_open:
                deny_fn()
                return
            win._permission_dialog_open = True
            entered["n"] += 1
        try:
            time.sleep(0.15)
            approve_fn()
        finally:
            with win._approval_lock:
                win._permission_dialog_open = False

    win._show_permission_dialog = slow_show

    def worker() -> None:
        gate.wait()
        win._show_permission_dialog("WRITE", "/tmp/a", approve, deny)

    t1 = threading.Thread(target=worker, daemon=True)
    t2 = threading.Thread(target=worker, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert entered["n"] == 1
    assert approve_count["n"] == 1
    assert deny_count["n"] == 1
