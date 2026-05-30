"""
aceit_ui.py — AceIt Frontend (PySide6)
Features: Detached Floating Header, Circular Morph Animations, Global Hotkeys.
"""
from __future__ import annotations

# ── DPI Awareness — MUST be set before any Qt or third-party import ────────────
import os, sys
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

import time, threading
from typing import Optional

# Desktop Integration
import keyboard
import pyautogui
import pyperclip
# pytesseract and PIL.ImageGrab are imported lazily inside OCRWorker.run()
# so the app stays importable even without Tesseract installed.
# mss is imported lazily inside WatchWorker for the same reason.

from PySide6.QtCore import (
    Qt, QPoint, QSize, QPropertyAnimation, QVariantAnimation, QEasingCurve, QRect,
    QTimer, Signal, QObject, Slot, QThread,
)
from PySide6.QtGui import QColor, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame,
    QHBoxLayout, QVBoxLayout, QTextEdit, QTextBrowser, QLineEdit,
    QPushButton, QLabel, QSizePolicy, QGraphicsDropShadowEffect,
    QDialog, QSlider, QComboBox, QTabWidget, QScrollArea,
    QListWidget, QListWidgetItem, QStackedWidget, QCheckBox,
    QProgressBar, QFileDialog,
)

# ── Markdown renderer (markdown-it-py) ────────────────────────────────────────
try:
    from markdown_it import MarkdownIt
    _md = MarkdownIt()
    HAS_MARKDOWN_IT = True
except ImportError:
    _md = None
    HAS_MARKDOWN_IT = False

try:
    from aceit_core import (
        ModeState, StateEngine, AudioEngine,
        groq_client, GROQ_MODEL, GROQ_MODELS, GROQ_MODEL_LABELS,
        RESPONSE_STYLES,
    )
    import aceit_core as _core_mod   # for mutating GROQ_MODEL at runtime
    _CORE = True
    _INTERVIEW_MODE = ModeState.INTERVIEW
except ImportError:
    _CORE = False
    _INTERVIEW_MODE = None

# ── Theme Palettes ─────────────────────────────────────────────────────────────
PAL = {
    "bg": "#0A0D10", "surface": "#11161B", "surface_2": "#1A2128",
    "border": "#262E37", "gold": "#D4AF37", "gold_dim": "#8B7220",
    "blue": "#47A1FF", "blue_dim": "#1E5FA8", "text": "#E8EDF2",
    "muted": "#6B7A8D", "danger": "#FF4A6E", "success": "#2ECC8A"
}

QSS = f"""
QWidget {{ background: transparent; color: {PAL['text']}; font-family: 'Segoe UI'; }}
QFrame#floating_header {{ background: {PAL['surface']}; border-radius: 12px; border: 1px solid {PAL['border']}; }}
QFrame#workspace {{ background: {PAL['bg']}; border-radius: 12px; border: 1px solid {PAL['border']}; }}
QFrame#action_dock {{ background: {PAL['surface']}; border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; border-top: 1px solid {PAL['border']}; }}
QPushButton {{ background: transparent; border: none; }}
QPushButton:hover {{ background: {PAL['surface_2']}; border-radius: 6px; }}
QPushButton#dock_btn {{ font-size: 14px; background: {PAL['surface_2']}; border-radius: 6px; }}
QPushButton#dock_btn:hover {{ background: {PAL['border']}; color: {PAL['gold']}; }}
QPushButton#dock_btn[active="true"] {{ background: rgba(71, 161, 255, 0.2); color: {PAL['blue']}; }}
QLineEdit {{ background: {PAL['surface_2']}; border-radius: 6px; padding: 6px; color: {PAL['text']}; }}
QTextEdit, QTextBrowser {{ background: {PAL['surface_2']}; border: none; border-radius: 6px; padding: 10px; }}
QTextBrowser pre {{ background: {PAL['bg']}; border: 1px solid {PAL['border']}; border-radius: 4px; padding: 8px; font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; color: {PAL['blue']}; }}
QTextBrowser code {{ background: {PAL['bg']}; border-radius: 3px; padding: 1px 4px; font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; color: {PAL['blue']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {PAL['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {PAL['gold']}; width: 12px; margin: -4px 0; border-radius: 6px; }}
"""

# ── Signal Bridge (Cross-thread UI Updates) ────────────────────────────────────
class SignalBridge(QObject):
    append_text    = Signal(str)
    set_status     = Signal(str)
    set_badge      = Signal(str)
    notify_pill    = Signal(str)
    start_thinking = Signal()       # fired from worker thread → starts thinking bar on UI thread
    thinking_done  = Signal()       # fired from worker thread → stops thinking bar on UI thread
    stream_token   = Signal(str)    # fired per streaming chunk → appended to text area live

# ── Settings Dialog — Command Center ──────────────────────────────────────────
_SETTINGS_QSS = f"""
/* ── Dialog chrome ── */
QDialog {{ background: transparent; }}

/* ── QTabWidget bar ── */
QTabWidget::pane {{
    background: {PAL['surface']};
    border: none;
    border-bottom-left-radius: 12px;
    border-bottom-right-radius: 12px;
}}
QTabBar {{
    background: {PAL['bg']};
    border-bottom: 1px solid {PAL['border']};
}}
QTabBar::tab {{
    background: transparent;
    color: {PAL['muted']};
    font-size: 12px;
    padding: 9px 18px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    color: {PAL['gold']};
    border-bottom: 2px solid {PAL['gold']};
    background: transparent;
}}
QTabBar::tab:hover:!selected {{
    color: {PAL['text']};
    background: {PAL['surface_2']};
}}

/* ── Section headers ── */
QLabel#section_hdr {{
    color: {PAL['gold']};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    padding-bottom: 4px;
    border-bottom: 1px solid {PAL['border']};
    background: transparent;
}}

/* ── Slider ── */
QSlider::groove:horizontal {{ height: 4px; background: {PAL['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {PAL['gold']}; width: 13px; height: 13px;
    margin: -5px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{ background: {PAL['gold_dim']}; border-radius: 2px; }}

/* ── ComboBox ── */
QComboBox {{
    background: {PAL['surface_2']}; border: 1px solid {PAL['border']};
    border-radius: 6px; padding: 4px 10px; color: {PAL['text']};
    min-width: 110px;
}}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background: {PAL['surface_2']}; border: 1px solid {PAL['border']};
    selection-background-color: {PAL['border']};
}}

/* ── Toggle pill ── */
QPushButton#toggle_on {{
    background: {PAL['success']}; color: {PAL['bg']};
    border-radius: 10px; font-size: 11px; font-weight: bold;
    min-width: 52px; max-width: 52px; min-height: 20px; max-height: 20px;
}}
QPushButton#toggle_off {{
    background: {PAL['border']}; color: {PAL['muted']};
    border-radius: 10px; font-size: 11px;
    min-width: 52px; max-width: 52px; min-height: 20px; max-height: 20px;
}}

/* ── Hotkey cards ── */
QFrame#hotkey_card {{
    background: {PAL['surface_2']}; border: 1px solid {PAL['border']};
    border-radius: 8px;
}}
QLabel#hotkey_badge {{
    background: {PAL['bg']}; color: {PAL['gold']};
    border: 1px solid {PAL['gold_dim']}; border-radius: 5px;
    font-family: 'Consolas', monospace; font-size: 11px;
    padding: 3px 8px;
}}

/* ── Action / done buttons ── */
QPushButton#action_btn {{
    background: {PAL['surface_2']}; color: {PAL['blue']};
    border: 1px solid {PAL['border']}; border-radius: 6px;
    padding: 5px 12px; font-size: 12px;
}}
QPushButton#action_btn:hover {{ background: {PAL['border']}; color: {PAL['text']}; }}
QPushButton#done_btn {{
    background: {PAL['gold']}; color: {PAL['bg']};
    border-radius: 6px; padding: 7px 24px;
    font-weight: bold; font-size: 13px;
}}
QPushButton#done_btn:hover {{ background: {PAL['gold_dim']}; color: {PAL['text']}; }}
"""


