"""
atlas_ui.py — Atlas Frontend (PySide6)  ·  Phase 1 Upgrade
==============================================================
Fully integrated with atlas_core.py.

Phase 1 Architectural Changes
------------------------------
1.  Updated Imports          — all backend symbols from atlas_core (not aceit_core).
2.  Unified Control Center   — single ControlPanel sidebar: Model, Style, Sensitivity,
                               Audio toggles, Camera toggle, FileSystem toggle.
3.  Dynamic Accent Engine    — property-driven accent; cyan idle, red Stealth, gold processing.
4.  Webcam Vision (cv2)      — Camera toggle; silent frame capture on each query via cv2;
                               webcam_b64 passed to state.handle_input().
5.  Complete Stealth Matrix  — WDA_EXCLUDEFROMCAPTURE applied to QMainWindow,
                               FloatBubble, and PillNotification HWNDs.
6.  Ghost UI                 — FloatBubble resized to 55×55; QVariantAnimation opacity
                               loop: idle → 0.15, hovered/active → 1.0.
7.  Permission Interceptor   — Custom PermissionDialog registered with
                               atlas_fs.register_permission_callback(); Accept / Deny
                               buttons trigger the respective closures.
8.  Voice Engine Hook        — voice_engine.speak(full_ans) called on stream completion;
                               "Skip Audio" button calls voice_engine.skip().
"""
from __future__ import annotations

# ── DPI Awareness — MUST be set before any Qt or third-party import ────────────
import os, sys, platform
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

import base64
import io as _io
import json
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

# Desktop Integration
import keyboard
import pyperclip

from PySide6.QtCore import (
    Qt, QPoint, QSize, QPropertyAnimation, QVariantAnimation, QParallelAnimationGroup,
    QEasingCurve, QRect,
    QRectF, QPointF, QTimer, Signal, QObject, Slot, QThread, Property, QUrl, QStringListModel,
    QEvent,
)
from PySide6.QtGui import (
    QColor, QFont, QIcon, QTextCursor, QPainter, QPen, QBrush, QAction, QDragEnterEvent, QDropEvent,
    QRadialGradient, QCursor, QKeyEvent, QMouseEvent, QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame,
    QHBoxLayout, QVBoxLayout, QTextEdit, QTextBrowser, QLineEdit,
    QPushButton, QLabel, QSizePolicy, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QDialog, QSlider, QComboBox, QTabWidget, QScrollArea,
    QListWidget, QListWidgetItem, QStackedWidget, QCheckBox,
    QProgressBar, QFileDialog, QMenu, QCompleter, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QGridLayout, QToolButton, QSizeGrip,
)

# ── Markdown renderer ─────────────────────────────────────────────────────────
try:
    from markdown_it import MarkdownIt
    _md = MarkdownIt()
    HAS_MARKDOWN_IT = True
except ImportError:
    _md = None
    HAS_MARKDOWN_IT = False

# ── Atlas Core ────────────────────────────────────────────────────────────────
try:
    from atlas_core import (
        ModeState, StateEngine, AudioEngine,
        groq_client, GROQ_MODEL, GROQ_MODELS, GROQ_MODEL_LABELS,
        RESPONSE_STYLES, voice_engine, atlas_fs, atlas_hands,
        step_orchestrator,
    )
    import atlas_core as _core_mod
    _CORE = True
    _INTERVIEW_MODE = ModeState.INTERVIEW
except ImportError:
    _CORE = False
    _INTERVIEW_MODE = None
    voice_engine = None
    atlas_fs = None
    atlas_hands = None

# ── OpenCV for webcam capture ─────────────────────────────────────────────────
try:
    import cv2 as _cv2
    HAS_CV2 = True
except ImportError:
    _cv2 = None
    HAS_CV2 = False

try:
    from atlas_overlay import HoloOverlay, AgentCursorOverlay, TaskStopOverlay
    HAS_OVERLAY = True
except ImportError:
    HoloOverlay = None  # type: ignore
    AgentCursorOverlay = None  # type: ignore
    TaskStopOverlay = None  # type: ignore
    HAS_OVERLAY = False

try:
    from atlas_telemetry import TelemetryClient, APP_VERSION
    HAS_TELEMETRY = True
except Exception:
    TelemetryClient = None  # type: ignore
    APP_VERSION = "0.9.0-mvp"
    HAS_TELEMETRY = False

try:
    from atlas_logging import setup_logging, install_thread_exception_hook, get_logger
    setup_logging()
    HAS_LOGGING = True
except Exception:
    HAS_LOGGING = False
    def get_logger(_n):  # type: ignore
        import logging
        return logging.getLogger(_n)

try:
    from atlas_accounts import AccountManager
    HAS_ACCOUNTS = True
except Exception:
    AccountManager = None  # type: ignore
    HAS_ACCOUNTS = False

try:
    from atlas_ipc import DaemonClient, AccountClient, ensure_daemon_running, DaemonError
    from atlas_state_proxy import AtlasStateProxy
    HAS_DAEMON = True
except Exception:
    DaemonClient = None  # type: ignore
    AccountClient = None  # type: ignore
    ensure_daemon_running = None  # type: ignore
    AtlasStateProxy = None  # type: ignore
    DaemonError = RuntimeError  # type: ignore
    HAS_DAEMON = False

AUTOSAVE_PATH = Path.home() / ".atlas" / "autosave.json"


# ═════════════════════════════════════════════════════════════════════════════
# DYNAMIC ACCENT ENGINE
# ═════════════════════════════════════════════════════════════════════════════

class AccentEngine:
    """
    Central source-of-truth for the UI's accent colour.

    States
    ------
    idle        — high-tech cyan (#00D4FF)
    stealth     — glowing red    (#FF2D55)
    processing  — warm gold      (#D4AF37)

    Call set_state() to transition; accent returns the current hex colour.
    """

    IDLE       = "#00D4FF"
    STEALTH    = "#FF2D55"
    PROCESSING = "#D4AF37"
    PTT        = "#FF2D55"
    ERROR      = "#FF3B30"
    IDLE_DIM   = "#007A93"
    STEALTH_DIM = "#8B0020"
    PROC_DIM   = "#8B7220"

    def __init__(self) -> None:
        self._state = "idle"

    def set_state(self, state: str) -> None:
        """state: 'idle' | 'stealth' | 'processing'"""
        if state in ("idle", "stealth", "processing", "ptt", "error"):
            self._state = state

    @property
    def accent(self) -> str:
        return {
            "idle":       self.IDLE,
            "stealth":    self.STEALTH,
            "processing": self.PROCESSING,
            "ptt":        self.PTT,
            "error":      self.ERROR,
        }[self._state]

    @property
    def dim(self) -> str:
        return {
            "idle":       self.IDLE_DIM,
            "stealth":    self.STEALTH_DIM,
            "processing": self.PROC_DIM,
            "ptt":        self.STEALTH_DIM,
            "error":      self.STEALTH_DIM,
        }[self._state]

    @property
    def state(self) -> str:
        return self._state


accent_engine = AccentEngine()


# ═════════════════════════════════════════════════════════════════════════════
# THEME PALETTE
# ═════════════════════════════════════════════════════════════════════════════

PAL = {
    "bg":        "#050505",   # deep cyber-black backdrop
    "surface":   "#0E0E10",   # surface panels
    "surface_2": "#141418",   # raised surface / inputs
    "border":    "#1A1A1F",   # hairline borders
    "gold":      "#D4AF37",
    "gold_dim":  "#8B7220",
    "cyan":      "#00D4FF",
    "cyan_dim":  "#007A93",
    "text":      "#E8EDF2",
    "muted":     "#6B7A8D",
    "danger":    "#FF2D55",
    "danger_dim":"#8B0020",
    "success":   "#2ECC8A",
    "proactive": "#D4AF37",   # Copilot alerts — same gold family as accent
}

from atlas_settings_ui import SettingsDialog, settings_dialog_qss

# Re-export for callers that import SettingsDialog from atlas_ui
__all_settings__ = ('SettingsDialog',)


try:
    from pygments import highlight as _pygments_highlight
    from pygments.lexers import get_lexer_by_name, TextLexer
    from pygments.formatters import HtmlFormatter
    HAS_PYGMENTS = True
except ImportError:
    _pygments_highlight = None  # type: ignore
    get_lexer_by_name = None  # type: ignore
    TextLexer = None  # type: ignore
    HtmlFormatter = None  # type: ignore
    HAS_PYGMENTS = False

QSS_BASE = f"""
QWidget {{ background: transparent; color: {PAL['text']}; font-family: 'Segoe UI'; font-size: 12px; cursor: default; }}
QFrame#titlebar {{ background: {PAL['surface']}; border-radius: 14px; border: 1px solid {PAL['border']}; cursor: default; }}
QFrame#floating_header {{ background: {PAL['surface']}; border-radius: 14px; border: 1px solid {PAL['border']}; cursor: default; }}
QFrame#orbzone {{ background: transparent; cursor: default; }}
QFrame#workspace {{ background: {PAL['surface']}; border-radius: 14px; border: 1px solid {PAL['border']}; cursor: default; }}
QFrame#action_dock {{ background: {PAL['surface_2']}; border-radius: 12px; border: 1px solid {PAL['border']}; cursor: default; }}
QPushButton {{ background: transparent; border: none; cursor: pointer; }}
QPushButton:hover {{ background: {PAL['surface_2']}; border-radius: 6px; }}
QPushButton#dock_btn {{ font-size: 14px; background: {PAL['surface_2']}; border-radius: 6px; }}
QPushButton#dock_btn:hover {{ background: {PAL['border']}; color: {PAL['cyan']}; }}
QPushButton#dock_btn[active="true"] {{ background: rgba(0,212,255,0.15); color: {PAL['cyan']}; }}
QLineEdit {{ background: transparent; border: none; padding: 8px; color: {PAL['text']}; font-size: 13px; }}
QTextEdit, QTextBrowser {{ background: transparent; border: none; padding: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {PAL['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {PAL['cyan_dim']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {PAL['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {PAL['cyan']}; width: 12px; margin: -4px 0; border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: {PAL['text']}; }}
QToolTip {{ background: {PAL['surface_2']}; color: {PAL['text']}; border: 1px solid {PAL['border']}; padding: 4px 8px; border-radius: 6px; }}
"""


# ═════════════════════════════════════════════════════════════════════════════
# SIGNAL BRIDGE
# ═════════════════════════════════════════════════════════════════════════════

class SignalBridge(QObject):
    append_text        = Signal(str)
    set_status         = Signal(str)
    notify_pill        = Signal(str)
    start_thinking     = Signal()
    thinking_done      = Signal()
    stream_token       = Signal(str)
    stream_started     = Signal()
    stream_complete    = Signal(str)
    spatial_coords     = Signal(dict)
    token_usage        = Signal(dict)
    ptt_active         = Signal(bool)
    ptt_breakin        = Signal()
    listen_state       = Signal(str)
    request_permission = Signal(str, str, object, object)
    accent_changed     = Signal(str)
    step_pending       = Signal(object)
    user_notice        = Signal(str, str)   # title, message
    interaction_error  = Signal(str)
    safety_prompt_req  = Signal(str)
    task_stop          = Signal()
    guide_step_started = Signal(int)
    step_verified      = Signal(int, bool, str)


# ═════════════════════════════════════════════════════════════════════════════
# PERMISSION INTERCEPTOR DIALOG  (Requirement 7)
# ═════════════════════════════════════════════════════════════════════════════

class PermissionDialog(QDialog):
    """
    Shown whenever atlas_fs needs WRITE or EXECUTE access.

    Registered via atlas_fs.register_permission_callback() in AtlasWindow.__init__.
    The callback fires on whatever thread called the FS method; we marshal to the
    main thread through SignalBridge.request_permission before touching Qt.
    """

    def __init__(self, action: str, path: str,
                 approve_fn: Callable, deny_fn: Callable,
                 parent=None):
        super().__init__(parent)
        self._approve_fn = approve_fn
        self._deny_fn    = deny_fn

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(460, 200)

        chrome = QFrame(self)
        chrome.setGeometry(0, 0, 460, 200)
        chrome.setStyleSheet(
            f"background: {PAL['surface']};"
            f"border: 1px solid {PAL['danger']};"
            f"border-radius: 12px;"
        )

        lay = QVBoxLayout(chrome)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        # Icon + title
        title_row = QHBoxLayout()
        icon_lbl = QLabel("⚠")
        icon_lbl.setStyleSheet(f"color: {PAL['danger']}; font-size: 22px; background: transparent;")
        title_row.addWidget(icon_lbl)
        title_lbl = QLabel(f"Permission Request: {action.upper()}")
        title_lbl.setStyleSheet(
            f"color: {PAL['danger']}; font-size: 13px; font-weight: bold; background: transparent;"
        )
        title_row.addWidget(title_lbl, 1)
        lay.addLayout(title_row)

        # Path
        path_lbl = QLabel(f"<b>Path:</b> {path}")
        path_lbl.setStyleSheet(f"color: {PAL['text']}; font-size: 11px; background: transparent;")
        path_lbl.setWordWrap(True)
        lay.addWidget(path_lbl)

        desc_lbl = QLabel(
            "Atlas is requesting elevated file-system access. "
            "Approve only if you initiated this operation."
        )
        desc_lbl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
        desc_lbl.setWordWrap(True)
        lay.addWidget(desc_lbl)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_deny = QPushButton("✕  Deny")
        btn_deny.setFixedSize(110, 32)
        btn_deny.setStyleSheet(
            f"QPushButton {{ background: {PAL['surface_2']}; color: {PAL['muted']};"
            f"  border: 1px solid {PAL['border']}; border-radius: 6px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {PAL['border']}; color: {PAL['text']}; }}"
        )
        btn_deny.clicked.connect(self._on_deny)
        btn_row.addWidget(btn_deny)

        btn_row.addSpacing(10)

        btn_approve = QPushButton("✓  Approve")
        btn_approve.setFixedSize(110, 32)
        btn_approve.setStyleSheet(
            f"QPushButton {{ background: {PAL['danger']}; color: #ffffff;"
            f"  border-radius: 6px; font-weight: bold; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {PAL['danger_dim']}; }}"
        )
        btn_approve.clicked.connect(self._on_approve)
        btn_row.addWidget(btn_approve)

        lay.addLayout(btn_row)

    def _on_approve(self):
        self.accept()
        threading.Thread(target=self._approve_fn, daemon=True).start()

    def _on_deny(self):
        self.reject()
        self._deny_fn()


class TypedConfirmDialog(QDialog):
    """
    Required for CRITICAL policy actions (money, delete, deauth, unlisted shell).

    The user must type the exact phrase — Approve stays disabled until it matches.
    """

    def __init__(self, policy_result, path: str, parent=None):
        super().__init__(parent)
        self._expected = (getattr(policy_result, "confirm_phrase", "") or "").strip()
        self._approved = False

        self.setWindowTitle("Typed confirmation required")
        self.setMinimumWidth(480)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        title = QLabel("This action requires typed confirmation")
        title.setStyleSheet(
            f"color: {PAL['danger']}; font-weight: bold; font-size: 13px;"
        )
        lay.addWidget(title)

        reason = getattr(policy_result, "reason", "") or ""
        if reason:
            r_lbl = QLabel(reason)
            r_lbl.setWordWrap(True)
            r_lbl.setStyleSheet(f"color: {PAL['text']}; font-size: 11px;")
            lay.addWidget(r_lbl)

        path_lbl = QLabel(f"<b>Target:</b> {path}")
        path_lbl.setWordWrap(True)
        lay.addWidget(path_lbl)

        phrase_lbl = QLabel(f"Type exactly: <b>{self._expected}</b>")
        phrase_lbl.setWordWrap(True)
        lay.addWidget(phrase_lbl)

        self._input = QLineEdit()
        self._input.setPlaceholderText(self._expected)
        self._input.textChanged.connect(self._sync_ok)
        lay.addWidget(self._input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        self._ok = QPushButton("Confirm")
        self._ok.setEnabled(False)
        self._ok.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok)
        lay.addLayout(btn_row)

    def _sync_ok(self, text: str) -> None:
        self._ok.setEnabled(text.strip() == self._expected)

    def _on_ok(self) -> None:
        from atlas_policy import validate_typed_confirmation

        typed = self._input.text().strip()
        if typed != self._expected:
            return
        self._approved = True
        self.accept()

    def approved(self) -> bool:
        return self._approved and self._input.text().strip() == self._expected


# ═════════════════════════════════════════════════════════════════════════════
# CHAT VIEW + COMPOSER  (rebuilt message display & input)
# ═════════════════════════════════════════════════════════════════════════════

_CHAT_MD_CSS = f"""
<style>
body {{ color: {PAL['text']}; font-family: 'Segoe UI', sans-serif; font-size: 13px;
        line-height: 1.55; margin: 0; }}
p {{ margin: 0 0 8px 0; }}
ul, ol {{ margin: 4px 0 8px 18px; padding-left: 8px; }}
li {{ margin: 2px 0; }}
hr {{ border: none; border-top: 1px solid {PAL['border']}; margin: 10px 0; }}
strong {{ color: {PAL['gold']}; font-weight: 600; }}
em {{ font-style: italic; }}
code {{ background: {PAL['surface_2']}; border: 1px solid {PAL['border']};
        border-radius: 4px; padding: 1px 5px;
        font-family: Consolas, 'Courier New', monospace; font-size: 12px;
        color: {PAL['cyan']}; }}
pre {{ background: {PAL['bg']}; border: 1px solid {PAL['border']};
       border-radius: 8px; padding: 10px 12px; margin: 6px 0 2px 0;
       font-family: Consolas, 'Courier New', monospace; font-size: 12px;
       line-height: 1.45; white-space: pre-wrap; }}
pre code {{ background: transparent; border: none; padding: 0; color: {PAL['text']}; }}
.code-actions {{ text-align: right; margin: 0 0 6px 0; }}
.code-btn {{ display: inline-block; background: {PAL['surface_2']};
              color: {PAL['cyan']}; border: 1px solid {PAL['cyan_dim']};
              border-radius: 5px; padding: 2px 8px; font-size: 10px;
              text-decoration: none; margin-left: 4px; }}
.stream-cursor {{ color: {PAL['cyan']}; animation: curblink 1s step-end infinite; }}
@keyframes curblink {{ 50% {{ opacity: 0; }} }}
</style>
"""

_SLASH_COMMANDS = [
    ("/guide",     "Start guided step-by-step help"),
    ("/task",      "Run a computer-use task"),
    ("/interview", "Toggle focus / interview mode"),
    ("/copilot",   "Toggle passive screen copilot"),
    ("/memory",    "Show what Atlas remembers"),
    ("/clear",     "Clear the conversation"),
    ("/settings",  "Open Control Center"),
]

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_DOC_EXTS = {".pdf", ".txt", ".md"}


class ChatMarkdown:
    """Markdown → HTML for chat bubbles."""

    @staticmethod
    def render(md_text: str, *, streaming: bool = False) -> str:
        import html as _html
        import re

        text = md_text or ""
        if HAS_MARKDOWN_IT and _md is not None:
            body = _md.render(text)
        else:
            body = "<p>" + _html.escape(text).replace("\n", "<br>") + "</p>"

        def _highlight_block(m: "re.Match") -> str:
            lang = (m.group(1) or "").strip()
            code = m.group(2)
            esc = _html.escape(code)
            if HAS_PYGMENTS and _pygments_highlight:
                try:
                    lexer = get_lexer_by_name(lang, stripall=True) if lang else TextLexer()
                except Exception:
                    lexer = TextLexer()
                fmt = HtmlFormatter(noclasses=True, style="monokai")
                highlighted = _pygments_highlight(code, lexer, fmt)
            else:
                highlighted = f"<pre><code>{esc}</code></pre>"
            token = base64.b64encode(code.encode()).decode()
            lang_token = base64.b64encode(lang.encode()).decode()
            run_link = ""
            if lang.lower() in (
                "bash", "sh", "shell", "zsh", "cmd", "powershell", "python", "py", "python3",
            ):
                run_link = f"<a class='code-btn' href='runcode:{lang_token}:{token}'>▶ Run</a>"
            actions = (
                f"<div class='code-actions'>"
                f"<a class='code-btn' href='copycode:{token}'>⎘ Copy</a>{run_link}</div>"
            )
            return highlighted + actions

        body = re.sub(
            r"<pre><code(?: class=\"language-([^\"]*)\")?>([\s\S]*?)</code></pre>",
            _highlight_block,
            body,
        )
        if streaming:
            body += "<span class='stream-cursor'>▍</span>"
        return _CHAT_MD_CSS + body


