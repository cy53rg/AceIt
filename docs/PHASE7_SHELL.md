# Atlas Electron shell (Phase 7)

Optional **Electron + React** UI that talks to the same `atlas_daemon` brain as PySide6.

PySide6 (`python atlas_ui.py`) remains the full-featured desktop UI. The Electron shell is a lighter chat + Focus/Glass control surface for users who prefer a web-tech shell.

## Prerequisites

- Node.js 20+ and npm
- Atlas daemon running: `python -m atlas_daemon`
- `.env` with `GROQ_API_KEY` (same as main Atlas)

## Quick start

```powershell
# Terminal 1 — brain
python -m atlas_daemon

# Terminal 2 — Electron shell
powershell -ExecutionPolicy Bypass -File scripts\start_atlas_shell.ps1
```

Or manually:

```powershell
cd atlas_shell
npm install
npm run dev
```

## Architecture

```
┌─────────────────────┐     HTTP + WS      ┌──────────────────┐
│  atlas_shell        │ ◄────────────────► │  atlas_daemon    │
│  (Electron/React)   │   localhost:17847 │  (StateEngine)   │
└─────────────────────┘                    └──────────────────┘

┌─────────────────────┐     HTTP + WS      ┌──────────────────┐
│  atlas_ui.py        │ ◄────────────────► │  atlas_daemon    │
│  (PySide6 — full)   │                    │                  │
└─────────────────────┘                    └──────────────────┘
```

## Features (v1)

- Chat with streaming via WebSocket (`chunk` / `complete`)
- Focus / Glass mode toggle
- **Settings drawer** — interview brief, auto-answer, connectors, safety mode, file index roots
- **Clipboard highlight** — toggle or `Ctrl+Shift+H`; sends `source=highlight` (Glass fast path when Focus is on)
- **Hold to talk** — browser speech recognition → `source=mic` (when supported)
- Cancel query + global killswitch
- Policy prompts (permission, safety, task confirm) over WebSocket
- Session + Glass status from daemon API

## Production build

```powershell
cd atlas_shell
npm run build
npm start
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `VITE_ATLAS_DAEMON_URL` | `http://127.0.0.1:17847` | Daemon base URL (build-time) |
| `ATLAS_DAEMON_PORT` | `17847` | Must match daemon |

## Parity gaps (use PySide6 for now)

- Screen overlay / guide markers
- Floating orb / point-and-talk
- Full PySide6 settings (SSH, scheduler tabs, etc.)
- Native Groq Whisper mic path (shell uses Web Speech API when available)
