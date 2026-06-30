"""Phase 7 — Electron + React shell scaffolding."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "atlas_shell"


def test_shell_package_exists():
    pkg = SHELL / "package.json"
    assert pkg.is_file()
    text = pkg.read_text(encoding="utf-8")
    assert "electron" in text
    assert "react" in text


def test_shell_entrypoints():
    required = [
        SHELL / "electron" / "main.mjs",
        SHELL / "electron" / "preload.mjs",
        SHELL / "src" / "App.tsx",
        SHELL / "src" / "api" / "daemon.ts",
        SHELL / "src" / "components" / "SettingsPanel.tsx",
        SHELL / "src" / "hooks" / "useClipboardHighlight.ts",
        SHELL / "src" / "hooks" / "usePushToTalk.ts",
        SHELL / "index.html",
        SHELL / "vite.config.ts",
    ]
    for path in required:
        assert path.is_file(), f"missing {path}"


def test_start_script_exists():
    script = ROOT / "scripts" / "start_atlas_shell.ps1"
    assert script.is_file()
    assert "npm run dev" in script.read_text(encoding="utf-8")


def test_daemon_has_cors_for_electron_shell():
    src = (ROOT / "atlas_daemon.py").read_text(encoding="utf-8")
    assert "CORSMiddleware" in src


@pytest.mark.parametrize(
    "name,needle",
    [
        ("daemon client", "handle_input"),
        ("websocket", "DaemonSocket"),
        ("focus toggle", "set_focus_mode"),
        ("killswitch", "killswitch"),
        ("settings panel", "SettingsPanel"),
        ("highlight", "useClipboardHighlight"),
        ("interview brief", "glass_interview_brief"),
    ],
)
def test_react_app_wires_daemon(name, needle):
    app = (SHELL / "src" / "App.tsx").read_text(encoding="utf-8")
    api = (SHELL / "src" / "api" / "daemon.ts").read_text(encoding="utf-8")
    settings = (SHELL / "src" / "components" / "SettingsPanel.tsx").read_text(encoding="utf-8")
    combined = app + api + settings
    assert needle in combined, f"{needle} not found for {name}"
