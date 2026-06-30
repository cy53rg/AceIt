"""
atlas_recap.py — Local weekly recap from audit log, goals, meetings, scheduler activity.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any


def build_weekly_recap(memory: Any, user_id: int, *, days: int = 7) -> str:
    """Plain-text recap of what Atlas did in the last N days."""
    since = time.time() - (max(1, days) * 86400)
    lines = ["**Your week with Atlas**", ""]

    audit = [
        row for row in memory.audit_list(user_id, limit=80)
        if float(row.get("created") or 0) >= since
    ]
    lines.append("**Actions Atlas took**")
    if audit:
        for row in audit[:12]:
            ts = datetime.fromtimestamp(float(row.get("created") or 0)).strftime("%a %H:%M")
            cat = str(row.get("category") or "general")
            summary = str(row.get("summary") or "")
            lines.append(f"- [{ts}] ({cat}) {summary}")
    else:
        lines.append("- No autonomous actions logged yet.")

    goals = memory.goal_list(user_id, limit=15)
    recent_goals = [
        g for g in goals
        if float(g.get("updated") or g.get("created") or 0) >= since
    ]
    lines.append("")
    lines.append("**Goals**")
    if recent_goals:
        for g in recent_goals[:6]:
            status = str(g.get("status") or "")
            text = str(g.get("goal_text") or g.get("text") or "")[:120]
            lines.append(f"- [{status}] {text}")
    else:
        lines.append("- No goals started this week.")

    meetings = [
        m for m in memory.glass_list_meetings(user_id, limit=20)
        if float(m.get("started_at") or 0) >= since
    ]
    lines.append("")
    lines.append("**Glass sessions**")
    if meetings:
        for m in meetings[:5]:
            title = str(m.get("title") or "Session")
            summary = str(m.get("summary") or "").strip()
            if summary:
                lines.append(f"- {title}: {summary[:200]}")
            else:
                lines.append(f"- {title}")
    else:
        lines.append("- No Glass sessions recorded.")

    try:
        activity = memory.list_scheduler_activity(user_id, since=since, limit=20)
    except Exception:
        activity = []
    lines.append("")
    lines.append("**Scheduled jobs**")
    if activity:
        for row in activity[:8]:
            lines.append(f"- {row.get('summary', '')}")
    else:
        lines.append("- No scheduler activity.")

    return "\n".join(lines).strip()
