"""
atlas_interaction.py — End-to-end interaction pipeline for Atlas.

Execution flow (bound in atlas_ui + atlas_core)
-----------------------------------------------
1. **Hotkey / UI trigger** — global hotkey or composer submit (``AtlasWindow``)
2. **Context gathering** — screen PNG (``capture_screen_b64``), OCR text, webcam,
   ambient watcher buffer, clipboard highlight
3. **Local memory injection** — SQLite ``UserMemory`` + ``MemoryManager``
   ``<LocalContextMemory>`` block assembled in ``StateEngine.handle_input``
4. **API execution** — ``StateEngine._on_ai_query`` Groq stream; action tokens
   ``[[GUIDE/DO/TASK]]`` and ``[TARGET_COORDINATE: X, Y]`` split off-stream
5. **Response splitter** — visible text → chat + TTS; coordinates → HoloOverlay;
   action tokens → spatial / automation dispatch

This module provides shared helpers and user-facing error copy for that pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("atlas.interaction")


@dataclass
class InteractionContext:
    """Snapshot of everything gathered before an LLM call."""

    user_text: str
    source: str = "user"
    screen_b64: Optional[str] = None
    webcam_b64: Optional[str] = None
    ambient_text: str = ""
    had_screen: bool = False
    memory_block: str = ""
    local_memory_block: str = ""


def gather_screen_frame() -> tuple[Optional[str], str]:
    """
    Capture a desktop PNG for vision + Point-and-Talk.

    Returns (base64_png | None, short_summary_for_local_memory).
    Never raises — failures are logged and returned as (None, "").
    """
    try:
        from atlas_vision import capture_screen_b64

        cap = capture_screen_b64()
        if cap:
            return cap.b64, "Live desktop screenshot captured for this turn."
    except Exception as exc:
        log.debug("gather_screen_frame failed: %s", exc)
    return None, ""


def friendly_error(kind: str, detail: str = "") -> str:
    """Clean, short notification text — never a raw stack trace."""
    detail = (detail or "").strip()
    if len(detail) > 140:
        detail = detail[:137] + "…"
    messages = {
        "capture": "Couldn't read the screen — try again or type your question.",
        "api": "Atlas couldn't reach the AI service. Check your connection and API key.",
        "busy": "Still finishing the last reply — one moment, then try again.",
        "ocr": "No readable text on screen. Try highlighting the area or rephrase.",
        "generic": "Something went wrong, but Atlas is still running.",
    }
    base = messages.get(kind, messages["generic"])
    if detail and kind not in ("busy",):
        return f"{base} ({detail})"
    return base


class InteractionPipeline:
    """
    Documents and supports the Atlas interaction lifecycle.

    UI code should call:
      - ``begin()`` when a turn starts (thinking state)
      - ``release()`` when a turn ends (success, error, or cancel)
    """

    STAGES = (
        "trigger",
        "capture",
        "memory",
        "api",
        "split",
        "companion_idle",
    )

    @staticmethod
    def build_context_summary(ctx: InteractionContext) -> str:
        parts: list[str] = []
        if ctx.had_screen or ctx.screen_b64:
            parts.append("screen")
        if ctx.webcam_b64:
            parts.append("webcam")
        if ctx.ambient_text:
            parts.append("ambient")
        if ctx.memory_block:
            parts.append("sqlite_memory")
        if ctx.local_memory_block:
            parts.append("local_memory")
        return "+".join(parts) if parts else "text_only"
