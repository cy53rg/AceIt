"""
atlas_glass/rag.py — Retrieve meeting transcript context for Glass turns.
"""
from __future__ import annotations

import time
from typing import Any


def build_glass_context_block(
    memory: Any,
    user_id: int,
    *,
    meeting_id: int | None,
    query: str = "",
    recent_limit: int = 24,
    search_top_k: int = 6,
    speaker_context: str = "",
) -> str:
    """Format live + retrieved meeting text for prompt injection."""
    lines: list[str] = [
        "<GlassSession>",
        "Live session context (mic = user, speaker = others on the call):",
    ]

    if meeting_id is not None:
        recent = memory.glass_recent_chunks(meeting_id, limit=recent_limit)
        for chunk in recent[-recent_limit:]:
            src = str(chunk.get("source") or "user")
            text = str(chunk.get("text") or "").strip()
            if text:
                lines.append(f"  [{src}] {text[:500]}")

    if speaker_context:
        lines.append(f"[Recent system audio] {speaker_context[:1200]}")

    if query:
        hits = memory.glass_search_chunks(
            user_id,
            query,
            top_k=search_top_k,
            meeting_id=meeting_id,
        )
        if hits:
            lines.append("Relevant prior meeting notes:")
            for hit in hits:
                title = hit.get("title") or "Meeting"
                text = str(hit.get("text") or "")[:320]
                lines.append(f"  • ({title}) {text}")

    meeting = None
    if meeting_id is not None:
        meeting = memory.glass_get_meeting(user_id, meeting_id)
    if meeting and meeting.get("profile"):
        lines.append(f"Active profile: {meeting['profile']}")

    lines.append("</GlassSession>")
    if len(lines) <= 3:
        return ""
    return "\n".join(lines)


def summarize_transcript(chunks: list[dict[str, Any]], *, max_chars: int = 12000) -> str:
    """Build a plain-text transcript for summarization."""
    parts: list[str] = []
    for chunk in chunks:
        src = chunk.get("source", "user")
        text = str(chunk.get("text") or "").strip()
        if text:
            parts.append(f"[{src}] {text}")
    body = "\n".join(parts)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…"
    return body


def summarize_with_groq(transcript: str, *, client: Any = None) -> str:
    """Optional Groq summary; falls back to excerpt."""
    text = (transcript or "").strip()
    if not text:
        return "No transcript captured."
    if len(text) < 400:
        return text
    if client is None:
        try:
            from atlas_core import groq_client as client
        except Exception:
            client = None
    if client is None:
        return text[:2000] + ("…" if len(text) > 2000 else "")
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize this meeting/interview transcript in 5-8 bullet points. "
                        "Include decisions, questions asked, and follow-ups."
                    ),
                },
                {"role": "user", "content": text[:10000]},
            ],
            temperature=0.2,
            max_tokens=500,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or text[:2000]
    except Exception:
        return text[:2000] + "…"
