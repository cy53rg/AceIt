"""
atlas_settings_ui.py — Atlas settings dialog (extracted from atlas_ui.py).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QFileDialog,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

try:
    from atlas_core import (
        GROQ_MODEL,
        GROQ_MODELS,
        GROQ_MODEL_LABELS,
        RESPONSE_STYLES,
        voice_engine,
    )
    import atlas_core as _core_mod
    _CORE = True
except ImportError:
    _core_mod = None  # type: ignore
    _CORE = False
    voice_engine = None


def _get_pal() -> dict:
    from atlas_ui import PAL

    return PAL


def settings_dialog_qss() -> str:
    """Lazy QSS builder — avoids circular import with atlas_ui at module load."""
    PAL = _get_pal()

    return f"""
QDialog, QWidget {{ background: transparent; }}
QFrame#ctrl_chrome {{
    background: {PAL['surface']};
    border: 1px solid {PAL['border']};
    border-radius: 12px;
}}
QLabel#section_hdr {{
    color: {PAL['cyan']};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    padding-bottom: 4px;
    border-bottom: 1px solid {PAL['border']};
    background: transparent;
}}
QComboBox {{
    background: {PAL['surface_2']}; border: 1px solid {PAL['border']};
    border-radius: 6px; padding: 4px 10px; color: {PAL['text']};
    min-width: 120px;
}}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background: {PAL['surface_2']}; border: 1px solid {PAL['border']};
    selection-background-color: {PAL['border']};
}}
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
QSlider::groove:horizontal {{ height: 4px; background: {PAL['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {PAL['cyan']}; width: 13px; height: 13px;
    margin: -5px 0; border-radius: 7px;
}}
QPushButton#done_btn {{
    background: {PAL['cyan']}; color: {PAL['bg']};
    border-radius: 6px; padding: 7px 24px;
    font-weight: bold; font-size: 13px;
}}
QPushButton#done_btn:hover {{ background: {PAL['cyan_dim']}; color: {PAL['text']}; }}
"""


class SettingsDialog(QDialog):
    """Settings: Model, Audio, Appearance, Skills, Memory, Context, Account, Hotkeys."""

    W, H = 520, 580

    def __init__(self, parent, engine, audio, ui_window) -> None:
        super().__init__(parent)
        self.setWindowTitle("Atlas Settings")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setStyleSheet(settings_dialog_qss())
        self.setFixedSize(self.W, self.H)
        self.ui = ui_window
        self.engine = engine
        self.audio = audio

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        self._build_model_tab()
        self._build_audio_tab()
        self._build_appearance_tab()
        self._build_skills_tab()
        self._build_memory_tab()
        self._build_context_tab()
        self._build_account_tab()
        self._build_connectors_tab()
        self._build_filesystem_tab()
        self._build_ssh_tab()
        self._build_scheduler_tab()
        self._build_security_tab()
        self._build_hotkeys_tab()
        self._build_teaching_tab()

        done = QPushButton("Close")
        done.setObjectName("done_btn")
        done.clicked.connect(self.hide)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(done)
        outer.addLayout(row)

    def show_tab(self, index: int) -> None:
        self.tabs.setCurrentIndex(index)
        self.show()
        self.raise_()
        self.activateWindow()

    def _build_model_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        self.model_combo = QComboBox()
        if _CORE:
            for mid in GROQ_MODELS:
                self.model_combo.addItem(GROQ_MODEL_LABELS.get(mid, mid), mid)
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) == GROQ_MODEL:
                    self.model_combo.setCurrentIndex(i)
                    break
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        lay.addWidget(QLabel("Groq model"))
        lay.addWidget(self.model_combo)
        self.style_combo = QComboBox()
        styles = RESPONSE_STYLES if _CORE else ["Terse", "Direct", "Balanced", "Detailed"]
        self.style_combo.addItems(styles)
        if self.engine:
            self.style_combo.setCurrentText(
                getattr(self.engine.session, "response_style", "Balanced")
            )
        self.style_combo.currentTextChanged.connect(self._on_style_changed)
        lay.addWidget(QLabel("Response style"))
        lay.addWidget(self.style_combo)
        self.max_tokens_sld = QSlider(Qt.Horizontal)
        self.max_tokens_sld.setRange(256, 8192)
        self.max_tokens_sld.setValue(2048)
        lay.addWidget(QLabel("Max tokens (session hint)"))
        lay.addWidget(self.max_tokens_sld)
        lay.addStretch()
        self.tabs.addTab(w, "Model")

    def _build_audio_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        for label, action in (
            ("Microphone", self.ui._action_mic),
            ("Speaker Capture", self.ui._action_spk),
            ("Voice Engine (TTS)", self.ui._action_ve),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch()
            btn = QPushButton("On" if action.isChecked() else "Off")
            btn.setCheckable(True)
            btn.setChecked(action.isChecked())
            btn.toggled.connect(lambda checked, a=action: a.setChecked(checked))
            action.toggled.connect(lambda checked, b=btn: b.setChecked(checked))
            row.addWidget(btn)
            lay.addLayout(row)
        self.voice_combo = QComboBox()
        # Populate from the actually-loaded voices.bin; fall back to the known
        # Kokoro v0.19 voice set so the list is never empty / never invalid.
        voices = []
        if voice_engine is not None:
            try:
                voices = voice_engine.available_voices()
            except Exception:
                voices = []
        if not voices:
            voices = [
                "af", "af_bella", "af_nicole", "af_sarah", "af_sky",
                "am_adam", "am_michael", "bf_emma", "bf_isabella",
                "bm_george", "bm_lewis",
            ]
        self.voice_combo.addItems(voices)
        current_voice = getattr(voice_engine, "voice", "af_sarah") if voice_engine else "af_sarah"
        if current_voice in voices:
            self.voice_combo.setCurrentText(current_voice)
        self.voice_combo.currentTextChanged.connect(self._on_voice_changed)

        # Premium ElevenLabs voice picker — named free-tier defaults; Brian on
        # launch.  Changing it switches the live streaming voice immediately.
        self.eleven_combo = QComboBox()
        eleven_voices = dict(getattr(_core_mod, "ELEVEN_VOICES", {})) if _CORE else {}
        if eleven_voices:
            for name, vid in eleven_voices.items():
                self.eleven_combo.addItem(name, vid)
            cur_id = getattr(voice_engine, "eleven_voice_id", "") if voice_engine else ""
            for i in range(self.eleven_combo.count()):
                if self.eleven_combo.itemData(i) == cur_id:
                    self.eleven_combo.setCurrentIndex(i)
                    break
            self.eleven_combo.currentIndexChanged.connect(self._on_eleven_voice_changed)
            lay.addWidget(QLabel("Voice (ElevenLabs)"))
            lay.addWidget(self.eleven_combo)
            lay.addWidget(QLabel("Fallback voice (offline Kokoro)"))
        else:
            lay.addWidget(QLabel("Voice"))
        lay.addWidget(self.voice_combo)
        self.speed_sld = QSlider(Qt.Horizontal)
        self.speed_sld.setRange(50, 200)
        self.speed_sld.setValue(100)
        self.speed_sld.valueChanged.connect(self._on_speed_changed)
        lay.addWidget(QLabel("Speech speed %"))
        lay.addWidget(self.speed_sld)

        # Quick "test voice" button so the user can confirm TTS audibly.
        test_btn = QPushButton("🔊 Test Voice")
        test_btn.clicked.connect(self._test_voice)
        lay.addWidget(test_btn)
        lay.addStretch()
        self.tabs.addTab(w, "Audio")

    def _on_voice_changed(self, name: str) -> None:
        if voice_engine is not None and name:
            voice_engine.set_voice(name)
            self.ui.bridge.set_status.emit(f"Voice → {name}")

    def _on_eleven_voice_changed(self, index: int) -> None:
        if voice_engine is None or index < 0:
            return
        name = self.eleven_combo.itemText(index)
        vid  = self.eleven_combo.itemData(index)
        if not vid:
            return
        try:
            voice_engine.set_eleven_voice(vid)
            self.ui.bridge.set_status.emit(f"Voice → {name} (ElevenLabs)")
            voice_engine.speak(f"This is {name}, your new Atlas voice.")
        except Exception as exc:
            self.ui.bridge.set_status.emit(f"Voice change failed: {exc}")

    def _on_speed_changed(self, pct: int) -> None:
        if voice_engine is not None:
            voice_engine.set_speed(pct / 100.0)

    def _test_voice(self) -> None:
        if voice_engine is None:
            self.ui.bridge.set_status.emit("Voice engine unavailable")
            return
        voice_engine.unmute()
        voice_engine.speak("Atlas voice engine online. You can hear me clearly.")
        self.ui.bridge.set_status.emit("🔊 Testing voice…")

    def _build_appearance_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        op = QSlider(Qt.Horizontal)
        op.setRange(20, 100)
        op.setValue(int(self.ui.windowOpacity() * 100))
        op.valueChanged.connect(lambda v: self.ui.setWindowOpacity(v / 100))
        lay.addWidget(QLabel("Window opacity"))
        lay.addWidget(op)
        accent = QComboBox()
        accent.addItems(["Cyan (default)", "Gold", "Purple"])
        lay.addWidget(QLabel("Accent preset"))
        lay.addWidget(accent)
        font_sld = QSlider(Qt.Horizontal)
        font_sld.setRange(11, 18)
        font_sld.setValue(13)
        lay.addWidget(QLabel("Chat font size (px)"))
        lay.addWidget(font_sld)
        lay.addStretch()
        self.tabs.addTab(w, "Appearance")

    def _build_skills_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.skills_list = QListWidget()
        lay.addWidget(self.skills_list)
        btn_row = QHBoxLayout()
        btn_install = QPushButton("Install…")
        btn_install.clicked.connect(self._install_skill)
        btn_uninstall = QPushButton("Uninstall")
        btn_uninstall.clicked.connect(self._uninstall_skill)
        btn_row.addWidget(btn_install)
        btn_row.addWidget(btn_uninstall)
        lay.addLayout(btn_row)
        self.tabs.addTab(w, "Skills")
        self.tabs.currentChanged.connect(lambda i: self._refresh_skills() if i == 3 else None)

    def _build_memory_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.memory_table = QTableWidget(0, 4)
        self.memory_table.setHorizontalHeaderLabels(["Category", "Key", "Value", ""])
        self.memory_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self.memory_table)
        stats = QLabel("")
        self._memory_stats_lbl = stats
        lay.addWidget(stats)
        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_memory)
        btn_export = QPushButton("Export JSON")
        btn_export.clicked.connect(self._export_memory)
        btn_clear = QPushButton("Clear all facts")
        btn_clear.clicked.connect(self._clear_memory)
        for b in (btn_refresh, btn_export, btn_clear):
            btn_row.addWidget(b)
        lay.addLayout(btn_row)
        self.tabs.addTab(w, "Memory")
        self.tabs.currentChanged.connect(lambda i: self._refresh_memory() if i == 4 else None)

    def _build_context_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            "<b>Standing context</b> — notes Atlas applies to every reply, "
            "in all modes. e.g. <i>\"I'm a left-handed designer; prefer concise "
            "answers.\"</i>"))
        self.context_list = QListWidget()
        self.context_list.setWordWrap(True)
        lay.addWidget(self.context_list, 1)
        add_row = QHBoxLayout()
        self.context_in = QLineEdit()
        self.context_in.setPlaceholderText("Add a standing note…")
        self.context_in.returnPressed.connect(self._add_context)
        btn_add = QPushButton("Add")
        btn_add.clicked.connect(self._add_context)
        add_row.addWidget(self.context_in, 1)
        add_row.addWidget(btn_add)
        lay.addLayout(add_row)
        btn_del = QPushButton("Delete selected")
        btn_del.clicked.connect(self._del_context)
        lay.addWidget(btn_del)
        self.tabs.addTab(w, "Context")
        self.tabs.currentChanged.connect(
            lambda i: self._refresh_context() if i == 5 else None)

    def _refresh_context(self) -> None:
        self.context_list.clear()
        if not self.engine:
            return
        try:
            items = self.engine.list_global_context()
        except Exception:
            items = []
        if not items:
            placeholder = QListWidgetItem("No standing notes yet.")
            placeholder.setFlags(Qt.NoItemFlags)
            self.context_list.addItem(placeholder)
            return
        for c in items:
            item = QListWidgetItem(c.get("content", ""))
            item.setData(Qt.UserRole, c.get("id"))
            self.context_list.addItem(item)

    def _add_context(self) -> None:
        if not self.engine:
            return
        text = self.context_in.text().strip()
        if not text:
            return
        try:
            self.engine.add_global_context(text)
            self.context_in.clear()
            self._refresh_context()
            self.ui.bridge.set_status.emit("Standing note added ✓")
        except Exception as exc:
            self.ui.bridge.set_status.emit(f"Couldn't add note: {exc}")

    def _del_context(self) -> None:
        if not self.engine:
            return
        item = self.context_list.currentItem()
        cid = item.data(Qt.UserRole) if item else None
        if cid is None:
            return
        try:
            self.engine.delete_global_context(int(cid))
            self._refresh_context()
            self.ui.bridge.set_status.emit("Standing note removed")
        except Exception as exc:
            self.ui.bridge.set_status.emit(f"Couldn't remove note: {exc}")

    def _build_account_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        self.account_lbl = QLabel("")
        self.account_lbl.setWordWrap(True)
        lay.addWidget(self.account_lbl)
        self.btn_sync = QPushButton("Sync now")
        self.btn_sync.clicked.connect(self._account_sync)
        lay.addWidget(self.btn_sync)
        btn_switch = QPushButton("Switch account…")
        btn_switch.clicked.connect(self._account_switch)
        lay.addWidget(btn_switch)
        lay.addStretch()
        self.tabs.addTab(w, "Account")
        self._account_tab_index = self.tabs.count() - 1
        self.tabs.currentChanged.connect(
            lambda i: self._refresh_account() if i == self._account_tab_index else None)

    def _build_connectors_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        hdr = QLabel(
            "<b>Connected Accounts</b> — OAuth and API integrations. "
            "Each service shows exactly what Atlas can do.")
        hdr.setWordWrap(True)
        lay.addWidget(hdr)
        self.connectors_list = QVBoxLayout()
        self.connectors_container = QWidget()
        self.connectors_container.setLayout(self.connectors_list)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.connectors_container)
        lay.addWidget(scroll, 1)
        self.tabs.addTab(w, "Connected Accounts")
        self._connectors_tab_index = self.tabs.count() - 1
        self.tabs.currentChanged.connect(
            lambda i: self._refresh_connectors() if i == self._connectors_tab_index else None)

    def _refresh_connectors(self) -> None:
        PAL = _get_pal()
        client = getattr(self.ui, "_daemon_client", None)
        while self.connectors_list.count():
            item = self.connectors_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not client:
            self.connectors_list.addWidget(QLabel("Daemon not connected."))
            return
        try:
            data = client._get("/api/connectors")
            connectors = data.get("connectors") or []
        except Exception as exc:
            self.connectors_list.addWidget(QLabel(f"Could not load connectors: {exc}"))
            return
        if not connectors:
            self.connectors_list.addWidget(QLabel("No connectors registered."))
            return
        for info in connectors:
            box = QFrame()
            box.setObjectName("ctrl_chrome")
            bl = QVBoxLayout(box)
            cid = info.get("id", "")
            name = info.get("display_name", cid)
            connected = info.get("connected", False)
            stub = cid in ("gmail", "notion") and not connected
            status = (
                f"<span style='color:{PAL['success']}'>Connected</span>"
                if connected else
                (
                    f"<span style='color:{PAL['gold']}'>Needs configuration</span>"
                    if stub else
                    f"<span style='color:{PAL['muted']}'>Not connected</span>"
                )
            )
            title = QLabel(f"<b>{name}</b> — {status}")
            bl.addWidget(title)
            boundary = QLabel(info.get("boundary_text") or "")
            boundary.setWordWrap(True)
            boundary.setStyleSheet(f"color: {PAL['text']}; font-size: 11px;")
            bl.addWidget(boundary)
            btn_row = QHBoxLayout()
            if stub:
                hint = QLabel(
                    "Set GOOGLE_CLIENT_ID / NOTION_TOKEN in .env and connect when OAuth is wired."
                    if cid == "gmail" else
                    "Notion OAuth pending — connect when configured."
                )
                hint.setWordWrap(True)
                hint.setStyleSheet(f"color: {PAL['muted']}; font-size: 10px;")
                bl.addWidget(hint)
            elif connected:
                btn = QPushButton("Disconnect")
                btn.clicked.connect(lambda _=False, c=cid: self._connector_toggle(c, False))
            else:
                btn = QPushButton("Connect")
                btn.clicked.connect(lambda _=False, c=cid: self._connector_toggle(c, True))
            btn_row.addWidget(btn)
            btn_row.addStretch()
            bl.addLayout(btn_row)
            self.connectors_list.addWidget(box)

    def _connector_toggle(self, connector_id: str, connect: bool) -> None:
        client = getattr(self.ui, "_daemon_client", None)
        if not client:
            return
        path = f"/api/connectors/{connector_id}/{'connect' if connect else 'disconnect'}"
        self.ui.bridge.set_status.emit(f"{'Connecting' if connect else 'Disconnecting'} {connector_id}…")

        def _work():
            try:
                data = client._post(path, {})
                msg = data.get("message", "Done")
                self.ui.bridge.set_status.emit(msg)
                QTimer.singleShot(0, self._refresh_connectors)
            except Exception as exc:
                self.ui.bridge.set_status.emit(f"Connector error: {exc}")

        threading.Thread(target=_work, daemon=True, name="atlas-connector").start()

    def _build_filesystem_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        hdr = QLabel(
            "<b>Filesystem write scopes</b> — Atlas can read your home directory except "
            "credential paths (SSH keys, .env, browser profiles). Writes are denied unless "
            "you add a directory here. Deletes always require confirmation.")
        hdr.setWordWrap(True)
        lay.addWidget(hdr)
        self.fs_scope_list = QListWidget()
        lay.addWidget(self.fs_scope_list, 1)
        row = QHBoxLayout()
        self.fs_scope_path = QLineEdit()
        self.fs_scope_path.setPlaceholderText("C:\\Users\\you\\Projects\\AtlasWorkspace")
        row.addWidget(self.fs_scope_path, 1)
        btn_add = QPushButton("Add scope")
        btn_add.clicked.connect(self._fs_add_scope)
        row.addWidget(btn_add)
        lay.addLayout(row)
        btn_rem = QPushButton("Remove selected scope")
        btn_rem.clicked.connect(self._fs_remove_scope)
        lay.addWidget(btn_rem)
        self.tabs.addTab(w, "Filesystem")
        self._filesystem_tab_index = self.tabs.count() - 1
        self.tabs.currentChanged.connect(
            lambda i: self._refresh_fs_scopes() if i == self._filesystem_tab_index else None)

    def _refresh_fs_scopes(self) -> None:
        client = getattr(self.ui, "_daemon_client", None)
        self.fs_scope_list.clear()
        if not client:
            self.fs_scope_list.addItem("Daemon not connected.")
            return
        try:
            data = client._get("/api/fs/write_scopes")
            for item in data.get("scopes") or []:
                self.fs_scope_list.addItem(f"{item.get('path')}  ({item.get('label', '')})")
        except Exception as exc:
            self.fs_scope_list.addItem(f"Error: {exc}")

    def _fs_add_scope(self) -> None:
        client = getattr(self.ui, "_daemon_client", None)
        path = self.fs_scope_path.text().strip()
        if not (client and path):
            return

        def _work():
            try:
                data = client._post("/api/fs/write_scopes", {"path": path})
                self.ui.bridge.set_status.emit(data.get("message", "Done"))
                QTimer.singleShot(0, self._refresh_fs_scopes)
            except Exception as exc:
                self.ui.bridge.set_status.emit(str(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _fs_remove_scope(self) -> None:
        client = getattr(self.ui, "_daemon_client", None)
        item = self.fs_scope_list.currentItem()
        if not (client and item):
            return
        path = item.text().split("  (")[0].strip()

        def _work():
            try:
                client._post("/api/fs/write_scopes/remove", {"path": path})
                self.ui.bridge.set_status.emit("Scope removed")
                QTimer.singleShot(0, self._refresh_fs_scopes)
            except Exception as exc:
                self.ui.bridge.set_status.emit(str(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _build_ssh_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        hdr = QLabel(
            "<b>SSH targets</b> — Register remote hosts explicitly (key-based auth only). "
            "Atlas applies the same shell allow/deny rules to remote commands.")
        hdr.setWordWrap(True)
        lay.addWidget(hdr)
        self.ssh_target_list = QListWidget()
        lay.addWidget(self.ssh_target_list, 1)
        form = QFormLayout()
        self.ssh_name = QLineEdit()
        self.ssh_host = QLineEdit()
        self.ssh_user = QLineEdit()
        self.ssh_key = QLineEdit()
        self.ssh_services = QLineEdit()
        self.ssh_services.setPlaceholderText("nginx, postgresql (comma-separated, optional)")
        form.addRow("Name", self.ssh_name)
        form.addRow("Host", self.ssh_host)
        form.addRow("User", self.ssh_user)
        form.addRow("Key path", self.ssh_key)
        form.addRow("Manageable services", self.ssh_services)
        lay.addLayout(form)
        row = QHBoxLayout()
        btn_add = QPushButton("Register host")
        btn_add.clicked.connect(self._ssh_add_target)
        btn_rem = QPushButton("Remove selected")
        btn_rem.clicked.connect(self._ssh_remove_target)
        row.addWidget(btn_add)
        row.addWidget(btn_rem)
        row.addStretch()
        lay.addLayout(row)
        self.tabs.addTab(w, "SSH Targets")
        self._ssh_tab_index = self.tabs.count() - 1
        self.tabs.currentChanged.connect(
            lambda i: self._refresh_ssh_targets() if i == self._ssh_tab_index else None)

    def _build_scheduler_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        hdr = QLabel(
            "<b>Scheduler</b> — Pending policy approvals, cron jobs, weekly routine, "
            "and recent activity (daemon-backed)."
        )
        hdr.setWordWrap(True)
        lay.addWidget(hdr)

        lay.addWidget(QLabel("<b>Pending approvals</b>"))
        self.sched_pending_list = QListWidget()
        lay.addWidget(self.sched_pending_list, 1)
        prow = QHBoxLayout()
        btn_approve = QPushButton("Approve selected")
        btn_deny = QPushButton("Deny selected")
        btn_approve.clicked.connect(lambda: self._resolve_scheduler_pending(True))
        btn_deny.clicked.connect(lambda: self._resolve_scheduler_pending(False))
        prow.addWidget(btn_approve)
        prow.addWidget(btn_deny)
        prow.addStretch()
        lay.addLayout(prow)

        lay.addWidget(QLabel("<b>Scheduled job definitions</b>"))
        self.sched_defs_list = QListWidget()
        lay.addWidget(self.sched_defs_list, 1)

        lay.addWidget(QLabel("<b>Weekly routine</b>"))
        wrow = QHBoxLayout()
        self.sched_weekly_day = QComboBox()
        self.sched_weekly_day.addItems(
            ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        )
        self.sched_weekly_hour = QSpinBox()
        self.sched_weekly_hour.setRange(0, 23)
        self.sched_weekly_hour.setValue(8)
        self.sched_weekly_min = QSpinBox()
        self.sched_weekly_min.setRange(0, 59)
        btn_weekly = QPushButton("Save weekly routine (Mon 8:00 UTC default)")
        btn_weekly.clicked.connect(self._save_weekly_routine)
        wrow.addWidget(QLabel("Day"))
        wrow.addWidget(self.sched_weekly_day)
        wrow.addWidget(QLabel("Hour UTC"))
        wrow.addWidget(self.sched_weekly_hour)
        wrow.addWidget(QLabel("Min"))
        wrow.addWidget(self.sched_weekly_min)
        wrow.addWidget(btn_weekly)
        wrow.addStretch()
        lay.addLayout(wrow)

        lay.addWidget(QLabel("<b>Activity (last 7 days)</b>"))
        self.sched_activity_list = QListWidget()
        lay.addWidget(self.sched_activity_list, 2)

        btn_refresh = QPushButton("Refresh scheduler data")
        btn_refresh.clicked.connect(self._refresh_scheduler_tab)
        lay.addWidget(btn_refresh)

        self.tabs.addTab(w, "Scheduler")
        self._scheduler_tab_index = self.tabs.count() - 1
        self.tabs.currentChanged.connect(
            lambda i: self._refresh_scheduler_tab() if i == self._scheduler_tab_index else None)

    def _daemon_client(self):
        return getattr(self.ui, "_daemon_client", None)

    def _refresh_scheduler_tab(self) -> None:
        client = self._daemon_client()
        self.sched_pending_list.clear()
        self.sched_defs_list.clear()
        self.sched_activity_list.clear()
        if not client:
            self.sched_pending_list.addItem("Daemon not connected.")
            return
        try:
            for row in client.list_scheduler_pending():
                pid = row.get("id")
                self.sched_pending_list.addItem(
                    QListWidgetItem(
                        f"#{pid} [{row.get('risk_class')}] {row.get('action_type')}: "
                        f"{str(row.get('reason') or row.get('detail', ''))[:80]}"
                    )
                )
                item = self.sched_pending_list.item(self.sched_pending_list.count() - 1)
                if item is not None:
                    item.setData(Qt.UserRole, int(pid))
            for job in client.list_scheduler_definitions():
                self.sched_defs_list.addItem(
                    f"{job.get('id', '?')} — {job.get('name', '')} "
                    f"({job.get('job_type', '')}) next={job.get('next_run', '—')}"
                )
            for act in client.list_scheduler_activity(7.0):
                self.sched_activity_list.addItem(
                    f"[{act.get('category')}] {act.get('summary', '')} "
                    f"({act.get('status', '')})"
                )
        except Exception as exc:
            self.sched_pending_list.addItem(f"Error: {exc}")

    def _resolve_scheduler_pending(self, approved: bool) -> None:
        client = self._daemon_client()
        item = self.sched_pending_list.currentItem()
        if not client or not item:
            return
        pid = item.data(Qt.UserRole)
        if pid is None:
            return

        def _work():
            try:
                client.resolve_scheduler_pending(int(pid), approved=approved)
                self.ui.bridge.set_status.emit(
                    "Scheduler action approved." if approved else "Scheduler action denied."
                )
                QTimer.singleShot(0, self._refresh_scheduler_tab)
            except Exception as exc:
                self.ui.bridge.set_status.emit(str(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _save_weekly_routine(self) -> None:
        client = self._daemon_client()
        if not client:
            return

        def _work():
            try:
                client.configure_weekly_routine(
                    day_of_week=self.sched_weekly_day.currentText(),
                    hour=int(self.sched_weekly_hour.value()),
                    minute=int(self.sched_weekly_min.value()),
                )
                self.ui.bridge.set_status.emit("Weekly routine saved.")
                QTimer.singleShot(0, self._refresh_scheduler_tab)
            except Exception as exc:
                self.ui.bridge.set_status.emit(str(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _refresh_ssh_targets(self) -> None:
        client = getattr(self.ui, "_daemon_client", None)
        self.ssh_target_list.clear()
        if not client:
            self.ssh_target_list.addItem("Daemon not connected.")
            return
        try:
            data = client._get("/api/ssh/targets")
            for item in data.get("targets") or []:
                svc = ", ".join(item.get("manageable_services") or [])
                self.ssh_target_list.addItem(
                    f"{item.get('name')} — {item.get('user')}@{item.get('host')}  [{svc}]")
        except Exception as exc:
            self.ssh_target_list.addItem(f"Error: {exc}")

    def _ssh_add_target(self) -> None:
        client = getattr(self.ui, "_daemon_client", None)
        if not client:
            return
        body = {
            "name": self.ssh_name.text().strip(),
            "host": self.ssh_host.text().strip(),
            "user": self.ssh_user.text().strip(),
            "key_path": self.ssh_key.text().strip(),
            "manageable_services": [
                s.strip() for s in self.ssh_services.text().split(",") if s.strip()
            ],
        }
        if not all(body[k] for k in ("name", "host", "user", "key_path")):
            self.ui.bridge.set_status.emit("Fill in name, host, user, and key path.")
            return

        def _work():
            try:
                data = client._post("/api/ssh/targets", body)
                self.ui.bridge.set_status.emit(data.get("message", "Done"))
                QTimer.singleShot(0, self._refresh_ssh_targets)
            except Exception as exc:
                self.ui.bridge.set_status.emit(str(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _ssh_remove_target(self) -> None:
        client = getattr(self.ui, "_daemon_client", None)
        item = self.ssh_target_list.currentItem()
        if not (client and item):
            return
        name = item.text().split(" — ")[0].strip()

        def _work():
            try:
                client._post("/api/ssh/targets/remove", {"name": name})
                self.ui.bridge.set_status.emit("SSH target removed")
                QTimer.singleShot(0, self._refresh_ssh_targets)
            except Exception as exc:
                self.ui.bridge.set_status.emit(str(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _refresh_account(self) -> None:
        PAL = _get_pal()
        acct = getattr(self.ui, "account", None)
        name = os.environ.get("ATLAS_USER", "guest")
        if acct and acct.cloud_available:
            who = acct.email or name
            self.account_lbl.setText(
                f"<b>{who}</b><br><span style='color:{PAL['success']}'>"
                f"☁ Cloud sync ON</span>")
            self.btn_sync.setEnabled(bool(getattr(acct.cloud, "cloud_id", None)))
        else:
            self.account_lbl.setText(
                f"<b>{name}</b><br><span style='color:{PAL['muted']}'>"
                f"Local profile (offline)</span>")
            self.btn_sync.setEnabled(False)

    def _account_sync(self) -> None:
        acct = getattr(self.ui, "account", None)
        if not (acct and self.engine):
            return
        self.ui.bridge.set_status.emit("Syncing…")

        def _work():
            try:
                acct.sync_up(self.engine.user_id)
                acct.sync_down(self.engine.user_id)
                self.ui.bridge.set_status.emit("Synced ✓")
            except Exception as exc:
                self.ui.bridge.set_status.emit(f"Sync failed: {exc}")

        threading.Thread(target=_work, daemon=True, name="atlas-sync").start()

    def _account_switch(self) -> None:
        from atlas_ui import LoginDialog

        acct = getattr(self.ui, "account", None)
        if not acct:
            return
        acct.sign_out()
        dlg = LoginDialog(acct, self)
        if dlg.exec() == QDialog.Accepted and dlg.user_id and self.engine:
            os.environ["ATLAS_USER"] = dlg.user_name
            self.engine.set_user(dlg.user_id, dlg.user_name)
            self._refresh_account()
            self.ui.bridge.set_status.emit(f"Signed in as {dlg.user_name}")

    def _build_security_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.addWidget(QLabel(
            "<b>Two-step verification</b> — extra check when signing in."))
        self.twofa_combo = QComboBox()
        self.twofa_combo.addItems(["Off", "Email code", "Authenticator app"])
        lay.addWidget(self.twofa_combo)
        self.btn_twofa_apply = QPushButton("Apply 2FA setting")
        self.btn_twofa_apply.clicked.connect(self._apply_twofa)
        lay.addWidget(self.btn_twofa_apply)
        self.twofa_status = QLabel("")
        self.twofa_status.setWordWrap(True)
        lay.addWidget(self.twofa_status)

        lay.addWidget(QLabel("<b>Change password</b> (local profile)"))
        self.sec_old_pw = QLineEdit()
        self.sec_old_pw.setPlaceholderText("Current password")
        self.sec_old_pw.setEchoMode(QLineEdit.Password)
        self.sec_new_pw = QLineEdit()
        self.sec_new_pw.setPlaceholderText("New password")
        self.sec_new_pw.setEchoMode(QLineEdit.Password)
        for f in (self.sec_old_pw, self.sec_new_pw):
            lay.addWidget(f)
        btn_pw = QPushButton("Update password")
        btn_pw.clicked.connect(self._change_password)
        lay.addWidget(btn_pw)

        lay.addWidget(QLabel("<b>App lock</b> — PIN required after idle (optional)"))
        self.sec_pin = QLineEdit()
        self.sec_pin.setPlaceholderText("4+ digit PIN")
        self.sec_pin.setEchoMode(QLineEdit.Password)
        self.sec_pin.setMaxLength(12)
        lay.addWidget(self.sec_pin)
        btn_pin = QPushButton("Set app lock PIN")
        btn_pin.clicked.connect(self._set_app_lock)
        lay.addWidget(btn_pin)

        self.chk_pause_sensing = QCheckBox("Pause all sensing (mic, screen watch, camera)")
        self.chk_pause_sensing.toggled.connect(self._toggle_pause_sensing)
        lay.addWidget(self.chk_pause_sensing)

        btn_clear = QPushButton("Clear all my remembered facts")
        btn_clear.clicked.connect(self._security_clear_memory)
        lay.addWidget(btn_clear)
        btn_export = QPushButton("Export my data (JSON)")
        btn_export.clicked.connect(self._security_export_data)
        lay.addWidget(btn_export)

        lay.addWidget(QLabel("<b>Computer Use — Safety Mode</b>"))
        # Semantics: off = auto-approve physical actions; always = PermissionDialog per
        # step; trusted = one task/routine confirm then auto for allowlisted goals.
        self.safety_combo = QComboBox()
        self.safety_combo.addItems([
            "Off — act without prompts",
            "Always — confirm each action",
            "Trusted — confirm once per session",
        ])
        btn_safety = QPushButton("Apply safety mode")
        btn_safety.clicked.connect(self._apply_safety_mode)
        lay.addWidget(self.safety_combo)
        lay.addWidget(btn_safety)

        lay.addStretch()
        self.tabs.addTab(w, "Security")
        self._security_tab_index = self.tabs.count() - 1
        self.tabs.currentChanged.connect(
            lambda i: self._refresh_security() if i == self._security_tab_index else None)

    def _refresh_security(self) -> None:
        acct = getattr(self.ui, "account", None)
        if not (acct and self.engine):
            return
        sec = acct.get_security(self.engine.user_id)
        method = sec.get("twofa_method", "none")
        idx = {"none": 0, "email": 1, "totp": 2}.get(method, 0)
        self.twofa_combo.setCurrentIndex(idx)
        self.chk_pause_sensing.blockSignals(True)
        self.chk_pause_sensing.setChecked(sec.get("pause_sensing", False))
        self.chk_pause_sensing.blockSignals(False)
        if sec.get("twofa_enabled"):
            self.twofa_status.setText(
                f"2FA active via {'email' if method == 'email' else 'authenticator app'}.")
        else:
            self.twofa_status.setText("Two-step verification is off.")
        mode = "off"
        if self.engine:
            mode = str(self.engine.get_user_prefs().get("safety_mode", "off"))
            self.engine.safety_mode = mode
        self.safety_combo.setCurrentIndex(
            {"off": 0, "always": 1, "trusted": 2}.get(mode, 0))

    def _apply_safety_mode(self) -> None:
        if not self.engine:
            return
        idx = self.safety_combo.currentIndex()
        mode = ("off", "always", "trusted")[idx]
        self.engine.safety_mode = mode
        self.engine.set_user_pref("safety_mode", mode)
        self.engine._safety_session_ok = False
        self.ui.bridge.set_status.emit(f"Safety mode: {mode}")

    def _apply_twofa(self) -> None:
        acct = getattr(self.ui, "account", None)
        if not (acct and self.engine):
            return
        uid = self.engine.user_id
        choice = self.twofa_combo.currentText()
        if choice == "Off":
            acct.disable_2fa(uid)
            self.ui.bridge.set_status.emit("Two-step verification disabled.")
            self._refresh_security()
            return
        if choice == "Email code":
            ok, msg = acct.enable_email_2fa(uid)
        else:
            secret, uri = acct.setup_totp_secret(uid)
            from PySide6.QtWidgets import QInputDialog
            code, ok_d = QInputDialog.getText(
                self, "Link authenticator",
                f"Add this secret to Google Authenticator / Authy:\n\n{secret}\n\n"
                f"Or scan URI:\n{uri}\n\nEnter the 6-digit code to confirm:")
            if not ok_d:
                return
            ok, msg = acct.confirm_totp_setup(uid, code)
        self.twofa_status.setText(msg)
        self.ui.bridge.set_status.emit(msg)
        self._refresh_security()

    def _change_password(self) -> None:
        acct = getattr(self.ui, "account", None)
        if not (acct and self.engine):
            return
        ok, msg = acct.change_password_local(
            self.engine.user_id, self.sec_old_pw.text(), self.sec_new_pw.text())
        self.ui.bridge.set_status.emit(msg)
        if ok:
            self.sec_old_pw.clear()
            self.sec_new_pw.clear()

    def _set_app_lock(self) -> None:
        acct = getattr(self.ui, "account", None)
        if not (acct and self.engine):
            return
        ok, msg = acct.set_app_lock_pin(self.engine.user_id, self.sec_pin.text())
        self.ui.bridge.set_status.emit(msg)
        if ok:
            self.sec_pin.clear()

    def _toggle_pause_sensing(self, checked: bool) -> None:
        acct = getattr(self.ui, "account", None)
        if not (acct and self.engine):
            return
        acct.set_security(self.engine.user_id, pause_sensing=checked)
        if checked:
            if self.ui.audio:
                try:
                    self.ui.audio.stop_mic()
                    self.ui.audio.stop_speaker()
                except Exception:
                    pass
            self.ui._stop_watch()
            self.ui._action_cam.setChecked(False)
            self.ui.bridge.set_status.emit("All sensing paused.")
        else:
            self.ui.bridge.set_status.emit("Sensing resumed — enable mic/watch manually.")

    def _security_clear_memory(self) -> None:
        if not self.engine:
            return
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(
                self, "Clear memory",
                "Delete all remembered facts for this profile?") != QMessageBox.Yes:
            return
        facts = self.engine.memory.recall(self.engine.user_id, 0.0)
        for f in facts:
            self.engine.memory.forget(
                self.engine.user_id, f["category"], f["key"])
        self.ui.bridge.set_status.emit("Memory cleared.")

    def _security_export_data(self) -> None:
        if not self.engine:
            return
        import json
        uid = self.engine.user_id
        payload = {
            "profile": self.engine.memory.get_profile(uid),
            "facts": self.engine.memory.recall(uid, 0.0),
            "context": self.engine.memory.list_context(uid),
            "routines": self.engine.memory.list_routines(uid),
            "prefs": self.engine.memory.get_prefs(uid),
        }
        path, _ = QFileDialog.getSaveFileName(
            self, "Export my data", "atlas-export.json", "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        self.ui.bridge.set_status.emit(f"Exported → {Path(path).name}")

    def _build_hotkeys_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        for shortcut, desc in (
            ("Ctrl+Shift+S", "Screen capture"),
            ("Ctrl+Shift+H", "Toggle clipboard watch"),
            ("Ctrl+Shift+W", "Toggle screen watcher"),
            ("Ctrl+R", "Hot reload"),
            ("Ctrl+,", "Open settings"),
            ("Ctrl+L", "Clear chat"),
            ("Ctrl+E", "Export session"),
            ("Ctrl+Space / Alt+Space (hold)", "Push-to-talk"),
        ):
            PAL = _get_pal()
            row = QLabel(f"<b>{shortcut}</b> — {desc}")
            row.setStyleSheet(f"color: {PAL['text']}; padding: 4px;")
            lay.addWidget(row)
        lay.addStretch()
        self.tabs.addTab(w, "Hotkeys")

    def _build_teaching_tab(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        hdr = QLabel("TEACHING PERFORMANCE")
        hdr.setObjectName("section_hdr")
        lay.addWidget(hdr)
        hint = QLabel(
            "Session stats from guided walkthroughs and step verification. "
            "Type /diagnose in chat for a quick summary."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {_get_pal()['muted']}; font-size: 11px;")
        lay.addWidget(hint)
        self._teaching_summary = QLabel("Open this tab to refresh session stats.")
        self._teaching_summary.setWordWrap(True)
        self._teaching_summary.setAlignment(Qt.AlignTop)
        self._teaching_summary.setStyleSheet(
            f"color: {_get_pal()['text']}; font-size: 12px; line-height: 1.5;"
        )
        lay.addWidget(self._teaching_summary, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_teaching)
        lay.addWidget(refresh, 0, Qt.AlignRight)
        self.tabs.addTab(w, "Teaching")
        self.tabs.currentChanged.connect(
            lambda i: self._refresh_teaching() if self.tabs.tabText(i) == "Teaching" else None
        )

    def _refresh_teaching(self) -> None:
        if not hasattr(self, "_teaching_summary"):
            return
        eng = getattr(self, "engine", None)
        if eng is None or not getattr(eng, "learning", None):
            self._teaching_summary.setText("Teaching stats unavailable.")
            return
        summary = eng.learning.format_teaching_summary_for_user()
        diag = eng.learning.get_self_diagnosis()
        extra = (
            f"\n\nCorrection rate: {float(diag.get('correction_rate', 0)):.0%} · "
            f"Confusion rate: {float(diag.get('confusion_rate', 0)):.0%} · "
            f"Research lookups: {int(diag.get('research_lookups_performed', 0))}"
        )
        key = diag.get("task_key") or ""
        if key:
            extra += f"\nTask type key: {key}"
        self._teaching_summary.setText(summary + extra)

    def _on_model_changed(self, index: int) -> None:
        if not _CORE:
            return
        mid = self.model_combo.itemData(index)
        if mid:
            _core_mod.GROQ_MODEL = mid
            self.ui.bridge.set_status.emit(f"Model → {GROQ_MODEL_LABELS.get(mid, mid)}")

    def _on_style_changed(self, style: str) -> None:
        if self.engine:
            self.engine.session.response_style = style
            self.ui.bridge.set_status.emit(f"Style → {style}")

    def _refresh_skills(self) -> None:
        self.skills_list.clear()
        if not self.engine:
            return
        for sk in self.engine.skill_registry.list_skills():
            item = QListWidgetItem(f"{sk.get('display', sk.get('name'))} — {sk.get('description', '')}")
            item.setData(Qt.UserRole, sk.get("name"))
            self.skills_list.addItem(item)

    def _install_skill(self) -> None:
        if not self.engine:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Install Skill", "", "Python (*.py)")
        if not path:
            return
        ok, msg = self.engine.skill_registry.install_from_file(path)
        self.ui.bridge.set_status.emit(msg)
        self._refresh_skills()

    def _uninstall_skill(self) -> None:
        if not self.engine:
            return
        item = self.skills_list.currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        self.engine.skill_registry.uninstall(name)
        self._refresh_skills()

    def _refresh_memory(self) -> None:
        self.memory_table.setRowCount(0)
        if not self.engine:
            return
        facts = self.engine.memory.recall(self.engine.user_id)
        for fact in facts:
            row = self.memory_table.rowCount()
            self.memory_table.insertRow(row)
            self.memory_table.setItem(row, 0, QTableWidgetItem(str(fact.get("category", ""))))
            self.memory_table.setItem(row, 1, QTableWidgetItem(str(fact.get("key", ""))))
            self.memory_table.setItem(row, 2, QTableWidgetItem(str(fact.get("value", ""))))
            del_btn = QPushButton("Delete")
            cat, key = fact.get("category", ""), fact.get("key", "")
            del_btn.clicked.connect(
                lambda _=False, c=cat, k=key: self._delete_fact(c, k)
            )
            self.memory_table.setCellWidget(row, 3, del_btn)
        report = self.engine.learning.get_learning_report()
        self._memory_stats_lbl.setText(
            f"Facts: {report.get('total_facts', len(facts))} | "
            f"High confidence: {report.get('high_confidence_facts', 0)} | "
            f"Turns: {report.get('turn_count', 0)}"
        )

    def _delete_fact(self, category: str, key: str) -> None:
        if self.engine:
            self.engine.memory.forget(self.engine.user_id, category, key)
            self._refresh_memory()

    def _export_memory(self) -> None:
        if not self.engine:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Memory", "atlas_memory.json", "JSON (*.json)")
        if not path:
            return
        facts = self.engine.memory.recall(self.engine.user_id)
        Path(path).write_text(json.dumps(facts, indent=2), encoding="utf-8")

    def _clear_memory(self) -> None:
        if not self.engine:
            return
        for fact in self.engine.memory.recall(self.engine.user_id):
            self.engine.memory.forget(
                self.engine.user_id, fact.get("category", ""), fact.get("key", "")
            )
        self._refresh_memory()

