# Atlas — Cursor Execution Plan (v1)

**Repo:** [cy53rg/AceIt](https://github.com/cy53rg/AceIt)  
**Purpose:** Hand this file to Cursor at the start of each session. Implement **one phase at a time**. Do not skip ahead.  
**Companion doc:** `Atlas_Architecture_Blueprint.md` (full rationale) — read once for context; execute from **this** file.

---

## Answers you asked for (read first)

### Do I need to train Atlas myself before putting it out?

**No — not ML fine-tuning.** Atlas does not need a custom-trained model for v1.

What you *do* need (and can ship without “training”):

| Layer | What it is | You do this by… |
|-------|------------|-----------------|
| **Brain** | Groq (or other APIs) + system prompts | Already in `atlas_core.py`; improve prompts per mode |
| **Memory** | Facts, prefs, session takeaways | `atlas_memory.py` — grows from use |
| **Skills** | `SKILL.md` files (open standard) | Drop skills in `~/.atlas/skills/` or bundle in repo |
| **Playbooks** | Recorded workflows | `atlas_recorder.py` + `atlas_playbooks.py` |
| **Connectors** | OAuth tokens for Calendar/GitHub/etc. | User connects once in Settings |

**Personalization = memory + skills + playbooks + connector auth**, not training a model. Fine-tuning is optional years later; it is not a blocker to shipping.

### Do I need to give Cursor the other repos’ full codebases?

**Not required.** The blueprint already extracts what matters from Clicky, OpenClicky, Natively, and Skales.

**Recommended:**

- **Default:** Use this plan + blueprint + your AceIt repo only.
- **Optional:** Clone reference repos into `reference/` (add to `.gitignore`) when implementing a specific feature:
  - Skales → Goal engine, code mode, workflows
  - Natively → Glass dual-audio, RAG/session memory
  - OpenClicky → MCP/connector routing patterns

Cursor can read 1–3 key files from a reference repo when stuck; you do not need all four codebases in context every session.

### Groq as brain — and free alternatives

**Keep Groq as primary** (already wired, free tier, fast).

Add a **provider router** (Phase 2) with fallbacks:

| Role | Free / cheap now | When to use |
|------|------------------|-------------|
| Chat + tools | Groq (`openai/gpt-oss-120b`, `llama-3.3-70b`) | Default |
| Long docs / meetings | Google AI Studio **Gemini** (free tier, huge context) | Glass, big file Q&A |
| Offline | **Ollama** (local, no key) | Privacy / no network |
| Fallback diversity | **OpenRouter** `:free` models | When Groq rate-limits |
| Web search | **DuckDuckGo** (already in `atlas_research.py`) | Default, no key |
| STT | Groq Whisper | Already in `atlas_audio.py` |
| TTS | Kokoro (local) → ElevenLabs (optional) | Already in `atlas_audio.py` |

---

## Product thesis (one paragraph)

Atlas is **one assistant** with four behaviors — **Buddy** (cursor companion), **Glass** (live meeting/interview), **Do** (autonomous goals + screen work), **Connect** (calendar, GitHub, email, etc.) — sharing **one memory, one policy gate, one router** that picks the right behavior from normal conversation. User never picks “modes” manually unless they want to.

---

## Current codebase truth (Feb 2026)

**Works today:** Daemon + PySide6 UI, Groq chat/vision stream, `[[GUIDE]]`/`[[DO]]`/`[[TASK]]`, policy engine, FS read (home-scoped), web search (`atlas_research.py`), GitHub connector (with OAuth), SSH targets, scheduler cron, voice STT/TTS, playbooks/recorder.

**Broken or stubbed (fix in Phase 1):**

| Gap | Location |
|-----|----------|
| Gmail / Notion OAuth | `atlas_connectors/gmail.py`, `notion.py` — stubs |
| **No calendar at all** | New connector required |
| Scheduler SSH route | `atlas_apscheduler.py` — `ssh` not in registry |
| Scheduled UI tasks don’t run | `JobDispatcher` logs only, no `run_task()` |
| Intent routing is regex/heuristic | `atlas_core.py` `route_command` — not unified LLM router |
| “Open file on PC” | No global file index/search |
| `StateEngine` monolith | `atlas_core.py` ~4000 lines — hard to extend |

**Default assumptions** (you skipped the questionnaire):

- **UI:** Polish PySide6 first; Electron shell is Phase 6+, not now.
- **Audience:** Personal → small beta (safety + onboarding matter).
- **Connectors:** Direct OAuth default; Composio optional later.
- **Filesystem:** Search entire PC; open with policy prompts; writes in granted scopes.

---

## Architecture (keep what works)

```
Atlas Shell (atlas_ui.py)  ←→  HTTP + WebSocket  ←→  Atlas Core (atlas_daemon.py)
                                                              ├── Mind (router)      [NEW - split from core]
                                                              ├── Policy             [atlas_policy.py]
                                                              ├── Connect            [atlas_connectors/]
                                                              ├── Do                 [tasks, recorder, playbooks]
                                                              ├── Glass              [dual audio, interview]
                                                              ├── Vision             [atlas_vision.py]
                                                              ├── Memory             [atlas_memory.py + vector]
                                                              └── Scheduler          [atlas_apscheduler.py]
```

Do **not** rewrite policy, memory schema, or daemon IPC until Phase 0 is done.

---

## The router (most important new piece)

Every user message goes through this order. **Model tool-call wins over regex** when the provider supports tools.

```
1. Atlas Stack     → time, math, unit convert (zero API)
2. Direct answer   → simple Q&A, no tools
3. Glass context   → if live meeting session active
4. Single tool     → search web, read calendar, RAG, find file, connector read
5. Skill           → matched SKILL.md
6. Goal / Do       → multi-step autonomous work
7. Code mode       → folder-bound edits (future)
8. Computer-use    → pyautogui last resort
```

**Implementation target:** `atlas_mind/router.py` (new), called from `StateEngine.handle_input` before streaming chat.

**Tools the router must expose to the model (v1 minimum):**

| Tool | Example user request | Implementation |
|------|----------------------|----------------|
| `web_search` | “What’s the latest on X?” | `atlas_research.py` |
| `find_file` | “Open my resume PDF” | **NEW** `atlas_files/indexer.py` |
| `open_path` | “Open that file” | OS `os.startfile` / `subprocess` + policy |
| `calendar_create_event` | “Remind me Friday 3pm” | **NEW** Google Calendar connector |
| `calendar_list` | “What’s on my calendar?” | Same |
| `github_create_repo` | “Create a repo called X” | Extend `atlas_connectors/github.py` |
| `github_push` | “Push this folder to GitHub” | `atlas_shell.py` + `git` + policy |
| `schedule_job` | “Every Monday email me a summary” | `atlas_apscheduler.py` |
| `run_task` | “Click through this installer” | `StateEngine.run_task()` |
| `guide_user` | “Walk me through Photoshop” | `[[GUIDE]]` path |

---

## Phase plan (execute in order)

### Phase 0 — Stabilize & split core (no new features)

**Goal:** Same behavior, testable modules. Daemon API unchanged.

**Tasks:**

1. Extract from `atlas_core.py` into packages (move, don’t rewrite logic):
   - `atlas_mind/` — streaming chat, chunk filters (`HarmonyStreamFilter` from `atlas_recorder.py`)
   - `atlas_do/` — task loop, `AtlasHands`, action tokens
   - `atlas_glass/` — interview mode hooks (thin for now)
2. Fix known wiring bugs:
   - Scheduler SSH: register SSH as connector **or** special-case in `JobDispatcher`
   - Scheduled tasks: `JobDispatcher` must call `StateEngine.run_task()` for `route: task`
   - GitHub weekly job: pass `repo` param or read from user prefs
3. All existing tests pass.

**Cursor prompt:**

```text
Read docs/ATLAS_CURSOR_EXECUTION_PLAN.md Phase 0 only.
Split atlas_core.py into atlas_mind/ and atlas_do/ without changing daemon API or behavior.
Fix scheduler SSH routing and scheduled task execution per the plan.
Show file layout before moving code. Run pytest when done.
```

**Done when:** `pytest` green; daemon + UI work as before.

---

### Phase 1 — Intent router + tool calling

**Goal:** User says “remind me Tuesday” or “create a GitHub repo” → correct tool runs without slash commands.

**Tasks:**

1. Add `atlas_mind/router.py` with Groq tool-calling (use models that support tools).
2. Wire tools: `web_search`, `schedule_job`, `github_*` (existing connector), `run_task`.
3. Regex `route_command` becomes **fallback only** when tool call unavailable.
4. Every tool invocation goes through `atlas_policy.py`.

**Cursor prompt:**

```text
Read docs/ATLAS_CURSOR_EXECUTION_PLAN.md Phase 1 only.
Implement atlas_mind/router.py with Groq tool-calling for web_search, schedule_job,
github actions, and run_task. Policy-gate all writes. Keep regex route_command as fallback.
Add tests for router tool selection.
```

**Done when:** Natural language triggers calendar reminder (stub OK if Calendar is Phase 2), web search, and GitHub issue create in tests.

---

### Phase 2 — Atlas Connect: Calendar + finish GitHub

**Goal:** Real reminders and GitHub repo lifecycle from conversation.

**Tasks:**

1. **Google Calendar connector** (`atlas_connectors/google_calendar.py`):
   - OAuth (reuse token store pattern from GitHub)
   - `list_events`, `create_event`, `delete_event` with policy risk tags
2. **Extend GitHub connector:**
   - `create_repo`, `list_repos`, `push` (via shell `git` in policy-gated `atlas_shell.py`)
3. Router tools map to connector actions.
4. Settings UI: connect Calendar, pick default repo.

**Free APIs:** Google Calendar API (your OAuth app, free quota).

**Cursor prompt:**

```text
Read docs/ATLAS_CURSOR_EXECUTION_PLAN.md Phase 2 only.
Implement google_calendar connector with OAuth, wire router tools, extend github connector
for create_repo and git push via atlas_shell. Update Settings connector tab.
```

**Done when:** “Remind me tomorrow at 9am to call John” creates a calendar event; “create repo foo and push this folder” works with confirmation.

---

### Phase 3 — Full PC file find & open

**Goal:** “Open my tax return from 2023” works without knowing the path.

**Tasks:**

1. `atlas_files/indexer.py` — background index of user-chosen roots (default: all drives or `%USERPROFILE%` + Desktop/Documents/Downloads).
2. SQLite `file_index` table: path, name, ext, mtime, optional content hash.
3. Tools: `find_file(query)`, `open_path(path)`, `read_file(path)` (policy READ).
4. Windows: `os.startfile`, `explorer /select,`.
5. Optional: Everything SDK or Windows Search API later for speed.

**Policy:** Indexing is local-only. Opening executables = `WRITE_SENSITIVE` or DENY by default.

**Cursor prompt:**

```text
Read docs/ATLAS_CURSOR_EXECUTION_PLAN.md Phase 3 only.
Add file indexer + find_file/open_path tools, policy-gated. Wire to router.
Settings: choose index roots, rebuild index button.
```

**Done when:** Natural language finds and opens a known file on disk.

---

### Phase 4 — Atlas Glass (meeting / interview)

**Goal:** Dual-channel audio, domain-aware answers, saved meeting memory.

**Tasks:**

1. Harden `atlas_audio.py` dual-channel (mic vs speaker) — already started.
2. `atlas_glass/session.py` — session state, profile router (coding vs behavioral vs general).
3. Meeting rows in SQLite (`meetings`, `meeting_chunks`) per blueprint §9.
4. UI: Glass mode toggle, meeting dashboard (can be simple list first).

**Reference:** Natively patterns in blueprint §4.2 — optional clone for dual-audio UX only.

**Done when:** 30-min session produces searchable transcript + summary.

---

### Phase 5 — Atlas Do (goals + workflows)

**Goal:** Background multi-step goals; recorded workflows replay.

**Tasks:**

1. `atlas_do/goal_engine.py` — resumable goals, step limit, killswitch (global hotkey).
2. Unify `atlas_playbooks.py` + `atlas_recorder.py` under one workflow plan JSON.
3. `SKILL.md` loader (`atlas_skills.py` extend or `skills_registry.py`).
4. Audit log table for “what Atlas did while you were away.”

**Reference:** Skales `/goal` behavior — optional clone `reference/skales` for goal state machine.

**Done when:** `/goal` or natural language goal survives UI restart; killswitch stops all actions.

---

### Phase 6 — Connect polish + provider router

**Tasks:**

1. Finish Gmail + Notion direct OAuth (or Composio opt-in bridge).
2. `ProviderRouter` — Groq primary, Gemini fallback, Ollama offline.
3. Onboarding wizard: API keys, mic permission, connector connect, index roots.
4. Installer / auto-update (optional for beta).

---

### Phase 7 — Shell upgrade (optional) ✅

Electron + React shell in `atlas_shell/` — talks to `atlas_daemon` over HTTP/WebSocket. PySide6 remains the default full UI until parity.

**Done when:** `scripts/start_atlas_shell.ps1` launches chat + Focus mode against a running daemon.

---

## Connector design (Atlas Connect)

Keep existing pattern:

```python
@connector_action("create_event", RiskClass.WRITE_SCOPED, "Create calendar event")
def create_event(self, title, start, end, ...): ...
```

**Priority connectors for v1:**

1. Google Calendar (reminders, schedule)
2. GitHub (repos, issues, push via git)
3. Gmail (read/send)
4. Notion (notes/tasks)
5. Composio bridge (optional fast path) — Phase 6

---

## Security rules (non-negotiable)

1. Every write/shell/connector action → `atlas_policy.py`.
2. Typed confirm for `IRREVERSIBLE` / `FINANCIAL` / dangerous shell.
3. Global killswitch stops goals + TTS + tasks immediately.
4. Speaker audio = context only, never user commands (already fixed).
5. Settings toggles must change real code paths (add tests).

---

## Ideas beyond what you listed

| Idea | Why |
|------|-----|
| Pre-meeting brief | Calendar + memory + Glass auto-load context before call |
| “What Atlas did while away” | Audit log feed in UI |
| Weekly local recap | Scheduler + memory summary, no cloud |
| Cross-app SKILL.md import | Skills from Cursor/Claude Code work unchanged |
| Smart file open | Index + “open the thing I was working on yesterday” via mtime + memory |
| Voice-first goals | “Hey Atlas, every Sunday summarize my week” → scheduler + Gmail |

---

## What to paste into Cursor (each session)

```text
Read docs/ATLAS_CURSOR_EXECUTION_PLAN.md and execute Phase N only (I will specify N).
Read the listed source files before editing. Do not start later phases.
Match existing code style. Policy-gate all side effects. Run pytest when done.
```

---

## Open decisions (override in chat if needed)

1. Index entire PC vs user profile only for file search  
2. Composio on by default or OAuth-only  
3. When to start Electron shell vs stay on PySide6  
4. macOS port timing (Windows first is correct)  
5. Public beta vs personal use (affects onboarding depth)

---

## Reference links

| Project | URL | Atlas takes |
|---------|-----|-------------|
| AceIt (yours) | https://github.com/cy53rg/AceIt | Base |
| Clicky | https://github.com/farzaa/clicky | Pointing tags |
| OpenClicky | https://github.com/jasonkneen/openclicky | Router, MCP, honesty |
| Natively | https://github.com/Natively-AI-assistant/natively-cluely-ai-assistant | Glass, RAG |
| Skales | https://github.com/skalesapp/skales | Do mode, goals |

---

*Last updated: 2026-06-01 — aligned with AceIt `main` and Atlas_Architecture_Blueprint.md*
