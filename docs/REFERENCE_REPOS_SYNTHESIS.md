# Reference Repos — Synthesis for Atlas

Patterns extracted from [Clicky](https://github.com/farzaa/clicky), [OpenClicky](https://github.com/jasonkneen/openclicky), [Natively Cluely](https://github.com/Natively-AI-assistant/natively-cluely-ai-assistant), and [Skales](https://github.com/skalesapp/skales). Use this when implementing Atlas features — no need to keep all four repos in Cursor context.

---

## Cross-cutting themes

| Theme | What works | Atlas mapping |
|-------|------------|---------------|
| **Tiered routing** | Fast deterministic path → LLM tool-call → full chat | `atlas_mind/stack_router.py` → `atlas_mind/router.py` → `StateEngine._on_ai_query` |
| **Model tool-call wins** | Regex/heuristics are fallback only when tools unavailable | OpenClicky lesson — implemented in Phase 1 router |
| **Policy before action** | Writes never trust model prose | `atlas_policy.py` on every tool/connector |
| **SKILL.md standard** | Portable skills with YAML frontmatter | `atlas_skills.py` + `~/.atlas/skills/` |
| **Daemon + thin UI** | Brain runs headless; UI is display + input | `atlas_daemon.py` + `atlas_ui.py` (already Atlas) |
| **Dual audio** | Mic and system audio never merged | `atlas_audio.py` AudioWatcher (Natively pattern) |
| **Resumable goals** | Checkpointed multi-step agent with killswitch | Phase 5 `atlas_do/goal_engine.py` |

---

## Clicky — Buddy / cursor companion

**What makes it feel good**

- Push-to-talk + screenshot in one atomic turn (screen context always fresh).
- `[POINT:x,y:label:screenN]` tags in model output → overlay draws cursor/labels.
- Streaming text + TTS in parallel; user sees pointer move while Atlas speaks.
- Minimal chrome overlay; stays out of the way.

**Atlas implementation targets**

| Pattern | File | Phase |
|---------|------|-------|
| Point tags in vision output | `atlas_vision.py` | Buddy polish |
| `BuddyCursorOverlay` in UI | `atlas_ui.py` | Buddy polish |
| PTT + capture single turn | `atlas_audio.py` + `handle_input(source="capture")` | Buddy polish |

---

## OpenClicky — Connectors + routing

**What makes it work**

- **Router order:** regex fast-path → **Groq tool-call (wins)** → streaming chat.
- Composio MCP as opt-in connector plane (not required for v1).
- `SKILL.md` discovery alongside built-in tools.
- Local bridge server (`:32123`) for desktop actions — Atlas uses daemon HTTP instead.
- **Toggle must match code path** — feature flags wired to the same branch the router uses.

**Atlas implementation targets**

| Pattern | File | Status |
|---------|------|--------|
| Unified LLM router | `atlas_mind/router.py` | Phase 1 |
| Tool: web_search, schedule, github, run_task | `atlas_mind/router.py` | Phase 1 |
| SKILL.md loader | `atlas_skills.py` | Phase 1+ |
| Composio MCP | optional later | Phase 6+ |

---

## Natively — Glass / meetings

**What makes it work**

- **Dual-channel audio:** mic transcript ≠ speaker/system transcript; never merged blindly.
- Profile Intelligence Router picks interview vs meeting vs copilot behavior.
- Local RAG (`sqlite-vec`) for session docs + meeting history.
- BYOK provider planes (Groq, Gemini, etc.) with one memory layer.
- Stealth/minimal UI during live sessions.

**Atlas implementation targets**

| Pattern | File | Status |
|---------|------|--------|
| Dual audio (mic vs speaker) | `atlas_audio.py` | Done |
| Glass session + profile router | `atlas_glass/session.py`, `profile_router.py` | Phase 4 |
| Local RAG for meetings | `atlas_glass/rag.py` | Phase 4 |
| Provider router (Groq → Gemini fallback) | `atlas_mind/providers.py` | Phase 2+ |

---

## Skales — Do / autonomous work

**What makes it work**

- `/goal` resumable agent with checkpoints and killswitch.
- **Skales Stack** — zero-API answers for time, math, units before LLM.
- Workflows as unified plan JSON (steps + connectors + code edits).
- Code mode: `edit_file` in a bound folder with policy.
- `SKILL.md` registry shared with router.

**Note:** Public Skales GitHub is partial; goal engine source not fully OSS. Use docs + killswitch/checkpoint modules as reference.

**Atlas implementation targets**

| Pattern | File | Status |
|---------|------|--------|
| Stack fast path | `atlas_mind/stack_router.py` | Phase 1 |
| Goal engine + killswitch | `atlas_do/goal_engine.py` | Phase 5 |
| Workflow JSON | `atlas_do/workflows.py` | Phase 5 |
| Code mode `edit_file` | `atlas_do/code_mode.py` | Phase 5 |

---

## Router order (locked for Atlas)

```
1. Control commands     → route_command / try_handle_command (stop, focus, /read)
2. Atlas Stack          → stack_router (time, math — zero API)
3. Mind router          → Groq tool-call (web, schedule, github, run_task, guide)
4. Skills               → SKILL.md / Python skills
5. Full chat stream     → Groq with memory + vision
6. Regex screen actions → route_command _detect_screen_action (fallback)
```

**Rule:** When Groq returns a tool call, execute it. Regex is fallback when tools fail or API unavailable.

---

## What Atlas already has (don't rewrite)

- Policy engine (`atlas_policy.py`)
- Memory + facts (`atlas_memory.py`)
- Daemon IPC (`atlas_daemon.py`, `atlas_state_proxy.py`)
- GitHub connector + scheduler (`atlas_connectors/`, `atlas_apscheduler.py`)
- Voice STT/TTS + echo filter (`atlas_audio.py`)
- Playbooks + recorder (`atlas_playbooks.py`, `atlas_recorder.py`)

---

## Clone reference repos (optional)

```bash
mkdir -p reference && cd reference
git clone --depth 1 https://github.com/jasonkneen/openclicky.git
git clone --depth 1 https://github.com/farzaa/clicky.git
# Add reference/ to .gitignore
```

Clone only when implementing a specific feature (e.g. OpenClicky router while building Phase 1).
