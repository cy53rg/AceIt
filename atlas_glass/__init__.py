"""Atlas Glass — live meeting / interview copilot."""
from __future__ import annotations

from atlas_glass.profile_router import GlassProfile, ProfileRouter
from atlas_glass.rag import build_glass_context_block, summarize_with_groq
from atlas_glass.session import GlassSession
from atlas_mind.modes import ModeState


def is_glass_mode(mode: ModeState) -> bool:
    """True when Atlas is in live-conversation (interview/meeting) mode."""
    return mode == ModeState.INTERVIEW


GLASS_MODE = ModeState.INTERVIEW

__all__ = [
    "GLASS_MODE",
    "GlassProfile",
    "GlassSession",
    "ModeState",
    "ProfileRouter",
    "build_glass_context_block",
    "is_glass_mode",
    "summarize_with_groq",
]
