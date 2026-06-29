"""
atlas_recorder.py — Demonstration recording and stream action-token filtering.

Extracted from atlas_core.py (RoutineRecorder + StreamBracketFilter + action token patterns).
"""
from __future__ import annotations

import io
import re
import threading
import time
from typing import Optional

from atlas_logging import get_logger
from atlas_vision import base64_encode, extract_target_coordinate

log = get_logger("recorder")


class ActionTokenPatterns:
    """Regex patterns for inline [[GUIDE/DO/TASK/RESEARCH:…]] action tokens."""

    #   [[GUIDE: target_name | instruction text | expected_state (optional)]]
    #   [[DO:    target_name | action_type]]
    _ACTION_TOKEN_RE = re.compile(
        r"\[\[\s*(GUIDE|DO)\s*:\s*([^|\]]+?)\s*\|\s*([^|\]]*?)"
        r"(?:\s*\|\s*([^\]]*?))?\s*\]\]",
        re.IGNORECASE,
    )
    #   [[TASK: full multi-step goal in plain language]]
    _TASK_TOKEN_RE = re.compile(
        r"\[\[\s*TASK\s*:\s*(.+?)\s*\]\]",
        re.IGNORECASE | re.DOTALL,
    )
    #   [[RESEARCH: search query in plain language]]
    _RESEARCH_TOKEN_RE = re.compile(
        r"\[\[\s*RESEARCH\s*:\s*(.+?)\s*\]\]",
        re.IGNORECASE | re.DOTALL,
    )