class ChatMessageCard(QFrame):
    """Single chat bubble with hover actions."""

    regenerate_requested = Signal()
    read_aloud_requested = Signal(str)
    pin_requested        = Signal(str)

    def __init__(
        self,
        role: str,
        text: str,
        *,
        html_body: str = "",
        ts: Optional[float] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.role = role
        self.text = text
        self.ts = ts or time.time()
        self._step_index: Optional[int] = None
        self.setFrameShape(QFrame.NoFrame)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 4, 16, 4)
        outer.setSpacing(8)

        self._avatar = QLabel("◆" if role == "assistant" else "👁" if role == "proactive" else "")
        self._avatar.setFixedWidth(22)
        self._avatar.setAlignment(Qt.AlignTop)
        if role == "assistant":
            self._avatar.setStyleSheet(
                f"color: {PAL['gold']}; font-weight: bold; font-size: 14px; background: transparent;")
        elif role == "proactive":
            self._avatar.setStyleSheet(
                f"color: {PAL['proactive']}; font-weight: bold; background: transparent;")
        else:
            self._avatar.hide()

        self._actions = QHBoxLayout()
        self._actions.addStretch(1)
        if role == "assistant":
            self._add_action("⎘", "Copy message", self._copy_self)
            self._add_action("↻", "Regenerate", lambda: self.regenerate_requested.emit())
            self._add_action("🔊", "Read aloud", lambda: self.read_aloud_requested.emit(self.text))
            self._add_action("📌", "Add to memory", lambda: self.pin_requested.emit(self.text))
        elif role == "user":
            self._add_action("⎘", "Copy message", self._copy_self)
        elif role in ("proactive",):
            self._add_action("⎘", "Copy", self._copy_self)
            self._add_action("🔊", "Read aloud", lambda: self.read_aloud_requested.emit(self.text))

        if role == "user":
            self._body = QLabel()
            self._body.setWordWrap(True)
            self._body.setText(text or "")
            self._body.setStyleSheet(
                f"color: {PAL['bg']}; font-family: Segoe UI, sans-serif; "
                f"font-size: 13px; line-height: 1.5; background: transparent; border: none;"
            )
            self._body.setTextInteractionFlags(
                Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
            )
        else:
            self._body = QTextBrowser()
            self._body.setOpenExternalLinks(False)
            self._body.setFrameShape(QFrame.NoFrame)
            self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._body.setTextInteractionFlags(
                Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard | Qt.LinksAccessibleByMouse)
            self._body.document().setDocumentMargin(0)
            self._body.setStyleSheet("background: transparent; border: none;")
            self._body.setHtml(html_body or ChatMarkdown.render(text))
            self._body.document().contentsChanged.connect(self._fit_body_height)

        if role == "user":
            outer.addStretch(1)
            card = QFrame()
            card.setMaximumWidth(440)
            card.setStyleSheet(
                f"background: {PAL['cyan']}; color: {PAL['bg']}; "
                f"border-radius: 18px 18px 4px 18px; padding: 12px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.addLayout(self._actions)
            cl.addWidget(self._body)
            outer.addWidget(card, 0, Qt.AlignRight)
        elif role == "system":
            self._avatar.hide()
            self._body.setStyleSheet(f"color: {PAL['muted']}; font-size: 11px;")
            outer.addStretch(1)
            outer.addWidget(self._body, 0, Qt.AlignCenter)
            outer.addStretch(1)
        elif role == "proactive":
            card = QFrame()
            card.setStyleSheet(
                f"background: rgba(212,175,55,0.08); border-left: 4px solid {PAL['proactive']}; "
                f"border-radius: 8px; padding: 12px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.addLayout(self._actions)
            cl.addWidget(self._body)
            outer.addWidget(self._avatar)
            outer.addWidget(card, 1)
        else:
            card = QFrame()
            card.setStyleSheet(
                f"background: {PAL['surface_2']}; border: 1px solid {PAL['border']}; "
                f"border-radius: 18px 18px 18px 4px; padding: 12px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.addLayout(self._actions)
            self._verify_lbl = QLabel("")
            self._verify_lbl.hide()
            self._verify_lbl.setStyleSheet(
                f"color: {PAL['muted']}; font-size: 10px; font-weight: 600; "
                f"background: transparent; border: none; padding: 0 0 4px 0;"
            )
            cl.addWidget(self._verify_lbl)
            cl.addWidget(self._body)
            outer.addWidget(self._avatar)
            outer.addWidget(card, 1)
            outer.addStretch(1)

        self.setToolTip(datetime.fromtimestamp(self.ts).strftime("%H:%M:%S"))
        if role != "user":
            self._fit_body_height()

    def _add_action(self, glyph: str, tip: str, fn: Callable) -> None:
        btn = QToolButton()
        btn.setText(glyph)
        btn.setToolTip(tip)
        btn.hide()
        btn.setStyleSheet(
            f"QToolButton {{ color: {PAL['muted']}; border: none; padding: 2px 4px; "
            f"font-size: 11px; background: transparent; }}"
            f"QToolButton:hover {{ color: {PAL['cyan']}; }}"
        )
        btn.clicked.connect(fn)
        self._actions.addWidget(btn)

    def _fit_body_height(self) -> None:
        if not isinstance(self._body, QTextBrowser):
            return
        doc = self._body.document()
        w = max(self._body.viewport().width(), 220)
        doc.setTextWidth(w)
        self._body.setFixedHeight(max(int(doc.size().height()) + 8, 24))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if isinstance(self._body, QTextBrowser):
            self._fit_body_height()

    def enterEvent(self, event: QEvent) -> None:
        for btn in self.findChildren(QToolButton):
            btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        for btn in self.findChildren(QToolButton):
            btn.hide()
        super().leaveEvent(event)

    def set_markdown(self, md_text: str, *, streaming: bool = False) -> None:
        self.text = md_text
        if isinstance(self._body, QTextBrowser):
            self._body.setHtml(ChatMarkdown.render(md_text, streaming=streaming))
            self._fit_body_height()

    def _copy_self(self) -> None:
        pyperclip.copy(self.text)

    def tag_guide_step(self, step_index: int) -> None:
        if self.role != "assistant":
            return
        self._step_index = step_index
        if hasattr(self, "_verify_lbl"):
            self._verify_lbl.setText(f"Step {step_index}")
            self._verify_lbl.setToolTip("")
            self._verify_lbl.show()

    def set_step_verification(self, step_index: int, passed: bool, discrepancy: str = "") -> None:
        if self.role != "assistant" or self._step_index != step_index:
            return
        if not hasattr(self, "_verify_lbl"):
            return
        if passed:
            self._verify_lbl.setText(f"Step {step_index} · ✓ verified")
            self._verify_lbl.setStyleSheet(
                f"color: {PAL['success']}; font-size: 10px; font-weight: 600; "
                f"background: transparent; border: none; padding: 0 0 4px 0;"
            )
            self._verify_lbl.setToolTip("")
        else:
            self._verify_lbl.setText(f"Step {step_index} · ⚠ mismatch")
            self._verify_lbl.setStyleSheet(
                f"color: {PAL['danger']}; font-size: 10px; font-weight: 600; "
                f"background: transparent; border: none; padding: 0 0 4px 0;"
            )
            self._verify_lbl.setToolTip((discrepancy or "Step did not match expected state")[:240])
        self._verify_lbl.show()


class ChatView(QWidget):
    """Scrollable message list with drag-drop and jump-to-latest."""

    anchor_clicked = Signal(QUrl)
    image_dropped  = Signal(str)
    message_added  = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 8, 0, 8)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll, 1)

        self._jump_btn = QPushButton("↓ Jump to latest")
        self._jump_btn.setStyleSheet(
            f"QPushButton {{ background: {PAL['surface_2']}; color: {PAL['cyan']}; "
            f"border: 1px solid {PAL['cyan_dim']}; border-radius: 14px; padding: 4px 12px; "
            f"font-size: 10px; }}"
            f"QPushButton:hover {{ background: {PAL['border']}; }}"
        )
        self._jump_btn.hide()
        self._jump_btn.clicked.connect(self._scroll_to_bottom)
        lay.addWidget(self._jump_btn, 0, Qt.AlignRight)

        self._drop_overlay = QLabel("Drop image to attach", self)
        self._drop_overlay.setAlignment(Qt.AlignCenter)
        self._drop_overlay.setStyleSheet(
            f"background: rgba(0,212,255,0.12); border: 2px dashed {PAL['cyan']}; "
            f"border-radius: 12px; color: {PAL['cyan']}; font-size: 13px;")
        self._drop_overlay.hide()

        self._pinned_bottom = True
        self._streaming_card: Optional[ChatMessageCard] = None
        self._cards: list[ChatMessageCard] = []
        self._step_cards: dict[int, ChatMessageCard] = {}

    def _on_scroll(self, value: int) -> None:
        sb = self._scroll.verticalScrollBar()
        self._pinned_bottom = value >= sb.maximum() - 24
        self._jump_btn.setVisible(not self._pinned_bottom and bool(self._cards))

    def _scroll_to_bottom(self) -> None:
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._pinned_bottom = True
        self._jump_btn.hide()

    def _insert_card(self, card: ChatMessageCard) -> None:
        self._layout.insertWidget(self._layout.count() - 1, card)
        self._cards.append(card)
        if isinstance(card._body, QTextBrowser):
            card._body.anchorClicked.connect(self.anchor_clicked.emit)
        self.message_added.emit(card)
        QTimer.singleShot(50, self._scroll_to_bottom_if_pinned)

    def _scroll_to_bottom_if_pinned(self) -> None:
        if self._pinned_bottom:
            self._scroll_to_bottom()

    def tag_guide_step(self, step_index: int) -> None:
        card = self._streaming_card
        if card is None and self._cards:
            card = self._cards[-1]
        if card is not None:
            card.tag_guide_step(step_index)
            self._step_cards[step_index] = card

    def apply_step_verification(
        self, step_index: int, passed: bool, discrepancy: str = "",
    ) -> None:
        card = self._step_cards.get(step_index)
        if card is not None:
            card.set_step_verification(step_index, passed, discrepancy)

    def add_message(self, role: str, text: str, *, ts: Optional[float] = None) -> dict:
        card = ChatMessageCard(role, text, ts=ts, parent=self._container)
        self._insert_card(card)
        return {"role": role, "text": text, "ts": ts or time.time()}

    def begin_assistant_stream(self) -> None:
        self._streaming_card = ChatMessageCard("assistant", "", parent=self._container)
        self._insert_card(self._streaming_card)

    def update_stream(self, md_buffer: str) -> None:
        if self._streaming_card:
            self._streaming_card.set_markdown(md_buffer, streaming=True)
            self._scroll_to_bottom_if_pinned()

    def finish_stream(self, full_text: str, elapsed: float = 0.0) -> None:
        if self._streaming_card:
            if full_text:
                self._streaming_card.set_markdown(full_text, streaming=False)
            else:
                self._layout.removeWidget(self._streaming_card)
                self._streaming_card.deleteLater()
                if self._streaming_card in self._cards:
                    self._cards.remove(self._streaming_card)
            self._streaming_card = None
        if full_text and elapsed > 0:
            self.add_message("system", f"Atlas · {elapsed:.1f}s")
        self._scroll_to_bottom_if_pinned()

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()
        self._streaming_card = None
        self._step_cards.clear()

    def rebuild_from_messages(self, messages: list[dict]) -> None:
        self.clear()
        for m in messages:
            role = m.get("role", "assistant")
            text = m.get("text") or ""
            if not text and m.get("html"):
                import re
                text = re.sub(r"<[^>]+>", " ", m.get("html", "")).strip()
            self.add_message(role, text, ts=m.get("ts"))

    def plain_text(self) -> str:
        return "\n\n".join(c.text for c in self._cards if c.text and c.role != "system")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in _IMAGE_EXTS:
                    event.acceptProposedAction()
                    self._drop_overlay.setGeometry(self.rect().adjusted(8, 8, -8, -8))
                    self._drop_overlay.show()
                    self._drop_overlay.raise_()
                    return

    def dragLeaveEvent(self, event: QEvent) -> None:
        self._drop_overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._drop_overlay.hide()
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.suffix.lower() in _IMAGE_EXTS:
                    self.image_dropped.emit(str(p))
                    event.acceptProposedAction()
                    return

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._drop_overlay.isVisible():
            self._drop_overlay.setGeometry(self.rect().adjusted(8, 8, -8, -8))


class ChatComposer(QFrame):
    """Multi-line composer with toolbar, slash commands, send/stop."""

    submitted            = Signal(str)
    stop_requested       = Signal()
    attach_file          = Signal(str)
    screenshot_requested = Signal()

    def __init__(self, window: "AtlasWindow", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._window = window
        self.setObjectName("chat_composer")
        self._streaming = False
        self._tts_next = True
        self._ptt_active = False

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        self._attach_lbl = QLabel("")
        self._attach_lbl.setStyleSheet(
            f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
        root.addWidget(self._attach_lbl)

        row = QHBoxLayout()
        self._input = QTextEdit()
        self._input.setPlaceholderText(
            "Ask Atlas anything... or just say 'look at my screen'")
        self._input.setAcceptRichText(False)
        self._input.setMinimumHeight(36)
        self._input.setMaximumHeight(132)
        self._input.setStyleSheet(
            f"QTextEdit {{ background: {PAL['surface_2']}; color: {PAL['text']}; "
            f"border: 1px solid {PAL['border']}; border-radius: 10px; padding: 8px 10px; "
            f"font-size: 13px; }}"
            f"QTextEdit:focus {{ border: 1px solid {PAL['cyan']}; }}"
        )
        self._input.textChanged.connect(self._sync_input_height)
        self._input.installEventFilter(self)
        row.addWidget(self._input, 1)

        self._send_btn = QPushButton("➤")
        self._send_btn.setFixedSize(40, 40)
        self._send_btn.setEnabled(False)
        self._send_btn.clicked.connect(self._on_send_clicked)
        self._style_send_idle()
        row.addWidget(self._send_btn)
        root.addLayout(row)

        tb = QHBoxLayout()
        self._tool_btns: list[QToolButton] = []
        for icon, tip, fn, ptt in [
            ("📎", "Attach file", self._pick_attach, False),
            ("📸", "Screenshot for next message", self._shot, False),
            ("🎤", "Hold to record (PTT)", None, True),
            ("🔊", "Speak replies aloud (on by default)", self._toggle_tts, False),
            ("😊", "Emoji", self._emoji_menu, False),
            ("+", "More actions", self._overflow, False),
        ]:
            btn = QToolButton()
            btn.setText(icon)
            btn.setToolTip(tip)
            btn.setFixedSize(28, 28)
            btn.setCheckable(icon == "🔊")
            btn.setChecked(icon == "🔊")
            btn.setStyleSheet(
                f"QToolButton {{ color: {PAL['muted']}; border: none; font-size: 14px; "
                f"border-radius: 6px; }}"
                f"QToolButton:hover {{ color: {PAL['cyan']}; background: {PAL['border']}; }}"
                f"QToolButton:checked {{ color: {PAL['cyan']}; background: rgba(0,212,255,0.12); }}"
            )
            if ptt:
                btn.pressed.connect(self._ptt_down)
                btn.released.connect(self._ptt_up)
            elif fn:
                btn.clicked.connect(fn)
            tb.addWidget(btn)
            self._tool_btns.append(btn)
        tb.addStretch(1)
        root.addLayout(tb)

        self._slash_popup = QListWidget()
        self._slash_popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self._slash_popup.setStyleSheet(
            f"QListWidget {{ background: {PAL['surface']}; color: {PAL['text']}; "
            f"border: 1px solid {PAL['border']}; border-radius: 8px; }}"
            f"QListWidget::item:selected {{ background: {PAL['border']}; color: {PAL['cyan']}; }}"
        )
        self._slash_popup.itemActivated.connect(self._slash_picked)
        self._slash_idx = 0

    def _style_send_idle(self) -> None:
        self._send_btn.setText("➤")
        self._send_btn.setToolTip("Send (Enter)")
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: {PAL['cyan']}; color: {PAL['bg']}; "
            f"border-radius: 10px; font-size: 16px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {PAL['text']}; }}"
            f"QPushButton:disabled {{ background: {PAL['border']}; color: {PAL['muted']}; }}"
        )

    def _style_send_stop(self) -> None:
        self._send_btn.setText("■")
        self._send_btn.setToolTip("Stop generation")
        self._send_btn.setEnabled(True)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255,45,85,0.2); color: {PAL['danger']}; "
            f"border: 1px solid {PAL['danger']}; border-radius: 10px; font-size: 14px; }}"
            f"QPushButton:hover {{ background: {PAL['danger']}; color: {PAL['bg']}; }}"
        )

    def set_streaming(self, active: bool) -> None:
        self._streaming = active
        if active:
            self._style_send_stop()
        else:
            self._style_send_idle()
            self._send_btn.setEnabled(bool(self._input.toPlainText().strip()))

    def set_attach_label(self, text: str) -> None:
        self._attach_lbl.setText(text)

    def tts_enabled_for_next(self) -> bool:
        return self._tts_next

    def eventFilter(self, obj, event) -> bool:
        if obj is self._input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
                if self._slash_popup.isVisible():
                    item = self._slash_popup.currentItem()
                    if item:
                        self._slash_picked(item)
                    return True
                self._on_send_clicked()
                return True
            if self._slash_popup.isVisible():
                if key == Qt.Key_Down:
                    self._slash_idx = min(self._slash_idx + 1, self._slash_popup.count() - 1)
                    self._slash_popup.setCurrentRow(self._slash_idx)
                    return True
                if key == Qt.Key_Up:
                    self._slash_idx = max(self._slash_idx - 1, 0)
                    self._slash_popup.setCurrentRow(self._slash_idx)
                    return True
                if key == Qt.Key_Escape:
                    self._slash_popup.hide()
                    return True
            if key == Qt.Key_Slash and self._input.toPlainText().strip() == "":
                QTimer.singleShot(0, self._show_slash_popup)
        return super().eventFilter(obj, event)

    def _sync_input_height(self) -> None:
        lines = max(1, min(6, self._input.document().blockCount()))
        lh = self._input.fontMetrics().lineSpacing()
        self._input.setFixedHeight(min(132, max(36, lines * lh + 16)))
        if not self._streaming:
            self._send_btn.setEnabled(bool(self._input.toPlainText().strip()))

    def _show_slash_popup(self) -> None:
        self._slash_popup.clear()
        for cmd, desc in _SLASH_COMMANDS:
            item = QListWidgetItem(f"{cmd}  —  {desc}")
            item.setData(Qt.UserRole, cmd)
            self._slash_popup.addItem(item)
        self._slash_idx = 0
        self._slash_popup.setCurrentRow(0)
        self._slash_popup.setFixedWidth(max(340, self._input.width()))
        pos = self._input.mapToGlobal(QPoint(0, -min(220, self._slash_popup.sizeHint().height()) - 6))
        self._slash_popup.move(pos)
        self._slash_popup.show()

    def _slash_picked(self, item: QListWidgetItem) -> None:
        cmd = str(item.data(Qt.UserRole) or item.text().split()[0])
        self._slash_popup.hide()
        self._input.setPlainText(cmd + " ")
        self._input.setFocus()
        self._sync_input_height()

    def _on_send_clicked(self) -> None:
        if self._streaming:
            self.stop_requested.emit()
            return
        text = self._input.toPlainText().strip()
        if not text:
            return
        self.submitted.emit(text)
        self._input.clear()
        self._sync_input_height()

    def _pick_attach(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._window, "Attach to message", "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp);;Documents (*.pdf *.txt *.md);;All (*)",
        )
        if path:
            self.attach_file.emit(path)

    def _shot(self) -> None:
        self.screenshot_requested.emit()

    def _toggle_tts(self) -> None:
        self._tts_next = self._tool_btns[3].isChecked()
        self._window.bridge.set_status.emit(
            f"TTS for next reply: {'on' if self._tts_next else 'off'}")

    def _emoji_menu(self) -> None:
        menu = QMenu(self)
        for em in "😀 😊 👍 🎉 🔥 💡 ❤️ 🤔 👀 ✅ ❌ 🚀 💬 🎯 ⚡ 🙏".split():
            menu.addAction(em, lambda checked=False, e=em: self._input.insertPlainText(e))
        menu.exec(self._tool_btns[4].mapToGlobal(QPoint(0, -100)))

    def _overflow(self) -> None:
        self._window._show_plus_menu()

    def _ptt_down(self) -> None:
        audio = getattr(self._window, "audio", None)
        if audio:
            audio.start_ptt_recording()
            self._ptt_active = True

    def _ptt_up(self) -> None:
        audio = getattr(self._window, "audio", None)
        if audio and self._ptt_active:
            audio.stop_ptt_recording()
        self._ptt_active = False


# ═════════════════════════════════════════════════════════════════════════════
# CHAT INPUT (legacy line edit — kept for reference)
# ═════════════════════════════════════════════════════════════════════════════

class ChatInputEntry(QLineEdit):
    """Chat input with history, drag-drop files, @skill completer, and Ctrl+Enter submit."""

    submitted = Signal(str)

    _FILE_EXTS = {".pdf", ".txt", ".md"}

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        skill_names_fn: Optional[Callable[[], list[str]]] = None,
    ) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._hist_idx = -1
        self._draft = ""
        self._file_prefix = ""
        self._skill_names_fn = skill_names_fn
        self.setPlaceholderText("Ask Atlas anything…  (@skill, /help, Ctrl+Enter)")
        self.setAcceptDrops(True)
        self._completer = QCompleter([], self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.activated.connect(self._on_skill_picked)
        self.setCompleter(self._completer)

    def refresh_skills(self) -> None:
        if not self._skill_names_fn:
            return
        self._completer.setModel(QStringListModel(self._skill_names_fn()))

    def _on_skill_picked(self, name: str) -> None:
        self.setText(f"@{name}: ")

    def keyPressEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._submit()
            return
        if event.key() == Qt.Key_Up and self._history:
            if self._hist_idx == len(self._history):
                self._draft = self.text()
            if self._hist_idx > 0:
                self._hist_idx -= 1
                self.setText(self._history[self._hist_idx])
            return
        if event.key() == Qt.Key_Down and self._history:
            if self._hist_idx < len(self._history) - 1:
                self._hist_idx += 1
                self.setText(self._history[self._hist_idx])
            else:
                self._hist_idx = len(self._history)
                self.setText(self._draft)
            return
        if event.text() == "@":
            self.refresh_skills()
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths: list[Path] = []
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    paths.append(Path(url.toLocalFile()))
        if not paths and event.mimeData().hasText():
            p = Path(event.mimeData().text().strip().strip('"'))
            if p.is_file():
                paths.append(p)
        for p in paths:
            if p.suffix.lower() in self._FILE_EXTS:
                size_kb = p.stat().st_size // 1024
                self._file_prefix = f"@file:{p.name}"
                self.setText(f"{self._file_prefix} ")
                self.setToolTip(f"{p.name} ({size_kb} KB)")
                break
        event.acceptProposedAction()

    def _submit(self) -> None:
        text = self.text().strip()
        if not text:
            return
        if self._file_prefix and not text.startswith("@file:"):
            text = f"{self._file_prefix} {text}"
        self._history.append(text)
        if len(self._history) > 50:
            self._history.pop(0)
        self._hist_idx = len(self._history)
        self._draft = ""
        self._file_prefix = ""
        self.clear()
        self.setToolTip("")
        self.submitted.emit(text)



# ═════════════════════════════════════════════════════════════════════════════
# UNIFIED CONTROL CENTER PANEL  (legacy — superseded by SettingsDialog)
# ═════════════════════════════════════════════════════════════════════════════

_CTRL_QSS = settings_dialog_qss()


class ControlCenter(QDialog):
    """
    Unified Control Center — single modal that consolidates:
      ‣ Model selector + Response Style
      ‣ Watcher Sensitivity
      ‣ Audio: Mic / Speaker / Voice Engine
      ‣ Camera toggle (cv2 webcam)
      ‣ File System Access toggle
      ‣ Hotkey reference
    """

    W, H = 480, 540

    def __init__(self, parent, engine, audio, ui_window):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(_CTRL_QSS)
        self.setFixedSize(self.W, self.H)

        self.ui    = ui_window
        self.audio = audio

        chrome = QFrame(self)
        chrome.setObjectName("ctrl_chrome")
        chrome.setGeometry(0, 0, self.W, self.H)

        outer = QVBoxLayout(chrome)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Title bar ────────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(44)
        title_bar.setStyleSheet(
            f"background: {PAL['bg']};"
            f"border-top-left-radius: 12px; border-top-right-radius: 12px;"
            f"border-bottom: 1px solid {PAL['border']};"
        )
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(16, 0, 12, 0)
        lbl = QLabel("⚙  Atlas Control Center")
        lbl.setStyleSheet(
            f"color: {PAL['cyan']}; font-size: 14px; font-weight: bold;"
            f"border: none; background: transparent;"
        )
        tb.addWidget(lbl)
        tb.addStretch()
        btn_x = QPushButton("✕")
        btn_x.setFixedSize(24, 24)
        btn_x.setStyleSheet(
            f"background: transparent; color: {PAL['muted']}; font-size: 13px;"
            f"border: none; border-radius: 4px;"
        )
        btn_x.clicked.connect(self.close)
        tb.addWidget(btn_x)
        outer.addWidget(title_bar)

        # ── Scrollable body ───────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(16)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ── MODEL ─────────────────────────────────────────────────────────────
        self._section(lay, "Model")
        self.model_combo = QComboBox()
        if _CORE:
            for mid in GROQ_MODELS:
                self.model_combo.addItem(GROQ_MODEL_LABELS.get(mid, mid), mid)
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) == GROQ_MODEL:
                    self.model_combo.setCurrentIndex(i)
                    break
        else:
            self.model_combo.addItem("Llama 3.3 70B")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._row(lay, "Model", "Groq inference model", self.model_combo)

        # ── RESPONSE STYLE ────────────────────────────────────────────────────
        self._section(lay, "Response Style")
        self.style_combo = QComboBox()
        _styles = RESPONSE_STYLES if _CORE else ["Terse", "Direct", "Balanced", "Detailed"]
        self.style_combo.addItems(_styles)
        self.style_combo.setCurrentText(
            getattr(engine.session, "response_style", "Balanced") if engine else "Balanced"
        )
        self.style_combo.currentTextChanged.connect(self._on_style_changed)
        self._row(lay, "Style", "Verbosity level for AI responses", self.style_combo)

        # ── SCREEN WATCHER ────────────────────────────────────────────────────
        self._section(lay, "Screen Watcher")
        sld = QSlider(Qt.Horizontal)
        sld.setRange(3, 15)
        sld.setValue(getattr(ui_window, "_watch_interval", 5))
        sld.setFixedWidth(110)
        iv_lbl = QLabel(f"{sld.value()} s")
        iv_lbl.setStyleSheet(
            f"color: {PAL['cyan']}; font-size: 11px; background: transparent; min-width: 28px;"
        )
        sld.valueChanged.connect(lambda v: (
            setattr(ui_window, "_watch_interval", v),
            iv_lbl.setText(f"{v} s"),
        ))
        sld_wrap = QWidget()
        sld_wrap.setStyleSheet("background: transparent;")
        sw = QHBoxLayout(sld_wrap)
        sw.setContentsMargins(0, 0, 0, 0)
        sw.addWidget(sld)
        sw.addWidget(iv_lbl)
        self._row(lay, "Scan Interval", "Seconds between watcher scans", sld_wrap)

        sens_combo = QComboBox()
        sens_combo.addItems(["Low", "Medium", "High"])
        sens_combo.setCurrentText(getattr(ui_window, "_watch_sensitivity", "Medium"))
        sens_combo.currentTextChanged.connect(
            lambda t: setattr(ui_window, "_watch_sensitivity", t)
        )
        self._row(lay, "Sensitivity", "Change-detection threshold", sens_combo)

        # ── AUDIO ─────────────────────────────────────────────────────────────
        self._section(lay, "Audio")

        mic_on = bool(audio and getattr(audio, "mic_active", False))
        self._mic_btn = self._toggle_btn(mic_on)
        self._mic_btn.toggled.connect(self._toggle_mic)
        self._row(lay, "🎤 Microphone", "Live STT via Groq Whisper", self._mic_btn)

        spk_on = bool(audio and getattr(audio, "speaker_active", False))
        self._spk_btn = self._toggle_btn(spk_on)
        self._spk_btn.toggled.connect(self._toggle_spk)
        self._row(lay, "🔊 Speaker", "Loopback / monitor capture", self._spk_btn)

        ve_muted = (voice_engine.is_muted if voice_engine else True)
        self._ve_btn = self._toggle_btn(not ve_muted)
        self._ve_btn.toggled.connect(self._toggle_voice_engine)
        self._row(lay, "🗣 Voice Engine (TTS)", "Kokoro neural TTS output", self._ve_btn)

        # ── CAMERA ───────────────────────────────────────────────────────────
        self._section(lay, "Camera")
        self._cam_btn = self._toggle_btn(getattr(ui_window, "camera_active", False))
        self._cam_btn.toggled.connect(lambda checked: setattr(ui_window, "camera_active", checked))
        cam_sub = "Capture webcam frame on each query (requires cv2)" if HAS_CV2 else "opencv-python not installed"
        self._row(lay, "📷 Camera Vision", cam_sub, self._cam_btn)

        # ── FILE SYSTEM ACCESS ────────────────────────────────────────────────
        self._section(lay, "File System Access")
        fs_on = getattr(ui_window, "fs_access_active", False)
        self._fs_btn = self._toggle_btn(fs_on)
        self._fs_btn.toggled.connect(self._toggle_fs_access)
        self._row(
            lay, "🗂 FS Access",
            "Enable Atlas write/execute file access (prompts on each op)",
            self._fs_btn,
        )

        # ── WINDOW OPACITY (Bug #17 — slider existed but was never accessible) ─
        self._section(lay, "Appearance")
        op_sld = QSlider(Qt.Horizontal)
        op_sld.setRange(20, 100)
        op_sld.setValue(int(getattr(ui_window, "windowOpacity", lambda: 0.95)() * 100))
        op_sld.setFixedWidth(120)
        op_val_lbl = QLabel(f"{op_sld.value()}%")
        op_val_lbl.setStyleSheet(
            f"color: {PAL['cyan']}; font-size: 11px; background: transparent; min-width: 36px;"
        )
        def _on_cc_opacity(v: int) -> None:
            op_val_lbl.setText(f"{v}%")
            # Drive the canonical header slider; it owns opacity application and
            # keeps both controls in sync without clobbering its bound methods.
            hdr = getattr(ui_window, "op_slider", None)
            if hdr is not None and hdr.value() != v:
                hdr.setValue(v)
            else:
                ui_window.set_window_opacity_pct(v)

        op_sld.valueChanged.connect(_on_cc_opacity)
        op_wrap = QWidget(); op_wrap.setStyleSheet("background: transparent;")
        ow = QHBoxLayout(op_wrap); ow.setContentsMargins(0, 0, 0, 0)
        ow.addWidget(op_sld); ow.addWidget(op_val_lbl)
        self._row(lay, "🪟 Opacity", "Window transparency (20–100%)", op_wrap)

        # ── HOTKEYS ───────────────────────────────────────────────────────────
        self._section(lay, "Global Hotkeys  (read-only)")
        hotkeys = [
            ("📷", "Screen Capture",  "Ctrl + Shift + S", "Screenshot → AI"),
            ("🔍", "Clipboard Watch", "Ctrl + Shift + H", "Toggle highlight mode"),
            ("👁", "Screen Watcher",  "Ctrl + Shift + W", "Toggle periodic scanning"),
            ("🎧", "Push-to-Talk",    "Ctrl + Space",      "Hold to speak · break-in"),
            ("🔄", "Hot Reload",      "Ctrl + R",          "Restart app window"),
        ]
        for icon, name, shortcut, desc in hotkeys:
            card = QFrame()
            card.setStyleSheet(
                f"background: {PAL['surface_2']}; border: 1px solid {PAL['border']};"
                f"border-radius: 8px;"
            )
            cl = QHBoxLayout(card)
            cl.setContentsMargins(12, 8, 12, 8)
            ico_lbl = QLabel(icon)
            ico_lbl.setStyleSheet("font-size: 16px; background: transparent;")
            cl.addWidget(ico_lbl)
            txt = QVBoxLayout()
            nl = QLabel(name)
            nl.setStyleSheet(
                f"color: {PAL['text']}; font-size: 12px; font-weight: bold; background: transparent;"
            )
            dl = QLabel(desc)
            dl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
            txt.addWidget(nl)
            txt.addWidget(dl)
            cl.addLayout(txt, 1)
            badge = QLabel(shortcut)
            badge.setStyleSheet(
                f"background: {PAL['bg']}; color: {PAL['cyan']};"
                f"border: 1px solid {PAL['cyan_dim']}; border-radius: 5px;"
                f"font-family: 'Consolas', monospace; font-size: 11px;"
                f"padding: 3px 8px;"
            )
            cl.addWidget(badge)
            lay.addWidget(card)

        lay.addStretch()

        # ── Footer ────────────────────────────────────────────────────────────
        footer = QWidget()
        footer.setFixedHeight(52)
        footer.setStyleSheet(
            f"background: {PAL['bg']};"
            f"border-top: 1px solid {PAL['border']};"
            f"border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;"
        )
        ft = QHBoxLayout(footer)
        ft.setContentsMargins(16, 0, 16, 0)
        ft.addStretch()
        btn_done = QPushButton("  Save & Close  ")
        btn_done.setObjectName("done_btn")
        btn_done.clicked.connect(self.close)
        ft.addWidget(btn_done)
        outer.addWidget(footer)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _section(self, lay, text):
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_hdr")
        lay.addWidget(lbl)

    def _row(self, lay, label, sub, control):
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        col = QVBoxLayout()
        nl = QLabel(label)
        nl.setStyleSheet(f"color: {PAL['text']}; font-size: 13px; background: transparent;")
        col.addWidget(nl)
        if sub:
            sl = QLabel(sub)
            sl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
            col.addWidget(sl)
        rl.addLayout(col, 1)
        rl.addWidget(control)
        lay.addWidget(row)

    def _toggle_btn(self, active: bool) -> QPushButton:
        btn = QPushButton("ON" if active else "OFF")
        btn.setObjectName("toggle_on" if active else "toggle_off")
        btn.setCheckable(True)
        btn.setChecked(active)

        def _refresh(checked):
            btn.setText("ON" if checked else "OFF")
            btn.setObjectName("toggle_on" if checked else "toggle_off")
            btn.setStyleSheet("")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        btn.toggled.connect(_refresh)
        return btn

    # ── Slot handlers ─────────────────────────────────────────────────────────

    def _on_model_changed(self, index: int):
        if not _CORE:
            return
        mid = self.model_combo.itemData(index)
        if mid:
            _core_mod.GROQ_MODEL = mid
            self.ui.bridge.set_status.emit(f"Model → {GROQ_MODEL_LABELS.get(mid, mid)}")

    def _on_style_changed(self, style: str):
        if self.ui.state:
            self.ui.state.session.response_style = style
            self.ui.bridge.set_status.emit(f"Style → {style}")

    def _toggle_mic(self, checked: bool):
        if not self.audio:
            return
        if checked:
            self.audio.start_mic()
        else:
            self.audio.stop_mic()
        self.ui._action_mic.setChecked(self.audio.mic_active)

    def _toggle_spk(self, checked: bool):
        if not self.audio:
            return
        if checked:
            self.audio.start_speaker()
        else:
            self.audio.stop_speaker()
        self.ui._action_spk.setChecked(self.audio.speaker_active)

    def _toggle_voice_engine(self, checked: bool):
        if not voice_engine:
            return
        if checked:
            voice_engine.unmute()
        else:
            voice_engine.mute()
        self.ui.bridge.set_status.emit(
            "TTS Voice Engine ON" if checked else "TTS Voice Engine muted"
        )

    def _toggle_fs_access(self, checked: bool):
        self.ui.fs_access_active = checked
        if self.ui.state and hasattr(self.ui.state, "set_fs_access_active"):
            self.ui.state.set_fs_access_active(checked)
        self.ui.bridge.set_status.emit(
            "File System Access ENABLED — prompts on write/exec"
            if checked else
            "File System Access DISABLED — screen clicks still prompt separately"
        )


