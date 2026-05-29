"""
aceit_ui.py — AceIt Frontend (PySide6)
Features: Detached Floating Header, Circular Morph Animations, Global Hotkeys.
"""
from __future__ import annotations
import sys, os, time, threading
from typing import Optional

# Desktop Integration
import keyboard
import pyautogui
import pyperclip
import pytesseract
from PIL import ImageGrab

from PySide6.QtCore import (
    Qt, QPoint, QSize, QPropertyAnimation, QVariantAnimation, QEasingCurve, QRect,
    QTimer, Signal, QObject, Slot,
)
from PySide6.QtGui import QColor, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame,
    QHBoxLayout, QVBoxLayout, QTextEdit, QLineEdit,
    QPushButton, QLabel, QSizePolicy, QGraphicsDropShadowEffect,
    QDialog, QSlider, QComboBox, QTabWidget, QScrollArea,
    QListWidget, QListWidgetItem, QStackedWidget, QCheckBox,
)

try:
    from aceit_core import ModeState, StateEngine, AudioEngine, groq_client, GROQ_MODEL
    _CORE = True
except ImportError:
    _CORE = False

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
QPushButton.icon_btn {{ font-size: 14px; background: {PAL['surface_2']}; border-radius: 6px; }}
QPushButton.icon_btn:hover {{ background: {PAL['border']}; color: {PAL['gold']}; }}
QPushButton.icon_btn[active="true"] {{ background: rgba(71, 161, 255, 0.2); color: {PAL['blue']}; }}
QLineEdit {{ background: {PAL['surface_2']}; border-radius: 6px; padding: 6px; color: {PAL['text']}; }}
QTextEdit {{ background: {PAL['surface_2']}; border: none; border-radius: 6px; padding: 10px; }}
QSlider::groove:horizontal {{ height: 4px; background: {PAL['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {PAL['gold']}; width: 12px; margin: -4px 0; border-radius: 6px; }}
"""

# ── Signal Bridge (Cross-thread UI Updates) ────────────────────────────────────
class SignalBridge(QObject):
    append_text = Signal(str)
    set_status  = Signal(str)
    set_badge   = Signal(str)
    notify_pill = Signal(str)

# ── Settings Dialog — Command Center ──────────────────────────────────────────
_SETTINGS_QSS = f"""
/* ── Dialog chrome ── */
QDialog {{ background: transparent; }}

/* ── Side nav list ── */
QListWidget {{
    background: {PAL['bg']};
    border: none;
    border-right: 1px solid {PAL['border']};
    border-top-left-radius: 12px;
    border-bottom-left-radius: 12px;
    outline: none;
    padding: 8px 0;
    font-size: 13px;
    color: {PAL['muted']};
}}
QListWidget::item {{
    padding: 10px 16px;
    border-radius: 0px;
}}
QListWidget::item:selected {{
    background: {PAL['surface_2']};
    color: {PAL['gold']};
    border-left: 3px solid {PAL['gold']};
}}
QListWidget::item:hover:!selected {{
    background: {PAL['surface']};
    color: {PAL['text']};
}}

/* ── Content panels ── */
QStackedWidget {{ background: transparent; }}
QWidget#tab_panel {{
    background: {PAL['surface']};
    border-top-right-radius: 12px;
    border-bottom-right-radius: 12px;
}}

/* ── Section headers inside panels ── */
QLabel#section_hdr {{
    color: {PAL['gold']};
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 1px;
    padding-bottom: 4px;
    border-bottom: 1px solid {PAL['border']};
}}

/* ── Row labels ── */
QLabel#row_label {{
    color: {PAL['text']};
    font-size: 13px;
}}
QLabel#row_sub {{
    color: {PAL['muted']};
    font-size: 11px;
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

/* ── Toggle (QPushButton used as pill switch) ── */
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

/* ── Info cards (hotkeys) ── */
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