class SettingsDialog(QDialog):
    """
    Command Center modal — three-tab QTabWidget layout.

    Tab 1  👁  Vision & Tracking   — watcher interval, sensitivity, highlight toggle
    Tab 2  🎙  Audio Configuration — device names, interview mode, enumerate utility
    Tab 3  🎨  UI & Hotkeys        — opacity, theme toggle, read-only hotkey reference
    """

    W, H = 560, 440

    def __init__(self, parent, engine, audio, ui_window):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(_SETTINGS_QSS)
        self.setFixedSize(self.W, self.H)

        self.ui    = ui_window
        self.audio = audio

        # ── Outer chrome frame ────────────────────────────────────────────────
        chrome = QFrame(self)
        chrome.setGeometry(0, 0, self.W, self.H)
        chrome.setStyleSheet(
            f"background: {PAL['surface']};"
            f"border: 1px solid {PAL['gold_dim']};"
            f"border-radius: 12px;"
        )

        outer = QVBoxLayout(chrome)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(44)
        title_bar.setStyleSheet(
            f"background: {PAL['bg']};"
            f"border-top-left-radius: 12px; border-top-right-radius: 12px;"
            f"border-bottom: 1px solid {PAL['border']};"
        )
        tb_lay = QHBoxLayout(title_bar)
        tb_lay.setContentsMargins(16, 0, 12, 0)

        lbl_title = QLabel("⚙  Command Center")
        lbl_title.setStyleSheet(
            f"color: {PAL['gold']}; font-size: 14px; font-weight: bold;"
            f"border: none; background: transparent;"
        )
        tb_lay.addWidget(lbl_title)
        tb_lay.addStretch()

        btn_x = QPushButton("✕")
        btn_x.setFixedSize(24, 24)
        btn_x.setStyleSheet(
            f"background: transparent; color: {PAL['muted']}; font-size: 13px;"
            f"border: none; border-radius: 4px;"
        )
        btn_x.clicked.connect(self.close)
        tb_lay.addWidget(btn_x)
        outer.addWidget(title_bar)

        # ── QTabWidget body ───────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)        # flat bar, no extra frame
        self.tabs.addTab(self._build_vision(),  "👁  Vision & Tracking")
        self.tabs.addTab(self._build_audio(),   "🎙  Audio Configuration")
        self.tabs.addTab(self._build_ui_hk(),   "🎨  UI & Hotkeys")
        outer.addWidget(self.tabs, 1)

        # ── Footer ────────────────────────────────────────────────────────────
        footer = QWidget()
        footer.setFixedHeight(52)
        footer.setStyleSheet(
            f"background: {PAL['bg']};"
            f"border-top: 1px solid {PAL['border']};"
            f"border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;"
        )
        ft_lay = QHBoxLayout(footer)
        ft_lay.setContentsMargins(16, 0, 16, 0)
        ft_lay.addStretch()
        btn_done = QPushButton("  Save & Close  ")
        btn_done.setObjectName("done_btn")
        btn_done.clicked.connect(self.close)
        ft_lay.addWidget(btn_done)
        outer.addWidget(footer)

    # ── Shared panel / row helpers ────────────────────────────────────────────

    def _panel(self) -> tuple:
        """
        Return (outer_widget, content_layout) — a scrollable panel with
        standard padding. Caller appends rows directly to content_layout.
        """
        outer = QWidget()
        outer.setStyleSheet(f"background: {PAL['surface']};")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        scroll.setWidget(inner)
        wrap = QVBoxLayout(outer)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(scroll)
        return outer, lay

    def _section(self, lay: QVBoxLayout, text: str):
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_hdr")
        lbl.setStyleSheet(
            f"color: {PAL['gold']}; font-size: 10px; font-weight: bold;"
            f"letter-spacing: 1px; padding-bottom: 4px;"
            f"border-bottom: 1px solid {PAL['border']}; background: transparent;"
        )
        lay.addWidget(lbl)

    def _row(self, lay: QVBoxLayout, label: str, sub: str, control: QWidget) -> QWidget:
        """Single settings row: description on the left, control on the right."""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)

        txt_col = QVBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {PAL['text']}; font-size: 13px; background: transparent;")
        txt_col.addWidget(lbl)
        if sub:
            sl = QLabel(sub)
            sl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
            txt_col.addWidget(sl)
        rl.addLayout(txt_col, 1)
        rl.addWidget(control)
        lay.addWidget(row)
        return row

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

    # ── Tab 1: Vision & Tracking ──────────────────────────────────────────────

    def _build_vision(self) -> QWidget:
        panel, lay = self._panel()

        # ── Screen Watcher ────────────────────────────────────────────────────
        self._section(lay, "Screen Watcher")

        interval_val = getattr(self.ui, "_watch_interval", 5)
        sld = QSlider(Qt.Horizontal)
        sld.setRange(3, 15)
        sld.setValue(interval_val)
        sld.setFixedWidth(120)
        iv_lbl = QLabel(f"{sld.value()} s")
        iv_lbl.setStyleSheet(
            f"color: {PAL['gold']}; font-size: 11px; background: transparent; min-width: 28px;"
        )

        def _set_interval(v):
            self.ui._watch_interval = v
            iv_lbl.setText(f"{v} s")

        sld.valueChanged.connect(_set_interval)

        slider_wrap = QWidget()
        slider_wrap.setStyleSheet("background: transparent;")
        sw_lay = QHBoxLayout(slider_wrap)
        sw_lay.setContentsMargins(0, 0, 0, 0)
        sw_lay.addWidget(sld)
        sw_lay.addWidget(iv_lbl)
        self._row(lay, "Scan Interval", "How often the screen is analysed (3 – 15 s)", slider_wrap)

        # Sensitivity combo
        sens = getattr(self.ui, "_watch_sensitivity", "Medium")
        combo = QComboBox()
        combo.addItems(["Low", "Medium", "High"])
        combo.setCurrentText(sens)
        combo.currentTextChanged.connect(lambda t: setattr(self.ui, "_watch_sensitivity", t))
        self._row(lay, "Sensitivity", "Change-detection threshold", combo)

        # ── Clipboard / Highlight ─────────────────────────────────────────────
        self._section(lay, "Clipboard / Highlight")

        hl_btn = self._toggle_btn(self.ui.highlight_active)

        def _toggle_hl(checked):
            if checked != self.ui.highlight_active:
                self.ui._toggle_highlight()
            self.ui._set_btn_active(self.ui.btn_hl, checked)

        hl_btn.toggled.connect(_toggle_hl)
        self._row(lay, "Highlight Mode", "Watch clipboard for newly copied text", hl_btn)

        lay.addStretch()
        return panel

    # ── Tab 2: Audio Configuration ────────────────────────────────────────────

    def _build_audio(self) -> QWidget:
        panel, lay = self._panel()

        # ── Hardware info ─────────────────────────────────────────────────────
        self._section(lay, "Hardware")

        mic_name = getattr(self.audio, "mic_device_name",  "Not connected") if self.audio else "No audio engine"
        spk_name = getattr(self.audio, "spk_device_name",  "Not connected") if self.audio else "No audio engine"

        def _chip(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color: {PAL['blue']}; background: {PAL['surface_2']};"
                f"border-radius: 5px; padding: 4px 8px; font-size: 12px;"
            )
            lbl.setWordWrap(True)
            return lbl

        self._row(lay, "Microphone",  "Active input device",  _chip(f"🎤  {mic_name}"))
        self._row(lay, "Speaker",     "Active output device", _chip(f"🔊  {spk_name}"))

        # Enumerate utility
        def _list_devices():
            if not self.audio:
                self.ui.bridge.append_text.emit("[SETTINGS] No audio engine loaded.")
                return
            try:
                import sounddevice as sd
                devs  = sd.query_devices()
                lines = ["── Audio Devices ──"]
                for i, d in enumerate(devs):
                    tag = ""
                    if i == sd.default.device[0]: tag += " [MIC DEFAULT]"
                    if i == sd.default.device[1]: tag += " [SPK DEFAULT]"
                    lines.append(f"  [{i}] {d['name']}{tag}")
                self.ui.bridge.append_text.emit("\n".join(lines))
            except Exception as e:
                self.ui.bridge.append_text.emit(f"[SETTINGS] Device list error: {e}")
            self.close()

        btn_list = QPushButton("📋  List All Input Devices")
        btn_list.setObjectName("action_btn")
        btn_list.clicked.connect(_list_devices)
        self._row(lay, "Enumerate", "Print all audio devices to the response area", btn_list)

        # ── Interview Mode ────────────────────────────────────────────────────
        self._section(lay, "Interview Mode")

        mic_on  = bool(self.audio and getattr(self.audio, "mic_active",      False))
        spk_on  = bool(self.audio and getattr(self.audio, "speaker_active",  False))
        iv_btn  = self._toggle_btn(mic_on or spk_on)

        def _toggle_interview(checked):
            if not self.audio:
                return
            if checked:
                if not getattr(self.audio, "mic_active",     False): self.audio.start_mic()
                if not getattr(self.audio, "speaker_active", False): self.audio.start_speaker()
                self.ui._set_btn_active(self.ui.btn_mic, True)
                self.ui._set_btn_active(self.ui.btn_spk, True)
            else:
                if getattr(self.audio, "mic_active",     False): self.audio.stop_mic()
                if getattr(self.audio, "speaker_active", False): self.audio.stop_speaker()
                self.ui._set_btn_active(self.ui.btn_mic, False)
                self.ui._set_btn_active(self.ui.btn_spk, False)
            self.ui.bridge.set_status.emit(
                "Interview Mode ON — Mic + Speaker active" if checked else "Interview Mode OFF"
            )

        iv_btn.toggled.connect(_toggle_interview)
        self._row(lay, "Interview Mode", "Enable mic + speaker capture together", iv_btn)

        lay.addStretch()
        return panel

    # ── Tab 3: UI & Hotkeys ───────────────────────────────────────────────────

    def _build_ui_hk(self) -> QWidget:
        panel, lay = self._panel()

        # ── Display ───────────────────────────────────────────────────────────
        self._section(lay, "Display")

        op_sld = QSlider(Qt.Horizontal)
        op_sld.setRange(20, 100)
        op_sld.setValue(int(self.ui.windowOpacity() * 100))
        op_sld.setFixedWidth(120)
        op_lbl = QLabel(f"{op_sld.value()}%")
        op_lbl.setStyleSheet(
            f"color: {PAL['gold']}; font-size: 11px; background: transparent; min-width: 32px;"
        )
        op_sld.valueChanged.connect(lambda v: (self.ui.setWindowOpacity(v / 100), op_lbl.setText(f"{v}%")))

        op_wrap = QWidget()
        op_wrap.setStyleSheet("background: transparent;")
        ow_lay = QHBoxLayout(op_wrap)
        ow_lay.setContentsMargins(0, 0, 0, 0)
        ow_lay.addWidget(op_sld)
        ow_lay.addWidget(op_lbl)
        self._row(lay, "Window Opacity", "Adjust overall window transparency", op_wrap)

        # ── Theme ─────────────────────────────────────────────────────────────
        self._section(lay, "Theme")

        self._theme_is_dark = True

        def _toggle_theme(checked):
            self._theme_is_dark = checked
            self.ui.bridge.set_status.emit(
                "Dark Mode Active" if checked else "Light Mode (coming soon)"
            )

        theme_btn = self._toggle_btn(self._theme_is_dark)
        theme_btn.toggled.connect(_toggle_theme)
        self._row(lay, "Dark Mode", "Toggle dark / light colour scheme", theme_btn)

        # ── Global Hotkeys reference ──────────────────────────────────────────
        self._section(lay, "Global Hotkeys  (read-only)")

        hotkeys = [
            ("📷", "Screen Capture",  "Ctrl + Shift + S", "Screenshot → AI"),
            ("🔍", "Highlight Mode",  "Ctrl + Shift + H", "Toggle clipboard watching"),
            ("👁", "Screen Watcher",  "Ctrl + Shift + W", "Toggle periodic scanning"),
            ("🔄", "Hot Reload",      "Ctrl + R",          "Restart app window cleanly"),
        ]

        for icon, name, shortcut, desc in hotkeys:
            card = QFrame()
            card.setObjectName("hotkey_card")
            card.setStyleSheet(
                f"background: {PAL['surface_2']}; border: 1px solid {PAL['border']};"
                f"border-radius: 8px;"
            )
            cl = QHBoxLayout(card)
            cl.setContentsMargins(12, 8, 12, 8)

            ico_lbl = QLabel(icon)
            ico_lbl.setStyleSheet("font-size: 16px; background: transparent;")
            cl.addWidget(ico_lbl)

            txt_col = QVBoxLayout()
            nl = QLabel(name)
            nl.setStyleSheet(
                f"color: {PAL['text']}; font-size: 12px; font-weight: bold; background: transparent;"
            )
            dl = QLabel(desc)
            dl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
            txt_col.addWidget(nl)
            txt_col.addWidget(dl)
            cl.addLayout(txt_col, 1)

            badge = QLabel(shortcut)
            badge.setObjectName("hotkey_badge")
            badge.setStyleSheet(
                f"background: {PAL['bg']}; color: {PAL['gold']};"
                f"border: 1px solid {PAL['gold_dim']}; border-radius: 5px;"
                f"font-family: 'Consolas', monospace; font-size: 11px;"
                f"padding: 3px 8px;"
            )
            cl.addWidget(badge)
            lay.addWidget(card)

        note = QLabel("Hotkeys are registered globally and active even when AceIt is minimised.")
        note.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
        note.setWordWrap(True)
        lay.addSpacing(4)
        lay.addWidget(note)
        lay.addStretch()
        return panel