# ═════════════════════════════════════════════════════════════════════════════
# WORKER THREADS  (unchanged in logic, updated import references)
# ═════════════════════════════════════════════════════════════════════════════

class OCRWorker(QThread):
    """Screen capture + Tesseract OCR — runs on a dedicated QThread."""

    grab_done     = Signal()
    ocr_completed = Signal(str)
    ocr_failed    = Signal(str)

    def __init__(self, hide_delay_s: float = 0.15, parent=None):
        super().__init__(parent)
        self._hide_delay = hide_delay_s

    def run(self) -> None:
        try:
            from PIL import ImageGrab
            import pytesseract
        except ImportError as exc:
            self.ocr_failed.emit(f"Import error: {exc}")
            return

        try:
            from dotenv import load_dotenv
            load_dotenv()
            _tess = os.environ.get("TESSERACT_CMD", "")
            if _tess:
                pytesseract.pytesseract.tesseract_cmd = _tess
        except Exception:
            pass

        time.sleep(self._hide_delay)

        try:
            img = ImageGrab.grab()
        except Exception as exc:
            self.ocr_failed.emit(f"Capture failed: {exc}")
            return
        finally:
            self.grab_done.emit()

        try:
            txt = pytesseract.image_to_string(img).strip()
        except Exception as exc:
            self.ocr_failed.emit(f"OCR error: {exc}")
            return

        if not txt:
            self.ocr_failed.emit("OCR: no text detected")
            return

        self.ocr_completed.emit(txt)


class WatchWorker(QThread):
    """Periodic screen-watcher — vision AI primary, OCR fallback."""

    screen_text = Signal(str)
    status      = Signal(str)

    def __init__(self, interval: int = 5, sensitivity: str = "Medium", parent=None):
        super().__init__(parent)
        self._interval    = interval
        self._sensitivity = sensitivity
        self._stop_flag   = False

    def stop(self) -> None:
        self._stop_flag = True

    def set_interval(self, seconds: int) -> None:
        self._interval = max(3, seconds)

    def set_sensitivity(self, level: str) -> None:
        self._sensitivity = level

    def run(self) -> None:
        try:
            import mss
            import mss.tools
            HAS_MSS = True
        except ImportError:
            HAS_MSS = False

        while not self._stop_flag:
            elapsed = 0.0
            while elapsed < self._interval and not self._stop_flag:
                time.sleep(0.5)
                elapsed += 0.5

            if self._stop_flag:
                break

            self.status.emit("👁 Scanning screen…")

            img_bytes: bytes | None = None
            if HAS_MSS:
                try:
                    import mss, mss.tools
                    with mss.mss() as sct:
                        monitor = sct.monitors[0]
                        shot    = sct.grab(monitor)
                        buf = _io.BytesIO()
                        mss.tools.to_png(shot.rgb, shot.size, output=buf)
                        img_bytes = buf.getvalue()
                except Exception as e:
                    self.status.emit(f"mss grab failed: {e}")

            if img_bytes is None:
                try:
                    from PIL import ImageGrab
                    pil_img   = ImageGrab.grab()
                    buf       = _io.BytesIO()
                    pil_img.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                except Exception as e:
                    self.status.emit(f"Screen grab error: {e}")
                    continue

            txt = self._try_vision_ai(img_bytes)
            if not txt:
                txt = self._try_ocr(img_bytes)

            if txt:
                self.screen_text.emit(txt)
            else:
                self.status.emit("👁 Nothing detected")

    def _try_vision_ai(self, img_bytes: bytes) -> str:
        try:
            from atlas_core import groq_client, ATLAS_WEBCAM_MODEL
            b64  = base64.b64encode(img_bytes).decode()
            resp = groq_client.chat.completions.create(
                model=ATLAS_WEBCAM_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Extract all readable text from this screenshot. "
                                "Return only the extracted text — no commentary, "
                                "no formatting tags, no preamble."
                            ),
                        },
                    ],
                }],
                max_tokens=800,
            )
            raw = resp.choices[0].message.content
            return (raw or "").strip()
        except Exception:
            return ""

    def _try_ocr(self, img_bytes: bytes) -> str:
        try:
            from PIL import Image
            import pytesseract
            try:
                tess = os.environ.get("TESSERACT_CMD", "")
                if tess:
                    pytesseract.pytesseract.tesseract_cmd = tess
            except Exception:
                pass
            pil_img = Image.open(_io.BytesIO(img_bytes))
            return pytesseract.image_to_string(pil_img).strip()
        except Exception:
            return ""


class ContextIngestWorker(QThread):
    """PDF / TXT → pinned SessionManager context entry."""

    ingest_done  = Signal(str)
    ingest_failed = Signal(str)
    progress      = Signal(str)

    MAX_CHARS = 12_000

    def __init__(self, filepath: str, parent=None):
        super().__init__(parent)
        self._filepath = filepath

    def run(self) -> None:
        path = self._filepath
        try:
            if path.lower().endswith(".pdf"):
                text = self._extract_pdf(path)
            else:
                text = self._extract_text(path)
        except Exception as exc:
            self.ingest_failed.emit(f"Ingest error: {exc}")
            return

        if not text or not text.strip():
            self.ingest_failed.emit("No text found in file.")
            return

        if len(text) > self.MAX_CHARS:
            text = text[: self.MAX_CHARS] + "\n\n[…document truncated for context window…]"

        self.ingest_done.emit(text.strip())

    def _extract_pdf(self, path: str) -> str:
        try:
            import fitz
        except ImportError:
            raise ImportError("PyMuPDF not installed. Run: pip install pymupdf")
        doc   = fitz.open(path)
        pages = []
        total = len(doc)
        for i, page in enumerate(doc, 1):
            self.progress.emit(f"Extracting page {i}/{total}…")
            pages.append(page.get_text())
        doc.close()
        return "\n".join(pages)

    @staticmethod
    def _extract_text(path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()


# ═════════════════════════════════════════════════════════════════════════════
# FLOAT BUBBLE  (Requirement 6 — Ghost UI)
# 55×55 circle, QVariantAnimation opacity: idle 0.15 → hovered/active 1.0
# Stealth matrix: WDA_EXCLUDEFROMCAPTURE applied to this HWND (Requirement 5)
# ═════════════════════════════════════════════════════════════════════════════

class FloatBubble(QWidget):
    """
    55×55 frameless circle.  Ghost opacity animation:
    • idle      → opacity 0.15  (barely visible)
    • hovered   → opacity 1.0   (fully visible)
    • active    → opacity 1.0   (held while active)
    """

    clicked = Signal()
    dragged = Signal(QPoint)

    SIZE = 55   # compact per Req 6

    def __init__(self, parent=None):
        super().__init__(parent,
                         Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint |
                         Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._border_color = QColor(PAL["cyan"])
        self._drag_offset  = QPoint()
        self._is_active    = False

        # Ghost opacity animation
        self._opacity_anim = QVariantAnimation(self)
        self._opacity_anim.setDuration(400)
        self._opacity_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._opacity_anim.valueChanged.connect(self._on_opacity_value)

        # Transient "flash to visible" timer used on communication transitions.
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        # Start idle
        self._set_ghost_opacity(0.15)

    # ── Opacity helpers ───────────────────────────────────────────────────────

    def _animate_opacity(self, target: float) -> None:
        current = self.windowOpacity()
        if abs(current - target) < 0.01:
            return
        self._opacity_anim.stop()
        self._opacity_anim.setStartValue(float(current))
        self._opacity_anim.setEndValue(float(target))
        self._opacity_anim.start()

    def _set_ghost_opacity(self, val: float) -> None:
        self._opacity_anim.stop()
        self.setWindowOpacity(val)

    def _on_opacity_value(self, val) -> None:
        self.setWindowOpacity(float(val))

    def set_active(self, active: bool) -> None:
        """Call with True while AI is processing; False when idle."""
        self._is_active = active
        if active:
            self._animate_opacity(1.0)
        else:
            self._animate_opacity(0.15)

    def flash_visible(self, hold_ms: int = 5200) -> None:
        """
        Snap to full opacity for a communication transition (Req 4E), then
        settle back to the ghost idle level unless the bubble is active/hovered.
        """
        self._animate_opacity(1.0)
        self._flash_timer.start(hold_ms)

    def _end_flash(self) -> None:
        if not self._is_active and not self.underMouse():
            self._animate_opacity(0.15)

    # ── Stealth cloak — applied by AtlasWindow._apply_stealth_to_hwnd ─────────

    def set_border_color(self, color: QColor | str) -> None:
        self._border_color = QColor(color) if isinstance(color, str) else color
        self.update()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(PAL["surface_2"])))
        p.setPen(Qt.NoPen)
        p.drawEllipse(3, 3, self.SIZE - 6, self.SIZE - 6)
        pen = QPen(self._border_color, 2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(3, 3, self.SIZE - 6, self.SIZE - 6)
        p.setPen(QPen(self._border_color, 1))
        f = QFont("Segoe UI Emoji", 16)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "⚡")

    # ── Hover ─────────────────────────────────────────────────────────────────

    def enterEvent(self, event):
        if not self._is_active:
            self._animate_opacity(1.0)

    def leaveEvent(self, event):
        if not self._is_active:
            self._animate_opacity(0.15)

    # ── Drag / click ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset  = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_global = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            self.dragged.emit(new_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            release = event.globalPosition().toPoint()
            press   = getattr(self, "_press_global", release)
            delta   = release - press
            if abs(delta.x()) < 5 and abs(delta.y()) < 5:
                self.clicked.emit()


# ═════════════════════════════════════════════════════════════════════════════
# PILL NOTIFICATION
# Stealth matrix applied to this HWND too (Requirement 5)
# ═════════════════════════════════════════════════════════════════════════════