class RoutineRecorder:
    """
    Captures a user demonstration (clicks + keystrokes) for Learn-and-Execute.

    Uses ``pynput`` to observe global mouse/keyboard input.  Each click stores a
    small screenshot crop around the cursor so the demonstration can later be
    *generalised* (by the vision model) into UI targets that are located fresh
    at replay time — robust to layout/resolution changes, unlike brittle
    absolute coordinates.  Printable keystrokes are coalesced into ``type``
    events; modifier combos become ``hotkey`` events.
    """

    _CROP_W, _CROP_H = 380, 240
    _DOUBLE_CLICK_S = 0.40
    _MODS = {
        "ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr",
        "cmd", "cmd_l", "cmd_r", "shift", "shift_l", "shift_r",
    }

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._text_buf: list[str] = []
        self._mouse = None
        self._kbd = None
        self._active = False
        self._lock = threading.Lock()
        self._mods: set[str] = set()
        self._last_click = (0.0, 0, 0)

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> bool:
        try:
            from pynput import mouse, keyboard
        except Exception as exc:
            log.warning("pynput unavailable; cannot record demonstration: %s", exc)
            return False
        if self._active:
            return True
        self.events.clear()
        self._text_buf.clear()
        self._mods.clear()
        self._last_click = (0.0, 0, 0)
        self._mouse = mouse.Listener(on_click=self._on_click)
        self._kbd = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._mouse.start()
        self._kbd.start()
        self._active = True
        return True

    def stop(self, drop_last_click: bool = True) -> list[dict]:
        self._active = False
        self._flush_text()
        for listener in (self._mouse, self._kbd):
            try:
                if listener:
                    listener.stop()
            except Exception:
                pass
        self._mouse = self._kbd = None
        # The user's final action is usually clicking Atlas's Stop button — drop
        # that trailing click so it doesn't become a replay step.
        if drop_last_click and self.events and self.events[-1].get("type") == "click":
            self.events.pop()
        return list(self.events)

    # ── input handlers ─────────────────────────────────────────────────────────

    def _flush_text(self) -> None:
        with self._lock:
            if self._text_buf:
                text = "".join(self._text_buf)
                self._text_buf.clear()
                if text.strip():
                    self.events.append({"type": "type", "text": text})

    def _on_click(self, x, y, button, pressed) -> None:
        if not pressed or not self._active:
            return
        self._flush_text()
        now = time.time()
        lt, lx, ly = self._last_click
        double = (now - lt < self._DOUBLE_CLICK_S
                  and abs(x - lx) < 6 and abs(y - ly) < 6)
        self._last_click = (now, x, y)
        self.events.append({
            "type": "click", "x": int(x), "y": int(y),
            "button": getattr(button, "name", "left"),
            "double": bool(double), "crop_b64": self._grab_crop(x, y),
        })

    def _grab_crop(self, x, y) -> Optional[str]:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            left = max(0, int(x) - self._CROP_W // 2)
            top = max(0, int(y) - self._CROP_H // 2)
            right = min(img.width, left + self._CROP_W)
            bottom = min(img.height, top + self._CROP_H)
            crop = img.crop((left, top, right, bottom))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            return base64_encode(buf.getvalue())
        except Exception:
            return None

    @staticmethod
    def _key_name(key) -> str:
        char = getattr(key, "char", None)
        if char is not None:
            return char
        return (getattr(key, "name", None) or str(key).replace("Key.", "")).strip()

    @staticmethod
    def _norm_mod(name: str) -> str:
        return name.replace("_l", "").replace("_r", "").replace("_gr", "")

    def _on_press(self, key) -> None:
        if not self._active:
            return
        name = self._key_name(key)
        if not name:
            return
        if name in self._MODS:
            self._mods.add(self._norm_mod(name))
            return
        base = self._norm_mod(name)
        active_mods = {m for m in self._mods if m != "shift"}
        if active_mods:   # ctrl/alt/cmd held → a shortcut, not typing
            self._flush_text()
            self.events.append({"type": "hotkey",
                                "keys": sorted(active_mods) + [base]})
            return
        if len(name) == 1:           # printable character
            with self._lock:
                self._text_buf.append(name)
            return
        if base == "space":
            with self._lock:
                self._text_buf.append(" ")
            return
        if base == "backspace":
            with self._lock:
                if self._text_buf:
                    self._text_buf.pop()
                    return
        self._flush_text()
        self.events.append({"type": "key", "key": base})

    def _on_release(self, key) -> None:
        name = self._key_name(key)
        if name in self._MODS:
            self._mods.discard(self._norm_mod(name))


class StreamBracketFilter:
    """
    Incrementally strips ``[[GUIDE/DO/TASK/RESEARCH:…]]`` action tokens out of a
    streaming LLM response so they never reach the chat view or the TTS engine,
    while surfacing each completed token exactly once for the action dispatcher.

    Plain double-brackets that are NOT a known action schema (e.g. ``list[[0]]``
    in a code answer) are passed through untouched.

    Usage
    -----
        visible, tokens = filt.feed(delta)   # per stream chunk
        tail            = filt.flush()       # at stream end
    """

    _PREFIX_RE = re.compile(
        r"\[\[\s*(?:GUIDE|DO|TASK|RESEARCH)\s*:", re.IGNORECASE,
    )

    def __init__(self) -> None:
        self._buf = ""
        self._in_token = False
        self.incomplete_action_token: str | None = None

    @staticmethod
    def _maybe_prefix(buf: str) -> bool:
        """True while ``buf`` (starting with '[[') could still grow into a token."""
        s = buf[2:].lstrip().lower()
        if s == "":
            return True
        return any(
            kw.startswith(s)
            for kw in ("guide:", "do:", "task:", "research:")
        )

    def feed(self, delta: str) -> tuple[str, list[str]]:
        self._buf += delta
        visible = ""
        tokens: list[str] = []
        while self._buf:
            if self._in_token:
                j = self._buf.find("]]")
                if j == -1:
                    break  # token still streaming — hold
                tokens.append(self._buf[: j + 2])
                self._buf = self._buf[j + 2:]
                self._in_token = False
                continue
            i = self._buf.find("[[")
            if i == -1:
                # Hold a lone trailing '[' in case it becomes '[[' next chunk.
                if self._buf.endswith("["):
                    visible += self._buf[:-1]
                    self._buf = self._buf[-1:]
                else:
                    visible += self._buf
                    self._buf = ""
                break
            if i > 0:
                visible += self._buf[:i]
                self._buf = self._buf[i:]
            # self._buf now starts with "[["
            if self._PREFIX_RE.match(self._buf):
                self._in_token = True
                continue
            if self._maybe_prefix(self._buf):
                break  # not enough chars yet to decide — wait for more
            visible += self._buf[:2]      # ordinary "[[" — pass through
            self._buf = self._buf[2:]
        return visible, tokens

    def flush(self) -> tuple[str, list[str]]:
        """Return leftover visible text and any complete token held at stream end."""
        tokens: list[str] = []
        self.incomplete_action_token = None
        if self._in_token:
            j = self._buf.find("]]")
            if j != -1:
                tokens.append(self._buf[: j + 2])
                visible = self._buf[j + 2 :]
            else:
                self.incomplete_action_token = self._buf
                visible = ""
        else:
            visible = self._buf
        self._buf = ""
        self._in_token = False
        return visible, tokens


class StreamCoordinateFilter:
    """
    Incrementally strips trailing ``[TARGET_COORDINATE: X, Y]`` tags from streamed
    LLM output so they never reach chat or TTS, while preserving partial tags at
    chunk boundaries until the stream completes.
    """

    _PARTIAL_TAIL_RE = re.compile(r"\[TARGET_COORDINATE[^\]]*$", re.IGNORECASE)

    def __init__(self) -> None:
        self._buf = ""
        self._coords: dict | None = None

    def feed(self, delta: str) -> str:
        self._buf += delta
        # Hold an incomplete tag suffix across chunks.
        hold = self._PARTIAL_TAIL_RE.search(self._buf)
        if hold:
            emit = self._buf[: hold.start()]
            self._buf = self._buf[hold.start() :]
        else:
            emit = self._buf
            self._buf = ""
        # Strip any fully-formed tag that arrived in this slice.
        clean, coord = extract_target_coordinate(emit)
        if coord:
            self._coords = coord
        return clean

    def flush(self) -> tuple[str, dict | None]:
        """Return leftover visible text and any captured coordinate tag."""
        clean, coord = extract_target_coordinate(self._buf)
        if coord:
            self._coords = coord
        self._buf = ""
        captured = self._coords
        self._coords = None
        return clean, captured


# Backward-compatible alias used by atlas_core during migration.
_StreamBracketFilter = StreamBracketFilter


class HarmonyStreamFilter:
    """
    Strip GPT-OSS / harmony *analysis* channel tokens from streamed text.

    Models like ``openai/gpt-oss-120b`` emit an internal reasoning channel before
    the user-facing *final* channel.  This filter keeps only final-channel prose
    for chat display and TTS.
    """

    _ANALYSIS_RE = re.compile(
        r"<\|start\|>assistant<\|channel\|>analysis",
        re.IGNORECASE,
    )
    _FINAL_RE = re.compile(
        r"<\|start\|>assistant<\|channel\|>final",
        re.IGNORECASE,
    )
    _CONTROL_TOKEN_RE = re.compile(r"<\|[^|]+\|>")

    def __init__(self) -> None:
        self._buf = ""
        self._emitting = True

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        self._buf += delta
        out: list[str] = []

        while self._buf:
            if not self._emitting:
                final = self._FINAL_RE.search(self._buf)
                if not final:
                    self._buf = self._buf[-80:]
                    break
                self._buf = self._buf[final.end():]
                if self._buf.startswith("<|message|>"):
                    self._buf = self._buf[len("<|message|>") :]
                self._emitting = True
                continue

            analysis = self._ANALYSIS_RE.search(self._buf)
            if analysis:
                prefix = self._buf[: analysis.start()]
                if prefix:
                    cleaned = self._clean(prefix)
                    if cleaned:
                        out.append(cleaned)
                self._buf = self._buf[analysis.end() :]
                self._emitting = False
                continue

            hold = 0
            for i in range(min(64, len(self._buf)), 0, -1):
                tail = self._buf[-i:]
                if "<|" in tail and "|>" not in tail[tail.rfind("<|") :]:
                    hold = i
                    break
            emit_len = len(self._buf) - hold
            if emit_len <= 0:
                break
            chunk = self._buf[:emit_len]
            self._buf = self._buf[emit_len:]
            cleaned = self._clean(chunk)
            if cleaned:
                out.append(cleaned)

        return "".join(out)

    def flush(self) -> str:
        if not self._emitting:
            self._buf = ""
            return ""
        rest = self._clean(self._buf)
        self._buf = ""
        return rest

    @classmethod
    def _clean(cls, text: str) -> str:
        return cls._CONTROL_TOKEN_RE.sub("", text or "")


__all__ = [
    "ActionTokenPatterns",
    "HarmonyStreamFilter",
    "RoutineRecorder",
    "StreamBracketFilter",
    "StreamCoordinateFilter",
    "_StreamBracketFilter",
]
