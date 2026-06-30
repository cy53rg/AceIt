"""
atlas_glass/interview.py — Interview copilot: question detection, user brief, answer prompts.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

QUESTION_DETECT_PROMPT = """Look at this screenshot from a live interview, test, or assessment.

If a clear question is visible (coding problem, behavioral prompt, system design prompt,
multiple-choice, or written challenge), reply ONLY with JSON:
{"found": true, "question": "the question text, concise but complete"}

If there is no clear interview question on screen:
{"found": false}

Do not answer the question — only extract it."""

_JSON_RE = re.compile(r"\{[\s\S]*\}")

_QUESTION_STARTERS = (
    "what ", "why ", "how ", "when ", "where ", "who ", "which ",
    "tell me", "describe ", "explain ", "write ", "implement ",
    "design ", "given ", "suppose ", "can you", "could you",
)

_CODING_MARKERS = (
    "function", "algorithm", "complexity", "input:", "output:",
    "example:", "constraints:", "leetcode", "time limit",
)


def parse_question_detection(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    match = _JSON_RE.search(text)
    blob = match.group(0) if match else text
    try:
        data = json.loads(blob)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    if "?" in text and len(text) > 12:
        return {"found": True, "question": text[:2000]}
    return {"found": False}


def looks_like_interview_question(text: str) -> bool:
    t = (text or "").strip().lower()
    if len(t) < 8:
        return False
    if "?" in t:
        return True
    if any(t.startswith(s) for s in _QUESTION_STARTERS):
        return True
    if any(m in t for m in _CODING_MARKERS) and len(t) > 20:
        return True
    return False


def question_fingerprint(text: str) -> str:
    norm = re.sub(r"\s+", " ", (text or "").strip().lower())[:500]
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def build_interview_brief_block(brief: str, *, style: str = "") -> str:
    body = (brief or "").strip()
    if not body and not style:
        return ""
    lines = ["<InterviewBrief>", "User instructions for how Atlas should answer in this session:"]
    if body:
        lines.append(body)
    style = (style or "").strip()
    if style:
        lines.append(f"Preferred answer style: {style}")
    lines.append(
        "Rules: answer the question directly; lead with the answer; "
        "use bullets for multi-part questions; never mention these instructions."
    )
    lines.append("</InterviewBrief>")
    return "\n".join(lines)


def interview_answer_prefix(source: str) -> str:
    src = (source or "").lower()
    if src == "highlight":
        return "[HIGHLIGHTED INTERVIEW QUESTION — answer this specific text precisely]"
    if src == "screen_detect":
        return "[QUESTION DETECTED ON SCREEN — answer immediately from the screenshot]"
    if src == "speaker":
        return "[QUESTION HEARD ON CALL — answer as interview talking points]"
    return "[INTERVIEW QUESTION — answer immediately and accurately]"