class PillNotification(QWidget):
    """
    Fade-in / fade-out notification pill beside FloatBubble.

    Geometry is calculated and set immediately in show_text() so the pill
    snaps to its final position with zero lag.  A QGraphicsOpacityEffect +
    QPropertyAnimation then animates opacity 0.0 → 1.0 on show and
    1.0 → 0.0 on hide.  This avoids the geometry animation stutter that
    occurs on Windows with high-DPI displays or Aero compositing.
    """

    PILL_W       = 260
    PILL_H       = 40
    FADE_IN_MS   = 220
    FADE_OUT_MS  = 180
    DISPLAY_MS   = 5000

    def __init__(self, parent=None):
        super().__init__(parent,
                         Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint |
                         Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.PILL_W, self.PILL_H)
        self.setStyleSheet(
            f"background: {PAL['surface']}; border: 1px solid {PAL['cyan']};"
            f"border-radius: 12px;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        self._lbl = QLabel()
        self._lbl.setStyleSheet(
            f"color: {PAL['text']}; font-size: 11px; background: transparent;"
        )
        self._lbl.setWordWrap(False)
        lay.addWidget(self._lbl)

        # ── Opacity effect — single instance, reused on every show/hide ───────
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        # Active animation reference (kept to stop/restart cleanly)
        self._anim: QPropertyAnimation | None = None

        # Auto-hide timer
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._animate_hide)

    # ── Internal animation helper ─────────────────────────────────────────────

    def _run_opacity_anim(
        self,
        start: float,
        end: float,
        duration_ms: int,
        finished_cb=None,
    ) -> None:
        """Start a new opacity animation, stopping any previous one first."""
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(duration_ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        if finished_cb:
            anim.finished.connect(finished_cb)
        self._anim = anim
        anim.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def show_text(self, text: str, bubble_geo: QRect, edge: str) -> None:
        """
        Position the pill relative to *bubble_geo* for the given *edge*,
        set geometry immediately (no geometry animation), then fade in.
        """
        self._lbl.setText(text)

        bx, by, bw, bh = (
            bubble_geo.x(), bubble_geo.y(),
            bubble_geo.width(), bubble_geo.height(),
        )
        cy = by + (bh - self.PILL_H) // 2   # vertically centred on bubble

        if edge == "right":
            # Pill sits to the LEFT of the bubble (bubble is on the right edge)
            final_geo = QRect(bx - self.PILL_W, cy, self.PILL_W, self.PILL_H)
        elif edge == "left":
            # Pill sits to the RIGHT of the bubble (bubble is on the left edge)
            final_geo = QRect(bx + bw, cy, self.PILL_W, self.PILL_H)
        elif edge == "top":
            # Pill sits BELOW the bubble (bubble is at the top)
            final_geo = QRect(
                bx + (bw - self.PILL_W) // 2,
                by + bh,
                self.PILL_W,
                self.PILL_H,
            )
        else:  # bottom
            # Pill sits ABOVE the bubble (bubble is at the bottom)
            final_geo = QRect(
                bx + (bw - self.PILL_W) // 2,
                by - self.PILL_H,
                self.PILL_W,
                self.PILL_H,
            )

        # Set final geometry immediately — no slide, zero lag on Windows
        self.setGeometry(final_geo)

        # Abort any running hide timer / animation, reset to transparent
        self._hide_timer.stop()
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        self._opacity_effect.setOpacity(0.0)

        # Show the widget (invisible until fade starts)
        self.show()
        self.raise_()

        # Fade in 0.0 → 1.0, then start the auto-hide countdown
        self._run_opacity_anim(
            start=0.0,
            end=1.0,
            duration_ms=self.FADE_IN_MS,
            finished_cb=lambda: self._hide_timer.start(self.DISPLAY_MS),
        )

    def _animate_hide(self) -> None:
        """Fade out 1.0 → 0.0, then hide the widget."""
        self._run_opacity_anim(
            start=self._opacity_effect.opacity(),
            end=0.0,
            duration_ms=self.FADE_OUT_MS,
            finished_cb=self.hide,
        )



# ═════════════════════════════════════════════════════════════════════════════
# STATUS ORB — voice-first hero element
# ═════════════════════════════════════════════════════════════════════════════

class StatusOrb(QWidget):
    """
    Animated central orb that visualises Atlas's live state.

    States
    ------
    idle       — cyan, slow breathing pulse  ("system alive")
    listening  — scarlet, fast reactive rings
    thinking   — gold, rotating scan arc
    processing — gold (transcribing)
    speaking   — bright cyan, audio-style pulse

    Clicking the orb is a shortcut for the talk hotkey.
    """

    clicked = Signal()
    SIZE = 150

    _GLYPHS = {
        "idle":       "⚡",
        "listening":  "🎙",
        "thinking":   "◌",
        "processing": "◌",
        "speaking":   "🔊",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._state = "idle"
        self._color = QColor(PAL["cyan"])
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_state(self, state: str, color: str | QColor) -> None:
        self._state = state
        self._color = QColor(color) if isinstance(color, str) else color
        self.update()

    def _tick(self) -> None:
        if not self.isVisible():
            return
        # Idle breathes slowly; active states animate faster.
        speed = {"listening": 0.34, "thinking": 0.22, "processing": 0.22,
                 "speaking": 0.30}.get(self._state, 0.09)
        self._phase += speed
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        import math

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        base = float(min(self.width(), self.height()))
        r0 = base * 0.20
        col = self._color
        s = self._state

        # Pulse waveform per state.
        if s == "listening":
            pulse = 0.5 + 0.5 * math.sin(self._phase * 3.0)
            rings = 3
        elif s in ("thinking", "processing"):
            pulse = 0.5 + 0.5 * math.sin(self._phase * 2.0)
            rings = 2
        elif s == "speaking":
            pulse = 0.5 + 0.5 * math.sin(self._phase * 3.4)
            rings = 3
        else:  # idle breathing
            pulse = 0.5 + 0.5 * math.sin(self._phase * 1.2)
            rings = 2

        # Concentric glow rings radiating outward.
        p.setPen(Qt.NoPen)
        for i in range(rings, 0, -1):
            rr = r0 + i * (base * 0.055) + pulse * (base * 0.05)
            alpha = int(max(0, 64 - i * 16) * (0.45 + 0.55 * pulse))
            c = QColor(col)
            c.setAlpha(alpha)
            p.setBrush(c)
            p.drawEllipse(QPointF(cx, cy), rr, rr)

        # Core orb with a soft radial gradient.
        grad = QRadialGradient(cx, cy - r0 * 0.2, r0 * 1.2)
        top = QColor(col).lighter(135)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, QColor(col))
        p.setBrush(QBrush(grad))
        ring_pen = QPen(QColor(col).lighter(160), 2)
        p.setPen(ring_pen)
        p.drawEllipse(QPointF(cx, cy), r0, r0)

        # Rotating scan arc while thinking/transcribing.
        if s in ("thinking", "processing"):
            pen = QPen(QColor("#FFFFFF"))
            pen.setWidth(3)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            ar = r0 + base * 0.11
            rect = QRectF(cx - ar, cy - ar, 2 * ar, 2 * ar)
            start = int(self._phase * 180.0 / math.pi) % 360
            p.drawArc(rect, -start * 16, 100 * 16)

        # Centre glyph.
        p.setPen(QColor("#05060A"))
        p.setFont(QFont("Segoe UI Emoji", int(base * 0.15)))
        p.drawText(self.rect(), Qt.AlignCenter, self._GLYPHS.get(s, "⚡"))


# ═════════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION WINDOW
# ═════════════════════════════════════════════════════════════════════════════

PASSWORD_RULES = [
    ("len", "At least 8 characters", lambda p: len(p) >= 8),
    ("upper", "An uppercase letter (A-Z)", lambda p: any(c.isupper() for c in p)),
    ("lower", "A lowercase letter (a-z)", lambda p: any(c.islower() for c in p)),
    ("digit", "A number (0-9)", lambda p: any(c.isdigit() for c in p)),
    ("special", "A special character (!@#$…)",
     lambda p: any(not c.isalnum() for c in p)),
]


class FeedbackDialog(QDialog):
    """Send Feedback — posts to configured endpoint (stub OK offline)."""

    def __init__(self, telemetry, parent=None, email: str = "") -> None:
        super().__init__(parent)
        self.telemetry = telemetry
        self._email = email
        self.setWindowTitle("Send Feedback")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Tell us what happened — bugs, ideas, or praise."))
        self.msg = QTextEdit()
        self.msg.setPlaceholderText("Your message…")
        self.msg.setMaximumHeight(140)
        lay.addWidget(self.msg)
        self.chk_shot = QCheckBox("Include a screenshot with this report")
        lay.addWidget(self.chk_shot)
        self.chk_ctx = QCheckBox("Include anonymized app context (version, OS)")
        self.chk_ctx.setChecked(True)
        lay.addWidget(self.chk_ctx)
        row = QHBoxLayout()
        send = QPushButton("Send")
        send.clicked.connect(self._send)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(send)
        lay.addLayout(row)

    def _send(self) -> None:
        text = self.msg.toPlainText().strip()
        if len(text) < 3:
            return
        shot = None
        if self.chk_shot.isChecked() and _CORE:
            try:
                from atlas_core import capture_screen_b64
                shot = capture_screen_b64()
                shot = shot.b64 if shot else None
            except Exception:
                pass
        if self.telemetry:
            ok, msg = self.telemetry.send_feedback(
                text,
                email=self._email,
                screenshot_b64=shot,
                include_context=self.chk_ctx.isChecked(),
            )
        else:
            ok, msg = False, "Feedback service unavailable."
        parent = self.parent()
        if parent and hasattr(parent, "bridge"):
            parent.bridge.set_status.emit(msg)
        if ok:
            self.accept()


class TwoFactorDialog(QDialog):
    """Second-step verification after password sign-in."""

    def __init__(self, account_mgr, user_id: int, method: str, email: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.account = account_mgr
        self.user_id = user_id
        self.setWindowTitle("Two-step verification")
        self.setFixedWidth(360)
        self.setStyleSheet(LoginDialog._qss_static())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        hint = ("Enter the 6-digit code from your authenticator app."
                if method == "totp"
                else f"Enter the code sent to {email or 'your email'}.")
        lay.addWidget(QLabel(hint))
        self.code_in = QLineEdit()
        self.code_in.setObjectName("login_field")
        self.code_in.setPlaceholderText("6-digit code")
        self.code_in.setMaxLength(8)
        lay.addWidget(self.code_in)
        self.msg = QLabel("")
        self.msg.setStyleSheet(f"color: {PAL['danger']}; font-size: 11px;")
        lay.addWidget(self.msg)
        btn = QPushButton("Verify")
        btn.setObjectName("primary")
        btn.clicked.connect(self._verify)
        self.code_in.returnPressed.connect(self._verify)
        lay.addWidget(btn)
        if method == "email":
            resend = QPushButton("Resend code")
            resend.setObjectName("link")
            resend.clicked.connect(self._resend)
            lay.addWidget(resend, alignment=Qt.AlignCenter)
        self.verified = False

    def _verify(self) -> None:
        ok, msg, uid = self.account.complete_pending_2fa(self.code_in.text().strip())
        if ok:
            self.verified = True
            self.accept()
        else:
            self.msg.setText(msg)

    def _resend(self) -> None:
        ok, msg = self.account.start_2fa_challenge(
            self.user_id, getattr(self.account, "_pending_2fa_email", ""))
        self.msg.setStyleSheet(f"color: {PAL['success'] if ok else PAL['danger']}; "
                               f"font-size: 11px;")
        self.msg.setText(msg)


class LoginDialog(QDialog):
    """
    Local-first auth gate with separate Sign In and Create Account views.

    Create Account enforces a strong password with a live criteria checklist and
    a confirm-password field. When Supabase is configured the dialog authenticates
    against the cloud and syncs the user's data; otherwise it uses a local
    profile. On success, ``user_id`` / ``user_name`` are set.
    """

    def __init__(self, account_mgr, parent=None) -> None:
        super().__init__(parent)
        self.account = account_mgr
        self.user_id: int | None = None
        self.user_name: str = "default"
        self._busy = False
        self.cloud_on = bool(account_mgr and account_mgr.cloud_available)

        self.setWindowTitle("Welcome to Atlas")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setFixedWidth(400)
        self.setStyleSheet(self._qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 22)
        outer.setSpacing(10)

        title = QLabel("ATLAS")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"font-size: 26px; font-weight: 700; letter-spacing: 6px; "
            f"color: {PAL['cyan']};")
        outer.addWidget(title)

        self.subtitle = QLabel("")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setStyleSheet(f"font-size: 13px; color: {PAL['text']};")
        outer.addWidget(self.subtitle)

        status = QLabel("☁  Cloud sync ON" if self.cloud_on
                        else "⛶  Local profile (offline)")
        status.setAlignment(Qt.AlignCenter)
        status.setStyleSheet(
            f"font-size: 11px; color: "
            f"{PAL['success'] if self.cloud_on else PAL['muted']};")
        outer.addWidget(status)
        outer.addSpacing(4)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_signin_view())   # index 0
        self.stack.addWidget(self._build_create_view())   # index 1
        if self.cloud_on:
            self.stack.addWidget(self._build_otp_view())    # index 2
        outer.addWidget(self.stack)

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet(f"font-size: 11px; color: {PAL['danger']};")
        outer.addWidget(self.msg)

        self.btn_google = QPushButton("Continue with Google  (soon)")
        self.btn_google.setObjectName("ghost")
        self.btn_google.setEnabled(False)
        self.btn_google.setToolTip("Google sign-in is coming after the trial.")
        outer.addWidget(self.btn_google)

        self.btn_guest = QPushButton("Continue as guest")
        self.btn_guest.setObjectName("link")
        self.btn_guest.clicked.connect(self._guest)
        outer.addWidget(self.btn_guest, alignment=Qt.AlignCenter)

        self._show_signin()

    # ── views ───────────────────────────────────────────────────────────────────

    def _build_signin_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.si_id = QLineEdit()
        self.si_id.setObjectName("login_field")
        self.si_id.setPlaceholderText("Email" if self.cloud_on else "Display name")
        self.si_pass = QLineEdit()
        self.si_pass.setObjectName("login_field")
        self.si_pass.setPlaceholderText("Password")
        self.si_pass.setEchoMode(QLineEdit.Password)
        self.si_pass.returnPressed.connect(self._sign_in)
        lay.addWidget(self.si_id)
        lay.addWidget(self.si_pass)
        btn = QPushButton("Sign In")
        btn.setObjectName("primary")
        btn.clicked.connect(self._sign_in)
        lay.addWidget(btn)
        self.btn_signin = btn
        switch = QPushButton("New here?  Create an account")
        switch.setObjectName("link")
        switch.clicked.connect(self._show_create)
        lay.addWidget(switch, alignment=Qt.AlignCenter)
        if self.cloud_on:
            otp_link = QPushButton("Sign in with email code instead")
            otp_link.setObjectName("link")
            otp_link.clicked.connect(self._show_otp)
            lay.addWidget(otp_link, alignment=Qt.AlignCenter)
        return w

    def _build_otp_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.otp_email = QLineEdit()
        self.otp_email.setObjectName("login_field")
        self.otp_email.setPlaceholderText("Email")
        self.otp_code = QLineEdit()
        self.otp_code.setObjectName("login_field")
        self.otp_code.setPlaceholderText("6-digit code from email")
        self.otp_code.setMaxLength(8)
        lay.addWidget(self.otp_email)
        self.btn_send_code = QPushButton("Send code")
        self.btn_send_code.setObjectName("ghost")
        self.btn_send_code.clicked.connect(self._send_otp)
        lay.addWidget(self.btn_send_code)
        lay.addWidget(self.otp_code)
        btn = QPushButton("Verify & Sign In")
        btn.setObjectName("primary")
        btn.clicked.connect(self._verify_otp_login)
        self.otp_code.returnPressed.connect(self._verify_otp_login)
        lay.addWidget(btn)
        self.btn_otp = btn
        back = QPushButton("← Back to password sign-in")
        back.setObjectName("link")
        back.clicked.connect(self._show_signin)
        lay.addWidget(back, alignment=Qt.AlignCenter)
        return w

    def _build_create_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.cr_name = QLineEdit()
        self.cr_name.setObjectName("login_field")
        self.cr_name.setPlaceholderText("Display name")
        self.cr_email = QLineEdit()
        self.cr_email.setObjectName("login_field")
        self.cr_email.setPlaceholderText(
            "Email" + ("" if self.cloud_on else " (optional)"))
        self.cr_pass = QLineEdit()
        self.cr_pass.setObjectName("login_field")
        self.cr_pass.setPlaceholderText("Password")
        self.cr_pass.setEchoMode(QLineEdit.Password)
        self.cr_pass2 = QLineEdit()
        self.cr_pass2.setObjectName("login_field")
        self.cr_pass2.setPlaceholderText("Confirm password")
        self.cr_pass2.setEchoMode(QLineEdit.Password)
        for f in (self.cr_name, self.cr_email, self.cr_pass, self.cr_pass2):
            lay.addWidget(f)
        self.cr_pass.textChanged.connect(self._refresh_strength)
        self.cr_pass2.textChanged.connect(self._refresh_strength)
        self.cr_pass2.returnPressed.connect(self._create)

        # Live password-strength checklist
        self._rule_labels: dict[str, QLabel] = {}
        rules_box = QVBoxLayout()
        rules_box.setSpacing(2)
        for key, label, _ in PASSWORD_RULES:
            lbl = QLabel(f"○  {label}")
            lbl.setStyleSheet(f"font-size: 10px; color: {PAL['muted']};")
            self._rule_labels[key] = lbl
            rules_box.addWidget(lbl)
        self._match_lbl = QLabel("○  Passwords match")
        self._match_lbl.setStyleSheet(f"font-size: 10px; color: {PAL['muted']};")
        rules_box.addWidget(self._match_lbl)
        lay.addLayout(rules_box)

        btn = QPushButton("Create Account")
        btn.setObjectName("primary")
        btn.clicked.connect(self._create)
        btn.setEnabled(False)
        lay.addWidget(btn)
        self.btn_create = btn
        switch = QPushButton("Already have an account?  Sign in")
        switch.setObjectName("link")
        switch.clicked.connect(self._show_signin)
        lay.addWidget(switch, alignment=Qt.AlignCenter)
        return w

    def _show_signin(self) -> None:
        self.subtitle.setText("Welcome back")
        self.msg.clear()
        self.stack.setCurrentIndex(0)
        self.adjustSize()

    def _show_create(self) -> None:
        self.subtitle.setText("Create your account")
        self.msg.clear()
        self.stack.setCurrentIndex(1)
        self._refresh_strength()
        self.adjustSize()

    def _show_otp(self) -> None:
        self.subtitle.setText("Sign in with email code")
        self.msg.clear()
        self.stack.setCurrentIndex(2)
        self.adjustSize()

    def _send_otp(self) -> None:
        if not self.account or self._busy:
            return
        email = self.otp_email.text().strip()
        self._set_busy(True, "Sending code…")
        ok, msg = self.account.cloud.send_email_otp(email)
        self._set_busy(False)
        self.msg.setStyleSheet(
            f"font-size: 11px; color: {PAL['success'] if ok else PAL['danger']};")
        self.msg.setText(msg)

    def _verify_otp_login(self) -> None:
        if not self.account or self._busy:
            return
        email = self.otp_email.text().strip()
        code = self.otp_code.text().strip()
        self._set_busy(True, "Verifying…")
        try:
            ok, msg, uid = self.account.login_otp_cloud(email, code)
        except Exception as exc:
            return self._fail(str(exc))
        if ok:
            name = email.split("@")[0]
            return self._finish(uid, name)
        self._fail(msg or "Verification failed.")

    # ── password strength ───────────────────────────────────────────────────────

    def _password_ok(self, pw: str) -> bool:
        return all(fn(pw) for _, _, fn in PASSWORD_RULES)

    def _refresh_strength(self) -> None:
        pw = self.cr_pass.text()
        for key, label, fn in PASSWORD_RULES:
            met = fn(pw)
            lbl = self._rule_labels[key]
            lbl.setText(f"{'●' if met else '○'}  {label}")
            lbl.setStyleSheet(
                f"font-size: 10px; color: "
                f"{PAL['success'] if met else PAL['muted']};")
        match = bool(pw) and pw == self.cr_pass2.text()
        self._match_lbl.setText(f"{'●' if match else '○'}  Passwords match")
        self._match_lbl.setStyleSheet(
            f"font-size: 10px; color: {PAL['success'] if match else PAL['muted']};")
        self.btn_create.setEnabled(self._password_ok(pw) and match)

    # ── shared styling / state ──────────────────────────────────────────────────

    def _qss(self) -> str:
        return LoginDialog._qss_static()

    @staticmethod
    def _qss_static() -> str:
        return (
            f"QDialog {{ background: {PAL['surface']}; }}"
            f"QLabel {{ color: {PAL['text']}; }}"
            f"QLineEdit#login_field {{ background: {PAL['surface_2']}; "
            f"border: 1px solid {PAL['border']}; border-radius: 8px; "
            f"padding: 10px; color: {PAL['text']}; font-size: 13px; }}"
            f"QLineEdit#login_field:focus {{ border: 1px solid {PAL['cyan']}; }}"
            f"QPushButton#primary {{ background: {PAL['cyan']}; color: {PAL['bg']}; "
            f"border-radius: 8px; padding: 10px; font-weight: 600; }}"
            f"QPushButton#primary:hover {{ background: {PAL['text']}; }}"
            f"QPushButton#primary:disabled {{ background: {PAL['border']}; "
            f"color: {PAL['muted']}; }}"
            f"QPushButton#ghost {{ background: {PAL['surface_2']}; color: {PAL['text']}; "
            f"border: 1px solid {PAL['border']}; border-radius: 8px; padding: 10px; }}"
            f"QPushButton#ghost:hover {{ border: 1px solid {PAL['cyan']}; color: {PAL['cyan']}; }}"
            f"QPushButton#ghost:disabled {{ color: {PAL['muted']}; }}"
            f"QPushButton#link {{ background: transparent; color: {PAL['muted']}; "
            f"border: none; font-size: 11px; }}"
            f"QPushButton#link:hover {{ color: {PAL['cyan']}; }}"
        )

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        for b in (self.btn_signin, self.btn_create, self.btn_guest):
            b.setEnabled(not busy)
        if not busy:
            self._refresh_strength()   # restore create-button gating
        if label:
            self.msg.setStyleSheet(f"font-size: 11px; color: {PAL['muted']};")
            self.msg.setText(label)
        QApplication.processEvents()

    def _fail(self, text: str) -> None:
        self.msg.setStyleSheet(f"font-size: 11px; color: {PAL['danger']};")
        self.msg.setText(text)
        self._set_busy(False)

    def _finish(self, uid: int, name: str) -> None:
        self.user_id = int(uid)
        self.user_name = name or "default"
        self.accept()

    # ── actions ─────────────────────────────────────────────────────────────────

    def _sign_in(self) -> None:
        if self._busy or not self.account:
            return
        ident = self.si_id.text().strip()
        pw = self.si_pass.text()
        self._set_busy(True, "Signing in…")
        try:
            if self.account.cloud_available:
                if not ident or not pw:
                    return self._fail("Enter your email and password.")
                ok, msg, uid = self.account.login_cloud(ident, pw)
            else:
                if not ident:
                    return self._fail("Enter your name.")
                ok, msg, uid = self.account.login_local(ident, pw or None)
        except Exception as exc:
            return self._fail(f"Sign-in error: {exc}")
        if ok:
            if msg == "2FA_REQUIRED":
                needs, method = self.account.needs_2fa(uid)
                twofa = TwoFactorDialog(
                    self.account, uid, method,
                    getattr(self.account, "_pending_2fa_email", ident), self)
                if twofa.exec() == QDialog.Accepted and twofa.verified:
                    disp = ident.split("@")[0] if "@" in ident else ident
                    return self._finish(uid, disp)
                return self._fail("Two-step verification required.")
            self._finish(uid, ident.split("@")[0] if "@" in ident else ident)
        else:
            self._fail(msg or "Sign-in failed.")

    def _create(self) -> None:
        if self._busy or not self.account:
            return
        name = self.cr_name.text().strip()
        email = self.cr_email.text().strip()
        pw = self.cr_pass.text()
        if not name:
            return self._fail("Choose a display name.")
        if not self._password_ok(pw):
            return self._fail("Password doesn't meet all the requirements.")
        if pw != self.cr_pass2.text():
            return self._fail("Passwords don't match.")
        self._set_busy(True, "Creating account…")
        try:
            if self.account.cloud_available:
                if not email:
                    return self._fail("Enter an email to create a cloud account.")
                ok, msg, uid = self.account.register_cloud(name, email, pw)
            else:
                ok, msg, uid = self.account.register_local(name, email or None, pw)
        except Exception as exc:
            return self._fail(f"Sign-up error: {exc}")
        if ok:
            self._finish(uid, name)
        else:
            self._fail(msg or "Could not create the account.")

    def _guest(self) -> None:
        if self._busy or not self.account:
            return self.reject()
        try:
            ok, _msg, uid = self.account.login_local("default")
            if ok:
                return self._finish(uid, "default")
        except Exception:
            pass
        self.reject()


