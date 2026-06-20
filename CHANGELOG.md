# Changelog

## Unreleased

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
