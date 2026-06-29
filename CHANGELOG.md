# Changelog

## Unreleased

### Structural wiring (daemon-only + unified memory)

- **Memory:** Session takeaways in `UserMemory` SQLite; `MemoryManager` is a deprecation shim; legacy JSON migrated once.
- **Runtime:** UI requires daemon (`AtlasStateProxy` only); embedded `StateEngine` reserved for tests (`tests/harness.py`).
- **Safety:** `DEFAULT_SAFETY_MODE = "off"` in `atlas_data`; reload on account switch; removed `atlas-hands://` and routine auto-approve bypasses.
- **Scheduler:** Split `/api/jobs/*` vs `/api/scheduler/*`; Settings Scheduler tab (pending, definitions, weekly routine, activity).
- **Connectors:** SSH in task/scheduler dispatch; connector status UI with Gmail/Notion stub honesty; weekly digest skips unconnected stubs.
- **Overlay:** DPI-aware rings/cursor, WCAG caption plate, top-edge caption flip, font fallback chain, multi-monitor geometry.
- **Polish:** Ambient context buffer 8s TTL; TTS gated on displayed text; routine recorder crop caps (50 events / 4MB); drift sampling start/mid/end.

### Earlier fixes

- Fixed Groq vision OCR crash when API returns null content (`WatchWorker`).
- Fixed SQLite `lastrowid` None crash on failed inserts.
- Fixed stream parser crash on empty Groq chunk choices.
- Added structured logging to stderr and `~/.atlas/atlas.log` with task correlation ids.
- Added global + thread exception hooks with user-visible error dialog.
- Added `StepEvent` orchestrator — single stream for cursor animation + narration sync.
- Added persistent animated `AgentCursorOverlay` (smooth moves, click pulse, no teleport).
- Removed permission gates for agent clicks/tasks; actions run directly.
- Added Safety Mode setting (off / always / trusted) in Security tab.
- Added `atlas_telemetry.py` — account status check (fail-open) + Send Feedback.
- Added daily license re-check timer and execution gate when blocked.
- GUIDE/DO coordinate scaling and NL intent routing retained from prior pass.
