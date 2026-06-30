# Atlas — Manual Test Checklist (Phases 0–7)

Use this after install. Report any step that fails with: **phase number**, **step**, **what you expected**, **what happened**.

## Before you start

```powershell
cd "c:\Users\USER\OneDrive\Desktop\AceIt_Project"
powershell -File scripts\install_atlas.ps1
```

1. Copy `.env.example` → `.env` and set `GROQ_API_KEY`.
2. Install **Tesseract** (see `INSTALL_WINDOWS.txt` if present).
3. Optional: `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` for Calendar/Gmail; `OPENROUTER_API_KEY` for Groq fallback.

**Start stack (every session):**

```powershell
python -m atlas_daemon          # terminal 1 — leave running
python atlas_ui.py              # terminal 2 — PySide6 UI (default)
# OR
powershell -File scripts\start_atlas_shell.ps1   # Electron shell
```

Automated sanity check: `python -m pytest -q`

---

## Phase 0 — Stabilize & split core

| # | Test | Expected |
|---|------|----------|
| 0.1 | Daemon starts without error | `http://127.0.0.1:17847/health` returns OK |
| 0.2 | UI connects to daemon | Chat loads; no “daemon not running” error |
| 0.3 | Basic chat | Ask “What is 2+2?” — streamed reply appears |
| 0.4 | Restart daemon, reopen UI | Session reconnects; prefs preserved |
| 0.5 | Data location | `%LOCALAPPDATA%\Atlas\` contains SQLite DB |

---

## Phase 1 — Intent router + tools

| # | Test | Expected |
|---|------|----------|
| 1.1 | Calendar intent | “What’s on my calendar?” (needs Google Calendar connected) — tool runs or clear “not connected” |
| 1.2 | GitHub intent | “List my GitHub repos” (needs GitHub connected) |
| 1.3 | File search | “Find my resume PDF” — searches indexed folders |
| 1.4 | Shell command | `/shell echo hello` — output in chat (safety mode dependent) |
| 1.5 | Stop / cancel | Start a long reply, say “stop” — generation stops |

---

## Phase 2 — Calendar + GitHub connectors

| # | Test | Expected |
|---|------|----------|
| 2.1 | Settings → Connected Accounts → Google Calendar | OAuth completes; status shows connected |
| 2.2 | “Create a calendar event tomorrow at 3pm called Atlas test” | Event appears in Google Calendar |
| 2.3 | “What’s on my calendar this week?” | Lists real events |
| 2.4 | GitHub connect | OAuth or token flow works |
| 2.5 | “Create a repo called atlas-smoke-test” | Repo created (or policy prompt) |

---

## Phase 3 — PC file find & open

| # | Test | Expected |
|---|------|----------|
| 3.1 | Settings → Filesystem → add index root (e.g. Documents) | Root saved |
| 3.2 | “Find files named budget” | Returns paths under your roots |
| 3.3 | “Open [path from result]” | File opens in default app |
| 3.4 | Safety mode “Always” | Write/delete actions ask for confirmation |

---

## Phase 4 — Atlas Glass (meetings)

| # | Test | Expected |
|---|------|----------|
| 4.1 | Toggle **Focus** (or say “focus mode”) | Glass session starts; status shows active |
| 4.2 | Speak or type while Focus on | Transcript chunks stored; replies use meeting context |
| 4.3 | Turn Focus off | Session ends; summary generated |
| 4.4 | Settings → Glass tab | Dual-audio / profile options visible |
| 4.5 | Prior meeting | Start Focus again — prior summaries retrievable in context |

---

## Interview Glass (extension)

| # | Test | Expected |
|---|------|----------|
| IV.1 | Settings → Glass → **Interview brief** | Save text like “Senior SWE, concise, STAR” |
| IV.2 | Focus on + screen watch | Written question on screen → Atlas answers in your style |
| IV.3 | Highlight text + hotkey (Ctrl+Shift+H) | Selected text sent; interview-style answer |
| IV.4 | Auto-answer toggle | Heard question triggers answer without extra prompt |

---

## Phase 5 — Atlas Do (goals + workflows)

| # | Test | Expected |
|---|------|----------|
| 5.1 | `/goal Organize my Downloads folder` | Goal starts; step-by-step plan |
| 5.2 | `/goal resume` | Resumes paused goal |
| 5.3 | `/kill` or killswitch in UI | Autonomous actions stop immediately |
| 5.4 | Settings → Activity tab | Audit log shows goal/automation entries |
| 5.5 | Playbook / routine | “Watch me” → do steps → “save routine as X” → “run routine X” |

---

## Phase 6 — Connect polish + provider router

| # | Test | Expected |
|---|------|----------|
| 6.1 | Gmail connect (Settings → Connected Accounts) | OAuth; list inbox via chat |
| 6.2 | Notion connect (OAuth or `NOTION_TOKEN`) | Search pages via chat |
| 6.3 | Onboarding wizard (first run) | Walks API key + connectors; can dismiss |
| 6.4 | Provider fallback | Temporarily remove `GROQ_API_KEY`, set `OPENROUTER_API_KEY` — chat still works |
| 6.5 | Composio (optional) | With `COMPOSIO_API_KEY`, extra tools register |

---

## Phase 7 — Electron shell

| # | Test | Expected |
|---|------|----------|
| 7.1 | `scripts\start_atlas_shell.ps1` | Electron window opens; daemon health green |
| 7.2 | Chat in shell | Same replies as PySide6 UI |
| 7.3 | Settings drawer | Interview brief, safety, index roots, connectors |
| 7.4 | Ctrl+Shift+H highlight | Highlight in any app → question to Atlas |
| 7.5 | Hold-to-talk (mic button) | Web Speech → text sent as `source=mic` |
| 7.6 | Focus toggle in shell | Glass session syncs with daemon |

---

## Post-plan additions (this session)

| # | Test | Expected |
|---|------|----------|
| P.1 | **Pre-meeting brief** | Connect Calendar; create event starting in ~10 min; enable Focus — brief appears (event title + context) |
| P.2 | **Activity log** | Settings → Activity → Refresh — shows audit entries after a `/goal` |
| P.3 | **Weekly recap** | Type `/recap` or Settings → Activity → “Generate weekly recap” |
| P.4 | **OpenRouter** | Groq key removed, OpenRouter key set — chat uses fallback (check logs) |
| P.5 | **Installer** | `scripts\install_atlas.ps1` completes; venv + deps OK |
| P.6 | Electron Activity section | Settings drawer shows audit list + weekly recap button |

---

## Scheduler & weekly routine

| # | Test | Expected |
|---|------|----------|
| S.1 | Settings → Scheduler | Pending approvals, cron jobs visible |
| S.2 | Configure weekly routine (e.g. Mon 8:00) | Job saved |
| S.3 | Wait for digest or trigger manually | Weekly digest message in chat (includes recap section) |

---

## Voice & vision (cross-cutting)

| # | Test | Expected |
|---|------|----------|
| V.1 | Push-to-talk / mic in PySide6 | Speech transcribed; Atlas replies |
| V.2 | “What’s on my screen?” | Screenshot analyzed (vision model) |
| V.3 | Guided mode `[[GUIDE]]` or guide intent | Overlay marker on target |

---

## Feedback template

Copy and fill for each issue:

```
Phase: 
Step #: 
Expected: 
Actual: 
Logs/screenshot: 
```

---

## Quick smoke (15 min)

If short on time, run only: **0.1–0.3**, **4.1–4.2**, **IV.1–IV.3**, **5.1**, **5.4**, **6.1**, **7.1–7.4**, **P.2–P.3**.