class AtlasWindow(QMainWindow):

    def __init__(self, daemon_client: Optional["DaemonClient"] = None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(420, 520)
        self.resize(540, 760)
        # Companion overlay: no native edge resize bands (prevents stray resize cursors).
        self._mouse_capture_locked = False

        self._is_floating   = False
        self.bridge         = SignalBridge()
        self.account        = None   # set by main() after the login gate
        self._daemon_client = daemon_client

        # ── Signal connections ────────────────────────────────────────────────
        self.bridge.append_text.connect(self._append_response)
        self.bridge.set_status.connect(self._set_status)
        self.bridge.notify_pill.connect(self._show_pill)
        self.bridge.start_thinking.connect(self._start_thinking)
        self.bridge.thinking_done.connect(self._stop_thinking)
        self.bridge.stream_token.connect(self._on_stream_token)
        self.bridge.stream_started.connect(self._on_stream_started)
        self.bridge.stream_complete.connect(self._on_stream_complete_slot)
        self.bridge.token_usage.connect(self._on_token_usage)
        self.bridge.spatial_coords.connect(
            self._on_spatial_coords,
            Qt.ConnectionType.QueuedConnection,
        )
        self.bridge.ptt_active.connect(self._on_ptt_active)
        self.bridge.ptt_breakin.connect(self._on_ptt_breakin)
        self.bridge.listen_state.connect(self._on_listen_state)
        self.bridge.request_permission.connect(self._on_permission_request)
        self.bridge.accent_changed.connect(self._on_accent_changed)
        self.bridge.step_pending.connect(self._on_step_pending)
        self.bridge.user_notice.connect(self._on_user_notice)
        self.bridge.interaction_error.connect(self._on_interaction_error)
        self.bridge.safety_prompt_req.connect(self._on_safety_prompt_req)
        self.bridge.task_stop.connect(self._stop_running_task)
        self.bridge.guide_step_started.connect(self._on_guide_step_started)
        self.bridge.step_verified.connect(self._on_step_verified)

        self.telemetry = TelemetryClient() if HAS_TELEMETRY and TelemetryClient else None

        # ── Status-bar persistence (Bug #23) ──────────────────────────────────
        # Messages are held for at least _STATUS_MIN_MS before being replaced.
        self._STATUS_MIN_MS:    int   = 2000
        self._last_status_time: float = 0.0
        self._pending_status:   str   = ""
        self._status_hold_timer = QTimer(self)
        self._status_hold_timer.setSingleShot(True)
        self._status_hold_timer.timeout.connect(self._flush_pending_status)

        # ── Stop event for generation interrupt ──────────────────────────────
        self._stop_gen = threading.Event()

        # ── Backend engines — StateEngine lives in atlas_daemon; UI uses proxy ──
        self.state = None
        if not daemon_client:
            raise RuntimeError(
                "Atlas requires the background daemon. "
                "Run: powershell -File scripts/install_atlas_daemon.ps1"
            )
        if _CORE and HAS_DAEMON and AtlasStateProxy:
            self.state = AtlasStateProxy(
                daemon_client,
                on_chunk=self.bridge.stream_token.emit,
                on_complete=self._on_stream_complete,
                on_error=self._on_engine_error,
                on_coordinates=self.bridge.spatial_coords.emit,
                on_token_usage=self.bridge.token_usage.emit,
                step_ui_handler=self._post_step_pending,
            )
            self.state.register_permission_handler(self._daemon_fs_permission)
            self.state.register_typed_confirm_handler(self._daemon_typed_confirm)
        self.audio = (
            AudioEngine(
                on_transcript=self._on_transcript,
                on_status=lambda m: self.bridge.set_status.emit(m),
                on_state=self.bridge.listen_state.emit,
            )
            if _CORE else None
        )
        if self.state and self.audio:
            self.state.audio_watcher.bind_audio(self.audio)

        # ── Feature state ─────────────────────────────────────────────────────
        self.copilot_active       = False
        self.watch_active       = False
        self.highlight_active   = False
        self.stealth_active     = False
        self._stealth_before_interview = False
        self._interview_headphone_shown = False
        self._capture_exclusion_warned = False
        self.camera_active      = False     # Req 4: webcam toggle
        self.fs_access_active   = False     # Req 7: FS permission gate
        self.last_clipboard     = ""
        self._watch_interval    = 5
        self._watch_sensitivity = "Medium"
        self._typed_confirm_event = threading.Event()
        self._typed_confirm_answer = False

        if self.state:
            try:
                from atlas_policy import PolicyContext, set_policy_context_provider

                def _policy_ctx() -> PolicyContext:
                    return PolicyContext(
                        safety_mode=str(
                            getattr(self.state, "safety_mode", None) or "off"
                        ),
                        fs_access_active=bool(self.fs_access_active),
                        execution_blocked=bool(
                            getattr(self.state, "execution_blocked", False)
                        ),
                    )

                set_policy_context_provider(_policy_ctx)
            except ImportError:
                pass

        # ── Push-to-talk state (global Ctrl/Alt+Space) ───────────────────────
        self._ptt_engaged = False   # True between key-down and key-release

        self._build_ui()
        self._bind_hotkeys()

        # ── Standalone float widgets ──────────────────────────────────────────
        self._bubble   = FloatBubble()
        self._bubble.clicked.connect(self._leave_float)
        self._bubble.dragged.connect(self._on_bubble_dragged)
        self._pill_win = PillNotification()

        # ── Session timer ────────────────────────────────────────────────────
        self._session_timer = QTimer(self)
        self._session_timer.setInterval(1000)
        self._session_timer.timeout.connect(self._tick_session)
        self._session_timer.start()

        # ── State engine events ───────────────────────────────────────────────
        if self.state:
            self.state.on_event(self._on_state_event)

        # ── Skip Audio visibility timer (Bug #6) ─────────────────────────────
        # Polls voice_engine.is_speaking every 250 ms; shows/hides the button.
        self._skip_audio_timer = QTimer(self)
        self._skip_audio_timer.setInterval(250)
        self._skip_audio_timer.timeout.connect(self._update_skip_audio_visibility)
        self._skip_audio_timer.start()

        # ── Stealth matrix: cloak the main window immediately if activated ───
        # (initial state is OFF; cloak applied in _toggle_stealth)

        # ── Holo overlay + autosave ───────────────────────────────────────────
        self.overlay = HoloOverlay() if HAS_OVERLAY and HoloOverlay else None
        # Holo overlay stays hidden until a guide marker or focus ring is needed.

        self.agent_cursor = (
            AgentCursorOverlay() if HAS_OVERLAY and AgentCursorOverlay else None
        )
        # Agent cursor stays hidden until a DO-mode step animates it.

        self.task_stop_overlay = (
            TaskStopOverlay(self._stop_running_task)
            if HAS_OVERLAY and TaskStopOverlay else None
        )

        if self.state:
            self.state._safety_prompt = self._safety_prompt
            self.state._task_confirm_cb = self._task_confirm
            self._safety_event = threading.Event()
            self._safety_answer = True

        self._chat_messages: list[dict] = []
        self._streaming_html = ""
        self._is_streaming = False
        self._interaction_source = ""
        self._stream_start_ts = 0.0
        self._render_pending = False
        self._token_prompt = 0
        self._token_completion = 0
        self._clipboard = QApplication.clipboard()
        self._clipboard.dataChanged.connect(self._on_clipboard_changed)

        self._action_mic = QAction("Microphone", self, checkable=True)
        self._action_spk = QAction("Speaker Capture", self, checkable=True)
        self._action_ve = QAction("Voice Engine", self, checkable=True)
        self._action_hl = QAction("Clipboard Watch", self, checkable=True)
        self._action_watch = QAction("Screen Watcher", self, checkable=True)
        self._action_cam = QAction("Camera Vision", self, checkable=True)
        self._action_mic.toggled.connect(self._toggle_mic)
        self._action_spk.toggled.connect(self._toggle_speaker)
        self._action_hl.toggled.connect(self._toggle_highlight_action)
        self._action_watch.toggled.connect(self._toggle_watch_action)
        self._action_cam.toggled.connect(self._toggle_camera_action)
        if voice_engine:
            # TTS is live by default — reflect that in the toggle so users know
            # Atlas WILL speak its replies (and can mute it here if desired).
            voice_engine.unmute()
            self._action_ve.setChecked(True)
            self._action_ve.toggled.connect(self._toggle_voice_action)

        self.settings_dialog = SettingsDialog(self, self.state, self.audio, self) if _CORE else None
        self._ambient_pulse_on = False
        self._ambient_border_phase = 0
        self._ambient_timer = QTimer(self)
        self._ambient_timer.setInterval(800)
        self._ambient_timer.timeout.connect(self._tick_ambient_border)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(5 * 60 * 1000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        # ── Deferred startup checks ───────────────────────────────────────────
        if voice_engine:
            voice_engine.preload()
        QTimer.singleShot(80, self._show_startup_message)
        QTimer.singleShot(250, self._run_startup_checks)
        QTimer.singleShot(400, self._offer_autosave_restore)

    # ═════════════════════════════════════════════════════════════════════════
    # UI CONSTRUCTION
    # ═════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        self.root_widget = QWidget()
        self.setCentralWidget(self.root_widget)

        self.main_lay = QVBoxLayout(self.root_widget)
        self.main_lay.setContentsMargins(10, 10, 10, 10)
        self.main_lay.setSpacing(8)

        # ══ CONTROL RIBBON ════════════════════════════════════════════════════
        # Docked in the flow directly above the dialogue box (below the orb).  It
        # rests dim (~20% opacity) and brightens to full while hovered, so it's
        # always discoverable without cluttering the voice-first hero.
        self.header = QFrame()
        self.header.setObjectName("titlebar")
        self.header.setFixedHeight(40)

        hdr_lay = QHBoxLayout(self.header)
        hdr_lay.setContentsMargins(12, 0, 10, 0)
        hdr_lay.setSpacing(7)

        self.logo = QLabel("⚡ ATLAS")
        self.logo.setStyleSheet(
            f"color: {PAL['cyan']}; font-weight: bold; font-size: 15px; letter-spacing: 2px;"
        )

        # ── "System alive" glow dot — faint cyan opacity pulse while idle ─────
        self.alive_dot = QLabel("●")
        self.alive_dot.setToolTip("Atlas is online")
        self.alive_dot.setStyleSheet(
            f"color: {PAL['cyan']}; font-size: 10px; background: transparent; padding-left: 2px;"
        )

        hdr_lay.addWidget(self.logo)
        hdr_lay.addWidget(self.alive_dot)

        self.mode_pill = QLabel("ATLAS")
        self.mode_pill.setStyleSheet(
            f"color: {PAL['cyan']}; background: rgba(0,212,255,0.10);"
            f"border: 1px solid {PAL['cyan_dim']}; border-radius: 8px;"
            f"font-size: 9px; font-weight: bold; letter-spacing: 1px;"
            f"padding: 2px 7px;"
        )
        hdr_lay.addWidget(self.mode_pill)

        self.btn_task_stop = QPushButton("■ STOP")
        self.btn_task_stop.setVisible(False)
        self.btn_task_stop.setFixedHeight(28)
        self.btn_task_stop.setCursor(Qt.PointingHandCursor)
        self.btn_task_stop.setToolTip(
            "Stop the autonomous task (Ctrl+Shift+Esc works even when Atlas is in the background)"
        )
        self.btn_task_stop.setStyleSheet(
            "QPushButton {"
            "  background: #DC2626;"
            "  color: #FFFFFF;"
            "  border: 2px solid #FFFFFF;"
            "  border-radius: 7px;"
            "  font-size: 11px;"
            "  font-weight: bold;"
            "  letter-spacing: 1px;"
            "  padding: 2px 10px;"
            "}"
            "QPushButton:hover { background: #B91C1C; }"
            "QPushButton:pressed { background: #991B1B; }"
        )
        self.btn_task_stop.clicked.connect(self._stop_running_task)
        hdr_lay.addWidget(self.btn_task_stop)

        hdr_lay.addStretch()

        self.btn_copilot = QPushButton("👁 Copilot")
        self.btn_copilot.setCheckable(True)
        self.btn_copilot.setFixedWidth(78)
        self.btn_copilot.setToolTip(
            "Copilot — passive screen awareness; optional always-on voice")
        self.btn_copilot.setStyleSheet(
            f"QPushButton {{ background: {PAL['surface_2']}; color: {PAL['muted']}; "
            f"border: 1px solid {PAL['border']}; border-radius: 6px; padding: 3px 6px; "
            f"font-size: 10px; }}"
            f"QPushButton:checked {{ background: rgba(245,158,11,0.22); color: #F59E0B; "
            f"border: 1px solid #D97706; }}")
        self.btn_copilot.toggled.connect(self._on_copilot_toggled)
        hdr_lay.addWidget(self.btn_copilot)

        self.btn_focus = QPushButton("Focus")
        self.btn_focus.setCheckable(True)
        self.btn_focus.setFixedWidth(58)
        self.btn_focus.setToolTip(
            "Focus mode — terse interview-style replies; Atlas always sees your screen")
        self.btn_focus.setStyleSheet(
            f"QPushButton {{ background: {PAL['surface_2']}; color: {PAL['muted']}; "
            f"border: 1px solid {PAL['border']}; border-radius: 6px; padding: 3px 6px; "
            f"font-size: 10px; }}"
            f"QPushButton:checked {{ background: rgba(155,89,182,0.2); color: #9B59B6; "
            f"border: 1px solid #6C3483; }}")
        self.btn_focus.toggled.connect(self._on_focus_toggled)
        hdr_lay.addWidget(self.btn_focus)

        # ── Opacity control (icon + slider) ───────────────────────────────────
        op_icon = QLabel("◑")
        op_icon.setToolTip("Window transparency")
        op_icon.setStyleSheet(f"color: {PAL['muted']}; font-size: 13px; background: transparent;")
        hdr_lay.addWidget(op_icon)

        self.op_slider = QSlider(Qt.Horizontal)
        self.op_slider.setRange(20, 100)
        self.op_slider.setValue(95)
        self.op_slider.setFixedWidth(58)
        self.op_slider.setToolTip("Window transparency")
        self.op_slider.valueChanged.connect(self.set_window_opacity_pct)
        hdr_lay.addWidget(self.op_slider)

        hdr_lay.addWidget(self._hdr_separator())

        # Clearer, function-accurate glyphs for the action cluster.
        self.btn_float   = self._make_hdr_btn("🫧", "Float — shrink Atlas into a small floating bubble (click the bubble to restore)", self._toggle_float, PAL["cyan"])
        self.btn_stealth = self._make_hdr_btn("🫥", "Stealth — hide Atlas from screen capture / screen sharing", self._toggle_stealth, PAL["muted"])
        self.btn_refresh  = self._make_hdr_btn("⟳", "Hot-reload Atlas", self._hot_reload)
        self.btn_settings = self._make_hdr_btn("⚙", "Settings & Control Center", self._open_control_center)
        self.btn_close    = self._make_hdr_btn("✕", "Close Atlas", self.close, PAL["danger"])
        for b in [self.btn_float, self.btn_stealth, self.btn_refresh, self.btn_settings, self.btn_close]:
            hdr_lay.addWidget(b)

        # ── Profile cluster: name + avatar with a dropdown menu ───────────────
        hdr_lay.addWidget(self._hdr_separator())
        self.user_name_lbl = QLabel("Guest")
        self.user_name_lbl.setStyleSheet(
            f"color: {PAL['text']}; font-size: 11px; font-weight: 600; "
            f"background: transparent;")
        hdr_lay.addWidget(self.user_name_lbl)
        self.btn_profile = QPushButton("G")
        self.btn_profile.setObjectName("profile_btn")
        self.btn_profile.setFixedSize(28, 28)
        self.btn_profile.setToolTip("Your profile")
        self.btn_profile.setStyleSheet(
            f"QPushButton#profile_btn {{ background: {PAL['cyan']}; color: {PAL['bg']}; "
            f"border-radius: 14px; font-weight: 700; font-size: 12px; }}"
            f"QPushButton#profile_btn:hover {{ background: {PAL['text']}; }}")
        self.btn_profile.clicked.connect(self._show_profile_menu)
        hdr_lay.addWidget(self.btn_profile)
        self.header.installEventFilter(self)
        self.header.setStyleSheet(
            f"QFrame#titlebar {{ background: rgba(14,14,16,0.94); "
            f"border: 1px solid {PAL['border']}; border-radius: 8px; }}"
        )

        # ── Control Ribbon (always readable — no opacity effect) ──────────────

        # ══ ORB ZONE — voice-first hero ═══════════════════════════════════════
        self.orb_zone = QFrame()
        self.orb_zone.setObjectName("orbzone")
        oz_lay = QVBoxLayout(self.orb_zone)
        oz_lay.setContentsMargins(0, 6, 0, 2)
        oz_lay.setSpacing(2)

        self.orb = StatusOrb()
        self.orb.clicked.connect(self._on_orb_clicked)
        orb_row = QHBoxLayout()
        orb_row.addStretch()
        orb_row.addWidget(self.orb)
        orb_row.addStretch()
        oz_lay.addLayout(orb_row)

        self.orb_state_lbl = QLabel("Tap Ctrl + Space to talk")
        self.orb_state_lbl.setAlignment(Qt.AlignCenter)
        self.orb_state_lbl.setStyleSheet(
            f"color: {PAL['text']}; font-size: 13px; font-weight: 600; letter-spacing: 0.5px;"
        )
        oz_lay.addWidget(self.orb_state_lbl)

        self.orb_hint_lbl = QLabel("hold a thought, pause when you're done")
        self.orb_hint_lbl.setAlignment(Qt.AlignCenter)
        self.orb_hint_lbl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px;")
        oz_lay.addWidget(self.orb_hint_lbl)

        self.main_lay.addWidget(self.orb_zone)
        # Control ribbon docks here — below the orb, directly atop the dialogue box.
        self.main_lay.addWidget(self.header)

        self.copilot_status_lbl = QLabel("")
        self.copilot_status_lbl.setAlignment(Qt.AlignCenter)
        self.copilot_status_lbl.setStyleSheet(
            f"color: #F59E0B; font-size: 10px; font-weight: 600; padding: 2px 8px; "
            f"background: rgba(245,158,11,0.08); border-radius: 4px;"
        )
        self.copilot_status_lbl.hide()
        self.main_lay.addWidget(self.copilot_status_lbl)

        # ══ WORKSPACE ═════════════════════════════════════════════════════════
        self.workspace = QFrame()
        self.workspace.setObjectName("workspace")
        ws_lay = QVBoxLayout(self.workspace)
        ws_lay.setContentsMargins(0, 0, 0, 0)
        ws_lay.setSpacing(0)

        # Response area
        resp_lay = QVBoxLayout()
        resp_lay.setContentsMargins(14, 12, 14, 4)
        resp_lay.setSpacing(6)

        util_lay = QHBoxLayout()
        util_lay.addWidget(
            QLabel("CONVERSATION",
                   styleSheet=f"color: {PAL['cyan_dim']}; font-size: 10px; font-weight: bold; letter-spacing: 1px;")
        )
        util_lay.addStretch()

        # ── Skip Audio button — hidden until TTS is actually playing (Bug #6) ──
        self.btn_skip_audio = QPushButton("⏭ Skip Audio")
        self.btn_skip_audio.setStyleSheet(
            f"QPushButton {{ background: rgba(0,212,255,0.10); color: {PAL['cyan']};"
            f"  border: 1px solid {PAL['cyan_dim']}; border-radius: 5px;"
            f"  font-size: 11px; padding: 2px 8px; }}"
            f"QPushButton:hover {{ background: {PAL['cyan_dim']}; color: {PAL['bg']}; }}"
        )
        self.btn_skip_audio.setToolTip("Stop the current voice output and clear the TTS queue")
        self.btn_skip_audio.clicked.connect(self._do_skip_audio)
        self.btn_skip_audio.hide()   # shown only while voice_engine.is_speaking
        util_lay.addWidget(self.btn_skip_audio)

        btn_download = QPushButton("⬇ Download")
        btn_download.setStyleSheet(f"color: {PAL['muted']}; font-size: 11px; padding: 2px 6px;")
        btn_download.setToolTip("Download this conversation as a PDF")
        btn_download.clicked.connect(self._download_pdf)
        btn_clear = QPushButton("🗑 Clear")
        btn_clear.setStyleSheet(f"color: {PAL['muted']}; font-size: 11px; padding: 2px 6px;")
        btn_clear.clicked.connect(self._clear_text)
        util_lay.addWidget(btn_download)
        util_lay.addWidget(btn_clear)
        resp_lay.addLayout(util_lay)

        # Thinking bar
        self.thinking_bar = QProgressBar()
        self.thinking_bar.setFixedHeight(2)
        self.thinking_bar.setTextVisible(False)
        self.thinking_bar.setRange(0, 0)
        self.thinking_bar.setStyleSheet(
            f"QProgressBar {{ background: {PAL['surface_2']}; border: none; border-radius: 1px; }}"
            f"QProgressBar::chunk {{"
            f"  background: qlineargradient("
            f"    x1:0, y1:0, x2:1, y2:0,"
            f"    stop:0 {PAL['cyan_dim']}, stop:0.5 {PAL['cyan']},"
            f"    stop:1 {PAL['gold']}); border-radius: 1px; }}"
        )
        self.thinking_bar.hide()
        resp_lay.addWidget(self.thinking_bar)

        self.chat_view = ChatView()
        self.chat_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.chat_view.setMinimumHeight(200)
        self.chat_view.anchor_clicked.connect(self._on_chat_anchor)
        self.chat_view.image_dropped.connect(self._on_chat_image_dropped)
        self.chat_view.message_added.connect(self._wire_message_card)
        resp_lay.addWidget(self.chat_view, 1)

        # Legacy alias — some helpers still reference text_area
        self.text_area = self.chat_view._scroll

        ws_lay.addLayout(resp_lay)

        # Status bar
        self.status_lbl = QLabel("  Ready")
        self.status_lbl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; padding: 4px 14px;")
        ws_lay.addWidget(self.status_lbl)

        self.chat_composer = ChatComposer(self)
        self.chat_composer.setObjectName("action_dock")
        self.chat_composer.setStyleSheet(
            f"QFrame#action_dock {{ background: {PAL['surface_2']}; "
            f"border-radius: 12px; border: 1px solid {PAL['border']}; }}"
        )
        self.chat_composer.submitted.connect(self._submit_query)
        self.chat_composer.stop_requested.connect(self._do_stop_gen)
        self.chat_composer.attach_file.connect(self._on_composer_attach)
        self.chat_composer.screenshot_requested.connect(self._on_composer_screenshot)
        ws_lay.addWidget(self.chat_composer)

        # Legacy aliases for hot-reload / menu paths
        self.input_row = self.chat_composer
        self.ask_entry = self.chat_composer._input
        self.btn_send = self.chat_composer._send_btn
        self.btn_stop = self.chat_composer._send_btn
        self.btn_plus = self.chat_composer._tool_btns[-1]
        self._pending_attachments: list[dict] = []
        self._last_user_query = ""
        self._md_ai_buffer = ""
        self._md_ai_streaming = False
        self._md_plain_prefix = ""

        self.token_footer = QLabel(" Tokens: 0 prompt · 0 completion | Session: 00:00 · 0 turns")
        self.token_footer.setStyleSheet(
            f"color: {PAL['muted']}; font-size: 9px; padding: 4px 14px; "
            f"border-top: 1px solid {PAL['border']};"
        )
        ws_lay.addWidget(self.token_footer)
        self.main_lay.addWidget(self.workspace, 1)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 4, 2)
        grip_row.addStretch()
        self._size_grip = QSizeGrip(self)
        self._size_grip.setToolTip("Drag to resize the window")
        grip_row.addWidget(self._size_grip, 0, Qt.AlignRight | Qt.AlignBottom)
        self.main_lay.addLayout(grip_row)

        self.setStyleSheet(QSS_BASE)
        self.setWindowOpacity(0.95)
        self._orb_state = "idle"
        self._position_ribbon()

    # ── Control Ribbon hover reveal ───────────────────────────────────────────

    def _position_ribbon(self) -> None:
        """No-op retained for call-site compatibility (ribbon is layout-managed)."""
        return

    def _animate_ribbon(self, target: float) -> None:
        """Legacy no-op — ribbon uses a static opaque stylesheet now."""
        return

    def _check_ribbon_hover(self) -> None:
        """Legacy no-op — hover dimming removed to avoid QPainter conflicts."""
        return

    # ── Widget factories ──────────────────────────────────────────────────────

    def _hdr_separator(self) -> QFrame:
        """Thin vertical divider between the ribbon's control groups."""
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedHeight(20)
        sep.setStyleSheet(f"color: {PAL['border']}; background: {PAL['border']}; max-width: 1px;")
        return sep

    def _make_hdr_btn(self, icon, tip, cmd=None, accent: str = ""):
        btn = QPushButton(icon)
        btn.setToolTip(tip)
        btn.setFixedSize(28, 28)
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; border-radius: 5px;"
            f"  color: {accent or PAL['muted']}; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {PAL['surface_2']};"
            f"  color: {accent or PAL['text']}; }}"
        )
        if cmd:
            btn.clicked.connect(cmd)
        return btn

    def _make_dock_btn(self, icon, tip, cmd=None):
        btn = QPushButton(icon)
        btn.setObjectName("dock_btn")
        btn.setToolTip(tip)
        btn.setFixedSize(32, 32)
        if cmd:
            btn.clicked.connect(cmd)
        return btn

    def _set_btn_active(self, btn, active):
        btn.setProperty("active", "true" if active else "false")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    # ── Dragging (title bar only) + companion click-through ───────────────────

    def _interactive_shell_widgets(self) -> list[QWidget]:
        """Top-level panels that must receive mouse input (not click-through)."""
        shells: list[QWidget] = []
        for name in (
            "header", "orb_zone", "workspace", "chat_composer",
            "token_footer", "copilot_status_lbl", "_size_grip",
        ):
            w = getattr(self, name, None)
            if isinstance(w, QWidget):
                shells.append(w)
        return shells

    def _mouse_target_accepts_input(self, local: QPoint) -> bool:
        """True when *local* coords hit a real UI control (not a transparent gutter)."""
        if self._mouse_capture_locked:
            return True
        target = self.childAt(local)
        if target is None:
            return False
        if target in (self.root_widget, self.centralWidget()):
            return False
        shells = self._interactive_shell_widgets()
        w: QWidget | None = target
        while w is not None and w is not self:
            if w in shells:
                return True
            w = w.parentWidget()
        return False

    def lock_mouse_capture(self, locked: bool = True) -> None:
        """Engage full hit-testing (e.g. while dragging the title bar or a modal)."""
        self._mouse_capture_locked = locked
        if not locked:
            self.unsetCursor()

    # ── Interaction lifecycle — companion mode state machine ──────────────────

    def _begin_interaction(self, source: str = "user") -> None:
        """Enter active processing state (thinking / capture / API)."""
        self._interaction_source = source
        self._stop_gen.clear()
        self._stream_start_ts = time.time()
        self._is_streaming = True
        self.lock_mouse_capture(False)
        self.unsetCursor()
        self.bridge.start_thinking.emit()

    def _sync_companion_visual_idle(self) -> None:
        """Return orb/accent/float bubble to idle when nothing is active."""
        if self._is_streaming:
            return
        if self.audio and getattr(self.audio, "is_listening", False):
            return
        if voice_engine and getattr(voice_engine, "is_speaking", False):
            return
        self._set_accent_state("stealth" if self.stealth_active else "idle")
        self._set_orb_state("idle")
        if self._is_floating:
            self._bubble.set_active(False)

    def _release_companion_mode(
        self,
        *,
        status: str = "Ready",
        error: str = "",
        finalize_thinking: bool = True,
    ) -> None:
        """
        Gracefully exit an interaction — non-obstructive companion layer restored.

        Clears streaming flags, mouse capture lock, and stray cursors so the
        desktop passthrough layer behaves correctly after every turn.
        """
        self._is_streaming = False
        self._md_ai_streaming = False
        self._md_ai_buffer = ""
        self._streaming_html = ""
        self.lock_mouse_capture(False)
        self.unsetCursor()
        if self.agent_cursor:
            self.agent_cursor.hide_cursor()
        self.chat_composer.set_streaming(False)
        if finalize_thinking:
            self.thinking_bar.hide()
            self.bridge.thinking_done.emit()
        if error:
            self.bridge.set_status.emit(f"  {error}")
            self._show_pill(error[:72])
        else:
            self.bridge.set_status.emit(f"  {status}")
        QTimer.singleShot(400, self._sync_companion_visual_idle)

    def _on_engine_error(self, msg: str) -> None:
        """Worker-thread API error → main-thread companion recovery."""
        self.bridge.interaction_error.emit(msg)

    @Slot(str)
    def _on_interaction_error(self, msg: str) -> None:
        from atlas_interaction import friendly_error
        text = msg if msg.startswith("⚠") or "Couldn't" in msg else friendly_error("api", msg)
        self._release_companion_mode(error=f"⚠ {text.lstrip('⚠ ')}", finalize_thinking=True)

    def eventFilter(self, obj, event) -> bool:
        if obj is getattr(self, "header", None):
            et = event.type()
            if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.lock_mouse_capture(True)
                self.header.setCursor(Qt.ClosedHandCursor)
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if et == QEvent.MouseMove and event.buttons() & Qt.LeftButton and hasattr(self, "_drag_pos"):
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
            if et == QEvent.MouseButtonRelease:
                self.lock_mouse_capture(False)
                self.header.unsetCursor()
                if hasattr(self, "_drag_pos"):
                    del self._drag_pos
                return False
        return super().eventFilter(obj, event)

    def leaveEvent(self, event) -> None:
        """Clear stale resize/drag cursors when the pointer exits the window."""
        self.unsetCursor()
        super().leaveEvent(event)

    def nativeEvent(self, eventType, message):
        """
        Windows companion-layer hit testing.

        Transparent layout gutters and outer margins return HTTRANSPARENT so the
        cursor and clicks pass through to the desktop (Skales / HeyClicky style).
        Edge resize bands are intentionally disabled — only the QSizeGrip resizes.
        """
        if platform.system() == "Windows" and eventType == b"windows_generic_MSG":
            try:
                import ctypes
                from ctypes import wintypes

                WM_NCHITTEST = 0x0084
                HTCLIENT = 1
                HTTRANSPARENT = -1

                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_NCHITTEST:
                    gx = ctypes.c_short(msg.lParam & 0xFFFF).value
                    gy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    local = self.mapFromGlobal(QPoint(gx, gy))
                    if not self.rect().contains(local):
                        return super().nativeEvent(eventType, message)
                    if self._is_floating or not self.isVisible():
                        return True, HTTRANSPARENT
                    if self._mouse_target_accepts_input(local):
                        return True, HTCLIENT
                    return True, HTTRANSPARENT
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    # ═════════════════════════════════════════════════════════════════════════
    # ACCENT ENGINE — dynamic colour updates  (Requirement 3)
    # ═════════════════════════════════════════════════════════════════════════

    def set_window_opacity_pct(self, pct: int) -> None:
        """
        Apply window opacity from a 20–100 percentage (Req 4C).

        Clamped to the valid range and applied to the whole top-level window so
        the translucent frameless surface scales cleanly with no clip artifacts.
        """
        pct = max(20, min(100, int(pct)))
        self.setWindowOpacity(pct / 100.0)

    def _set_accent_state(self, state: str) -> None:
        """Transition the accent engine and propagate to UI elements."""
        accent_engine.set_state(state)
        self.bridge.accent_changed.emit(accent_engine.accent)

    @Slot(str)
    def _on_accent_changed(self, hex_color: str) -> None:
        """Update every dynamic accent element when the state changes."""
        # Update logo, mode pill, bubble border
        self.logo.setStyleSheet(
            f"color: {hex_color}; font-weight: bold; font-size: 15px; padding-left: 4px;"
        )

        # ── Idle "alive" glow pulse ───────────────────────────────────────────
        # Only pulses while idle; held solid (full opacity) during ptt/processing
        # so the accent state reads cleanly.
        state = accent_engine.state
        self.alive_dot.setStyleSheet(
            f"color: {hex_color}; font-size: 11px; background: transparent; padding-left: 2px;"
        )

        if self._is_floating:
            self._bubble.set_border_color(hex_color)
        # Thinking bar gradient
        dim = accent_engine.dim
        self.thinking_bar.setStyleSheet(
            f"QProgressBar {{ background: {PAL['surface_2']}; border: none; border-radius: 1px; }}"
            f"QProgressBar::chunk {{"
            f"  background: qlineargradient("
            f"    x1:0, y1:0, x2:1, y2:0,"
            f"    stop:0 {dim}, stop:0.5 {hex_color},"
            f"    stop:1 {PAL['gold']}); border-radius: 1px; }}"
        )

    # ═════════════════════════════════════════════════════════════════════════
    # STEALTH MATRIX  (Requirement 5)
    # WDA_EXCLUDEFROMCAPTURE on main window, FloatBubble, PillNotification,
    # HoloOverlay, and AgentCursorOverlay (Windows 10 build 19041+)
    # ═════════════════════════════════════════════════════════════════════════

    def _stealth_widgets(self) -> list:
        widgets = [self, self._bubble, self._pill_win]
        if self.overlay:
            widgets.append(self.overlay)
        if self.agent_cursor:
            widgets.append(self.agent_cursor)
        return widgets

    def _warn_capture_exclusion_unsupported(self) -> None:
        if self._capture_exclusion_warned:
            return
        self._capture_exclusion_warned = True
        msg = (
            "Screen exclusion requires Windows 10 v2004 or later. Your version "
            "doesn't support it — Atlas windows may be visible in screen shares."
        )
        if HAS_LOGGING:
            get_logger("atlas.ui").warning(msg)
        self.bridge.set_status.emit(msg)
        if hasattr(self, "_append_response"):
            self._append_response(msg)

    def _apply_stealth_to_hwnd(self, hwnd_int: int, enable: bool) -> bool:
        """Apply or remove WDA_EXCLUDEFROMCAPTURE on any HWND. Returns True on success."""
        if HAS_OVERLAY:
            from atlas_overlay import apply_capture_exclusion, capture_exclusion_supported
            if enable and not capture_exclusion_supported():
                self._warn_capture_exclusion_unsupported()
                return False
            return apply_capture_exclusion(hwnd_int, enable)
        import ctypes
        import ctypes.wintypes
        if enable:
            try:
                if sys.platform == "win32" and sys.getwindowsversion().build < 19041:
                    self._warn_capture_exclusion_unsupported()
                    return False
            except Exception:
                pass
        WDA_NONE = 0x00000000
        WDA_EXCLUDEFROMCAPTURE = 0x00000011
        flag = WDA_EXCLUDEFROMCAPTURE if enable else WDA_NONE
        try:
            ok = ctypes.windll.user32.SetWindowDisplayAffinity(
                ctypes.wintypes.HWND(hwnd_int),
                ctypes.wintypes.DWORD(flag),
            )
            return bool(ok)
        except (AttributeError, OSError):
            return False

    def _set_stealth_active(self, active: bool, *, update_ui: bool = True) -> None:
        """Apply capture exclusion to every Atlas HWND."""
        results: list[bool] = []
        for widget in self._stealth_widgets():
            try:
                results.append(self._apply_stealth_to_hwnd(int(widget.winId()), active))
            except Exception:
                results.append(False)
        if self.overlay and hasattr(self.overlay, "set_capture_excluded"):
            self.overlay.set_capture_excluded(active)
        if self.agent_cursor and hasattr(self.agent_cursor, "set_capture_excluded"):
            self.agent_cursor.set_capture_excluded(active)

        if active:
            if any(results):
                self.stealth_active = True
                if update_ui:
                    self._set_accent_state("stealth")
                    self.btn_stealth.setStyleSheet(
                        f"QPushButton {{ background: rgba(255,45,85,0.15);"
                        f"  color: {PAL['danger']}; border: none; border-radius: 5px;"
                        f"  font-size: 13px; }}"
                        f"QPushButton:hover {{ background: rgba(255,45,85,0.30);"
                        f"  color: {PAL['danger']}; }}"
                    )
                    self.btn_stealth.setToolTip("Stealth ACTIVE — click to disable")
                    self.bridge.set_status.emit(
                        "🥷 Stealth ON — hidden from all screen capture APIs")
            elif HAS_OVERLAY:
                from atlas_overlay import capture_exclusion_supported
                if capture_exclusion_supported():
                    import ctypes
                    err = ctypes.GetLastError() if hasattr(ctypes, "GetLastError") else "n/a"
                    self.bridge.set_status.emit(
                        f"Stealth: SetWindowDisplayAffinity failed (err {err}) — "
                        "requires Windows 10 build 19041+"
                    )
        else:
            self.stealth_active = False
            if update_ui:
                self._set_accent_state("idle")
                self.btn_stealth.setStyleSheet(
                    f"QPushButton {{ background: transparent; border: none; border-radius: 5px;"
                    f"  color: {PAL['muted']}; font-size: 13px; }}"
                    f"QPushButton:hover {{ background: {PAL['surface_2']};"
                    f"  color: {PAL['text']}; }}"
                )
                self.btn_stealth.setToolTip(
                    "Stealth Mode — hide from screen capture (Windows only)")
                self.bridge.set_status.emit("🥷 Stealth OFF — visible in screen share")

    def _toggle_stealth(self):
        """Toggle capture exclusion on all Atlas windows."""
        self._set_stealth_active(not self.stealth_active, update_ui=True)

    def _show_interview_headphone_reminder(self) -> None:
        """One-time per run reminder when Interview / Focus mode starts."""
        if self._interview_headphone_shown:
            return
        self._interview_headphone_shown = True
        msg = (
            "🎧 Reminder: if TTS is on, Atlas will speak out loud. Use headphones "
            "so your mic doesn't pick it up during the call."
        )
        if hasattr(self, "chat_view"):
            self._append_response(msg)
        self.bridge.set_status.emit(msg)
        QTimer.singleShot(3000, lambda: self.bridge.set_status.emit("  Ready"))

    def _on_focus_mode_changed(self, enabled: bool) -> None:
        """Interview / Focus mode side effects — UI sync, stealth, mic, reminders."""
        self._sync_focus_ui(enabled)
        if enabled:
            self._stealth_before_interview = self.stealth_active
            if not self.stealth_active:
                self._set_stealth_active(True, update_ui=True)
            self._show_interview_headphone_reminder()
            self.style_combo_set("Direct")
            self.bridge.set_status.emit(
                "Focus mode on — use Ctrl+Space to talk; enable mic in settings if needed"
            )
        else:
            if not self._stealth_before_interview and self.stealth_active:
                self._set_stealth_active(False, update_ui=True)
            self.bridge.set_status.emit("Focus mode off")

    # ═════════════════════════════════════════════════════════════════════════
    # PERMISSION INTERCEPTOR  (Requirement 7)
    # ═════════════════════════════════════════════════════════════════════════

    def _daemon_fs_permission(self, action: str, path: str) -> bool:
        """IPC permission gate — runs dialog on Qt main thread, returns bool."""
        if threading.current_thread() is threading.main_thread():
            return self._fs_permission_sync(action, path)
        self._perm_event = getattr(self, "_perm_event", threading.Event())
        self._perm_answer = False
        self._perm_event.clear()

        def _run() -> None:
            self._perm_answer = self._fs_permission_sync(action, path)
            self._perm_event.set()

        QTimer.singleShot(0, _run)
        self._perm_event.wait(timeout=300.0)
        return bool(self._perm_answer)

    def _fs_permission_sync(self, action: str, path: str) -> bool:
        p = str(path)
        if p.startswith("atlas-task://"):
            approved = {"v": False}

            def _approve():
                approved["v"] = True

            def _deny():
                approved["v"] = False

            dlg = PermissionDialog(action, path, _approve, _deny, parent=self)
            dlg.exec()
            return approved["v"]
        if p.startswith("atlas-hands://"):
            approved = {"v": False}

            def _approve():
                approved["v"] = True

            def _deny():
                approved["v"] = False

            dlg = PermissionDialog(action, path, _approve, _deny, parent=self)
            dlg.exec()
            return approved["v"]
        if p.startswith("atlas-routine://"):
            approved = {"v": False}

            def _approve():
                approved["v"] = True

            def _deny():
                approved["v"] = False

            dlg = PermissionDialog(action, path, _approve, _deny, parent=self)
            dlg.exec()
            return approved["v"]
        if not self.fs_access_active:
            self.bridge.set_status.emit(
                "⛔ File system access is OFF — enable it in the control panel"
            )
            return False
        approved = {"v": False}

        def _approve():
            approved["v"] = True

        def _deny():
            approved["v"] = False

        dlg = PermissionDialog(action, path, _approve, _deny, parent=self)
        dlg.exec()
        return approved["v"]

    def _daemon_typed_confirm(self, msg: dict) -> bool:
        from types import SimpleNamespace

        policy_result = SimpleNamespace(
            reason=str(msg.get("reason", "")),
            confirm_phrase=str(msg.get("confirm_phrase", "")),
            audit_id=str(msg.get("audit_id", "")),
        )
        path = str(msg.get("path", ""))
        if threading.current_thread() is threading.main_thread():
            return self._typed_confirm_ui(policy_result, path)
        self._typed_confirm_event.clear()
        self._typed_confirm_answer = False

        def _run() -> None:
            self._typed_confirm_answer = self._typed_confirm_ui(policy_result, path)
            self._typed_confirm_event.set()

        QTimer.singleShot(0, _run)
        self._typed_confirm_event.wait(timeout=300.0)
        return bool(self._typed_confirm_answer)

    def _typed_confirm_policy(self, policy_result, *, path: str) -> bool:
        """Policy-layer typed confirmation (CRITICAL actions only)."""
        if threading.current_thread() is threading.main_thread():
            return self._typed_confirm_ui(policy_result, path)
        self._typed_confirm_event.clear()
        self._typed_confirm_answer = False

        def _run() -> None:
            self._typed_confirm_answer = self._typed_confirm_ui(policy_result, path)
            self._typed_confirm_event.set()

        QTimer.singleShot(0, _run)
        self._typed_confirm_event.wait(timeout=300.0)
        return bool(self._typed_confirm_answer)

    def _typed_confirm_ui(self, policy_result, path: str) -> bool:
        dlg = TypedConfirmDialog(policy_result, path, parent=self)
        dlg.exec()
        return dlg.approved()

    def _fs_permission_callback(self, action: str, path: str,
                                 approve_fn: Callable, deny_fn: Callable) -> None:
        """
        Called by atlas_fs on whatever thread triggered the FS operation.
        Marshals to the Qt main thread via signal.
        """
        self.bridge.request_permission.emit(action, path, approve_fn, deny_fn)

    def _shell_permission_sync(self, action: str, path: str) -> bool:
        """Blocking shell ASK gate for non-daemon mode."""
        event = threading.Event()
        result = {"ok": False}

        def approve() -> None:
            result["ok"] = True
            event.set()

        def deny() -> None:
            result["ok"] = False
            event.set()

        if threading.current_thread() is threading.main_thread():
            self._on_permission_request(action, path, approve, deny)
            return bool(result["ok"])

        self.bridge.request_permission.emit(action, path, approve, deny)
        event.wait(timeout=300.0)
        return bool(result["ok"])

    @Slot(str, str, object, object)
    def _on_permission_request(self, action: str, path: str,
                               approve_fn, deny_fn) -> None:
        """Gate file writes on fs_access; show PermissionDialog for task steps."""
        p = str(path)
        if p.startswith("atlas-task://"):
            dlg = PermissionDialog(action, path, approve_fn, deny_fn, parent=self)
            dlg.exec()
            return
        if p.startswith("atlas-hands://"):
            dlg = PermissionDialog(action, path, approve_fn, deny_fn, parent=self)
            dlg.exec()
            return
        if p.startswith("atlas-routine://"):
            dlg = PermissionDialog(action, path, approve_fn, deny_fn, parent=self)
            dlg.exec()
            return
        if not self.fs_access_active:
            deny_fn()
            self.bridge.set_status.emit(
                "File system access is off — enable it in settings for file operations."
            )
            return
        dlg = PermissionDialog(action, path, approve_fn, deny_fn, parent=self)
        dlg.exec()

    def _post_step_pending(self, pending) -> None:
        """Called from worker threads — marshal step sync to the Qt main thread."""
        self.bridge.step_pending.emit(pending)

    @Slot(object)
    def _on_step_pending(self, pending) -> None:
        """Animate agent cursor + narrate + execute one StepEvent in order."""
        evt = pending.event
        evt.status = "animating"
        narr = bool(
            _CORE and step_orchestrator and step_orchestrator.narration_enabled
            and voice_engine
        )
        if narr and evt.description:
            voice_engine.speak(evt.description)

        def _after_action() -> None:
            if self.agent_cursor and pending.do_action:
                self.agent_cursor.pulse_click(on_done=_finish_step)
            else:
                _finish_step()

        def _after_anim() -> None:
            evt.status = "executing"
            if pending.do_action:
                try:
                    pending.do_action()
                except Exception as exc:
                    pending.result_ok = False
                    evt.error = str(exc)
                    evt.status = "failed"
                    _finish_step()
                    return
            min_ms = step_orchestrator.min_step_ms if step_orchestrator else 400
            QTimer.singleShot(min_ms, _after_action)

        def _finish_step() -> None:
            evt.status = "completed" if pending.result_ok else "failed"
            if self.agent_cursor:
                self.agent_cursor.hide_cursor()
            pending.done.set()

        if self.agent_cursor:
            self.agent_cursor.animate_to(
                int(evt.x), int(evt.y), duration_ms=250, on_done=_after_anim,
            )
        else:
            _after_anim()

    def _safety_prompt(self, description: str) -> bool:
        if threading.current_thread() is threading.main_thread():
            return self._safety_prompt_ui(description)
        self._safety_event.clear()
        self.bridge.safety_prompt_req.emit(description or "Allow this action?")
        self._safety_event.wait(timeout=120.0)
        return self._safety_answer

    def _task_confirm(self, task: str) -> bool:
        """Safety gate for starting an autonomous [[TASK:]] loop."""
        summary = (task or "").strip()
        if len(summary) > 400:
            summary = summary[:397] + "…"
        return self._safety_prompt(f"Run autonomous task:\n{summary}")

    def _safety_prompt_ui(self, description: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Confirm action")
        box.setText(description or "Allow this action?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        return box.exec() == QMessageBox.Yes

    @Slot(str)
    def _on_safety_prompt_req(self, description: str) -> None:
        self._safety_answer = self._safety_prompt_ui(description)
        self._safety_event.set()

    @Slot(str, str)
    def _on_user_notice(self, title: str, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title or "Atlas")
        box.setText(message)
        box.setIcon(QMessageBox.Warning)
        box.exec()

    def _send_feedback(self) -> None:
        email = ""
        acct = getattr(self, "account", None)
        if acct:
            email = getattr(acct, "email", "") or ""
        dlg = FeedbackDialog(self.telemetry, parent=self, email=email)
        dlg.exec()

    def _on_license_update(self, status) -> None:
        if not status.allowed:
            self.bridge.user_notice.emit(
                "Account status",
                status.message or "Agent actions are paused for your account.",
            )
        if self.telemetry and self.state:
            self.telemetry.apply_execution_gate(self.state)
        if status.source == "network" and status.allowed:
            self.bridge.set_status.emit("Account status verified.")

    # ═════════════════════════════════════════════════════════════════════════
    # WEBCAM CAPTURE HELPER  (Requirement 4)
    # ═════════════════════════════════════════════════════════════════════════

    def _capture_webcam_frame(self) -> Optional[str]:
        """
        Silently capture one frame from the default webcam using cv2.
        Returns base64-encoded JPEG string, or None on any failure.
        """
        if not HAS_CV2 or not self.camera_active:
            return None
        try:
            cap = _cv2.VideoCapture(0)
            if not cap.isOpened():
                return None
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return None
            _, buf = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 80])
            return base64.b64encode(buf.tobytes()).decode("utf-8")
        except Exception:
            return None

    def _update_skip_audio_visibility(self) -> None:
        """
        Poll voice_engine.is_speaking every 250 ms and show/hide the Skip Audio
        button accordingly.  Previously the button was always visible regardless
        of whether anything was playing (Bug #6).
        """
        speaking = bool(voice_engine and voice_engine.is_speaking)
        if speaking and self.btn_skip_audio.isHidden():
            self.btn_skip_audio.show()
        elif not speaking and not self.btn_skip_audio.isHidden():
            self.btn_skip_audio.hide()

        # Reflect speaking on the orb without fighting listening/thinking states.
        if speaking:
            if self._orb_state not in ("speaking", "listening"):
                self._set_orb_state("speaking")
        else:
            if self._orb_state == "speaking" and not self._is_streaming \
                    and not (self.audio and self.audio.is_listening):
                self._set_orb_state("idle")

    def _show_startup_message(self) -> None:
        """
        Emit a one-time startup card that surfaces the most common first-run
        gotchas.  Addresses Bugs #1, #2, #5, #26.
        """
        lines = ["⚡ Atlas ready.\n"]

        # Bug #26: API key check
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            lines.append(
                "  ⚠  GROQ_API_KEY not set — queries will fail.\n"
                "     Create a .env file with GROQ_API_KEY=gsk_… and restart.\n"
            )

        # Bug #5: TTS model files check
        if _CORE:
            try:
                from atlas_core import KokoroVoiceEngine
                if not KokoroVoiceEngine.models_present():
                    lines.append(
                        "  ⚠  Kokoro TTS model files not found — voice output is DISABLED.\n"
                        "     Place kokoro-v0_19.onnx and voices.bin alongside atlas_ui.py,\n"
                        "     then run: pip install kokoro-onnx sounddevice\n"
                    )
            except Exception:
                pass

        # How to actually talk to Atlas + how it talks back.
        lines.append(
            "  🎧  TALK TO ATLAS:  tap  Ctrl + Space  (or Alt + Space), then speak.\n"
            "       Atlas keeps listening and replies automatically after you pause\n"
            "       for about 1.5 seconds. The ring glows scarlet while listening, gold\n"
            "       while thinking.\n"
            "  🔇  Tap Ctrl + Space again while Atlas is talking to interrupt it and\n"
            "       immediately start listening again.\n"
            "  🗣  Atlas SPEAKS its replies aloud (voice: af_sarah). Tune it in\n"
            "       ⚙ Control Center → Audio → Voice / Test Voice / Speech speed.\n"
            "  💬  Or just type a message and press Enter.\n"
            "\n"
            "  📍  GUIDE:  \"Guide me through setting up …\" — highlights each step on screen.\n"
            "  🤖  DO:     \"Do it for me — open Spotify and play …\" — Atlas performs the task.\n"
            "  🎯  FOCUS:  toggle the Focus button for terse interview-style help.\n"
            "  👁  WATCH:  + menu → Screen Watcher so Atlas can see your screen.\n"
        )

        self._append_response("".join(lines))

        if voice_engine is not None:
            QTimer.singleShot(150, lambda: voice_engine.speak("Atlas ready."))

    # ═════════════════════════════════════════════════════════════════════════
    # VOICE ENGINE — speak & skip  (Requirement 8)
    # ═════════════════════════════════════════════════════════════════════════

    def _do_skip_audio(self) -> None:
        """Nuke the entire TTS backlog: flush all queued items, then interrupt the current utterance."""
        if voice_engine:
            voice_engine.flush()   # drop every pending utterance in the queue
            voice_engine.skip()    # interrupt whatever is playing right now
            self.bridge.set_status.emit("⏭ Audio nuked")

    # ═════════════════════════════════════════════════════════════════════════
    # CONTROL CENTER  (Requirement 2)
    # ═════════════════════════════════════════════════════════════════════════

    def _open_control_center(self):
        self._open_settings()

    # ═════════════════════════════════════════════════════════════════════════
    # SESSION & MODE
    # ═════════════════════════════════════════════════════════════════════════

    def _tick_session(self):
        if self.state and self.state.session.is_active:
            self.status_lbl.setText(f"  ⏱ {self.state.session.summary}")

    def _on_state_event(self, event_type: str, payload: dict):
        if event_type == "mode_changed":
            new_mode = payload.get("to", "")
            QTimer.singleShot(0, lambda: self._sync_mode_ui(new_mode))
        elif event_type in ("task_status", "learn_status", "command_done"):
            text = str(payload.get("text", ""))
            if text:
                self.bridge.set_status.emit(text)
            if event_type == "learn_status":
                QTimer.singleShot(0, self._sync_learn_btn)
        elif event_type == "task_truncated":
            msg = (
                f"⚠ Atlas stopped after {payload.get('max_steps', 16)} steps — "
                "the task may be incomplete."
            )

            def _notify_truncated(m: str = msg) -> None:
                self.bridge.set_status.emit(m)
                self._show_pill(m[:72])

            QTimer.singleShot(0, _notify_truncated)
        elif event_type == "task_running":
            active = bool(payload.get("active", False))
            QTimer.singleShot(0, lambda: (
                self._show_task_stop_ui() if active else self._hide_task_stop_ui()
            ))
        elif event_type == "user_notice":
            title = str(payload.get("title", "Atlas"))
            message = str(payload.get("message", ""))
            QTimer.singleShot(0, lambda: self.bridge.user_notice.emit(title, message))
        elif event_type == "weekly_digest":
            message = str(payload.get("message", ""))
            if message:
                QTimer.singleShot(0, lambda: self._append_response(message))
        elif event_type == "focus_changed":
            QTimer.singleShot(0, lambda: self._on_focus_mode_changed(
                payload.get("enabled", False)))
        elif event_type == "toggle_ambient":
            if payload.get("enabled"):
                QTimer.singleShot(0, self._start_watch)
            else:
                QTimer.singleShot(0, self._stop_watch)
        elif event_type == "guide_marker":
            if payload.get("found") and self.overlay:
                x, y = int(payload["x"]), int(payload["y"])
                w, h = int(payload.get("w", 0)), int(payload.get("h", 0))
                label = str(payload.get("label", ""))

                def _mark_guide(x=x, y=y, w=w, h=h, label=label) -> None:
                    if self.overlay:
                        self.overlay.mark_target(x, y, w, h, label=label)

                QTimer.singleShot(0, _mark_guide)
        elif event_type == "spatial_error":
            err = str(payload.get("error", "Location failed"))

            def _notify_spatial_err(e: str = err) -> None:
                self.bridge.set_status.emit(f"  ⚠ {e}")
                self._show_pill(e[:72])

            QTimer.singleShot(0, _notify_spatial_err)
        elif event_type == "proactive_alert":
            msg = str(payload.get("message") or payload.get("summary") or "")
            if msg:
                QTimer.singleShot(0, lambda m=msg: self._append_proactive_alert(m))
        elif event_type == "copilot_changed":
            QTimer.singleShot(0, lambda: self._sync_copilot_ui(
                payload.get("active", False)))
        elif event_type == "query_started":
            src = str(payload.get("source", "user"))

            def _start_query(s: str = src) -> None:
                self._begin_interaction(s)
                self.bridge.set_status.emit("Thinking…")

            QTimer.singleShot(0, _start_query)
        elif event_type == "command_handled":
            QTimer.singleShot(0, lambda: self._release_companion_mode(
                status="Done ✓",
                finalize_thinking=False,
            ))
        elif event_type == "query_rejected":
            from atlas_interaction import friendly_error
            QTimer.singleShot(0, lambda: self._release_companion_mode(
                error=f"⚠ {friendly_error('busy')}",
            ))
        elif event_type == "query_failed":
            from atlas_interaction import friendly_error
            err = str(payload.get("error", ""))
            QTimer.singleShot(0, lambda e=err: self._release_companion_mode(
                error=f"⚠ {friendly_error('generic', e)}",
            ))
        elif event_type == "guide_step_started":
            idx = int(payload.get("step_index", 0))
            if idx:
                QTimer.singleShot(0, lambda i=idx: self.bridge.guide_step_started.emit(i))
        elif event_type == "step_verified":
            idx = int(payload.get("step_index", 0))
            passed = bool(payload.get("passed", False))
            disc = str(payload.get("discrepancy") or "")
            QTimer.singleShot(
                0,
                lambda i=idx, p=passed, d=disc: self.bridge.step_verified.emit(i, p, d),
            )

    @Slot(int)
    def _on_guide_step_started(self, step_index: int) -> None:
        if hasattr(self, "chat_view"):
            self.chat_view.tag_guide_step(step_index)
        for msg in reversed(getattr(self, "_chat_messages", [])):
            if msg.get("role") == "assistant" and not msg.get("step_index"):
                msg["step_index"] = step_index
                break

    @Slot(int, bool, str)
    def _on_step_verified(self, step_index: int, passed: bool, discrepancy: str) -> None:
        if hasattr(self, "chat_view"):
            self.chat_view.apply_step_verification(step_index, passed, discrepancy)
        for msg in reversed(getattr(self, "_chat_messages", [])):
            if msg.get("step_index") == step_index:
                msg["verified"] = passed
                msg["verification_note"] = discrepancy
                break

    def _on_copilot_toggled(self, checked: bool) -> None:
        self.copilot_active = checked
        if self.state:
            self.state.set_copilot_mode(checked)
        self._sync_copilot_ui(checked)
        self.bridge.set_status.emit(
            f"Copilot {'on — Atlas is watching' if checked else 'off'}")

    def _sync_copilot_ui(self, active: bool) -> None:
        if hasattr(self, "btn_copilot"):
            self.btn_copilot.blockSignals(True)
            self.btn_copilot.setChecked(active)
            self.btn_copilot.blockSignals(False)
        self.copilot_active = bool(active)
        if not hasattr(self, "copilot_status_lbl"):
            return
        if active:
            voice_on = bool(
                self.state
                and getattr(self.state, "audio_watcher", None)
                and self.state.audio_watcher.voice_listening
            )
            if voice_on:
                self.copilot_status_lbl.setText(
                    "Atlas is watching your screen + listening")
            else:
                self.copilot_status_lbl.setText("Atlas is watching your screen")
            self.copilot_status_lbl.show()
        else:
            self.copilot_status_lbl.hide()

    def _append_proactive_alert(self, message: str) -> None:
        msg = self.chat_view.add_message("proactive", message)
        self._chat_messages.append(msg)
        self.bridge.set_status.emit("👁 Copilot noticed something on your screen")

    def _sync_focus_ui(self, enabled: bool) -> None:
        if hasattr(self, "btn_focus"):
            self.btn_focus.blockSignals(True)
            self.btn_focus.setChecked(enabled)
            self.btn_focus.blockSignals(False)
        if enabled:
            self.mode_pill.setText("FOCUS")
            self.mode_pill.setStyleSheet(
                f"color: #9B59B6; background: rgba(155,89,182,0.12);"
                f"border: 1px solid #6C3483; border-radius: 8px;"
                f"font-size: 9px; font-weight: bold; letter-spacing: 1px; padding: 2px 7px;"
            )
        else:
            self.mode_pill.setText("ATLAS")
            self.mode_pill.setStyleSheet(
                f"color: {PAL['cyan']}; background: rgba(0,212,255,0.10);"
                f"border: 1px solid {PAL['cyan_dim']}; border-radius: 8px;"
                f"font-size: 9px; font-weight: bold; letter-spacing: 1px; padding: 2px 7px;"
            )

    def _on_focus_toggled(self, checked: bool) -> None:
        if not self.state:
            return
        self._stop_gen.set()
        if voice_engine:
            voice_engine.flush()
            voice_engine.skip()
        self.state.set_focus_mode(checked)
        self._on_focus_mode_changed(checked)

    def _sync_mode_ui(self, mode_name: str):
        """Legacy hook — maps old mode names to focus toggle."""
        if str(mode_name).upper() in ("INTERVIEW", "FOCUS"):
            self._sync_focus_ui(True)
        else:
            self._sync_focus_ui(False)

    def _on_mode_combo_changed(self, text: str):
        """Deprecated — kept so hot-reload paths don't break."""
        if text == "Interview":
            self._on_focus_toggled(True)
        elif text == "Active":
            self._on_focus_toggled(False)

    def style_combo_set(self, style: str):
        """Helper to update session response style without a visible combo widget on dock."""
        if self.state:
            self.state.session.response_style = style

    # ═════════════════════════════════════════════════════════════════════════
    # HOT RELOAD
    # ═════════════════════════════════════════════════════════════════════════

    def _hot_reload(self):
        self._do_reload()

    def _do_reload(self):
        try:
            self._stop_gen.set()
            try:
                keyboard.unhook_all()
            except Exception:
                pass
            self.watch_active = False
            self.highlight_active = False
            if self.stealth_active:
                self._set_stealth_active(False, update_ui=False)
            worker = getattr(self, "_watch_worker", None)
            if worker:
                worker.stop()
                worker.quit()
                worker.wait(1500)
                self._watch_worker = None
            if self.audio:
                try:
                    if getattr(self.audio, "mic_active", False):
                        self.audio.stop_mic()
                    if getattr(self.audio, "speaker_active", False):
                        self.audio.stop_speaker()
                except Exception:
                    pass
            if voice_engine:
                voice_engine.flush()
                voice_engine.shutdown()
            self._stop_pulse()
            self._bubble.hide()
            self._pill_win.hide()
            if self.overlay:
                self.overlay.hide()
            if getattr(self, "task_stop_overlay", None):
                self.task_stop_overlay.hide_stop()
        finally:
            os.execv(sys.executable, [sys.executable] + sys.argv)

    # ═════════════════════════════════════════════════════════════════════════
    # HOTKEYS
    # ═════════════════════════════════════════════════════════════════════════

    def _bind_hotkeys(self):
        keyboard.add_hotkey("ctrl+shift+s", lambda: (
            self.bridge.set_status.emit("Triggering Capture"), self._do_capture()
        ))
        keyboard.add_hotkey("ctrl+shift+h", lambda: (
            self.bridge.set_status.emit("Triggering Highlight"), self._toggle_highlight()
        ))
        keyboard.add_hotkey("ctrl+shift+w", lambda: (
            self.bridge.set_status.emit("Triggering Watcher"), self._toggle_watch()
        ))
        keyboard.add_hotkey("ctrl+r", lambda: self._do_reload())
        keyboard.add_hotkey("ctrl+comma", lambda: self._open_settings())
        keyboard.add_hotkey("ctrl+l", lambda: self._clear_text())
        keyboard.add_hotkey("ctrl+e", lambda: self._export_session())

        # Emergency stop for autonomous TASK / routine loops — works globally,
        # even when another app has keyboard focus.
        keyboard.add_hotkey(
            "ctrl+shift+escape",
            lambda: self.bridge.task_stop.emit(),
        )

        # ── Talk hotkey: dedicated global shortcut (Ctrl+Space / Alt+Space) ───
        # TAP to start a listening session; Atlas auto-responds after a ~1.5s pause.
        # Tap again while it is thinking/talking to interrupt and start listening
        # anew. A single low-level Space hook keeps the gesture from getting stuck
        # when the UI loses focus; the modifier is checked at press time. These
        # callbacks run on the keyboard listener thread, so all Qt/engine work is
        # marshalled to the main thread through SignalBridge.
        keyboard.on_press_key("space", self._ptt_key_down, suppress=False)
        keyboard.on_release_key("space", self._ptt_key_up, suppress=False)

    # ═════════════════════════════════════════════════════════════════════════
    # QUERY PIPELINE
    # ═════════════════════════════════════════════════════════════════════════

    def _do_ask(self) -> None:
        text = self.chat_composer._input.toPlainText().strip()
        if text:
            self.chat_composer._input.clear()
            self.chat_composer._sync_input_height()
            self._submit_query(text)

    def _submit_query(self, text: str) -> None:
        if not text:
            return
        self._last_user_query = text
        self._stop_ambient_on_input()
        if text.startswith("/"):
            if self._handle_slash_command(text):
                return
        if self.state:
            msg = self.chat_view.add_message("user", text)
            self._chat_messages.append(msg)
            if not self.chat_composer.tts_enabled_for_next() and voice_engine:
                voice_engine.flush()
            webcam_b64 = self._capture_webcam_frame()
            screen_b64 = None
            doc_ctx = ""
            for att in self._pending_attachments:
                if att.get("type") == "image":
                    screen_b64 = att.get("b64") or screen_b64
                elif att.get("type") == "screen":
                    screen_b64 = att.get("b64") or screen_b64
                elif att.get("type") == "doc":
                    doc_ctx += att.get("text", "") + "\n\n"
            if doc_ctx:
                text = f"[Attached document]\n{doc_ctx.strip()}\n\n{text}"
            if screen_b64 and self.state:
                self.state.inject_screen_capture(screen_b64)
            if not self.chat_composer.tts_enabled_for_next() and voice_engine:
                voice_engine.flush()
            threading.Thread(
                target=self.state.handle_input,
                args=(text,),
                kwargs={"source": "user", "webcam_b64": webcam_b64},
                daemon=True,
            ).start()
            self._pending_attachments.clear()
            self.chat_composer.set_attach_label("")

    def _start_thinking(self):
        self.thinking_bar.setRange(0, 0)
        self.thinking_bar.show()
        self.chat_composer.set_streaming(True)
        self._set_accent_state("processing")
        self._set_orb_state("thinking")
        if self._is_floating:
            self._bubble.set_active(True)

    def _stop_thinking(self):
        self.thinking_bar.setRange(0, 1)
        self.thinking_bar.setValue(1)
        self.thinking_bar.hide()
        self.chat_composer.set_streaming(False)
        self.lock_mouse_capture(False)
        self.unsetCursor()
        self._set_accent_state("stealth" if self.stealth_active else "idle")
        if not (voice_engine and getattr(voice_engine, "is_speaking", False)):
            self._set_orb_state("idle")
        if self._is_floating:
            self._bubble.set_active(False)

    def _get_username(self) -> str:
        return os.environ.get("ATLAS_USER", "default").strip() or "default"

    def _on_stream_complete(self, full_text: str) -> None:
        """StateEngine on_complete callback (runs on worker thread)."""
        self.bridge.stream_complete.emit(full_text)

    @Slot(str)
    def _on_stream_complete_slot(self, full_text: str) -> None:
        elapsed = time.time() - self._stream_start_ts if self._stream_start_ts else 0.0
        step_idx = None
        stream_card = getattr(self.chat_view, "_streaming_card", None)
        if stream_card is not None and getattr(stream_card, "_step_index", None):
            step_idx = stream_card._step_index
        if full_text:
            self.chat_view.finish_stream(full_text, elapsed)
            msg = {"role": "assistant", "text": full_text, "ts": time.time()}
            if step_idx:
                msg["step_index"] = step_idx
            self._chat_messages.append(msg)
        self._md_ai_streaming = False
        self._md_ai_buffer = ""
        self._streaming_html = ""
        if not self.chat_composer.tts_enabled_for_next() and voice_engine:
            voice_engine.flush()
            voice_engine.skip()
        elif full_text and voice_engine and not voice_engine.is_muted:
            if self.chat_composer.tts_enabled_for_next():
                pending = (
                    getattr(voice_engine.kokoro, "_is_speaking", False)
                    or not voice_engine.kokoro._queue.empty()
                )
                if not pending:
                    from atlas_audio import _strip_markdown
                    clean = _strip_markdown(full_text.strip())
                    if clean:
                        voice_engine.speak(clean)
        if full_text:
            if self._is_floating:
                self.bridge.notify_pill.emit(full_text.replace("\n", " ")[:60] + "…")
            ping = Path("assets/sounds/ping.wav")
            if ping.is_file() and voice_engine and not voice_engine.is_speaking:
                try:
                    from PySide6.QtMultimedia import QSoundEffect
                    from PySide6.QtCore import QUrl

                    sfx = QSoundEffect()
                    sfx.setSource(QUrl.fromLocalFile(str(ping.resolve())))
                    sfx.setVolume(0.2)
                    sfx.play()
                except Exception:
                    pass
        self._release_companion_mode(status="Done ✓", finalize_thinking=True)

    @Slot()
    def _on_stream_started(self) -> None:
        self._md_ai_buffer = ""
        self._md_ai_streaming = True
        self._streaming_html = ""
        self.chat_view.begin_assistant_stream()

    @Slot(str)
    def _on_stream_token(self, token: str) -> None:
        if not self._md_ai_streaming:
            self.bridge.stream_started.emit()
        self._streaming_html += token
        self._md_ai_buffer += token
        if not self._render_pending:
            self._render_pending = True
            QTimer.singleShot(120, self._flush_render)

    def _flush_render(self) -> None:
        self._render_pending = False
        self.chat_view.update_stream(self._md_ai_buffer)

    def _wire_message_card(self, card: object) -> None:
        if not isinstance(card, ChatMessageCard):
            return
        card.regenerate_requested.connect(self._regenerate_last)
        card.read_aloud_requested.connect(self._read_message_aloud)
        card.pin_requested.connect(self._pin_message)

    def _regenerate_last(self) -> None:
        if self._last_user_query:
            self._submit_query(self._last_user_query)

    def _read_message_aloud(self, text: str) -> None:
        if voice_engine and text.strip():
            voice_engine.speak(text.strip())

    def _pin_message(self, text: str) -> None:
        if not self.state or not text.strip():
            return
        snippet = text.strip()[:2000]
        self.state.session.add_pinned_context(snippet, source="user")
        self.bridge.set_status.emit("Pinned to session context ✓")

    def _on_chat_anchor(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("copycode:"):
            try:
                code = base64.b64decode(href[9:]).decode("utf-8")
                pyperclip.copy(code)
                self.bridge.set_status.emit("Code copied ✓")
            except Exception:
                pass
        elif href.startswith("runcode:"):
            try:
                rest = href[len("runcode:"):]
                lang_b64, code_b64 = rest.split(":", 1)
                lang = base64.b64decode(lang_b64).decode("utf-8")
                code = base64.b64decode(code_b64).decode("utf-8")
                self._run_code_snippet(code, lang)
            except Exception as exc:
                self.bridge.set_status.emit(f"Run failed: {exc}")

    def _run_code_snippet(self, code: str, lang: str) -> None:
        def _approve() -> None:
            threading.Thread(
                target=self._execute_code_snippet, args=(code, lang),
                daemon=True, name="atlas-run-snippet",
            ).start()

        def _deny() -> None:
            self.bridge.set_status.emit("Run cancelled")

        self.bridge.request_permission.emit(
            "EXECUTE", f"{lang or 'code'} snippet", _approve, _deny)

    def _execute_code_snippet(self, code: str, lang: str) -> None:
        import subprocess
        lang_l = (lang or "").lower()
        try:
            if lang_l in ("python", "py", "python3"):
                proc = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True, text=True, timeout=30,
                )
            elif lang_l in ("bash", "sh", "shell", "zsh"):
                proc = subprocess.run(
                    ["bash", "-c", code],
                    capture_output=True, text=True, timeout=30,
                )
            elif lang_l in ("cmd", "powershell"):
                proc = subprocess.run(
                    ["powershell", "-Command", code],
                    capture_output=True, text=True, timeout=30,
                )
            else:
                self.bridge.set_status.emit(f"Unsupported language for Run: {lang}")
                return
            out = (proc.stdout or proc.stderr or "").strip() or "(no output)"
            self.bridge.append_text.emit(out[:4000])
        except Exception as exc:
            self.bridge.set_status.emit(f"Execution error: {exc}")

    def _on_chat_image_dropped(self, path: str) -> None:
        self._attach_path(path)

    def _on_composer_attach(self, path: str) -> None:
        self._attach_path(path)

    def _on_composer_screenshot(self) -> None:
        try:
            from atlas_core import capture_screen_b64_str
            b64 = capture_screen_b64_str()
        except Exception:
            b64 = None
        if not b64:
            self.bridge.set_status.emit("Screenshot capture failed")
            return
        self._pending_attachments.append({
            "type": "screen", "b64": b64, "label": "Screenshot",
        })
        chips = " · ".join(a["label"] for a in self._pending_attachments)
        self.chat_composer.set_attach_label(f"Attached: {chips}")
        self.bridge.set_status.emit("Screenshot attached — send your message")

    def _attach_path(self, path: str) -> None:
        p = Path(path)
        if not p.is_file():
            return
        ext = p.suffix.lower()
        if ext in _IMAGE_EXTS:
            data = p.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            self._pending_attachments.append({
                "type": "image", "b64": b64, "label": p.name, "path": str(p),
            })
        elif ext in _DOC_EXTS:
            worker = ContextIngestWorker(str(p), parent=None)
            worker.ingest_done.connect(self._on_attach_doc_ready)
            worker.ingest_failed.connect(self.bridge.set_status.emit)
            worker.finished.connect(worker.deleteLater)
            worker.start()
            self.bridge.set_status.emit(f"Reading {p.name}…")
            return
        else:
            self.bridge.set_status.emit("Unsupported attachment type")
            return
        chips = " · ".join(a["label"] for a in self._pending_attachments)
        self.chat_composer.set_attach_label(f"Attached: {chips}")

    def _on_attach_doc_ready(self, text: str) -> None:
        label = "Document"
        self._pending_attachments.append({"type": "doc", "text": text, "label": label})
        chips = " · ".join(a["label"] for a in self._pending_attachments)
        self.chat_composer.set_attach_label(f"Attached: {chips}")
        self.bridge.set_status.emit("Document attached ✓")

    @Slot(str)
    def _append_response(self, text: str) -> None:
        t = text or ""
        role = "assistant"
        if t.startswith("[") or t.startswith("**Startup") or "Autosave" in t or "Slash commands" in t:
            role = "system"
        msg = self.chat_view.add_message(role, t)
        self._chat_messages.append(msg)
        self._md_ai_streaming = False
        self._md_ai_buffer = ""

    def _render_html(self) -> None:
        pass

    def _append_user_bubble(self, text: str) -> None:
        msg = self.chat_view.add_message("user", text)
        self._chat_messages.append(msg)

    def _show_plus_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("📷 Capture Screen", self._do_capture)
        menu.addAction(self._action_hl)
        menu.addAction(self._action_watch)
        menu.addSeparator()
        menu.addAction(self._action_mic)
        menu.addAction(self._action_spk)
        menu.addAction(self._action_cam)
        if voice_engine:
            menu.addAction(self._action_ve)
        menu.addSeparator()
        learning = bool(self.state and self.state.is_learning)
        menu.addAction(
            "⏹ Stop & Save Routine…" if learning else "🎓 Learn a Routine…",
            self._toggle_learning)
        menu.addAction("▶ Run a Routine…", self._run_routine_prompt)
        menu.addSeparator()
        menu.addAction("📄 Upload Context…", self._do_upload_context)
        menu.addAction("⚙ Browse Skills…", self._open_settings_skills_tab)
        anchor = self.chat_composer._tool_btns[-1]
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def apply_account_identity(self, name: str) -> None:
        """Reflect the signed-in user's name + avatar in the header."""
        clean = (name or "Guest").strip() or "Guest"
        self._account_name = clean
        if hasattr(self, "user_name_lbl"):
            self.user_name_lbl.setText(clean if len(clean) <= 18 else clean[:17] + "…")
        if hasattr(self, "btn_profile"):
            initials = "".join(p[0] for p in clean.split()[:2]).upper() or "G"
            self.btn_profile.setText(initials)
            self.btn_profile.setToolTip(f"{clean} — profile & account")

    def _show_profile_menu(self) -> None:
        menu = QMenu(self)
        name = getattr(self, "_account_name", "Guest")
        acct = getattr(self, "account", None)
        cloud_on = bool(acct and acct.cloud_available
                        and getattr(acct.cloud, "cloud_id", None))
        header = menu.addAction(f"👤  {name}")
        header.setEnabled(False)
        sub = menu.addAction("☁ Cloud synced" if cloud_on else "Local profile")
        sub.setEnabled(False)
        menu.addSeparator()
        menu.addAction("⚙ Preferences…", self._open_settings_account_tab)
        menu.addAction("🔒 Security…", self._open_settings_security_tab)
        menu.addAction("🧠 What Atlas knows…", self._open_settings_memory_tab)
        menu.addAction("🔗 Connect Account…", self._connect_account)
        menu.addAction("💳 Billing  (soon)", lambda: self.bridge.set_status.emit(
            "Billing is coming soon."))
        menu.addAction("💬 Send Feedback…", self._send_feedback)
        menu.addSeparator()
        menu.addAction("⎋ Sign out / Switch user", self._sign_out)
        menu.exec(self.btn_profile.mapToGlobal(self.btn_profile.rect().bottomLeft()))

    def _open_settings_account_tab(self) -> None:
        if self.settings_dialog:
            self.settings_dialog.show_tab(6)   # Account tab

    def _open_settings_security_tab(self) -> None:
        if self.settings_dialog:
            self.settings_dialog.show_tab(7)

    def _open_settings_memory_tab(self) -> None:
        if self.settings_dialog:
            self.settings_dialog.show_tab(4)   # Memory tab

    def _connect_account(self) -> None:
        acct = getattr(self, "account", None)
        if not acct:
            return
        if not acct.cloud_available:
            self.bridge.set_status.emit(
                "Cloud sync isn't configured on this machine.")
            return
        if self.settings_dialog:
            self.settings_dialog.show_tab(6)
            self.settings_dialog._account_sync()

    def _sign_out(self) -> None:
        acct = getattr(self, "account", None)
        if not acct:
            return
        acct.sign_out()
        dlg = LoginDialog(acct, self)
        if dlg.exec() == QDialog.Accepted and dlg.user_id and self.state:
            os.environ["ATLAS_USER"] = dlg.user_name
            self.state.set_user(dlg.user_id, dlg.user_name)
            self.apply_account_identity(dlg.user_name)
            self.bridge.set_status.emit(f"Signed in as {dlg.user_name}")

    def _toggle_learning(self) -> None:
        """Start watching a demonstration, or stop and save it as a routine."""
        if not self.state:
            return
        if self.state.is_learning:
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(
                self, "Save Routine",
                "Name this routine (e.g. 'morning setup'):")
            if not ok:
                return
            self.state.stop_learning(name.strip())
        else:
            self.bridge.set_status.emit(
                "🎓 Tip: minimise Atlas, perform the steps, then re-open and Stop.")
            self.state.start_learning()
        self._sync_learn_btn()

    def _run_routine_prompt(self) -> None:
        if not self.state:
            return
        try:
            routines = self.state.memory.list_routines(self.state.user_id)
        except Exception:
            routines = []
        if not routines:
            self.bridge.set_status.emit(
                "No routines yet — use “Learn a Routine” first.")
            return
        from PySide6.QtWidgets import QInputDialog
        names = [r.get("name", "") for r in routines]
        name, ok = QInputDialog.getItem(
            self, "Run Routine", "Choose a routine to replay:", names, 0, False)
        if ok and name:
            self.state.run_routine(name)

    def _sync_learn_btn(self) -> None:
        """Reflect learning state on the orb/status (button label is dynamic)."""
        if self.state and self.state.is_learning:
            self._set_orb_state("listening")
        else:
            if not (voice_engine and getattr(voice_engine, "is_speaking", False)):
                self._set_orb_state("idle")

    def _open_settings(self) -> None:
        if self.settings_dialog:
            self.settings_dialog.show()
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()

    def _open_settings_skills_tab(self) -> None:
        if self.settings_dialog:
            self.settings_dialog.show_tab(3)
        else:
            self._open_settings()

    def _toggle_voice_action(self, checked: bool) -> None:
        if not voice_engine:
            return
        if checked:
            voice_engine.unmute()
        else:
            voice_engine.mute()

    def _toggle_watch_action(self, checked: bool) -> None:
        if checked:
            self._start_watch()
        else:
            self._stop_watch()

    def _toggle_camera_action(self, checked: bool) -> None:
        self.camera_active = checked
        if checked and not HAS_CV2:
            self.camera_active = False
            self._action_cam.setChecked(False)
            self.bridge.set_status.emit("opencv-python not installed")

    @Slot(dict)
    def _on_token_usage(self, usage: dict) -> None:
        self._token_prompt += int(usage.get("prompt", 0))
        self._token_completion += int(usage.get("completion", 0))
        turns = self.state.session._turn_count if self.state else 0
        sess = self.state.session.summary if self.state else "Session 00:00 · 0 turns"
        self.token_footer.setText(
            f" Tokens: {self._token_prompt} prompt · {self._token_completion} completion "
            f"| {sess} · {turns} turns"
        )

    @Slot(dict)
    def _on_spatial_coords(self, coords: dict) -> None:
        target = str(coords.get("target", "") or "target")
        if not coords.get("found"):
            self.bridge.set_status.emit(
                f"Couldn't find \"{target}\" on screen — try making it visible or describe it differently."
            )
            return
        x, y = int(coords["x"]), int(coords["y"])
        w, h = int(coords.get("w", 0)), int(coords.get("h", 0))
        if coords.get("source") == "point_and_talk" and self.overlay:
            label = str(coords.get("label", "") or "Here")
            self.overlay.mark_target(x, y, w, h, label=label)
            self.bridge.set_status.emit("Point-and-Talk — highlighting target on screen")
        elif coords.get("guide") and self.overlay:
            self.overlay.mark_target(x, y, w, h, label=str(coords.get("label", "")))
            self.bridge.set_status.emit(f"Guide: highlighting {target}")
        elif not coords.get("guide"):
            # DO path: agent cursor handles visuals via StepEvent; skip teleport ring.
            self.bridge.set_status.emit(f"Located {target} — executing…")
        else:
            pass

    # ── Talk hotkey — conversation loop (tap-to-talk + silence endpointing) ───

    def _ptt_key_down(self, _event) -> None:
        """
        Global talk-key press (keyboard listener thread).

        Fires only when Space is pressed while Ctrl OR Alt is held.  The
        ``_ptt_engaged`` latch ignores OS auto-repeat so one physical press = one
        action.  All engine/UI work is marshalled to the main thread.

        Behaviour:
          • Atlas thinking or speaking  → interrupt it, then start listening.
          • Already listening           → finalise now (send what was captured).
          • Idle                        → start listening.
        """
        if self._ptt_engaged:
            return
        if not (keyboard.is_pressed("ctrl") or keyboard.is_pressed("alt")):
            return
        self._ptt_engaged = True
        self.bridge.ptt_active.emit(True)   # routed to _on_talk_key (main thread)

    def _ptt_key_up(self, _event) -> None:
        """Release just clears the one-press latch (tap model, not hold)."""
        self._ptt_engaged = False

    @Slot(bool)
    def _on_ptt_active(self, _active: bool) -> None:
        """Main-thread handler for a talk-key press (decides the action)."""
        if not self.audio:
            return

        busy = self._is_streaming or bool(voice_engine and getattr(voice_engine, "is_speaking", False))

        if busy:
            # Conversational break-in — silence Atlas, stop generation, re-listen.
            self._on_ptt_breakin()
            self.audio.start_listening()
        elif self.audio.is_listening:
            # Second tap while listening → stop waiting and transcribe now.
            self.audio.finalize_listening()
        else:
            self.audio.start_listening()

    @Slot()
    def _on_ptt_breakin(self) -> None:
        """Main-thread break-in: silence the AI and halt the live stream."""
        if self.state:
            self.state.cancel_current()
        self._do_stop_gen()
        if voice_engine:
            voice_engine.skip()

    def _on_orb_clicked(self) -> None:
        """Orb / Talk-button click — same decision as the talk hotkey."""
        self._on_ptt_active(True)

    def _set_orb_state(self, state: str) -> None:
        """Update the central status orb's visual state + caption."""
        self._orb_state = state
        colors = {
            "idle":       PAL["cyan"],
            "listening":  PAL["danger"],
            "thinking":   PAL["gold"],
            "processing": PAL["gold"],
            "speaking":   PAL["cyan"],
        }
        labels = {
            "idle":       "Tap Ctrl + Space to talk",
            "listening":  "Listening…",
            "thinking":   "Thinking…",
            "processing": "Transcribing…",
            "speaking":   "Speaking…",
        }
        hints = {
            "idle":       "hold a thought, pause when you're done",
            "listening":  "pause ~1.5s when you finish speaking",
            "thinking":   "composing a response",
            "processing": "turning your speech into text",
            "speaking":   "tap Ctrl + Space to interrupt",
        }
        if hasattr(self, "orb"):
            self.orb.set_state(state, colors.get(state, PAL["cyan"]))
            self.orb_state_lbl.setText(labels.get(state, ""))
            self.orb_hint_lbl.setText(hints.get(state, ""))

    @Slot(str)
    def _on_listen_state(self, state: str) -> None:
        """Drive the accent engine / orb / status from the listening session."""
        if state == "listening":
            self._set_accent_state("ptt")
            self._set_orb_state("listening")
            self.bridge.set_status.emit("🎧 Listening… (pause ~1.5s when done)")
            if self._is_floating:
                self._bubble.set_border_color(PAL["danger"])
                self._bubble.set_active(True)
        elif state == "processing":
            self._set_accent_state("processing")
            self._set_orb_state("processing")
            self.bridge.set_status.emit("📝 Transcribing…")
            if self._is_floating:
                self._bubble.set_active(True)
        else:  # idle
            if not self._is_streaming:
                self._set_accent_state("stealth" if self.stealth_active else "idle")
                self._set_orb_state("idle")
                if self._is_floating:
                    self._bubble.set_active(False)

    def _handle_slash_command(self, text: str) -> bool:
        cmd = text.lower().strip().split()[0]
        args = text.strip()[len(cmd):].strip()
        if cmd == "/debug" and self.state:
            self._append_response(self.state.get_debug_state())
        elif cmd == "/clear":
            self._clear_text()
        elif cmd == "/export":
            self._export_session()
        elif cmd == "/reload":
            self._do_reload()
        elif cmd == "/restore":
            self._restore_autosave()
        elif cmd == "/guide":
            self._submit_query(args or "Guide me through what I'm looking at")
            return True
        elif cmd == "/task":
            self._submit_query(args or "Do it for me: complete this task on screen")
            return True
        elif cmd == "/interview":
            self._on_focus_toggled(not getattr(self, "btn_focus", None) or not self.btn_focus.isChecked())
        elif cmd == "/copilot":
            if hasattr(self, "btn_copilot"):
                self.btn_copilot.toggle()
        elif cmd == "/memory" and self.state:
            report = self.state.learning.get_learning_report()
            self._append_response(json.dumps(report, indent=2))
        elif cmd == "/settings":
            self._open_control_center()
        elif cmd == "/help":
            lines = [f"{c} — {d}" for c, d in _SLASH_COMMANDS]
            self._append_response("Slash commands:\n" + "\n".join(lines))
        elif cmd == "/skills" and self.state:
            names = [s.get("display", s.get("name")) for s in self.state.skill_registry.list_skills()]
            self._append_response("Skills: " + (", ".join(names) if names else "(none)"))
        else:
            self._append_response(f"Unknown command: {text}")
        return True

    def _autosave(self) -> None:
        try:
            AUTOSAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {"ts": time.time(), "messages": self._chat_messages}
            AUTOSAVE_PATH.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

    def _offer_autosave_restore(self) -> None:
        if not AUTOSAVE_PATH.is_file():
            return
        try:
            data = json.loads(AUTOSAVE_PATH.read_text(encoding="utf-8"))
            age = time.time() - float(data.get("ts", 0))
            if age < 15 * 60:
                self._append_response(
                    "Autosave found (< 15 min). Type /restore to load it."
                )
        except Exception:
            pass

    def _restore_autosave(self) -> None:
        if not AUTOSAVE_PATH.is_file():
            return
        try:
            data = json.loads(AUTOSAVE_PATH.read_text(encoding="utf-8"))
            self._chat_messages = data.get("messages", [])
            self.chat_view.rebuild_from_messages(self._chat_messages)
            self.bridge.set_status.emit("Autosave restored ✓")
        except Exception as exc:
            self._append_response(f"Restore failed: {exc}")

    def _export_session(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Session", "atlas_session.md", "Markdown (*.md)")
        if not path:
            return
        lines = [f"# Atlas Session — {datetime.now().isoformat()}\n"]
        for msg in self._chat_messages:
            role = msg.get("role", "assistant")
            who = {"user": "You", "assistant": "Atlas", "proactive": "Copilot"}.get(role, role)
            plain = msg.get("text") or ""
            if not plain and msg.get("html"):
                import re
                plain = re.sub(r"<[^>]+>", " ", msg.get("html", "")).strip()
            lines.append(f"**{who}:** {plain.strip()}\n")
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        self.bridge.set_status.emit(f"Exported → {path}")

    def _run_startup_checks(self) -> None:
        checks = [
            ("groq", "groq"),
            ("cv2", "opencv-python"),
            ("pytesseract", "pytesseract"),
            ("sounddevice", "sounddevice"),
            ("kokoro_onnx", "kokoro-onnx"),
            ("pyautogui", "pyautogui"),
            ("mss", "mss"),
        ]
        lines = ["**Startup checks**"]
        for mod, pkg in checks:
            try:
                __import__(mod)
                lines.append(f"✓ {pkg}")
            except ImportError:
                lines.append(f"✗ {pkg} not installed")
        if not os.environ.get("GROQ_API_KEY"):
            lines.append("⚠ GROQ_API_KEY not set")
        self._append_response("\n".join(lines))

        def _validate_api() -> None:
            if not _CORE:
                return
            try:
                groq_client.models.list()
            except Exception as exc:
                self.bridge.append_text.emit(f"[API] Validation failed: {exc}")

        threading.Thread(target=_validate_api, daemon=True).start()

    def _do_stop_gen(self):
        """
        Single 'shut up' action: stops text streaming AND flushes the TTS queue.
        """
        self._stop_gen.set()
        if self.state:
            self.state.cancel_current()
            self.state.stop_task()
        if voice_engine:
            voice_engine.flush()
            voice_engine.skip()
        self.chat_composer.set_streaming(False)
        self._release_companion_mode(status="Stopped ⏹", finalize_thinking=True)

    def _stop_running_task(self) -> None:
        """Stop an in-flight TASK / routine loop and hide emergency-stop UI."""
        if self.state and getattr(self.state, "_task_running", False):
            self.state.stop_task()
        else:
            self._hide_task_stop_ui()

    def _show_task_stop_ui(self) -> None:
        if hasattr(self, "btn_task_stop"):
            self.btn_task_stop.setVisible(True)
        if getattr(self, "task_stop_overlay", None):
            self.task_stop_overlay.show_stop()

    def _hide_task_stop_ui(self) -> None:
        if hasattr(self, "btn_task_stop"):
            self.btn_task_stop.setVisible(False)
        if getattr(self, "task_stop_overlay", None):
            self.task_stop_overlay.hide_stop()

    # ═════════════════════════════════════════════════════════════════════════
    # TEXT AREA HELPERS
    # ═════════════════════════════════════════════════════════════════════════

    @Slot(str)
    def _set_status(self, msg: str) -> None:
        """
        Display a status message, holding it for at least _STATUS_MIN_MS before
        it can be overwritten.  If a new message arrives during the hold period
        it is queued and shown automatically when the hold expires (Bug #23).
        """
        import time as _time
        now = _time.monotonic()
        elapsed_ms = (now - self._last_status_time) * 1000

        if elapsed_ms >= self._STATUS_MIN_MS or self._last_status_time == 0.0:
            self.status_lbl.setText(f"  {msg}")
            self._last_status_time = now
            self._pending_status   = ""
        else:
            # Still in hold period — queue for later display
            self._pending_status = msg
            remaining = int(self._STATUS_MIN_MS - elapsed_ms) + 50
            self._status_hold_timer.stop()
            self._status_hold_timer.start(remaining)

    def _flush_pending_status(self) -> None:
        """Called when the hold timer fires; shows any queued status message."""
        if self._pending_status:
            import time as _time
            self.status_lbl.setText(f"  {self._pending_status}")
            self._last_status_time = _time.monotonic()
            self._pending_status   = ""

    def _on_anchor_clicked(self, url: QUrl) -> None:
        self._on_chat_anchor(url)

    def _copy_text(self):
        pyperclip.copy(self.chat_view.plain_text())
        self.bridge.set_status.emit("Copied ✓")

    def _download_pdf(self):
        """Export the current conversation to a styled PDF via reportlab."""
        if not self._chat_messages:
            self.bridge.set_status.emit("Nothing to download yet.")
            return
        default = f"atlas-chat-{time.strftime('%Y%m%d-%H%M')}.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Download conversation", default, "PDF (*.pdf)")
        if not path:
            return
        try:
            self._export_chat_pdf(path)
            self.bridge.set_status.emit(f"Saved PDF → {Path(path).name}")
        except Exception as exc:
            self.bridge.set_status.emit(f"PDF export failed: {exc}")

    def _export_chat_pdf(self, path: str) -> None:
        import html as _html
        import re as _re
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Preformatted)

        styles = getSampleStyleSheet()
        user_style = ParagraphStyle(
            "User", parent=styles["Normal"], alignment=TA_RIGHT,
            textColor=colors.HexColor("#0B5563"), spaceBefore=10, spaceAfter=2,
            fontSize=10.5, leading=15)
        atlas_style = ParagraphStyle(
            "Atlas", parent=styles["Normal"],
            textColor=colors.HexColor("#1A1A1F"), spaceBefore=10, spaceAfter=2,
            fontSize=10.5, leading=15)
        label_style = ParagraphStyle(
            "Label", parent=styles["Normal"], fontSize=8,
            textColor=colors.HexColor("#888888"), spaceAfter=1)
        code_style = ParagraphStyle(
            "Code", parent=styles["Code"], fontSize=8.5, leading=11,
            backColor=colors.HexColor("#F2F3F5"), borderPadding=6,
            textColor=colors.HexColor("#0A3A4A"))

        def _to_plain(text: str) -> str:
            return text or ""

        story = [
            Paragraph("Atlas — Conversation", styles["Title"]),
            Paragraph(time.strftime("%A, %d %b %Y · %H:%M"), label_style),
            Spacer(1, 0.18 * inch),
        ]
        fence = _re.compile(r"```[\w+-]*\n?([\s\S]*?)```")
        for msg in self._chat_messages:
            role = msg.get("role")
            raw = msg.get("text")
            if raw is None:
                # Older user bubbles stored only HTML — strip tags for the PDF.
                raw = _re.sub(r"<[^>]+>", "", msg.get("html", ""))
                raw = _html.unescape(raw)
            who = "You" if role == "user" else "Atlas"
            story.append(Paragraph(who, label_style))
            # Split out fenced code blocks into monospace boxes.
            pos = 0
            for m in fence.finditer(raw):
                before = raw[pos:m.start()].strip()
                if before:
                    story.append(Paragraph(
                        _html.escape(before).replace("\n", "<br/>"),
                        user_style if role == "user" else atlas_style))
                story.append(Preformatted(m.group(1).rstrip(), code_style))
                pos = m.end()
            tail = raw[pos:].strip()
            if tail:
                story.append(Paragraph(
                    _html.escape(tail).replace("\n", "<br/>"),
                    user_style if role == "user" else atlas_style))

        doc = SimpleDocTemplate(
            path, pagesize=LETTER, title="Atlas Conversation",
            leftMargin=0.8 * inch, rightMargin=0.8 * inch,
            topMargin=0.8 * inch, bottomMargin=0.8 * inch)
        doc.build(story)

    def _clear_text(self):
        self._stop_gen.set()
        if self.state:
            self.state.cancel_current()
        if voice_engine:
            voice_engine.flush()
            voice_engine.skip()
        self.chat_view.clear()
        self.chat_composer.set_streaming(False)
        self._md_plain_prefix = ""
        self._md_ai_buffer = ""
        self._md_ai_streaming = False
        self._chat_messages = []
        self._streaming_html = ""
        self._pending_attachments.clear()
        self.chat_composer.set_attach_label("")
        self._token_prompt = 0
        self._token_completion = 0
        if self.state:
            self.state.session.end()
        self.token_footer.setText(" Tokens: 0 prompt · 0 completion | Session: 00:00 · 0 turns")
        self.bridge.set_status.emit("Cleared ✓")

    # ═════════════════════════════════════════════════════════════════════════
    # AUDIO / CAMERA TOGGLES
    # ═════════════════════════════════════════════════════════════════════════

    def _toggle_mic(self, checked: bool = False):
        if not self.audio:
            return
        if checked:
            self.audio.start_mic()
        else:
            self.audio.stop_mic()
        if self.state and getattr(self.state, "audio_watcher", None):
            watcher = self.state.audio_watcher
            if hasattr(watcher, "bind_audio"):
                watcher.bind_audio(self.audio)
        self._action_mic.blockSignals(True)
        self._action_mic.setChecked(self.audio.mic_active)
        self._action_mic.blockSignals(False)

    def _toggle_speaker(self, checked: bool = False):
        if not self.audio:
            return
        if checked:
            self.audio.start_speaker()
        else:
            self.audio.stop_speaker()
        self._action_spk.blockSignals(True)
        self._action_spk.setChecked(self.audio.speaker_active)
        self._action_spk.blockSignals(False)

    def _toggle_camera(self):
        """Toggle the cv2 webcam capture state."""
        self.camera_active = not self.camera_active
        self._action_cam.setChecked(self.camera_active)
        if self.camera_active and not HAS_CV2:
            self.bridge.set_status.emit("📸 Camera: opencv-python not installed (pip install opencv-python)")
            self.camera_active = False
            self._action_cam.setChecked(False)
        else:
            status = "📸 Camera ON — webcam frame will be sent with each query" if self.camera_active else "📸 Camera OFF"
            self.bridge.set_status.emit(status)

    def _on_transcript(self, text, source):
        src = (source or "").lower()
        if src == "mic" and self.audio and not self.audio.mic_active:
            return
        routed = "forward"
        if self.state and getattr(self.state, "audio_watcher", None):
            routed = self.state.audio_watcher.route_transcript(text, source)
        if routed == "buffer":
            return
        if routed == "drop":
            self.bridge.set_status.emit(
                f"🎤 Heard ({source}) — ignored (you typed recently)")
            return
        clean = (text or "").strip()
        if not clean:
            return
        self.bridge.set_status.emit(f"🎤 Heard ({source}) — processing…")
        msg = self.chat_view.add_message("user", clean)
        self._chat_messages.append(msg)
        if self.state and routed != "consumed":
            self.bridge.set_status.emit(f"🎤 Heard ({source}) — processing…")
            threading.Thread(
                target=self.state.handle_input,
                args=(text, source),
                daemon=True,
            ).start()
        elif self.state and routed == "consumed":
            self._begin_interaction(source)

    # ═════════════════════════════════════════════════════════════════════════
    # SCREEN CAPTURE + OCR
    # ═════════════════════════════════════════════════════════════════════════

    def _do_capture(self):
        self.hide()
        self.lock_mouse_capture(False)
        self.bridge.set_status.emit("📷 Capturing screen…")

        worker = OCRWorker(hide_delay_s=0.15, parent=None)
        worker.grab_done.connect(self._on_capture_grab_done)
        worker.ocr_completed.connect(self._on_ocr_ready)
        worker.ocr_failed.connect(self._on_ocr_failed)
        worker.finished.connect(worker.deleteLater)

        if not hasattr(self, "_active_ocr_workers"):
            self._active_ocr_workers: list = []
        self._active_ocr_workers.append(worker)

        def _remove(w=worker):
            try:
                self._active_ocr_workers.remove(w)
            except ValueError:
                pass

        worker.finished.connect(_remove)
        worker.start()

    @Slot()
    def _on_capture_grab_done(self) -> None:
        self.show()
        self.lock_mouse_capture(False)
        self.unsetCursor()

    @Slot(str)
    def _on_ocr_failed(self, msg: str) -> None:
        from atlas_interaction import friendly_error
        self.show()
        self._release_companion_mode(
            error=f"⚠ {friendly_error('ocr', msg)}",
            finalize_thinking=False,
        )

    @Slot(str)
    def _on_ocr_ready(self, txt: str):
        from atlas_interaction import gather_screen_frame

        if self.state:
            screen_b64, _summary = gather_screen_frame()
            if screen_b64:
                self.state.inject_screen_capture(screen_b64)
            msg = self.chat_view.add_message("user", f"[Screen capture]\n{txt[:500]}")
            self._chat_messages.append(msg)
            self._begin_interaction("capture")
            self.bridge.set_status.emit("Analyzing screen…")
            webcam_b64 = self._capture_webcam_frame()
            threading.Thread(
                target=self.state.handle_input,
                args=(txt,),
                kwargs={"source": "capture", "webcam_b64": webcam_b64},
                daemon=True,
            ).start()
        else:
            self._release_companion_mode(status="Capture complete ✓", finalize_thinking=False)

    # ═════════════════════════════════════════════════════════════════════════
    # CLIPBOARD WATCHER
    # ═════════════════════════════════════════════════════════════════════════

    def _toggle_highlight(self):
        self.highlight_active = not self.highlight_active
        self._action_hl.setChecked(self.highlight_active)
        if self.highlight_active:
            self._last_clipboard = self._clipboard.text().strip()

    def _toggle_highlight_action(self, checked: bool) -> None:
        self.highlight_active = checked
        if checked:
            self._last_clipboard = self._clipboard.text().strip()

    def _on_clipboard_changed(self) -> None:
        if not self.highlight_active:
            return
        text = self._clipboard.text().strip()
        if text and text != getattr(self, "_last_clipboard", ""):
            self._last_clipboard = text
            self._append_user_bubble(f"[clipboard] {text[:120]}…")
            if self.state:
                threading.Thread(
                    target=self.state.handle_input,
                    args=(text,),
                    kwargs={"source": "highlight"},
                    daemon=True,
                ).start()

    # ═════════════════════════════════════════════════════════════════════════
    # SCREEN WATCHER
    # ═════════════════════════════════════════════════════════════════════════

    def _start_watch(self) -> None:
        if self.watch_active:
            return
        self.watch_active = True
        self._action_watch.blockSignals(True)
        self._action_watch.setChecked(True)
        self._action_watch.blockSignals(False)
        if self.state:
            self.state.set_screen_vision(True)   # Atlas can now SEE the screen
        self.bridge.set_status.emit("Watcher Active — Atlas can see your screen")
        worker = WatchWorker(
            interval=getattr(self, "_watch_interval", 5),
            sensitivity=getattr(self, "_watch_sensitivity", "Medium"),
            parent=None,
        )
        worker.screen_text.connect(self._on_watch_text)
        worker.status.connect(self.bridge.set_status)
        worker.finished.connect(worker.deleteLater)
        self._watch_worker = worker
        worker.start()

    def _stop_watch(self) -> None:
        if not self.watch_active:
            return
        self.watch_active = False
        self._action_watch.blockSignals(True)
        self._action_watch.setChecked(False)
        self._action_watch.blockSignals(False)
        if self.state:
            self.state.set_screen_vision(False)
        worker = getattr(self, "_watch_worker", None)
        if worker:
            worker.stop()
            worker.quit()
            worker.wait(2000)
            self._watch_worker = None
        self.bridge.set_status.emit("Watcher Stopped")

    def _toggle_watch(self):
        if self.watch_active:
            self._stop_watch()
        else:
            self._start_watch()

    @Slot(str)
    def _on_watch_text(self, txt: str):
        if self.state:
            # Bug #20: previously showed "[WATCHER Sent]" with no explanation.
            # Now tells the user exactly what happened so they know it's buffered,
            # not silently ignored, and how to surface it.
            self.bridge.set_status.emit("👁 Screen snapshot buffered — ask Atlas anything to include it")
            threading.Thread(
                target=self.state.handle_input,
                args=(txt, "watch"),
                daemon=True,
            ).start()

    # ═════════════════════════════════════════════════════════════════════════
    # DOCUMENT UPLOAD & CONTEXT INJECTION
    # ═════════════════════════════════════════════════════════════════════════

    def _do_upload_context(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Upload Interview Context",
            "",
            "Documents (*.pdf *.txt *.md);;All Files (*)",
        )
        if not path:
            return

        self.bridge.set_status.emit(f"Loading {os.path.basename(path)}…")
        worker = ContextIngestWorker(path, parent=None)
        worker.ingest_done.connect(self._on_context_ingest_done)
        worker.ingest_failed.connect(self.bridge.set_status)
        worker.progress.connect(self.bridge.set_status)
        worker.finished.connect(worker.deleteLater)

        if not hasattr(self, "_ingest_workers"):
            self._ingest_workers: list = []
        self._ingest_workers.append(worker)
        worker.finished.connect(
            lambda w=worker: self._ingest_workers.remove(w) if w in self._ingest_workers else None
        )
        worker.start()

    @Slot(str)
    def _on_context_ingest_done(self, text: str):
        if not self.state:
            self.bridge.set_status.emit("Context loaded (no active session).")
            return
        if not self.state.session.is_active:
            from atlas_core import MODE_SYSTEMS
            self.state.session.start(MODE_SYSTEMS[self.state.mode])
        char_count = len(text)
        label = f"[USER CONTEXT — {char_count:,} chars]\n\n{text}"
        self.state.session.add_pinned_context(label, source="context")
        preview = text[:80].replace("\n", " ")
        self.bridge.append_text.emit(
            f"📄 Context injected ({char_count:,} chars) — AI will reference this throughout.\n"
            f"   Preview: {preview}…\n"
        )
        self.bridge.set_status.emit(f"Context loaded ✓  ({char_count:,} chars pinned)")

    # ═════════════════════════════════════════════════════════════════════════
    # FLOAT MODE & GHOST UI  (Requirement 6 — see also FloatBubble)
    # ═════════════════════════════════════════════════════════════════════════

    def _on_bubble_dragged(self, new_pos: QPoint) -> None:
        self._bubble_pos = new_pos

    def _get_pulse_accent(self) -> tuple[str, str]:
        if self.stealth_active:
            return PAL["danger"], PAL["danger_dim"]
        mic_on = self.audio and (
            self.audio.mic_active or getattr(self.audio, "speaker_active", False)
        )
        if mic_on:
            return "#9B59B6", "#4A235A"
        if self.watch_active:
            return PAL["cyan"], PAL["cyan_dim"]
        return PAL["cyan"], PAL["cyan_dim"]

    def _nearest_edge(self, cx: int, cy: int) -> str:
        screen = QApplication.primaryScreen().availableGeometry()
        dists = {
            "left":   cx - screen.left(),
            "right":  screen.right()  - cx,
            "top":    cy - screen.top(),
            "bottom": screen.bottom() - cy,
        }
        return min(dists, key=dists.__getitem__)

    def _snap_geo_for_edge(self, edge: str, cx: int, cy: int) -> QRect:
        screen = QApplication.primaryScreen().availableGeometry()
        size   = FloatBubble.SIZE
        if edge == "left":
            return QRect(screen.left(), cy - size // 2, size, size)
        if edge == "right":
            return QRect(screen.right() - size, cy - size // 2, size, size)
        if edge == "top":
            return QRect(cx - size // 2, screen.top(), size, size)
        return QRect(cx - size // 2, screen.bottom() - size, size, size)

    def _set_ambient_visual(self, active: bool) -> None:
        self._ambient_pulse_on = active
        if active:
            self._ambient_timer.start()
        else:
            self._ambient_timer.stop()
            self.workspace.setStyleSheet("")

    def _stop_ambient_on_input(self) -> None:
        if self.watch_active:
            pass  # screen watch stays on; only the ambient border pulse pauses on type

    def _tick_ambient_border(self) -> None:
        if not self._ambient_pulse_on:
            return
        self._ambient_border_phase = (self._ambient_border_phase + 1) % 2
        alpha = "88" if self._ambient_border_phase else "33"
        self.workspace.setStyleSheet(
            f"QFrame#workspace {{ background: {PAL['bg']}; border-radius: 12px; "
            f"border: 1px solid #{alpha}{PAL['cyan_dim'].lstrip('#')}; }}"
        )

    # ── Geometric Vortex Morph (Float Mode transition) ────────────────────────

    def _orb_center_in_root(self) -> QPoint:
        """Absolute geometric centre of the StatusOrb in workspace-parent coords."""
        return self.orb.mapTo(
            self.root_widget,
            QPoint(self.orb.width() // 2, self.orb.height() // 2),
        )

    def _run_vortex(self, collapsing: bool, on_done: Optional[Callable] = None) -> None:
        """
        Warp the workspace card into / out of the central StatusOrb.

        A QParallelAnimationGroup drives the actual QRect geometry of the card —
        scaling it down and sliding it up so its bounding centre converges on the
        orb's absolute centre — paired with a QGraphicsOpacityEffect fade.
        Collapse uses InBack (sucked into the vortex over 350ms); the reverse
        uses OutCubic (projected back out).  The orb never moves; the layout is
        frozen for the duration so it can't fight the geometry animation.
        """
        ws = self.workspace
        old = getattr(self, "_vortex_group", None)
        if old is not None:
            old.stop()

        eff = getattr(self, "_ws_vortex_effect", None)
        if eff is None:
            eff = QGraphicsOpacityEffect(ws)
            self._ws_vortex_effect = eff
        ws.setGraphicsEffect(eff)

        # Freeze the layout so it doesn't reassert the card's geometry mid-warp.
        self.main_lay.setEnabled(False)

        c = self._orb_center_in_root()
        small = QRect(c.x() - 12, c.y() - 12, 24, 24)

        if collapsing:
            full = ws.geometry()
            self._ws_full_rect = QRect(full)
            start_rect, end_rect = full, small
            start_op, end_op, curve = 1.0, 0.0, QEasingCurve.InBack
        else:
            full = getattr(self, "_ws_full_rect", None) or ws.geometry()
            ws.setGeometry(small)
            start_rect, end_rect = small, full
            start_op, end_op, curve = 0.0, 1.0, QEasingCurve.OutCubic

        eff.setOpacity(start_op)

        grp = QParallelAnimationGroup(self)
        geo = QPropertyAnimation(ws, b"geometry", self)
        geo.setDuration(350)
        geo.setStartValue(start_rect)
        geo.setEndValue(end_rect)
        geo.setEasingCurve(curve)
        op = QPropertyAnimation(eff, b"opacity", self)
        op.setDuration(350)
        op.setStartValue(start_op)
        op.setEndValue(end_op)
        op.setEasingCurve(curve)
        grp.addAnimation(geo)
        grp.addAnimation(op)

        def _cleanup() -> None:
            if not collapsing:
                # Hand geometry management back to the layout and drop the effect.
                self.main_lay.setEnabled(True)
                self.main_lay.activate()
                ws.setGraphicsEffect(None)
            if on_done:
                on_done()

        grp.finished.connect(_cleanup)
        self._vortex_group = grp
        grp.start()

    def _enter_float(self):
        if self._is_floating:
            return
        self._ws_full_h = self.workspace.height()
        self._is_floating = True
        if getattr(self, "_ribbon_visible", False):
            self._animate_ribbon(0.0)
            self._ribbon_visible = False
        # Collapse the card into the orb, THEN dock the bubble.
        self._run_vortex(collapsing=True, on_done=self._finish_enter_float)

    def _finish_enter_float(self):
        self.hide()
        bright, _ = self._get_pulse_accent()
        center = self.geometry().center()
        cx, cy = center.x(), center.y()
        self._docked_edge = self._nearest_edge(cx, cy)
        snap = self._snap_geo_for_edge(self._docked_edge, cx, cy)
        self._bubble.set_border_color(bright)
        self._bubble.setGeometry(snap)
        self._bubble.show()
        self._bubble.raise_()
        self._start_pulse()

    def _leave_float(self):
        self._stop_pulse()
        self._pill_win.hide()
        self._bubble.hide()
        self.show()
        self.raise_()
        # Project the workspace back out of the orb, then clear the float flag.
        self._run_vortex(
            collapsing=False,
            on_done=lambda: setattr(self, "_is_floating", False),
        )

    def _toggle_float(self):
        if self._is_floating:
            self._leave_float()
        else:
            self._enter_float()

    def _start_pulse(self):
        self._stop_pulse()
        bright, dim = self._get_pulse_accent()

        self._pulse_float_glow = QVariantAnimation()
        self._pulse_float_glow.setStartValue(QColor(dim))
        self._pulse_float_glow.setEndValue(QColor(bright))
        self._pulse_float_glow.setDuration(900)
        self._pulse_float_glow.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse_float_glow.setLoopCount(-1)
        self._pulse_forward = True

        def _on_value_changed(color: QColor):
            if not self._is_floating:
                return
            self._bubble.set_border_color(color)

        def _on_loop():
            self._pulse_forward = not self._pulse_forward
            if self._pulse_forward:
                self._pulse_float_glow.setStartValue(QColor(dim))
                self._pulse_float_glow.setEndValue(QColor(bright))
            else:
                self._pulse_float_glow.setStartValue(QColor(bright))
                self._pulse_float_glow.setEndValue(QColor(dim))

        self._pulse_float_glow.valueChanged.connect(_on_value_changed)
        self._pulse_float_glow.currentLoopChanged.connect(_on_loop)
        self._pulse_float_glow.start()

        self._pulse_refresh_timer = QTimer(self)
        self._pulse_refresh_timer.setInterval(2000)
        self._pulse_refresh_timer.timeout.connect(self._refresh_pulse_accent)
        self._pulse_refresh_timer.start()

    def _stop_pulse(self):
        anim = getattr(self, "_pulse_float_glow", None)
        if anim:
            anim.stop()
            self._pulse_float_glow = None
        timer = getattr(self, "_pulse_refresh_timer", None)
        if timer:
            timer.stop()
            self._pulse_refresh_timer = None

    def _refresh_pulse_accent(self):
        if getattr(self, "_pulse_float_glow", None) and self._is_floating:
            self._start_pulse()

    @Slot(str)
    def _show_pill(self, text: str):
        if not self._is_floating:
            return
        edge = getattr(self, "_docked_edge", "right")
        # Communication transition — snap the bubble fully visible (Req 4E).
        self._bubble.flash_visible()
        self._pill_win.show_text(text, self._bubble.geometry(), edge)


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    def _global_excepthook(exc_type, exc_value, exc_tb) -> None:
        import traceback
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        get_logger("ui").error("Uncaught exception:\n%s", tb)
        try:
            app = QApplication.instance()
            if app:
                from PySide6.QtWidgets import QMessageBox
                box = QMessageBox()
                box.setWindowTitle("Atlas hit a problem")
                box.setText(
                    "Something unexpected happened, but Atlas is still running.\n"
                    f"{exc_value}"
                )
                box.setDetailedText(tb)
                box.setIcon(QMessageBox.Warning)
                box.exec()
        except Exception:
            pass

    sys.excepthook = _global_excepthook
    if HAS_LOGGING:
        install_thread_exception_hook(
            lambda t, v, tb: _global_excepthook(t, v, tb)
        )

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))

    # ── Account gate — Atlas requires atlas_daemon ────────────────────────────
    if not HAS_DAEMON or not ensure_daemon_running:
        QMessageBox.critical(
            None,
            "Atlas daemon required",
            "Atlas must run with the background daemon.\n\n"
            "Install and start it:\n"
            "  powershell -File scripts/install_atlas_daemon.ps1\n\n"
            "Or manually: python -m atlas_daemon",
        )
        sys.exit(1)
    daemon_client = None
    account = None
    chosen_uid, chosen_name = None, None
    try:
        daemon_client = ensure_daemon_running()
    except DaemonError as exc:
        QMessageBox.critical(
            None,
            "Atlas daemon",
            f"{exc}\n\nStart manually: python -m atlas_daemon",
        )
        sys.exit(1)
    if daemon_client and HAS_ACCOUNTS and AccountClient:
        account = AccountClient(daemon_client)
        login = LoginDialog(account)
        login.exec()
        chosen_uid, chosen_name = login.user_id, login.user_name
        if chosen_name:
            os.environ["ATLAS_USER"] = chosen_name

    win = AtlasWindow(daemon_client=daemon_client)
    win.account = account
    if account and chosen_uid and win.state:
        win.state.set_user(chosen_uid, chosen_name)
    win.apply_account_identity(chosen_name or "Guest")
    if win.state and win.state.focus_mode:
        win._on_focus_mode_changed(True)
    if win.telemetry and win.state:
        email = getattr(account, "email", "") if account else ""
        st = win.telemetry.check_license(email)
        win.telemetry.apply_execution_gate(win.state)
        if not st.allowed:
            win._on_license_update(st)
        win.telemetry.start_daily_check(email, on_update=win._on_license_update)
    win.show()
    sys.exit(app.exec())