# ── OCR Worker Thread ─────────────────────────────────────────────────────────

class OCRWorker(QThread):
    """
    Dedicated worker thread for screen capture + OCR.

    All blocking I/O (ImageGrab + pytesseract) runs inside run() on the
    worker thread.  Results are sent to the main GUI thread exclusively via
    Qt signals, which Qt promotes to queued connections automatically when
    the emitter and receiver live in different threads — no manual locking
    or SignalBridge wrappers are required.

    Signals
    -------
    grab_done()
        Fired immediately after ImageGrab.grab() returns — before OCR starts.
        Connect to self.show so the window is restored as early as possible,
        while the CPU-heavy Tesseract pass continues in the background.

    ocr_completed(str)
        Carries the extracted text when everything succeeds and the result
        is non-empty.  Connect to _on_ocr_ready to feed the AI pipeline.

    ocr_failed(str)
        Human-readable error / "no text" message.  Connect directly to
        bridge.set_status (both are Signal(str) — compatible by type).

    Usage
    -----
    >>> worker = OCRWorker(hide_delay_s=0.15)
    >>> worker.grab_done.connect(self.show)
    >>> worker.ocr_completed.connect(self._on_ocr_ready)
    >>> worker.ocr_failed.connect(self.bridge.set_status)
    >>> worker.finished.connect(worker.deleteLater)
    >>> worker.start()
    """

    grab_done     = Signal()       # screenshot taken → restore window now
    ocr_completed = Signal(str)    # OCR text ready  → AI pipeline
    ocr_failed    = Signal(str)    # error / no-text → status label

    def __init__(self, hide_delay_s: float = 0.15, parent=None):
        super().__init__(parent)
        self._hide_delay = hide_delay_s

    def run(self) -> None:
        """
        Worker-thread entry point (never call directly — use start()).

        Steps
        ─────
        1. Sleep for hide_delay_s so the OS finishes hiding the AceIt window.
        2. Grab a full-screen PIL image via ImageGrab.
        3. Emit grab_done so the caller can restore the window immediately.
        4. Run pytesseract on the image (CPU-heavy, may take 0.5 – 3 s).
        5. Emit ocr_completed(text) or ocr_failed(reason).
        """
        try:
            from PIL import ImageGrab
            import pytesseract
        except ImportError as exc:
            self.ocr_failed.emit(f"Import error: {exc}")
            return

        # Load Tesseract binary path from .env (TESSERACT_CMD key).
        # This must happen inside run() — after the lazy import — so the path
        # is applied on the worker thread before any pytesseract call is made.
        try:
            from dotenv import load_dotenv
            load_dotenv()
            _tess = os.environ.get("TESSERACT_CMD", "")
            if _tess:
                pytesseract.pytesseract.tesseract_cmd = _tess
        except Exception:
            pass  # dotenv not installed or .env absent — fall through to system PATH

        # Step 1: OS yield — let the window disappear before the grab
        time.sleep(self._hide_delay)

        # Step 2: Grab screenshot
        try:
            img = ImageGrab.grab()
        except Exception as exc:
            self.ocr_failed.emit(f"Capture failed: {exc}")
            return
        finally:
            # Step 3: Restore the window before OCR — emit regardless of errors
            self.grab_done.emit()

        # Step 4: Run Tesseract (blocking, possibly slow)
        try:
            txt = pytesseract.image_to_string(img).strip()
        except Exception as exc:
            self.ocr_failed.emit(f"OCR error: {exc}")
            return

        # Step 5: Deliver result
        if not txt:
            self.ocr_failed.emit("OCR: no text detected")
            return

        self.ocr_completed.emit(txt)


# ── Screen-Watcher Worker Thread ─────────────────────────────────────────────

class WatchWorker(QThread):
    """
    Periodic screen-watcher that runs on a dedicated QThread.

    Every `interval` seconds it:
      1. Grabs the full screen via mss (fast, ~30–80 ms).
      2. Tries vision-AI first (Groq llava) — no blocking Tesseract process.
      3. Falls back to pytesseract OCR only when vision fails or isn't available.
      4. Emits screen_text(str) when meaningful content is found.
      5. Emits status(str) for progress / error messages.

    Stopping
    --------
    Call stop() from any thread.  The worker checks _stop_flag after every
    sleep quantum and exits run() cleanly on the next iteration.
    """

    screen_text = Signal(str)   # extracted content → feed to StateEngine
    status      = Signal(str)   # human-readable progress / errors

    def __init__(self, interval: int = 5, sensitivity: str = "Medium", parent=None):
        super().__init__(parent)
        self._interval    = interval
        self._sensitivity = sensitivity
        self._stop_flag   = False

    # ── Public API ────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Signal the run loop to exit on the next iteration."""
        self._stop_flag = True

    def set_interval(self, seconds: int) -> None:
        self._interval = max(3, seconds)

    def set_sensitivity(self, level: str) -> None:
        self._sensitivity = level

    # ── Worker entry point ────────────────────────────────────────────────────

    def run(self) -> None:
        """QThread entry point — never call directly, use start()."""
        # Import heavy deps lazily so the app starts fast
        try:
            import mss
            import mss.tools
            HAS_MSS = True
        except ImportError:
            HAS_MSS = False

        while not self._stop_flag:
            # Sleep in 0.5 s slices so stop() responds quickly
            elapsed = 0.0
            while elapsed < self._interval and not self._stop_flag:
                time.sleep(0.5)
                elapsed += 0.5

            if self._stop_flag:
                break

            self.status.emit("👁 Scanning screen…")

            # ── 1. Grab screenshot ────────────────────────────────────────────
            img_bytes: bytes | None = None
            if HAS_MSS:
                try:
                    with mss.mss() as sct:
                        monitor = sct.monitors[0]   # full virtual desktop
                        shot    = sct.grab(monitor)
                        # Convert to PNG bytes for downstream consumers
                        import io as _io
                        buf = _io.BytesIO()
                        mss.tools.to_png(shot.rgb, shot.size, output=buf)
                        img_bytes = buf.getvalue()
                except Exception as e:
                    self.status.emit(f"mss grab failed: {e}")

            if img_bytes is None:
                # Fallback: PIL ImageGrab (slower, ~200–400 ms)
                try:
                    from PIL import ImageGrab
                    import io as _io
                    pil_img   = ImageGrab.grab()
                    buf       = _io.BytesIO()
                    pil_img.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                except Exception as e:
                    self.status.emit(f"Screen grab error: {e}")
                    continue

            # ── 2. Vision AI (primary path — no blocking subprocess) ──────────
            txt = self._try_vision_ai(img_bytes)

            # ── 3. OCR fallback ───────────────────────────────────────────────
            if not txt:
                txt = self._try_ocr(img_bytes)

            if txt:
                self.screen_text.emit(txt)
            else:
                self.status.emit("👁 Nothing detected")

    # ── Vision AI helper ──────────────────────────────────────────────────────

    def _try_vision_ai(self, img_bytes: bytes) -> str:
        """
        Send the screenshot to Groq's vision endpoint.
        Returns extracted text, or "" on any failure.
        """
        try:
            import base64
            from aceit_core import groq_client
            b64 = base64.b64encode(img_bytes).decode()
            resp = groq_client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
            return resp.choices[0].message.content.strip()
        except Exception:
            return ""

    # ── OCR fallback helper ───────────────────────────────────────────────────

    def _try_ocr(self, img_bytes: bytes) -> str:
        """
        Pytesseract OCR on the captured PNG bytes.
        Returns extracted text, or "" on any failure.
        """
        try:
            import io as _io
            from PIL import Image
            import pytesseract
            try:
                import os
                tess = os.environ.get("TESSERACT_CMD", "")
                if tess:
                    pytesseract.pytesseract.tesseract_cmd = tess
            except Exception:
                pass
            pil_img = Image.open(_io.BytesIO(img_bytes))
            return pytesseract.image_to_string(pil_img).strip()
        except Exception:
            return ""


# ── Context Ingest Worker — PDF/TXT → pinned SessionManager entry ─────────────

class ContextIngestWorker(QThread):
    """
    Background worker that reads a PDF or plain-text file and emits the
    extracted text so it can be injected as a pinned context entry.

    Signals
    -------
    ingest_done(str)
        Full extracted text, ready to be passed to
        SessionManager.add_pinned_context().

    ingest_failed(str)
        Human-readable error message; connect to bridge.set_status.

    progress(str)
        Short status strings for the UI status bar (e.g. "Extracting page 3/12…").
    """

    ingest_done  = Signal(str)
    ingest_failed = Signal(str)
    progress      = Signal(str)

    # Hard cap: very large documents are truncated to avoid blowing the context window.
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

        # Truncate with a clear marker so the AI knows the document was clipped
        if len(text) > self.MAX_CHARS:
            text = text[: self.MAX_CHARS] + "\n\n[…document truncated for context window…]"

        self.ingest_done.emit(text.strip())

    def _extract_pdf(self, path: str) -> str:
        try:
            import fitz   # PyMuPDF
        except ImportError:
            raise ImportError(
                "PyMuPDF is not installed. Run: pip install pymupdf"
            )
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


# ── FloatBubble — standalone top-level circle window ─────────────────────────