/* ── Misc buttons ── */
QPushButton#action_btn {{
    background: {PAL['surface_2']}; color: {PAL['blue']};
    border: 1px solid {PAL['border']}; border-radius: 6px;
    padding: 5px 12px; font-size: 12px;
}}
QPushButton#action_btn:hover {{
    background: {PAL['border']}; color: {PAL['text']};
}}
QPushButton#done_btn {{
    background: {PAL['gold']}; color: {PAL['bg']};
    border-radius: 6px; padding: 7px 24px;
    font-weight: bold; font-size: 13px;
}}
QPushButton#done_btn:hover {{ background: {PAL['gold_dim']}; color: {PAL['text']}; }}
"""

class SettingsDialog(QDialog):
    """Tabbed Command Center — four categorized panels in a side-nav layout."""

    W, H = 560, 420

    def __init__(self, parent, engine, audio, ui_window):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(_SETTINGS_QSS)
        self.setFixedSize(self.W, self.H)

        self.ui  = ui_window
        self.audio = audio

        # ── Outer chrome ──────────────────────────────────────────────────────
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

        # Title bar
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

        # Body: side nav + stacked panels
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        # Side nav
        self.nav = QListWidget()
        self.nav.setFixedWidth(148)
        nav_items = [
            ("🎨  Appearance", "UI"),
            ("🎙  Audio & Voice", "Audio"),
            ("👁  Vision & Capture", "Vision"),
            ("⌨  Hotkeys", "Hotkeys"),
        ]
        for label, _ in nav_items:
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(148, 42))
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        body_lay.addWidget(self.nav)

        # Stacked content
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_appearance())
        self.stack.addWidget(self._build_audio())
        self.stack.addWidget(self._build_vision())
        self.stack.addWidget(self._build_hotkeys())
        body_lay.addWidget(self.stack, 1)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        outer.addWidget(body, 1)

        # Footer
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

    # ── Panel builders ────────────────────────────────────────────────────────

    def _panel(self) -> QWidget:
        """Blank scrollable panel with standard padding."""
        outer = QWidget()
        outer.setObjectName("tab_panel")
        outer.setStyleSheet(f"background: {PAL['surface']};")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._current_lay = QVBoxLayout(inner)
        self._current_lay.setContentsMargins(20, 16, 20, 16)
        self._current_lay.setSpacing(14)

        scroll.setWidget(inner)

        wrap = QVBoxLayout(outer)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(scroll)
        return outer

    def _section(self, text: str):
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_hdr")
        lbl.setStyleSheet(
            f"color: {PAL['gold']}; font-size: 10px; font-weight: bold;"
            f"letter-spacing: 1px; padding-bottom: 4px;"
            f"border-bottom: 1px solid {PAL['border']}; background: transparent;"
        )
        self._current_lay.addWidget(lbl)

    def _row(self, label: str, sub: str, control: QWidget) -> QWidget:
        """Single setting row: left labels + right control."""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)

        txt = QVBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {PAL['text']}; font-size: 13px; background: transparent;")
        txt.addWidget(lbl)
        if sub:
            sl = QLabel(sub)
            sl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
            txt.addWidget(sl)
        rl.addLayout(txt, 1)
        rl.addWidget(control)
        self._current_lay.addWidget(row)
        return row

    def _toggle_btn(self, active: bool) -> QPushButton:
        btn = QPushButton("ON" if active else "OFF")
        btn.setObjectName("toggle_on" if active else "toggle_off")
        btn.setCheckable(True)
        btn.setChecked(active)

        def _refresh(checked):
            btn.setText("ON" if checked else "OFF")
            btn.setObjectName("toggle_on" if checked else "toggle_off")
            btn.setStyleSheet("")           # force style re-evaluation
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        btn.toggled.connect(_refresh)
        return btn

    # ── Tab 1: Appearance ─────────────────────────────────────────────────────

    def _build_appearance(self) -> QWidget:
        p = self._panel()
        self._section("Display")

        # Opacity
        sld = QSlider(Qt.Horizontal)
        sld.setRange(20, 100)
        sld.setValue(int(self.ui.windowOpacity() * 100))
        sld.setFixedWidth(140)
        sld.valueChanged.connect(lambda v: self.ui.setWindowOpacity(v / 100))
        self._row("Window Opacity", f"Current: {sld.value()}%", sld)

        # Live label update
        val_lbl = QLabel(f"{sld.value()}%")
        val_lbl.setStyleSheet(f"color: {PAL['gold']}; font-size: 11px; background: transparent;")
        sld.valueChanged.connect(lambda v: val_lbl.setText(f"{v}%"))
        # Attach label to last row
        self._current_lay.itemAt(self._current_lay.count() - 1).widget().layout().addWidget(val_lbl)

        self._section("Theme")

        # Dark / Light toggle (wired to placeholder; extend when theme engine exists)
        self._theme_is_dark = True

        def _toggle_theme(checked):
            self._theme_is_dark = checked
            # Placeholder — swap PAL and re-apply QSS here when light theme exists
            self.ui.bridge.set_status.emit(
                "Dark Mode Active" if checked else "Light Mode (coming soon)"
            )

        theme_btn = self._toggle_btn(self._theme_is_dark)
        theme_btn.toggled.connect(_toggle_theme)
        self._row("Dark Mode", "Toggle dark/light colour scheme", theme_btn)

        self._current_lay.addStretch()
        return p

    # ── Tab 2: Audio & Voice ──────────────────────────────────────────────────

    def _build_audio(self) -> QWidget:
        p = self._panel()
        self._section("Devices")

        mic_name  = getattr(self.audio, "mic_device_name",  "Not connected") if self.audio else "No audio engine"
        spk_name  = getattr(self.audio, "spk_device_name",  "Not connected") if self.audio else "No audio engine"

        def _info_lbl(text):
            l = QLabel(text)
            l.setStyleSheet(
                f"color: {PAL['blue']}; background: {PAL['surface_2']};"
                f"border-radius: 5px; padding: 4px 8px; font-size: 12px;"
            )
            l.setWordWrap(True)
            return l

        self._row("Microphone",  "Input device in use", _info_lbl(f"🎤  {mic_name}"))
        self._row("Speaker",     "Output device in use", _info_lbl(f"🔊  {spk_name}"))

        # List devices button
        def _list_devices():
            if not self.audio:
                self.ui.bridge.append_text.emit("[SETTINGS] No audio engine loaded.")
                return
            try:
                import sounddevice as sd
                devs = sd.query_devices()
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

        btn_list = QPushButton("List All Devices →")
        btn_list.setObjectName("action_btn")
        btn_list.clicked.connect(_list_devices)
        self._row("Enumerate", "Print all devices to response area", btn_list)

        self._section("Interview Mode")

        mic_on = bool(self.audio and getattr(self.audio, "mic_active", False))
        spk_on = bool(self.audio and getattr(self.audio, "speaker_active", False))
        interview_on = mic_on or spk_on

        iv_btn = self._toggle_btn(interview_on)

        def _toggle_interview(checked):
            if not self.audio:
                return
            if checked:
                if not getattr(self.audio, "mic_active", False):
                    self.audio.start_mic()
                if not getattr(self.audio, "speaker_active", False):
                    self.audio.start_speaker()
                self.ui._set_btn_active(self.ui.btn_mic, True)
                self.ui._set_btn_active(self.ui.btn_spk, True)
            else:
                if getattr(self.audio, "mic_active", False):
                    self.audio.stop_mic()
                if getattr(self.audio, "speaker_active", False):
                    self.audio.stop_speaker()
                self.ui._set_btn_active(self.ui.btn_mic, False)
                self.ui._set_btn_active(self.ui.btn_spk, False)
            self.ui.bridge.set_status.emit(
                "Interview Mode ON — Mic + Speaker active" if checked
                else "Interview Mode OFF"
            )

        iv_btn.toggled.connect(_toggle_interview)
        self._row("Interview Mode", "Enable mic + speaker capture together", iv_btn)

        self._current_lay.addStretch()
        return p

    # ── Tab 3: Vision & Capture ───────────────────────────────────────────────

    def _build_vision(self) -> QWidget:
        p = self._panel()
        self._section("Screen Watcher")

        # Interval slider
        interval_val = getattr(self.ui, "_watch_interval", 5)
        sld = QSlider(Qt.Horizontal)
        sld.setRange(3, 15)
        sld.setValue(interval_val)
        sld.setFixedWidth(130)
        iv_lbl = QLabel(f"{sld.value()} s")
        iv_lbl.setStyleSheet(f"color: {PAL['gold']}; font-size: 11px; background: transparent; min-width: 28px;")

        def _set_interval(v):
            self.ui._watch_interval = v
            iv_lbl.setText(f"{v} s")

        sld.valueChanged.connect(_set_interval)

        row_w = QWidget()
        row_w.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row_w)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(sld)
        rl.addWidget(iv_lbl)
        self._row("Scan Interval", "How often the screen is analysed", row_w)

        # Sensitivity combo
        sens = getattr(self.ui, "_watch_sensitivity", "Medium")
        combo = QComboBox()
        combo.addItems(["Low", "Medium", "High"])
        combo.setCurrentText(sens)
        combo.currentTextChanged.connect(lambda t: setattr(self.ui, "_watch_sensitivity", t))
        self._row("Sensitivity", "Change-detection threshold", combo)

        self._section("Clipboard / Highlight")

        hl_btn = self._toggle_btn(self.ui.highlight_active)

        def _toggle_hl(checked):
            if checked != self.ui.highlight_active:
                self.ui._toggle_highlight()
            self.ui._set_btn_active(self.ui.btn_hl, checked)

        hl_btn.toggled.connect(_toggle_hl)
        self._row("Highlight Mode", "Watch clipboard for new copied text", hl_btn)

        self._current_lay.addStretch()
        return p

    # ── Tab 4: Hotkeys ────────────────────────────────────────────────────────

    def _build_hotkeys(self) -> QWidget:
        p = self._panel()
        self._section("Global Hotkeys  (read-only)")

        hotkeys = [
            ("📷", "Screen Capture",  "Ctrl + Shift + S", "Grab a screenshot and send to AI"),
            ("🔍", "Highlight Mode",  "Ctrl + Shift + H", "Toggle clipboard watching"),
            ("👁", "Screen Watcher",  "Ctrl + Shift + W", "Toggle periodic screen scanning"),
            ("♻", "Hot Reload",      "Ctrl + R",          "Reload the UI without restarting"),
        ]

        for icon, name, combo, desc in hotkeys:
            card = QFrame()
            card.setObjectName("hotkey_card")
            card.setStyleSheet(
                f"background: {PAL['surface_2']}; border: 1px solid {PAL['border']};"
                f"border-radius: 8px;"
            )
            cl = QHBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)

            ico_lbl = QLabel(icon)
            ico_lbl.setStyleSheet("font-size: 18px; background: transparent;")
            cl.addWidget(ico_lbl)

            txt = QVBoxLayout()
            nl = QLabel(name)
            nl.setStyleSheet(f"color: {PAL['text']}; font-size: 13px; font-weight: bold; background: transparent;")
            dl = QLabel(desc)
            dl.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
            txt.addWidget(nl)
            txt.addWidget(dl)
            cl.addLayout(txt, 1)

            badge = QLabel(combo)
            badge.setObjectName("hotkey_badge")
            badge.setStyleSheet(
                f"background: {PAL['bg']}; color: {PAL['gold']};"
                f"border: 1px solid {PAL['gold_dim']}; border-radius: 5px;"
                f"font-family: 'Consolas', monospace; font-size: 11px;"
                f"padding: 3px 8px;"
            )
            cl.addWidget(badge)
            self._current_lay.addWidget(card)

        note = QLabel("Hotkeys are registered globally and work even when AceIt is minimised.")
        note.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px; background: transparent;")
        note.setWordWrap(True)
        self._current_lay.addSpacing(4)
        self._current_lay.addWidget(note)
        self._current_lay.addStretch()
        return p

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
        
        # Backend Engines
        self.state = StateEngine(raw_query_fn=self._on_ai_query) if _CORE else None
        self.audio = AudioEngine(on_transcript=self._on_transcript, on_status=lambda m: self.bridge.set_status.emit(m)) if _CORE else None
        
        # Desktop Tools State
        self.watch_active = False
        self.highlight_active = False
        self.last_clipboard = ""

        self._build_ui()
        self._bind_hotkeys()

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
        hdr_lay.setContentsMargins(12, 0, 12, 0)
        
        self.logo = QLabel("⚡ AceIt")
        self.logo.setStyleSheet(f"color: {PAL['gold']}; font-weight: bold; font-size: 15px;")
        hdr_lay.addWidget(self.logo)
        
        hdr_lay.addStretch()
        
        # Opacity Slider
        hdr_lay.addWidget(QLabel("Opacity", styleSheet=f"color: {PAL['muted']}; font-size: 10px;"))
        self.op_slider = QSlider(Qt.Horizontal)
        self.op_slider.setRange(30, 100)
        self.op_slider.setValue(95)
        self.op_slider.setFixedWidth(60)
        self.op_slider.valueChanged.connect(lambda v: self.setWindowOpacity(v/100))
        hdr_lay.addWidget(self.op_slider)
        
        # Header Buttons
        self.btn_theme = self._make_hdr_btn("☀️", "Toggle Theme")
        self.btn_settings = self._make_hdr_btn("⚙", "Settings", self._open_settings)
        self.btn_float = self._make_hdr_btn("🗗", "Float Mode", self._toggle_float)
        self.btn_close = self._make_hdr_btn("✕", "Close", self.close)
        
        for b in [self.btn_theme, self.btn_settings, self.btn_float, self.btn_close]:
            hdr_lay.addWidget(b)
            
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

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
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
            
        self.ask_entry = QLineEdit()
        self.ask_entry.setPlaceholderText("Ask anything...")
        self.ask_entry.returnPressed.connect(self._do_ask)
        dock_lay.addWidget(self.ask_entry)
        
        btn_send = QPushButton("➤")
        btn_send.setStyleSheet(f"background: {PAL['gold']}; color: {PAL['bg']}; border-radius: 6px; padding: 6px 12px; font-weight: bold;")
        btn_send.clicked.connect(self._do_ask)
        dock_lay.addWidget(btn_send)

        ws_lay.addWidget(self.dock)
        self.main_lay.addWidget(self.workspace)

        # ── Notification Pill (Hidden by default) ──
        self.pill = QWidget(self)
        self.pill.setStyleSheet(f"background: {PAL['surface']}; border: 1px solid {PAL['gold']}; border-radius: 12px;")
        self.pill.setGeometry(0, 0, 0, 40)
        self.pill.hide()
        
        self.pill_lay = QHBoxLayout(self.pill)
        self.pill_lay.setContentsMargins(10, 0, 10, 0)
        self.pill_lbl = QLabel("")
        self.pill_lbl.setStyleSheet(f"color: {PAL['text']}; font-size: 11px;")
        self.pill_lay.addWidget(self.pill_lbl)

        self.setStyleSheet(QSS)
        self.setWindowOpacity(0.95)

    def _make_hdr_btn(self, icon, tip, cmd=None):
        btn = QPushButton(icon)
        btn.setToolTip(tip)
        btn.setFixedSize(28, 28)
        if cmd: btn.clicked.connect(cmd)
        return btn
        
    def _make_dock_btn(self, icon, tip, cmd=None):
        btn = QPushButton(icon)
        btn.setProperty("class", "icon_btn")
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

    # ── UI Interactions ──
    def _open_settings(self):
        SettingsDialog(self, self.state, self.audio, self).exec()

    def _copy_text(self):
        pyperclip.copy(self.text_area.toPlainText())
        self.bridge.set_status.emit("Copied ✓")

    def _clear_text(self):
        self.text_area.clear()
        if self.state: self.state.session.end()
        self.bridge.set_status.emit("Cleared memory ✓")

    @Slot(str)
    def _append_response(self, text):
        self.text_area.moveCursor(QTextCursor.End)
        self.text_area.insertPlainText(text + "\n")
        self.text_area.moveCursor(QTextCursor.End)

    @Slot(str)
    def _set_status(self, msg):
        self.status_lbl.setText(f"  {msg}")

    # ── Logic & Hardware Hooks ──
    def _bind_hotkeys(self):
        keyboard.add_hotkey('ctrl+shift+s', lambda: self.bridge.set_status.emit("Triggering Capture") or self._do_capture())
        keyboard.add_hotkey('ctrl+shift+h', lambda: self.bridge.set_status.emit("Triggering Highlight") or self._toggle_highlight())
        keyboard.add_hotkey('ctrl+shift+w', lambda: self.bridge.set_status.emit("Triggering Watcher") or self._toggle_watch())

    def _do_ask(self):
        text = self.ask_entry.text().strip()
        if text and self.state:
            self.ask_entry.clear()
            self._append_response(f"You: {text}")
            threading.Thread(target=self.state.handle_input, args=(text,), daemon=True).start()

    def _on_ai_query(self, messages):
        self.bridge.set_status.emit("Thinking...")
        def call():
            try:
                resp = groq_client.chat.completions.create(model=GROQ_MODEL, messages=messages, max_tokens=1000)
                ans = resp.choices[0].message.content
                if self.state: self.state.store_ai_response(ans)
                self.bridge.append_text.emit(f"AI: {ans}")
                self.bridge.set_status.emit("Done ✓")
                if self._is_floating:
                    self.bridge.notify_pill.emit(ans.replace("\n", " ")[:60] + "...")
            except Exception as e:
                self.bridge.set_status.emit(f"API Error: {e}")
        threading.Thread(target=call, daemon=True).start()

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
        self.hide()
        time.sleep(0.3)
        img = pyautogui.screenshot()
        self.show()
        try: txt = pytesseract.image_to_string(img).strip()
        except: txt = ""
        if txt and self.state:
            self.bridge.append_text.emit("[SCREEN CAPTURE Sent]")
            threading.Thread(target=self.state.handle_input, args=(txt, "capture"), daemon=True).start()

    def _toggle_highlight(self):
        self.highlight_active = not self.highlight_active
        self._set_btn_active(self.btn_hl, self.highlight_active)
        if self.highlight_active:
            self.last_clipboard = pyperclip.paste()
            threading.Thread(target=self._hl_loop, daemon=True).start()

    def _hl_loop(self):
        while self.highlight_active:
            try:
                curr = pyperclip.paste()
                if curr and curr != self.last_clipboard:
                    self.last_clipboard = curr
                    if self.state: self.state.handle_input(curr, "highlight")
                    self.bridge.append_text.emit("[CLIPBOARD Sent]")
            except: pass
            time.sleep(1)

    def _toggle_watch(self):
        self.watch_active = not self.watch_active
        self._set_btn_active(self.btn_watch, self.watch_active)
        self.bridge.set_status.emit("Watcher Active" if self.watch_active else "Watcher Stopped")

    # ── Float Mode: Native Translucent Circle + Breathing Glow ──────────────────

    def _get_pulse_accent(self) -> tuple[str, str]:
        """Return (bright_color, dim_color) for the current active mode."""
        mic_on = self.audio and (self.audio.mic_active or getattr(self.audio, "speaker_active", False))
        if mic_on:
            return "#9B59B6", "#4A235A"          # Purple  – Mic / Interview
        if self.watch_active:
            return "#47A1FF", "#1E5FA8"           # Cyan    – Screen Watcher
        return PAL["gold"], PAL["gold_dim"]       # Gold    – Default / Active

    def _enter_float(self):
        """Morph the window into a native translucent circle."""
        self._is_floating = True

        # Hide everything except the float button
        self.workspace.hide()
        for w in [self.logo, self.op_slider, self.btn_theme, self.btn_settings, self.btn_close]:
            w.hide()

        # Re-style header as a perfect circle using native border-radius.
        # No chroma-key: WA_TranslucentBackground (set on __init__) does the work.
        bright, _ = self._get_pulse_accent()
        self.header.setStyleSheet(
            f"background: {PAL['surface_2']};"
            f"border-radius: 30px;"
            f"border: 2px solid {bright};"
        )

        # Shrink window to 80×80 (10 px margin each side → 60×60 circle visible)
        self._float_morph_step(
            end_geo=QRect(self.x(), self.y(), 80, 80),
            duration=280,
            on_done=self._start_pulse,
        )

    def _leave_float(self):
        """Expand back to the full panel and stop all pulse animations."""
        self._stop_pulse()
        self._is_floating = False

        # Restore header style
        self.header.setStyleSheet(
            f"background: {PAL['surface']};"
            f"border-radius: 12px;"
            f"border: 1px solid {PAL['border']};"
        )

        def _restore_ui():
            self.workspace.show()
            for w in [self.logo, self.op_slider, self.btn_theme, self.btn_settings, self.btn_close]:
                w.show()

        self._float_morph_step(
            end_geo=QRect(self.x(), self.y(), 520, 750),
            duration=250,
            on_done=_restore_ui,
        )

    def _float_morph_step(self, end_geo: QRect, duration: int, on_done=None):
        """Shared geometry animation used by both enter and leave."""
        self.anim = QPropertyAnimation(self, b"geometry")
        self.anim.setDuration(duration)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.setEndValue(end_geo)
        if on_done:
            self.anim.finished.connect(on_done)
        self.anim.start()

    # ── Toggle entry point ──────────────────────────────────────────────────────
    def _toggle_float(self):
        if self._is_floating:
            self._leave_float()
        else:
            self._enter_float()

    # ── Breathing / Pulse Animation ─────────────────────────────────────────────

    def _start_pulse(self):
        """Kick off the QVariantAnimation breathing loop."""
        self._stop_pulse()   # safety: never double-start

        bright, dim = self._get_pulse_accent()

        self._pulse_float_glow = QVariantAnimation()
        self._pulse_float_glow.setStartValue(QColor(dim))
        self._pulse_float_glow.setEndValue(QColor(bright))
        self._pulse_float_glow.setDuration(900)           # half-cycle: dim→bright
        self._pulse_float_glow.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse_float_glow.setLoopCount(-1)           # infinite

        # On every frame, flip direction at each loop end to create the
        # smooth dim→bright→dim breathing effect.
        self._pulse_forward = True

        def _on_value_changed(color: QColor):
            if not self._is_floating:
                return
            hex_col = color.name()
            self.header.setStyleSheet(
                f"background: {PAL['surface_2']};"
                f"border-radius: 30px;"
                f"border: 2px solid {hex_col};"
            )

        def _on_loop():
            # Reverse interpolation direction each loop to ping-pong
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

        # Refresh accent color whenever mode changes while floating
        self._pulse_refresh_timer = QTimer(self)
        self._pulse_refresh_timer.setInterval(2000)
        self._pulse_refresh_timer.timeout.connect(self._refresh_pulse_accent)
        self._pulse_refresh_timer.start()

    def _stop_pulse(self):
        """Cleanly stop and discard any running pulse animation."""
        anim = getattr(self, "_pulse_float_glow", None)
        if anim:
            anim.stop()
            self._pulse_float_glow = None
        timer = getattr(self, "_pulse_refresh_timer", None)
        if timer:
            timer.stop()
            self._pulse_refresh_timer = None

    def _refresh_pulse_accent(self):
        """Called every 2 s to re-sync pulse color when mode toggles while floating."""
        anim = getattr(self, "_pulse_float_glow", None)
        if not anim or not self._is_floating:
            return
        bright, dim = self._get_pulse_accent()
        # Restart with new colors so the transition feels intentional
        self._start_pulse()

    @Slot(str)
    def _show_pill(self, text):
        if not self._is_floating: return
        self.pill_lbl.setText(text)
        
        # Position pill next to the circle
        self.pill.setGeometry(80, 20, 0, 40)
        self.pill.show()
        
        # Slide Out
        self.p_anim_out = QPropertyAnimation(self.pill, b"geometry")
        self.p_anim_out.setDuration(300)
        self.p_anim_out.setEasingCurve(QEasingCurve.OutBack)
        self.p_anim_out.setEndValue(QRect(80, 20, 250, 40))
        self.p_anim_out.start()
        
        # Close after 5s
        QTimer.singleShot(5000, self._hide_pill)

    def _hide_pill(self):
        self.p_anim_in = QPropertyAnimation(self.pill, b"geometry")
        self.p_anim_in.setDuration(200)
        self.p_anim_in.setEasingCurve(QEasingCurve.InCubic)
        self.p_anim_in.setEndValue(QRect(80, 20, 0, 40))
        self.p_anim_in.start()

if __name__ == "__main__":
    # Must be set before QApplication is created to avoid DPI access-denied warning on Windows
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    win = AceItWindow()
    win.show()
    sys.exit(app.exec())