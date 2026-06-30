"""
atlas_glass/profile_router.py — Domain-aware profile for live sessions.

Picks coding vs behavioral vs general/meeting context from recent speech.
"""
from __future__ import annotations

import re
from typing import Literal

GlassProfile = Literal["coding", "behavioral", "general", "meeting"]

_CODING = (
    "algorithm", "complexity", "big o", "leetcode", "python", "javascript",
    "typescript", "react", "api", "database", "sql", "system design",
    "architecture", "debug", "compile", "runtime", "function", "class",
    "inheritance", "recursion", "data structure", "binary search", "docker",
    "kubernetes", "aws", "code", "implement", "refactor",
)

_BEHAVIORAL = (
    "tell me about a time", "describe a situation", "weakness", "strength",
    "conflict", "disagreement", "teamwork", "leadership", "failure",
    "mistake", "proudest", "why should we hire", "why do you want",
    "where do you see yourself", "salary", "culture fit",
)

_MEETING = (
    "agenda", "action item", "follow up", "stakeholder", "quarterly",
    "roadmap", "sync", "standup", "retro", "minutes", "decision",
)


class ProfileRouter:
    """Lightweight keyword router — no ML required."""

    def classify(self, text: str, current: GlassProfile = "general") -> GlassProfile:
        t = (text or "").lower()
        if not t:
            return current
        if any(k in t for k in _CODING):
            return "coding"
        if any(k in t for k in _BEHAVIORAL):
            return "behavioral"
        if any(k in t for k in _MEETING):
            return "meeting"
        if re.search(r"\b(interview|whiteboard|coding round)\b", t):
            return "coding"
        return current or "general"

    @staticmethod
    def prompt_suffix(profile: GlassProfile) -> str:
        if profile == "coding":
            return (
                "GLASS PROFILE: Technical / coding interview.\n"
                "Give crisp talking points, tradeoffs, and complexity notes. "
                "Prefer bullet structure. No filler."
            )
        if profile == "behavioral":
            return (
                "GLASS PROFILE: Behavioral interview.\n"
                "Use STAR briefly (Situation, Task, Action, Result). "
                "Sound natural, not scripted. One story at a time."
            )
        if profile == "meeting":
            return (
                "GLASS PROFILE: Live meeting.\n"
                "Track decisions and action items. Answer what's being asked on the call. "
                "Be concise — the user is multitasking."
            )
        return (
            "GLASS PROFILE: General live conversation.\n"
            "Be terse and actionable. The user may be on a call or in focus mode."
        )
