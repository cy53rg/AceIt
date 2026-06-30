"""
atlas_onboarding.py — First-run setup wizard (API keys, mic, connectors, file index).
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class OnboardingWizard(QDialog):
    """Multi-step first-run wizard shown once per user."""

    def __init__(
        self,
        *,
        engine: Any = None,
        daemon_client: Any = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.daemon_client = daemon_client
        self.setWindowTitle("Welcome to Atlas")
        self.setMinimumWidth(480)
        self._stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(self._stack)

        self._stack.addWidget(self._page_welcome())
        self._stack.addWidget(self._page_api_key())
        self._stack.addWidget(self._page_mic())
        self._stack.addWidget(self._page_connectors())
        self._stack.addWidget(self._page_files())
        self._stack.addWidget(self._page_done())

        nav = QHBoxLayout()
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._go_back)
        self._next_btn = QPushButton("Next")
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._back_btn)
        nav.addStretch()
        nav.addWidget(self._next_btn)
        lay.addLayout(nav)
        self._update_nav()

    def _page_welcome(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<h2>Welcome to Atlas</h2>"))
        lay.addWidget(QLabel(
            "This quick setup covers your AI key, microphone, optional connectors, "
            "and file search roots. You can change everything later in Settings."
        ))
        return w

    def _page_api_key(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>Groq API key</b>"))
        has_key = bool((os.environ.get("GROQ_API_KEY") or "").strip())
        lay.addWidget(QLabel(
            "Groq powers Atlas chat and vision."
            + (" Your .env already has a key." if has_key else " Paste a key from console.groq.com")
        ))
        self._groq_input = QLineEdit()
        self._groq_input.setPlaceholderText("gsk_…")
        self._groq_input.setEchoMode(QLineEdit.Password)
        if has_key:
            self._groq_input.setPlaceholderText("Already configured in .env")
        lay.addWidget(self._groq_input)
        return w

    def _page_mic(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>Microphone</b>"))
        lay.addWidget(QLabel(
            "Atlas uses your mic for voice commands and interview mode. "
            "Allow microphone access when Windows prompts you."
        ))
        self._mic_ok = QCheckBox("My microphone is working")
        lay.addWidget(self._mic_ok)
        return w

    def _page_connectors(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>Connect accounts (optional)</b>"))
        lay.addWidget(QLabel(
            "Connect services you want Atlas to use. Requires OAuth credentials in .env."
        ))
        row = QHBoxLayout()
        self._btn_cal = QPushButton("Google Calendar")
        self._btn_gmail = QPushButton("Gmail")
        self._btn_notion = QPushButton("Notion")
        self._btn_cal.clicked.connect(lambda: self._connect("google_calendar"))
        self._btn_gmail.clicked.connect(lambda: self._connect("gmail"))
        self._btn_notion.clicked.connect(lambda: self._connect("notion"))
        row.addWidget(self._btn_cal)
        row.addWidget(self._btn_gmail)
        row.addWidget(self._btn_notion)
        lay.addLayout(row)
        self._connector_status = QLabel("")
        lay.addWidget(self._connector_status)
        return w

    def _page_files(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<b>File search roots</b>"))
        lay.addWidget(QLabel(
            "Optional: folders Atlas should index (one per line). "
            "Leave blank to configure later in Settings → Filesystem."
        ))
        self._index_roots = QTextEdit()
        home = os.path.expanduser("~")
        self._index_roots.setPlaceholderText(f"{home}\\Documents\n{home}\\Desktop")
        self._index_roots.setMaximumHeight(100)
        lay.addWidget(self._index_roots)
        return w

    def _page_done(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<h3>You're ready</h3>"))
        lay.addWidget(QLabel(
            "Press Finish to start using Atlas. Use Focus mode for interviews "
            "and Ctrl+Shift+H to highlight questions."
        ))
        return w

    def _go_back(self) -> None:
        idx = self._stack.currentIndex()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._update_nav()

    def _go_next(self) -> None:
        idx = self._stack.currentIndex()
        if idx == 1:
            self._save_groq_key()
        if idx == 4:
            self._save_index_roots()
        if idx >= self._stack.count() - 1:
            self._mark_complete()
            self.accept()
            return
        self._stack.setCurrentIndex(idx + 1)
        self._update_nav()

    def _update_nav(self) -> None:
        idx = self._stack.currentIndex()
        self._back_btn.setEnabled(idx > 0)
        self._next_btn.setText("Finish" if idx >= self._stack.count() - 1 else "Next")

    def _save_groq_key(self) -> None:
        key = self._groq_input.text().strip()
        if not key:
            return
        os.environ["GROQ_API_KEY"] = key
        if self.engine:
            self.engine.set_user_pref("groq_api_key", key)
        elif self.daemon_client:
            self.daemon_client._post("/api/invoke", {
                "method": "set_user_pref",
                "args": ["groq_api_key", key],
                "kwargs": {},
            })

    def _save_index_roots(self) -> None:
        raw = self._index_roots.toPlainText().strip()
        if not raw:
            return
        roots = [line.strip() for line in raw.splitlines() if line.strip()]
        if not roots:
            return
        if self.daemon_client:
            try:
                self.daemon_client._post("/api/files/index/roots", {"roots": roots})
            except Exception as exc:
                QMessageBox.warning(self, "Index roots", str(exc))

    def _connect(self, connector_id: str) -> None:
        if self.daemon_client:
            try:
                result = self.daemon_client._post(
                    f"/api/connectors/{connector_id}/connect",
                    {},
                )
                msg = result.get("message") or str(result)
                self._connector_status.setText(msg)
            except Exception as exc:
                self._connector_status.setText(str(exc))
        else:
            self._connector_status.setText("Daemon not connected.")

    def _mark_complete(self) -> None:
        if self.engine:
            self.engine.set_user_pref("onboarding_complete", True)
        elif self.daemon_client:
            try:
                self.daemon_client._post("/api/invoke", {
                    "method": "set_user_pref",
                    "args": ["onboarding_complete", True],
                    "kwargs": {},
                })
            except Exception:
                pass


def should_show_onboarding(engine: Any = None, daemon_client: Any = None) -> bool:
    if engine:
        return not bool(engine.get_user_prefs().get("onboarding_complete"))
    if daemon_client:
        try:
            data = daemon_client._post("/api/invoke", {
                "method": "get_user_prefs",
                "args": [],
                "kwargs": {},
            })
            prefs = data.get("result") or data.get("data") or {}
            return not bool(prefs.get("onboarding_complete"))
        except Exception:
            return False
    return False


def run_onboarding_if_needed(
    *,
    engine: Any = None,
    daemon_client: Any = None,
    parent: Optional[QWidget] = None,
) -> bool:
    """Show wizard when needed. Returns True if wizard was shown."""
    if not should_show_onboarding(engine, daemon_client):
        return False
    dlg = OnboardingWizard(engine=engine, daemon_client=daemon_client, parent=parent)
    dlg.exec()
    return True