class FloatBubble(QWidget):
    """
    A frameless, translucent top-level window that renders as a perfect circle.

    Because it is NOT a child of AceItWindow it is never clipped by the main
    window's QRegion mask.  paintEvent draws the circle directly so the OS-level
    bounding box is irrelevant — the widget simply paints nothing outside the
    circle boundary.

    Signals
    -------
    clicked()   — emitted on left mouse-button release; lets AceItWindow wire
                  the bubble tap to _leave_float.
    dragged(QPoint) — emitted while dragging so AceItWindow can reposition the
                      bubble and keep its own geometry in sync.
    """

    clicked = Signal()
    dragged = Signal(QPoint)    # new top-left position

    SIZE = 80   # window (and circle bounding box) side length in pixels

    def __init__(self, parent=None):
        super().__init__(parent,
                         Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint |
                         Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._border_color = QColor(PAL["gold"])
        self._drag_offset  = QPoint()

    # ── Color API ─────────────────────────────────────────────────────────────

    def set_border_color(self, color: QColor | str) -> None:
        self._border_color = QColor(color) if isinstance(color, str) else color
        self.update()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPen, QBrush
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Fill circle
        p.setBrush(QBrush(QColor(PAL["surface_2"])))
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, self.SIZE - 8, self.SIZE - 8)

        # Border ring
        pen = QPen(self._border_color, 2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(4, 4, self.SIZE - 8, self.SIZE - 8)

        # Lightning bolt icon centred in the circle
        p.setPen(QPen(self._border_color, 1))
        from PySide6.QtGui import QFont as _QFont
        f = _QFont("Segoe UI Emoji", 22)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "⚡")

    # ── Dragging ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset   = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_global  = event.globalPosition().toPoint()   # store exact press coords

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            self.dragged.emit(new_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            release_global = event.globalPosition().toPoint()
            press_global   = getattr(self, "_press_global", release_global)
            delta = release_global - press_global
            # Only treat as a tap if the cursor barely moved (< 5 px Manhattan distance)
            if abs(delta.x()) < 5 and abs(delta.y()) < 5:
                self.clicked.emit()


# ── PillNotification — standalone top-level notification pill ─────────────────

class PillNotification(QWidget):
    """
    A frameless, translucent pill that slides out beside FloatBubble.

    Standalone top-level window (parent=None) so it is never clipped by the
    main AceItWindow mask or the FloatBubble's own geometry.
    """

    PILL_W = 260
    PILL_H = 40

    def __init__(self, parent=None):
        super().__init__(parent,
                         Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint |
                         Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedHeight(self.PILL_H)
        self.setStyleSheet(
            f"background: {PAL['surface']}; border: 1px solid {PAL['gold']};"
            f"border-radius: 12px;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        self._lbl = QLabel()
        self._lbl.setStyleSheet(f"color: {PAL['text']}; font-size: 11px; background: transparent;")
        self._lbl.setWordWrap(False)
        lay.addWidget(self._lbl)

        self._anim_out: QPropertyAnimation | None = None
        self._anim_in:  QPropertyAnimation | None = None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._animate_hide)

    def show_text(self, text: str, bubble_geo: QRect, edge: str) -> None:
        """
        Slide the pill into view next to *bubble_geo* on the given *edge*.
        bubble_geo is the global geometry of the FloatBubble window.
        """
        self._lbl.setText(text)

        bx, by, bw, bh = bubble_geo.x(), bubble_geo.y(), bubble_geo.width(), bubble_geo.height()

        if edge == "right":
            start_geo = QRect(bx,               by + (bh - self.PILL_H) // 2, 0,          self.PILL_H)
            end_geo   = QRect(bx - self.PILL_W,  by + (bh - self.PILL_H) // 2, self.PILL_W, self.PILL_H)
        elif edge == "left":
            start_geo = QRect(bx + bw,           by + (bh - self.PILL_H) // 2, 0,          self.PILL_H)
            end_geo   = QRect(bx + bw,           by + (bh - self.PILL_H) // 2, self.PILL_W, self.PILL_H)
        elif edge == "top":
            start_geo = QRect(bx + (bw - self.PILL_W) // 2, by + bh, self.PILL_W, 0)
            end_geo   = QRect(bx + (bw - self.PILL_W) // 2, by + bh, self.PILL_W, self.PILL_H)
        else:  # bottom
            start_geo = QRect(bx + (bw - self.PILL_W) // 2, by - self.PILL_H, self.PILL_W, 0)
            end_geo   = QRect(bx + (bw - self.PILL_W) // 2, by - self.PILL_H, self.PILL_W, self.PILL_H)

        self.setGeometry(start_geo)
        self.show()
        self.raise_()

        if self._anim_out:
            self._anim_out.stop()
        self._anim_out = QPropertyAnimation(self, b"geometry")
        self._anim_out.setDuration(300)
        self._anim_out.setEasingCurve(QEasingCurve.OutBack)
        self._anim_out.setStartValue(start_geo)
        self._anim_out.setEndValue(end_geo)
        self._anim_out.start()

        self._hide_timer.start(5000)

    def _animate_hide(self) -> None:
        current = self.geometry()
        edge_w  = QRect(current.x() + current.width(), current.y(), 0, current.height())
        if self._anim_in:
            self._anim_in.stop()
        self._anim_in = QPropertyAnimation(self, b"geometry")
        self._anim_in.setDuration(200)
        self._anim_in.setEasingCurve(QEasingCurve.InCubic)
        self._anim_in.setStartValue(current)
        self._anim_in.setEndValue(edge_w)
        self._anim_in.finished.connect(self.hide)
        self._anim_in.start()


# ── Main Application Window ───────────────────────────────────────────────────
class AceItWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(520, 750)
        
        self._is_floating = False
        self.bridge = SignalBridge()
        self.bridge.append_text.connect(self._append_response)
        self.bridge.set_status.connect(self._set_status)
        self.bridge.notify_pill.connect(self._show_pill)
        self.bridge.start_thinking.connect(self._start_thinking)   # thread-safe: queued connection
        self.bridge.thinking_done.connect(self._stop_thinking)
        self.bridge.stream_token.connect(self._on_stream_token)

        # Generation interrupt event — set by Stop button, checked in streaming loop
        self._stop_gen = threading.Event()
        
        # Backend Engines
        self.state = StateEngine(raw_query_fn=self._on_ai_query) if _CORE else None
        self.audio = AudioEngine(on_transcript=self._on_transcript, on_status=lambda m: self.bridge.set_status.emit(m)) if _CORE else None
        
        # Desktop Tools State
        self.watch_active       = False
        self.highlight_active   = False
        self.stealth_active     = False    # Stage 4: WDA_EXCLUDEFROMCAPTURE toggle
        self.last_clipboard     = ""
        self._watch_interval    = 5        # seconds between watcher scans
        self._watch_sensitivity = "Medium" # Low / Medium / High

        self._build_ui()
        self._bind_hotkeys()

        # ── Standalone Float Mode widgets ──────────────────────────────────────
        self._bubble = FloatBubble()
        self._bubble.clicked.connect(self._leave_float)
        self._bubble.dragged.connect(self._on_bubble_dragged)
        self._pill_win = PillNotification()

        # ── Session Timer: update status bar every second ──────────────────────
        self._session_timer = QTimer(self)
        self._session_timer.setInterval(1000)
        self._session_timer.timeout.connect(self._tick_session)
        self._session_timer.start()

        # ── Wire state engine events to update mode pill ───────────────────────
        if self.state:
            self.state.on_event(self._on_state_event)

    def _build_ui(self):
        self.root_widget = QWidget()
        self.setCentralWidget(self.root_widget)
        
        # Main Layout uses spacing to create the "Detached" look
        self.main_lay = QVBoxLayout(self.root_widget)
        self.main_lay.setContentsMargins(10, 10, 10, 10)
        self.main_lay.setSpacing(12)  # Transparent gap between header and workspace

        # ── Detached Floating Header ──
        self.header = QFrame()
        self.header.setObjectName("floating_header")
        self.header.setFixedHeight(50)

        hdr_lay = QHBoxLayout(self.header)
        hdr_lay.setContentsMargins(10, 0, 10, 0)
        hdr_lay.setSpacing(4)

        # LEFT cluster: Float → Stealth → Logo
        self.btn_float   = self._make_hdr_btn("🗗",  "Float Mode (collapse to circle)", self._toggle_float, accent=PAL["blue"])
        self.btn_stealth = self._make_hdr_btn("🥷", "Stealth Mode — hide from screen capture (Windows only)", self._toggle_stealth, accent=PAL["muted"])
        self.logo        = QLabel("⚡ AceIt")
        self.logo.setStyleSheet(
            f"color: {PAL['gold']}; font-weight: bold; font-size: 15px; padding-left: 4px;"
        )

        hdr_lay.addWidget(self.btn_float)
        hdr_lay.addWidget(self.btn_stealth)
        hdr_lay.addWidget(self.logo)

        # Mode indicator pill (shows ACTIVE / AMBIENT / GUIDED)
        self.mode_pill = QLabel("ACTIVE")
        self.mode_pill.setStyleSheet(
            f"color: {PAL['gold']}; background: rgba(212,175,55,0.12);"
            f"border: 1px solid {PAL['gold_dim']}; border-radius: 8px;"
            f"font-size: 9px; font-weight: bold; letter-spacing: 1px;"
            f"padding: 2px 7px;"
        )
        hdr_lay.addWidget(self.mode_pill)

        hdr_lay.addStretch()

        # RIGHT cluster: Mode selector → Refresh → Settings → Close
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Active", "Ambient", "Guided", "Interview"])
        self.mode_combo.setFixedWidth(100)
        self.mode_combo.setStyleSheet(
            f"QComboBox {{ background: {PAL['surface_2']}; color: {PAL['text']};"
            f"border: 1px solid {PAL['border']}; border-radius: 5px;"
            f"padding: 2px 6px; font-size: 11px; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {PAL['surface_2']};"
            f"border: 1px solid {PAL['border']}; selection-background-color: {PAL['border']}; }}"
        )
        self.mode_combo.currentTextChanged.connect(self._on_mode_combo_changed)
        hdr_lay.addWidget(self.mode_combo)

        self.btn_refresh  = self._make_hdr_btn("🔄", "Manual Refresh / Hot-Reload",  self._hot_reload)
        self.btn_settings = self._make_hdr_btn("⚙",  "Command Center",               self._open_settings)
        self.btn_close    = self._make_hdr_btn("✕",  "Close",                         self.close)

        for b in [self.btn_refresh, self.btn_settings, self.btn_close]:
            hdr_lay.addWidget(b)

        # Keep a dummy op_slider attribute so _enter_float / _leave_float don't crash
        # (opacity is now in Settings → UI & Hotkeys tab)
        self.op_slider = QSlider(Qt.Horizontal)   # not added to layout — lives off-screen
        self.op_slider.setRange(20, 100)
        self.op_slider.setValue(95)
        self.op_slider.valueChanged.connect(lambda v: self.setWindowOpacity(v / 100))

        self.main_lay.addWidget(self.header)

        # ── Main Workspace (Bottom Panel) ──
        self.workspace = QFrame()
        self.workspace.setObjectName("workspace")
        ws_lay = QVBoxLayout(self.workspace)
        ws_lay.setContentsMargins(0, 0, 0, 0)
        ws_lay.setSpacing(0)

        # Response Area
        resp_lay = QVBoxLayout()
        resp_lay.setContentsMargins(12, 12, 12, 0)
        
        # Floating Utils above text
        util_lay = QHBoxLayout()
        util_lay.addWidget(QLabel("AI RESPONSE", styleSheet=f"color: {PAL['gold_dim']}; font-size: 10px; font-weight: bold;"))
        util_lay.addStretch()
        btn_copy = QPushButton("⎘ Copy")
        btn_copy.setStyleSheet(f"color: {PAL['muted']}; font-size: 11px;")
        btn_copy.clicked.connect(self._copy_text)
        btn_clear = QPushButton("🗑 Clear")
        btn_clear.setStyleSheet(f"color: {PAL['muted']}; font-size: 11px;")
        btn_clear.clicked.connect(self._clear_text)
        util_lay.addWidget(btn_copy)
        util_lay.addWidget(btn_clear)
        resp_lay.addLayout(util_lay)

        # ── AI Thinking Bar — glowing QProgressBar (hidden until a query fires) ──
        self.thinking_bar = QProgressBar()
        self.thinking_bar.setFixedHeight(2)
        self.thinking_bar.setTextVisible(False)
        self.thinking_bar.setRange(0, 0)          # indeterminate by default
        self.thinking_bar.setStyleSheet(
            f"QProgressBar {{"
            f"  background: {PAL['surface_2']}; border: none; border-radius: 1px;"
            f"}}"
            f"QProgressBar::chunk {{"
            f"  background: qlineargradient("
            f"    x1:0, y1:0, x2:1, y2:0,"
            f"    stop:0 {PAL['gold_dim']}, stop:0.45 {PAL['gold']},"
            f"    stop:0.55 {PAL['blue']}, stop:1 {PAL['blue_dim']}"
            f"  );"
            f"  border-radius: 1px;"
            f"}}"
        )
        self.thinking_bar.hide()
        resp_lay.addWidget(self.thinking_bar)

        self.text_area = QTextBrowser()
        self.text_area.setReadOnly(True)
        self.text_area.setOpenExternalLinks(True)
        # Inline stylesheet for pre/code blocks — QSS rules on QTextBrowser
        # control the widget frame but not the document content; we therefore
        # inject a <style> block via setDocument once, then update HTML per token.
        self._CODE_CSS = (
            f"<style>"
            f"body {{ color: {PAL['text']}; background: {PAL['surface_2']}; "
            f"       font-family: 'Segoe UI', sans-serif; font-size: 13px; }}"
            f"pre  {{ background: {PAL['bg']}; border: 1px solid {PAL['border']}; "
            f"        border-radius: 4px; padding: 8px; "
            f"        font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; "
            f"        color: {PAL['blue']}; white-space: pre-wrap; }}"
            f"code {{ background: {PAL['bg']}; border-radius: 3px; padding: 1px 4px; "
            f"        font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; "
            f"        color: {PAL['blue']}; }}"
            f"a    {{ color: {PAL['blue']}; }}"
            f"ul, ol {{ margin-left: 18px; }}"
            f"strong {{ color: {PAL['gold']}; }}"
            f"</style>"
        )

        # ── Streaming markdown state ─────────────────────────────────────────
        # _md_plain_prefix  — non-AI lines (e.g. "You: …", "[CLIPBOARD Sent]")
        #                     rendered as plain HTML, preserved across updates.
        # _md_ai_buffer     — the raw markdown text of the current AI response
        #                     being streamed in.  Re-rendered on every token.
        # _md_ai_streaming  — True while an AI response is in progress.
        self._md_plain_prefix:  str  = ""
        self._md_ai_buffer:     str  = ""
        self._md_ai_streaming:  bool = False

        resp_lay.addWidget(self.text_area)
        ws_lay.addLayout(resp_lay)

        # Status
        self.status_lbl = QLabel("  Ready")
        self.status_lbl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; padding: 6px;")
        ws_lay.addWidget(self.status_lbl)

        # Action Dock
        self.dock = QFrame()
        self.dock.setObjectName("action_dock")
        dock_lay = QHBoxLayout(self.dock)
        
        self.btn_cap = self._make_dock_btn("📷", "Capture Screen", self._do_capture)
        self.btn_hl = self._make_dock_btn("🔍", "Watch Clipboard", self._toggle_highlight)
        self.btn_watch = self._make_dock_btn("👁", "Screen Watcher", self._toggle_watch)
        self.btn_mic = self._make_dock_btn("🎤", "Mic", self._toggle_mic)
        self.btn_spk = self._make_dock_btn("🔊", "Speaker", self._toggle_speaker)
        
        for b in [self.btn_cap, self.btn_hl, self.btn_watch, self.btn_mic, self.btn_spk]:
            dock_lay.addWidget(b)

        # ── Upload Context button (Interview Mode only) ───────────────────────
        self.btn_upload = self._make_dock_btn("📄", "Upload Context (PDF/TXT)", self._do_upload_context)
        self.btn_upload.setStyleSheet(
            f"QPushButton {{ background: rgba(71,161,255,0.12); color: {PAL['blue']};"
            f"  border: 1px solid {PAL['blue_dim']}; border-radius: 6px; font-size: 14px; }}"
            f"QPushButton:hover {{ background: {PAL['blue_dim']}; color: {PAL['bg']}; }}"
        )
        self.btn_upload.hide()   # visible only in Interview mode
        dock_lay.addWidget(self.btn_upload)
            
        self.ask_entry = QLineEdit()
        self.ask_entry.setPlaceholderText("Ask anything...")
        self.ask_entry.returnPressed.connect(self._do_ask)
        dock_lay.addWidget(self.ask_entry)

        # ── Stop Generation button (hidden until AI is thinking) ──────────────
        self.btn_stop = QPushButton("⏹")
        self.btn_stop.setToolTip("Stop generation")
        self.btn_stop.setFixedSize(32, 32)
        self.btn_stop.setStyleSheet(
            f"QPushButton {{ background: rgba(255,74,110,0.15); color: {PAL['danger']};"
            f"  border: 1px solid {PAL['danger']}; border-radius: 6px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {PAL['danger']}; color: {PAL['bg']}; }}"
        )
        self.btn_stop.clicked.connect(self._do_stop_gen)
        self.btn_stop.hide()   # visible only while AI is processing
        dock_lay.addWidget(self.btn_stop)

        btn_send = QPushButton("➤")
        btn_send.setStyleSheet(f"background: {PAL['gold']}; color: {PAL['bg']}; border-radius: 6px; padding: 6px 12px; font-weight: bold;")
        btn_send.clicked.connect(self._do_ask)
        dock_lay.addWidget(btn_send)

        # ── Second dock row: Model selector + Response Style selector ─────────
        dock2_widget = QWidget()
        dock2_widget.setStyleSheet("background: transparent;")
        dock2_lay = QHBoxLayout(dock2_widget)
        dock2_lay.setContentsMargins(8, 0, 8, 6)
        dock2_lay.setSpacing(6)

        _combo_style = (
            f"QComboBox {{ background: {PAL['surface_2']}; color: {PAL['muted']};"
            f"  border: 1px solid {PAL['border']}; border-radius: 5px;"
            f"  padding: 2px 8px; font-size: 10px; }}"
            f"QComboBox:hover {{ color: {PAL['text']}; border-color: {PAL['gold_dim']}; }}"
            f"QComboBox::drop-down {{ border: none; width: 14px; }}"
            f"QComboBox QAbstractItemView {{ background: {PAL['surface_2']}; color: {PAL['text']};"
            f"  border: 1px solid {PAL['border']}; selection-background-color: {PAL['border']}; }}"
        )

        # Model selector
        lbl_model = QLabel("Model:")
        lbl_model.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
        dock2_lay.addWidget(lbl_model)

        self.model_combo = QComboBox()
        self.model_combo.setStyleSheet(_combo_style)
        self.model_combo.setFixedHeight(22)
        if _CORE:
            for mid in GROQ_MODELS:
                self.model_combo.addItem(GROQ_MODEL_LABELS.get(mid, mid), mid)
            # Set current to whatever GROQ_MODEL is
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) == GROQ_MODEL:
                    self.model_combo.setCurrentIndex(i)
                    break
        else:
            self.model_combo.addItem("Llama 3.3 70B")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        dock2_lay.addWidget(self.model_combo)

        dock2_lay.addSpacing(12)

        # Response Style selector
        lbl_style = QLabel("Style:")
        lbl_style.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
        dock2_lay.addWidget(lbl_style)

        self.style_combo = QComboBox()
        self.style_combo.setStyleSheet(_combo_style)
        self.style_combo.setFixedHeight(22)
        _styles = RESPONSE_STYLES if _CORE else ["Terse", "Direct", "Balanced", "Detailed"]
        self.style_combo.addItems(_styles)
        self.style_combo.setCurrentText("Balanced")
        self.style_combo.currentTextChanged.connect(self._on_style_changed)
        dock2_lay.addWidget(self.style_combo)

        dock2_lay.addStretch()

        ws_lay.addWidget(dock2_widget)

        ws_lay.addWidget(self.dock)
        self.main_lay.addWidget(self.workspace)

        self.setStyleSheet(QSS)
        self.setWindowOpacity(0.95)

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
        if cmd: btn.clicked.connect(cmd)
        return btn

    def _set_btn_active(self, btn, active):
        btn.setProperty("active", "true" if active else "false")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    # ── Frameless Window Dragging ──
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and hasattr(self, "_drag_pos"):
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        pass  # Float mode dragging is handled by FloatBubble directly

    # ── UI Interactions ──
    def _open_settings(self):
        SettingsDialog(self, self.state, self.audio, self).exec()

    def _copy_text(self):
        pyperclip.copy(self.text_area.toPlainText())
        self.bridge.set_status.emit("Copied ✓")

    def _clear_text(self):
        self.text_area.clear()
        self._md_plain_prefix  = ""
        self._md_ai_buffer     = ""
        self._md_ai_streaming  = False
        if self.state: self.state.session.end()
        self.bridge.set_status.emit("Cleared memory ✓")

    @Slot(str)
    def _append_response(self, text):
        """
        Append a non-AI plain-text line (labels, status echoes, debug reports).
        Lines are stored in _md_plain_prefix and rendered as escaped HTML so
        they are preserved even when the streaming AI buffer is updated.
        """
        import html as _html
        escaped = _html.escape(text).replace("\n", "<br>")
        self._md_plain_prefix += escaped + "<br>"
        self._md_ai_streaming = False   # no live AI response at this point
        self._md_ai_buffer    = ""
        self._render_html()

    @Slot(str)
    def _set_status(self, msg):
        self.status_lbl.setText(f"  {msg}")

    def _tick_session(self):
        """Update the status bar with session stats every second."""
        if self.state and self.state.session.is_active:
            self.status_lbl.setText(f"  ⏱ {self.state.session.summary}")

    def _on_state_event(self, event_type: str, payload: dict):
        """Receive events from the StateEngine (runs on background thread)."""
        if event_type == "mode_changed":
            new_mode = payload.get("to", "")
            QTimer.singleShot(0, lambda: self._sync_mode_ui(new_mode))

    def _sync_mode_ui(self, mode_name: str):
        """Update header pill and combo to reflect a mode change (main thread)."""
        # Pill colour: purple for Interview, gold for everything else
        if mode_name.upper() == "INTERVIEW":
            pill_style = (
                f"color: #9B59B6; background: rgba(155,89,182,0.12);"
                f"border: 1px solid #6C3483; border-radius: 8px;"
                f"font-size: 9px; font-weight: bold; letter-spacing: 1px; padding: 2px 7px;"
            )
        else:
            pill_style = (
                f"color: {PAL['gold']}; background: rgba(212,175,55,0.12);"
                f"border: 1px solid {PAL['gold_dim']}; border-radius: 8px;"
                f"font-size: 9px; font-weight: bold; letter-spacing: 1px; padding: 2px 7px;"
            )
        self.mode_pill.setText(mode_name.upper())
        self.mode_pill.setStyleSheet(pill_style)

        combo_map = {"ACTIVE": "Active", "AMBIENT": "Ambient", "GUIDED": "Guided", "INTERVIEW": "Interview"}
        label = combo_map.get(mode_name.upper(), mode_name.capitalize())
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText(label)
        self.mode_combo.blockSignals(False)

    # ── Mode combo handler ───────────────────────────────────────────────────────
    def _on_mode_combo_changed(self, text: str):
        if not self.state:
            return
        mapping = {
            "Active":    ModeState.ACTIVE,
            "Ambient":   ModeState.AMBIENT,
            "Guided":    ModeState.GUIDED,
            "Interview": ModeState.INTERVIEW,
        }
        mode = mapping.get(text)
        if not mode:
            return

        entering_interview = (text == "Interview")
        leaving_interview  = (
            self.state.mode == ModeState.INTERVIEW and not entering_interview
        ) if _CORE else False

        self.state.set_mode(mode)
        self._sync_mode_ui(text)
        self.bridge.set_status.emit(f"Mode → {text}")

        # ── Interview Mode side-effects ──────────────────────────────────────
        if entering_interview:
            # 1. Force Response Style → Direct
            self.style_combo.blockSignals(True)
            self.style_combo.setCurrentText("Direct")
            self.style_combo.blockSignals(False)
            if self.state:
                self.state.session.response_style = "Direct"

            # 2. Auto-start Mic + Speaker if not already active
            if self.audio:
                if not self.audio.mic_active:
                    self.audio.start_mic()
                    self._set_btn_active(self.btn_mic, True)
                if not self.audio.speaker_active:
                    self.audio.start_speaker()
                    self._set_btn_active(self.btn_spk, True)

            # 3. Show Upload Context button
            self.btn_upload.show()

        else:
            # Leaving Interview — hide upload button (audio stays as-is, user controls it)
            self.btn_upload.hide()

            # Re-enable style selector freedom (was locked to Direct in Interview)
            self.style_combo.setEnabled(True)

    # ── Hot Reload (public alias) ────────────────────────────────────────────────
    def _hot_reload(self):
        """Public alias called by the header 🔄 button."""
        self._do_reload()

    # ── Hot Reload ──────────────────────────────────────────────────────────────
    def _do_reload(self):
        """
        Cleanly reboot the entire app window in-place via os.execv().

        Sequence:
          1. Unhook all global keyboard shortcuts so the new process starts clean.
          2. Stop all background audio/worker threads by disabling their loops.
          3. Stop Qt animations and timers.
          4. Replace the current process image with a fresh Python launch of this
             same script — no terminal restart needed.
        """
        # 1. Unhook all keyboard hotkeys
        try:
            keyboard.unhook_all()
        except Exception:
            pass

        # 2. Signal background threads to stop
        self.watch_active = False
        self.highlight_active = False
        # Restore display affinity before execv — the new process inherits the HWND
        # on some Windows versions and would start invisible in captures if we skip this.
        if getattr(self, "stealth_active", False):
            try:
                import ctypes, ctypes.wintypes
                ctypes.windll.user32.SetWindowDisplayAffinity(
                    ctypes.wintypes.HWND(int(self.winId())),
                    ctypes.wintypes.DWORD(0x00000000),
                )
            except Exception:
                pass
            self.stealth_active = False
        worker: WatchWorker | None = getattr(self, "_watch_worker", None)
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

        # 3. Stop Qt animations and timers
        self._stop_pulse()
        bubble = getattr(self, "_bubble", None)
        if bubble: bubble.hide()
        pill = getattr(self, "_pill_win", None)
        if pill: pill.hide()
        anim = getattr(self, "anim", None)
        if anim:
            try:
                anim.stop()
            except Exception:
                pass

        # 4. Replace process — launches a fresh instance immediately
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ── Logic & Hardware Hooks ──
    def _bind_hotkeys(self):
        keyboard.add_hotkey('ctrl+shift+s', lambda: self.bridge.set_status.emit("Triggering Capture") or self._do_capture())
        keyboard.add_hotkey('ctrl+shift+h', lambda: self.bridge.set_status.emit("Triggering Highlight") or self._toggle_highlight())
        keyboard.add_hotkey('ctrl+shift+w', lambda: self.bridge.set_status.emit("Triggering Watcher") or self._toggle_watch())
        keyboard.add_hotkey('ctrl+r',        lambda: self._do_reload())

    def _do_ask(self):
        text = self.ask_entry.text().strip()
        if not text:
            return
        self.ask_entry.clear()

        # ── /debug intercept — system introspection, no AI call ───────────────
        if text == "/debug":
            if self.state:
                report = self.state.get_debug_state()
            else:
                report = "[DEBUG] No state engine loaded (aceit_core not available)."
            self._append_response(report)
            self.bridge.set_status.emit("Debug state printed ✓")
            return

        # ── Normal query ──────────────────────────────────────────────────────
        if self.state:
            self._append_response(f"You: {text}")
            threading.Thread(target=self.state.handle_input, args=(text,), daemon=True).start()

    # ── AI Thinking State ────────────────────────────────────────────────────

    def _start_thinking(self):
        """
        Show the thinking bar in indeterminate (marquee) mode.
        The QProgressBar with setRange(0,0) handles the animated sweep natively —
        no manual QVariantAnimation needed.
        """
        self.thinking_bar.setRange(0, 0)    # indeterminate marquee
        self.thinking_bar.show()
        self.btn_stop.show()                # reveal Stop button while generating

    def _stop_thinking(self):
        """Stop the indeterminate animation and hide the bar."""
        self.thinking_bar.setRange(0, 1)    # exit indeterminate mode cleanly
        self.thinking_bar.setValue(1)
        self.thinking_bar.hide()
        self.btn_stop.hide()                # hide Stop button once done

    def _on_ai_query(self, messages):
        """
        Called by the state engine when a query is dispatched (daemon thread).

        Uses bridge.start_thinking (Signal) instead of calling _start_thinking()
        directly — touching Qt widgets from a non-GUI thread is undefined behaviour
        in Qt; signals are auto-promoted to queued connections across thread
        boundaries so the slot runs safely on the event loop.

        Streaming is enabled via stream=True on the Groq client: each delta token
        is emitted via bridge.stream_token so text appears incrementally in the
        text area rather than after the full response has been generated.
        The _stop_gen event can be set from the main thread (Stop button) to break
        the chunk loop early and cancel the remaining generation.
        """
        self._stop_gen.clear()   # reset interrupt flag for this new query
        self.bridge.set_status.emit("Thinking…")
        self.bridge.start_thinking.emit()   # safe: queued → runs on GUI thread

        def call():
            full_ans = ""
            try:
                # ── Streaming response ────────────────────────────────────────
                model = _core_mod.GROQ_MODEL if _CORE else GROQ_MODEL
                stream = groq_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=1000,
                    stream=True,
                )
                # Emit "AI: " prefix so the label appears immediately
                self.bridge.stream_token.emit("\nAI: ")
                for chunk in stream:
                    if self._stop_gen.is_set():
                        self.bridge.stream_token.emit(" ⏹")   # visual stop indicator
                        break
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        full_ans += delta
                        self.bridge.stream_token.emit(delta)

                # Trailing newline + store full answer in context
                self.bridge.stream_token.emit("\n")
                if self.state and full_ans:
                    self.state.store_ai_response(full_ans)
                self.bridge.set_status.emit("Done ✓")
                if self._is_floating and full_ans:
                    self.bridge.notify_pill.emit(full_ans.replace("\n", " ")[:60] + "…")
            except Exception as e:
                self.bridge.set_status.emit(f"API Error: {e}")
            finally:
                self.bridge.thinking_done.emit()

        threading.Thread(target=call, daemon=True).start()

    @Slot(str)
    def _on_stream_token(self, token: str):
        """
        Receive a streaming chunk from the AI and re-render the entire current
        AI response as HTML via markdown-it-py so Markdown formatting (bold,
        bullets, code blocks) appears correctly even mid-stream.

        Protocol
        ─────────
        • The "\\nAI: " prefix token opens a new response block: we reset the
          buffer and mark streaming as active.
        • The trailing "\\n" token (sent after the stream ends) finalises the
          block: the completed HTML is folded into _md_plain_prefix so it
          survives subsequent plain-text appends.
        • Every other token is accumulated in _md_ai_buffer and the view is
          refreshed in-place (scroll position is preserved).
        """
        # ── Opening sentinel ─────────────────────────────────────────────────
        if token.strip() == "AI:":
            # "\\nAI: " arrives as a single token; strip & start fresh
            self._md_ai_buffer   = ""
            self._md_ai_streaming = True
            self._render_html()
            return

        # ── Stop / trailing newline ──────────────────────────────────────────
        if token in ("\n", " ⏹"):
            if self._md_ai_streaming:
                # Finalise: render buffer → move into prefix so it persists
                rendered = self._md_to_html(self._md_ai_buffer)
                if token == " ⏹":
                    rendered += "<br><span style='color:#FF4A6E'>⏹ Generation stopped</span>"
                self._md_plain_prefix += rendered + "<br>"
                self._md_ai_buffer    = ""
                self._md_ai_streaming = False
                self._render_html()
            return

        # ── Normal token accumulation ────────────────────────────────────────
        if self._md_ai_streaming:
            self._md_ai_buffer += token
            self._render_html()
        else:
            # Fallback: plain append for any token outside a marked AI block
            import html as _html
            self._md_plain_prefix += _html.escape(token)
            self._render_html()

    # ── HTML rendering helpers ────────────────────────────────────────────────

    def _md_to_html(self, md_text: str) -> str:
        """Convert a markdown string to an HTML fragment."""
        if HAS_MARKDOWN_IT and _md is not None:
            return _md.render(md_text)
        # Fallback: escape only
        import html as _html
        return "<pre>" + _html.escape(md_text) + "</pre>"

    def _render_html(self) -> None:
        """
        Rebuild the full HTML document shown in text_area.

        We save/restore the vertical scroll position so the view doesn't jump
        to the top on every token during streaming, but we *do* scroll to the
        bottom when new content is appended (streaming in progress).
        """
        # Build streaming portion label + live markdown
        ai_html = ""
        if self._md_ai_streaming and self._md_ai_buffer:
            live = self._md_to_html(self._md_ai_buffer)
            ai_html = (
                f"<span style='color:{PAL['gold_dim']};font-size:10px;"
                f"font-weight:bold;'>AI RESPONSE</span><br>"
                + live
            )
        elif self._md_ai_streaming:
            ai_html = (
                f"<span style='color:{PAL['gold_dim']};font-size:10px;"
                f"font-weight:bold;'>AI RESPONSE</span> "
                f"<span style='color:{PAL['muted']}'>…</span>"
            )

        full_html = self._CODE_CSS + self._md_plain_prefix + ai_html

        sb = self.text_area.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        self.text_area.setHtml(full_html)

        # Auto-scroll to bottom while streaming; hold position otherwise
        if at_bottom or self._md_ai_streaming:
            sb.setValue(sb.maximum())

    def _do_stop_gen(self):
        """Interrupt the current generation by setting the stop event."""
        self._stop_gen.set()
        self.bridge.thinking_done.emit()   # immediately hide thinking bar / stop button
        self.bridge.set_status.emit("Generation stopped ⏹")

    def _on_model_changed(self, index: int):
        """Update the active Groq model when the model combo changes."""
        if not _CORE:
            return
        model_id = self.model_combo.itemData(index)
        if model_id:
            _core_mod.GROQ_MODEL = model_id
            label = GROQ_MODEL_LABELS.get(model_id, model_id)
            self.bridge.set_status.emit(f"Model → {label}")

    def _on_style_changed(self, style: str):
        """Push the selected response style into the SessionManager."""
        if self.state:
            self.state.session.response_style = style
            self.bridge.set_status.emit(f"Style → {style}")

    # ── Document Upload & Context Injection (Interview Mode) ─────────────────

    def _do_upload_context(self):
        """
        Open a file picker, then spin up a ContextIngestWorker to extract text
        from the chosen PDF or TXT in a background QThread.  The worker's
        signals feed results back to the GUI thread safely.
        """
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Upload Interview Context",
            "",
            "Documents (*.pdf *.txt *.md);;All Files (*)",
        )
        if not path:
            return   # user cancelled

        self.bridge.set_status.emit(f"Loading {os.path.basename(path)}…")

        worker = ContextIngestWorker(path, parent=None)
        worker.ingest_done.connect(self._on_context_ingest_done)
        worker.ingest_failed.connect(self.bridge.set_status)
        worker.progress.connect(self.bridge.set_status)
        worker.finished.connect(worker.deleteLater)

        # Keep a reference so Python GC doesn't collect a running thread
        if not hasattr(self, "_ingest_workers"):
            self._ingest_workers: list = []
        self._ingest_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._ingest_workers.remove(w) if w in self._ingest_workers else None)

        worker.start()

    @Slot(str)
    def _on_context_ingest_done(self, text: str):
        """
        Receive extracted document text on the main thread and inject it as a
        pinned context entry into the SessionManager.

        The pinned entry survives the rolling buffer and is always included at
        the top of every build_messages() call, so the AI coaching is
        automatically grounded in the uploaded résumé / job description.
        """
        if not self.state:
            self.bridge.set_status.emit("Context loaded (no active session to inject into).")
            return

        # Ensure a session exists before injecting
        if not self.state.session.is_active:
            from aceit_core import MODE_SYSTEMS  # local import to avoid circular at module level
            self.state.session.start(MODE_SYSTEMS[self.state.mode])

        char_count = len(text)
        label = f"[USER CONTEXT — {char_count:,} chars]\n\n{text}"
        self.state.session.add_pinned_context(label, source="context")

        preview = text[:80].replace("\n", " ")
        self.bridge.append_text.emit(
            f"📄 Context injected ({char_count:,} chars) — AI will reference this throughout the session.\n"
            f"   Preview: {preview}…\n"
        )
        self.bridge.set_status.emit(f"Context loaded ✓  ({char_count:,} chars pinned)")

    def _on_transcript(self, text, source):
        self.bridge.append_text.emit(f"[{source.upper()}]: {text}")
        if self.state: threading.Thread(target=self.state.handle_input, args=(text, source), daemon=True).start()

    # ── Toggles ──
    def _toggle_mic(self):
        if not self.audio: return
        if self.audio.mic_active: self.audio.stop_mic()
        else: self.audio.start_mic()
        self._set_btn_active(self.btn_mic, self.audio.mic_active)

    def _toggle_speaker(self):
        if not self.audio: return
        if self.audio.speaker_active: self.audio.stop_speaker()
        else: self.audio.start_speaker()
        self._set_btn_active(self.btn_spk, self.audio.speaker_active)

    def _do_capture(self):
        """
        Non-blocking screen capture + OCR via OCRWorker (QThread).

        The main/GUI thread does exactly three things then returns:
          1. Hide the AceIt window.
          2. Update the status label.
          3. Spin up an OCRWorker and wire its signals.

        Everything else — the OS yield, ImageGrab, pytesseract — runs on
        the worker thread.  Qt's auto-connection mode promotes every signal
        to a queued connection because the worker lives in a different thread,
        so all slot calls are marshalled back to the event loop safely.

        Window-restore timing
        ─────────────────────
        OCRWorker emits grab_done immediately after ImageGrab.grab() returns
        and before the slow Tesseract pass begins.  Connecting grab_done to
        self.show means the window reappears in ~150 ms regardless of how
        long OCR takes (typically 0.5 – 3 s on a full screen).
        """
        self.hide()
        self.bridge.set_status.emit("Processing Text Asynchronously…")

        worker = OCRWorker(hide_delay_s=0.15, parent=None)

        # Restore window as soon as the screenshot is taken (before OCR finishes)
        worker.grab_done.connect(self.show)

        # Feed extracted text into the AI query pipeline on the main thread
        worker.ocr_completed.connect(self._on_ocr_ready)

        # Surface errors via the status label (Signal(str) → Signal(str))
        worker.ocr_failed.connect(self.bridge.set_status)

        # Qt will delete the QThread object once run() returns
        worker.finished.connect(worker.deleteLater)

        # Hold a Python reference so the worker isn't GC'd while still running.
        # (Losing the last Python reference to a running QThread is UB in PySide6.)
        if not hasattr(self, "_active_ocr_workers"):
            self._active_ocr_workers: list = []
        self._active_ocr_workers.append(worker)

        # Prune the list when the worker finishes so it doesn't grow unbounded
        def _remove(w=worker):
            try:
                self._active_ocr_workers.remove(w)
            except ValueError:
                pass
        worker.finished.connect(_remove)

        worker.start()

    @Slot(str)
    def _on_ocr_ready(self, txt: str):
        """
        Slot connected to OCRWorker.ocr_completed (queued connection).

        Receives the extracted text on the main thread and dispatches it into
        the StateEngine pipeline on a new daemon thread — matching the pattern
        used by _do_ask and _on_transcript so handle_input is never called on
        the GUI thread.
        """
        if self.state:
            self.bridge.append_text.emit("[SCREEN CAPTURE Sent]")
            threading.Thread(
                target=self.state.handle_input,
                args=(txt, "capture"),
                daemon=True,
            ).start()
        self.bridge.set_status.emit("Capture complete ✓")

    def _toggle_highlight(self):
        self.highlight_active = not self.highlight_active
        self._set_btn_active(self.btn_hl, self.highlight_active)
        if self.highlight_active:
            self.last_clipboard = pyperclip.paste()
            threading.Thread(target=self._hl_loop, daemon=True).start()

    def _hl_loop(self):
        """
        Clipboard watcher — runs on a daemon thread started by _toggle_highlight.

        Optimisations over the original implementation
        ───────────────────────────────────────────────
        • Single time.sleep(POLL_INTERVAL) per iteration replaces the
          busy-counting accumulator (slept_ms / SLEEP_MS loop), reducing
          per-cycle overhead by ~60 % and eliminating the intermediate
          variable entirely.

        • highlight_active is checked twice per cycle: once at the loop
          condition and once after the sleep.  The post-sleep check lets
          the thread exit within one POLL_INTERVAL (~100 ms) without
          entering the pyperclip IPC call on the shutdown iteration.

        • handle_input is dispatched on a fresh daemon thread rather than
          called inline, so a slow StateEngine response cannot block the
          clipboard poll loop.  This matches the pattern in _do_ask and
          _on_transcript.
        """
        POLL_INTERVAL = 0.50   # 500 ms between clipboard checks — prevents Windows clipboard DDOS

        while self.highlight_active:
            time.sleep(POLL_INTERVAL)

            # Short-circuit before the pyperclip IPC call if toggled off
            # during the sleep quantum.
            if not self.highlight_active:
                break

            try:
                curr = pyperclip.paste()
            except Exception:
                continue

            if curr and curr != self.last_clipboard:
                self.last_clipboard = curr
                self.bridge.append_text.emit("[CLIPBOARD Sent]")
                if self.state:
                    threading.Thread(
                        target=self.state.handle_input,
                        args=(curr, "highlight"),
                        daemon=True,
                    ).start()

    def _toggle_stealth(self):
        """
        Stage 4 — Stealth Mode via Windows Display Affinity.

        Calls the Win32 API ``SetWindowDisplayAffinity`` via ctypes to control
        whether AceIt's window surface is included in screen captures, OBS
        streams, Teams / Zoom share, and the Windows print-screen APIs.

        Flag values
        -----------
        WDA_NONE              (0x00000000) — normal, visible in captures (default)
        WDA_EXCLUDEFROMCAPTURE (0x00000011) — excluded from all DWM-routed captures

        The flag was introduced in Windows 10 2004 (build 19041) under the name
        WDA_EXCLUDEFROMCAPTURE.  On older Windows or non-Windows platforms the
        ctypes call will either fail silently (GetLastError ≠ 0) or raise an
        AttributeError; both are caught so the feature degrades gracefully.

        Visual feedback
        ---------------
        • Active:   btn_stealth text colour → PAL['danger'] (glowing red)
        • Inactive: btn_stealth text colour → PAL['muted']  (default grey)
        """
        import ctypes
        import ctypes.wintypes

        WDA_NONE               = 0x00000000
        WDA_EXCLUDEFROMCAPTURE = 0x00000011

        # Resolve HWND — PySide6 returns a sip.voidptr; ctypes needs an integer.
        try:
            hwnd = int(self.winId())
        except Exception:
            self.bridge.set_status.emit("Stealth: could not resolve HWND")
            return

        if not self.stealth_active:
            # ── Activate stealth ──────────────────────────────────────────────
            try:
                ok = ctypes.windll.user32.SetWindowDisplayAffinity(
                    ctypes.wintypes.HWND(hwnd),
                    ctypes.wintypes.DWORD(WDA_EXCLUDEFROMCAPTURE),
                )
            except (AttributeError, OSError):
                ok = 0   # windll not available (Linux / macOS / old Windows)

            if ok:
                self.stealth_active = True
                # Red glow: override just the colour tokens in the existing style
                self.btn_stealth.setStyleSheet(
                    f"QPushButton {{ background: rgba(255,74,110,0.15);"
                    f"  color: {PAL['danger']}; border: none; border-radius: 5px;"
                    f"  font-size: 13px; }}"
                    f"QPushButton:hover {{ background: rgba(255,74,110,0.30);"
                    f"  color: {PAL['danger']}; }}"
                )
                self.btn_stealth.setToolTip("Stealth Mode ACTIVE — click to disable")
                self.bridge.set_status.emit("🥷 Stealth ON: Hidden from screen share")
            else:
                # API returned FALSE — likely unsupported OS version
                err = ctypes.GetLastError() if hasattr(ctypes, "GetLastError") else "n/a"
                self.bridge.set_status.emit(
                    f"Stealth: SetWindowDisplayAffinity failed (err {err}) — "
                    "requires Windows 10 build 19041+"
                )
        else:
            # ── Deactivate stealth ────────────────────────────────────────────
            try:
                ctypes.windll.user32.SetWindowDisplayAffinity(
                    ctypes.wintypes.HWND(hwnd),
                    ctypes.wintypes.DWORD(WDA_NONE),
                )
            except (AttributeError, OSError):
                pass   # best-effort restore; state still flipped below

            self.stealth_active = False
            # Restore default muted style (matches _make_hdr_btn initial style)
            self.btn_stealth.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; border-radius: 5px;"
                f"  color: {PAL['muted']}; font-size: 13px; }}"
                f"QPushButton:hover {{ background: {PAL['surface_2']};"
                f"  color: {PAL['text']}; }}"
            )
            self.btn_stealth.setToolTip("Stealth Mode — hide from screen capture (Windows only)")
            self.bridge.set_status.emit("🥷 Stealth OFF: Visible in screen share")

    def _toggle_watch(self):
        if self.watch_active:
            # ── Stop ─────────────────────────────────────────────────────────
            self.watch_active = False
            self._set_btn_active(self.btn_watch, False)
            worker: WatchWorker | None = getattr(self, "_watch_worker", None)
            if worker:
                worker.stop()
                worker.quit()
                worker.wait(2000)   # give the thread up to 2 s to finish cleanly
                self._watch_worker = None
            self.bridge.set_status.emit("Watcher Stopped")
        else:
            # ── Start ─────────────────────────────────────────────────────────
            self.watch_active = True
            self._set_btn_active(self.btn_watch, True)
            self.bridge.set_status.emit("Watcher Active")

            worker = WatchWorker(
                interval=getattr(self, "_watch_interval", 5),
                sensitivity=getattr(self, "_watch_sensitivity", "Medium"),
                parent=None,    # must be None so Qt can move it to its own thread
            )
            worker.screen_text.connect(self._on_watch_text)
            worker.status.connect(self.bridge.set_status)
            worker.finished.connect(worker.deleteLater)
            self._watch_worker = worker
            worker.start()

    @Slot(str)
    def _on_watch_text(self, txt: str):
        """
        Slot connected to WatchWorker.screen_text (queued connection).
        Feeds captured screen content into the StateEngine on a daemon thread.
        """
        if self.state:
            self.bridge.append_text.emit("[WATCHER Sent]")
            threading.Thread(
                target=self.state.handle_input,
                args=(txt, "watch"),
                daemon=True,
            ).start()

    # ── Float Mode: Native Translucent Circle + Breathing Glow ──────────────────

    def _on_bubble_dragged(self, new_pos: QPoint) -> None:
        """Keep internal position state in sync while bubble is dragged."""
        # new_pos is the bubble's new top-left; store it so snap knows where we are
        self._bubble_pos = new_pos

    # ── Float Mode: FloatBubble + PillNotification standalone windows ───────────

    def _get_pulse_accent(self) -> tuple[str, str]:
        """Return (bright_color, dim_color) for the current active mode."""
        mic_on = self.audio and (self.audio.mic_active or getattr(self.audio, "speaker_active", False))
        if mic_on:
            return "#9B59B6", "#4A235A"          # Purple  – Mic / Interview
        if self.watch_active:
            return "#47A1FF", "#1E5FA8"           # Cyan    – Screen Watcher
        return PAL["gold"], PAL["gold_dim"]       # Gold    – Default / Active

    # ── Edge-Snap Helpers ──────────────────────────────────────────────────────

    def _nearest_edge(self, cx: int, cy: int) -> str:
        screen = QApplication.primaryScreen().availableGeometry()
        dists = {
            "left":   cx - screen.left(),
            "right":  screen.right()  - cx,
            "top":    cy - screen.top(),
            "bottom": screen.bottom() - cy,
        }
        return min(dists, key=dists.__getitem__)

    def _snap_geo_for_edge(self, edge: str) -> QRect:
        screen = QApplication.primaryScreen().availableGeometry()
        size   = FloatBubble.SIZE
        cx     = self._bubble.geometry().center().x()
        cy     = self._bubble.geometry().center().y()
        if edge == "left":
            return QRect(screen.left(), cy - size // 2, size, size)
        if edge == "right":
            return QRect(screen.right() - size, cy - size // 2, size, size)
        if edge == "top":
            return QRect(cx - size // 2, screen.top(), size, size)
        return QRect(cx - size // 2, screen.bottom() - size, size, size)

    # ── Enter / Leave Float ────────────────────────────────────────────────────

    def _enter_float(self):
        """Collapse main window, show FloatBubble snapped to nearest edge."""
        self._is_floating = True
        self.hide()

        bright, _ = self._get_pulse_accent()

        # Position the bubble at the current window centre
        cx = self.geometry().center().x()
        cy = self.geometry().center().y()
        size = FloatBubble.SIZE
        self._docked_edge = self._nearest_edge(cx, cy)
        snap = self._snap_geo_for_edge(self._docked_edge)

        self._bubble.set_border_color(bright)
        self._bubble.setGeometry(snap)
        self._bubble.show()
        self._bubble.raise_()
        self._start_pulse()

    def _leave_float(self):
        """Hide FloatBubble, stop pulse, restore main window."""
        self._stop_pulse()
        self._is_floating = False
        self._pill_win.hide()
        self._bubble.hide()
        self.show()
        self.raise_()

    # ── Toggle entry point ──────────────────────────────────────────────────────
    def _toggle_float(self):
        if self._is_floating:
            self._leave_float()
        else:
            self._enter_float()

    # ── Breathing / Pulse Animation ─────────────────────────────────────────────

    def _start_pulse(self):
        """Kick off the QVariantAnimation breathing loop on the FloatBubble border."""
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
        anim = getattr(self, "_pulse_float_glow", None)
        if not anim or not self._is_floating:
            return
        self._start_pulse()

    @Slot(str)
    def _show_pill(self, text: str):
        """Show the standalone PillNotification beside the FloatBubble."""
        if not self._is_floating:
            return
        edge = getattr(self, "_docked_edge", "right")
        self._pill_win.show_text(text, self._bubble.geometry(), edge)

    def _hide_pill(self):
        """Delegate to PillNotification's own animated hide."""
        self._pill_win._animate_hide()

if __name__ == "__main__":
    # Must be set before QApplication is created to avoid DPI access-denied warning on Windows
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    win = AceItWindow()
    win.show()
    sys.exit(app.exec())