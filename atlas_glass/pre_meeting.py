"""
atlas_glass/pre_meeting.py — Calendar + memory context before Glass / Focus sessions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("atlas_glass.pre_meeting")


def _parse_event_start(raw: str) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10 and text[4] == "-":
            dt = datetime.fromisoformat(text)
            return dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt
    except ValueError:
        return None


def _upcoming_calendar_event(engine: Any, *, window_minutes: int = 45) -> Optional[dict[str, Any]]:
    reg = getattr(engine, "connectors", None)
    if reg is None:
        return None
    try:
        cal = reg.get("google_calendar")
        if cal is None or not cal.is_connected():
            return None
    except Exception:
        return None

    now = datetime.now(timezone.utc)
    time_max = (now + timedelta(minutes=max(5, window_minutes))).isoformat()
    try:
        result = reg.execute(
            "google_calendar",
            "list_events",
            safety_mode=getattr(engine, "safety_mode", "off"),
            max_results=5,
            time_max=time_max,
        )
    except Exception as exc:
        log.debug("pre-meeting calendar lookup failed: %s", exc)
        return None

    if not result.get("ok"):
        return None
    events = (result.get("result") or {}).get("events") or []
    horizon = now + timedelta(minutes=window_minutes)
    for ev in events:
        start = _parse_event_start(str(ev.get("start") or ""))
        if start is None:
            continue
        start_utc = start.astimezone(timezone.utc)
        if now - timedelta(minutes=5) <= start_utc <= horizon:
            return ev
    return None


def _prior_meeting_notes(memory: Any, user_id: int, title: str) -> list[str]:
    needle = (title or "").strip().lower()
    if not needle:
        return []
    notes: list[str] = []
    for meeting in memory.glass_list_meetings(user_id, limit=20):
        mt = str(meeting.get("title") or "").lower()
        if needle not in mt and mt not in needle:
            continue
        summary = str(meeting.get("summary") or "").strip()
        if summary:
            notes.append(summary[:600])
        if len(notes) >= 2:
            break
    return notes


def fetch_pre_meeting_context(engine: Any, *, window_minutes: int = 45) -> dict[str, Any]:
    """Return {title, event_title, brief, event} for the next calendar block."""
    user_id = int(getattr(engine, "user_id", 0) or 0)
    memory = getattr(engine, "memory", None)
    event = _upcoming_calendar_event(engine, window_minutes=window_minutes)
    event_title = str((event or {}).get("summary") or "").strip()
    title = event_title or "Focus session"

    lines = ["<PreMeetingBrief>", "Context for this session:"]
    if event_title:
        start_raw = str((event or {}).get("start") or "")
        lines.append(f"Upcoming: {event_title} ({start_raw})")
    else:
        lines.append("No calendar event in the next few minutes — general focus session.")

    interview_brief = ""
    try:
        interview_brief = str(engine.get_glass_interview_brief() or "").strip()
    except Exception:
        pass
    if interview_brief:
        lines.append(f"Your answer style: {interview_brief[:800]}")

    if memory is not None and event_title:
        for note in _prior_meeting_notes(memory, user_id, event_title):
            lines.append(f"Prior notes: {note}")

    lines.append("</PreMeetingBrief>")
    brief = "\n".join(lines)
    return {
        "title": title,
        "event_title": event_title,
        "brief": brief,
        "event": event,
    }


def build_pre_meeting_brief_block(brief: str) -> str:
    return (brief or "").strip()
