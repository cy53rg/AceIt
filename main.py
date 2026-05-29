"""
AceIt Co-Pilot — Phase 4  (all fixes + Interview Mode)
═══════════════════════════════════════════════════════════
Fixes in this build
───────────────────
UI / Layout
  ✓ Window 480×940 — response box is now dominant element
  ✓ Controls panel uses reliable wrapper-frame toggle (no pack_slaves index)
  ✓ Controls default CLOSED so response area is immediately visible
  ✓ Darker palette — BASE #060606, SURFACE #0d0d0d, PANEL #111111
  ✓ Response font size 11, padx/pady enlarged for readability
  ✓ Mode pill in header tracks mode colour (gold/blue/orange)

Audio
  ✓ soundfile imported at module level with HAS_SF guard
  ✓ _record_loop uses queue + InputStream callback — reliable stop
  ✓ WASAPI loopback: broader detection (name-match AND exclusive-flag fallback)
  ✓ Audio section shows live device names on startup
  ✓ "List Devices" debug button added to audio section
  ✓ Min transcript length filter (< 4 words skipped) prevents trivial AI calls
  ✓ Interview Mode — full dual-source coaching (see below)

Session Memory
  ✓ build_messages keeps: [system] + [first anchor] + [last N] — preserves
    goal/topic context even after many turns
  ✓ Mode-specific system prompts injected on session start / mode switch
  ✓ Interview mode injects specialist coaching prompt
  ✓ Session summary now includes token-estimate

Interview Mode (new)
  ✓ Toggle in AUDIO section — activates dual-source coaching
  ✓ SPEAKER audio → AI gives 3 talking-point answer suggestions
  ✓ MIC audio → AI critiques user's response, suggests improvements
  ✓ Auto-starts session; injects InterviewCoach system prompt
  ✓ Live transcript feed shows speaker vs mic in separate colours
"""

from __future__ import annotations

import collections
import difflib
import hashlib
import io
import queue
import threading
import time
import sys
import os
import subprocess
import tkinter as tk
from tkinter import ttk, scrolledtext
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Deque, List, Optional

import keyboard
import pyautogui
import pyperclip
import pytesseract
from PIL import ImageGrab
from dotenv import load_dotenv

load_dotenv()

_tess = os.getenv("TESSERACT_CMD", "")
if _tess:
    pytesseract.pytesseract.tesseract_cmd = _tess

from groq import Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
groq_client  = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL   = os.getenv("ACEIT_MODEL", "llama-3.3-70b-versatile")

# ── Audio imports ─────────────────────────────────────────────────────────────
try:
    import numpy as np
    import sounddevice as sd
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False
    np = None
    sd = None

try:
    import soundfile as sf
    HAS_SF = True
except ImportError:
    HAS_SF = False
    sf = None

# ─────────────────────────────────────────────────────────────────────────────
# THEME ENGINE  — Dark (goldish-black) + Light
# ─────────────────────────────────────────────────────────────────────────────
THEMES = {
    # ── Dark: Elite Goldish-Black ─────────────────────────────────────────────
    "dark": {
        "BASE":       "#050505",   # Obsidian Black  — root / outermost bg
        "SURFACE":    "#0F0F0F",   # Card structures — mode bar, panels
        "PANEL":      "#141414",   # Control panels  — settings body, resp box
        "RAISED":     "#1A1A1A",   # Elevated tiles  — entry fields, icon btns
        "BORDER":     "#262626",   # Hairline dividers
        "ACCENT":     "#D4AF37",   # Metallic Gold   — primary CTAs, headings
        "ACCENT_HI":  "#F0CF6A",   # Hover Gold      — button hover state
        "ACCENT_DIM": "#6B5A28",   # Muted Gold      — subtle highlights
        "ACCENT_SUB": "#1E190A",   # Dark Gold tint  — active tab bg, pill bg
        "TEXT_MAIN":  "#EAEAEA",   # Crisp body text
        "MUTED":      "#808080",   # Secondary / placeholder text
        "DIM":        "#2E2E2E",   # Disabled / very faint elements
    },
    # ── Light: Minimal ────────────────────────────────────────────────────────
    "light": {
        "BASE":       "#F5F5F7",   # Clean light grey — root bg
        "SURFACE":    "#FFFFFF",   # Pure white cards — mode bar, panels
        "PANEL":      "#F0F0F0",   # Slightly inset panels
        "RAISED":     "#E5E5E5",   # Elevated tiles  — entry fields, icon btns
        "BORDER":     "#D1D1D1",   # Hairline dividers
        "ACCENT":     "#B8860B",   # Dark Goldenrod  — primary CTAs, headings
        "ACCENT_HI":  "#C9960C",   # Slightly warmer — hover state
        "ACCENT_DIM": "#8B6914",   # Mid-tone gold   — subtle highlights
        "ACCENT_SUB": "#FFF8DC",   # Cornsilk tint   — active tab bg, pill bg
        "TEXT_MAIN":  "#111111",   # Deep near-black text
        "MUTED":      "#666666",   # Secondary / placeholder
        "DIM":        "#C0C0C0",   # Disabled / very faint elements
    },
}

# Live colour references — start dark, updated by AceItApp._apply_theme()
BASE      = THEMES["dark"]["BASE"]
SURFACE   = THEMES["dark"]["SURFACE"]
PANEL     = THEMES["dark"]["PANEL"]
RAISED    = THEMES["dark"]["RAISED"]
BORDER    = THEMES["dark"]["BORDER"]
GOLD      = THEMES["dark"]["ACCENT"]
GOLD_HI   = THEMES["dark"]["ACCENT_HI"]
GOLD_DIM  = THEMES["dark"]["ACCENT_DIM"]
GOLD_SUB  = THEMES["dark"]["ACCENT_SUB"]
CREAM     = THEMES["dark"]["TEXT_MAIN"]
MUTED     = THEMES["dark"]["MUTED"]
DIM       = THEMES["dark"]["DIM"]

# Semantic / status colours (theme-invariant)
GREEN    = "#4caf7d"   # success / session active
CYAN     = "#3ab5c4"   # watcher / speaker
AMBER    = "#e8a020"   # warnings
RED      = "#b84040"   # danger / stop
MIC_CLR  = "#9b59e6"   # purple — microphone
SPK_CLR  = "#3ab5c4"   # cyan  — speaker loopback
IVEW_CLR = "#e07040"   # orange — interview mode

FONT_UI   = ("Segoe UI", 10)
FONT_MONO = ("Consolas",  11)   # enlarged for readability
FONT_SM   = ("Segoe UI",  9)
FONT_TINY = ("Segoe UI",  8)

SENSITIVITY = {
    "Low":    {"sim": 0.60, "chars": 80},
    "Medium": {"sim": 0.75, "chars": 30},
    "High":   {"sim": 0.88, "chars": 10},
}

MIN_TRANSCRIPT_WORDS = 4   # ignore fragments shorter than this


# ─────────────────────────────────────────────────────────────────────────────
# MODE STATE
# ─────────────────────────────────────────────────────────────────────────────
class ModeState(Enum):
    ACTIVE   = auto()
    AMBIENT  = auto()
    GUIDED   = auto()

MODE_COLORS = {
    ModeState.ACTIVE:  GOLD,
    ModeState.AMBIENT: CYAN,
    ModeState.GUIDED:  IVEW_CLR,
}

MODE_SYSTEMS = {
    ModeState.ACTIVE: (
        "You are AceIt in Active Mode — a razor-sharp real-time task assistant. "
        "Immediately solve any questions, debug errors, or complete explicit tasks "
        "visible on screen. Be direct, thorough, and fast. "
        "Use numbered steps for multi-step problems."
    ),
    ModeState.AMBIENT: (
        "You are AceIt in Ambient Mode — a silent, observant co-pilot. "
        "Only speak when you notice something genuinely actionable: a mistake, "
        "efficiency tip, or important pattern. "
        "If nothing is noteworthy, respond with exactly: NOTHING\n"
        "Otherwise prefix with '💡 Tip:' / '⚠️ Notice:' and use at most 2 sentences."
    ),
    ModeState.GUIDED: (
        "You are AceIt in Guided Mode — an expert interactive mentor. "
        "Guide the user step-by-step. Break every explanation into numbered steps. "
        "After each response, ask one focused follow-up question. Never skip steps. "
        "Acknowledge completed milestones."
    ),
}

INTERVIEW_SYSTEM = (
    "You are AceIt in Interview Coach Mode. You receive transcripts from two sources:\n"
    "[SPEAKER] = the interviewer speaking\n"
    "[MIC]     = the user (interviewee) speaking\n\n"
    "When you receive [SPEAKER] content:\n"
    "  • If it contains a question: provide 3 concise bullet-point talking points "
    "the user should hit, then a 2-sentence model answer example.\n"
    "  • If it's context/statement: note it briefly and suggest how to build on it.\n\n"
    "When you receive [MIC] content:\n"
    "  • Evaluate the response in 1 sentence (what was strong).\n"
    "  • Suggest 1-2 specific improvements or stronger phrasings.\n"
    "  • Keep feedback constructive, brief, and actionable.\n\n"
    "Be fast — the conversation is live. Lead every response with the source label."
)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION MANAGER  — smarter context window
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ContextEntry:
    role:      str
    content:   str
    source:    str
    timestamp: float = field(default_factory=time.time)
    pinned:    bool  = False   # pinned entries always included in build

    def to_chat_message(self) -> dict:
        role   = "assistant" if self.role == "assistant" else "user"
        prefix = f"[{self.source.upper()}] " if self.role not in ("user", "assistant") else ""
        return {"role": role, "content": f"{prefix}{self.content}"}


class SessionManager:
    """
    Ephemeral session buffer with smart context window.

    build_messages strategy:
      [system]  → always
      [first anchor message]  → always (preserves goal / topic)
      [recent N entries]       → sliding window of latest context
      [pinned entries]         → key facts user explicitly set

    This means context coherence is maintained even at 100+ turns.
    """
    MAX_BUFFER   = 200
    RECENT_TURNS = 24   # last N messages fed to API (covers ~12 exchanges)
    MAX_AGE_H    = 2    # expire entries older than 2 hours
    DEFAULT_SYSTEM = (
        "You are AceIt, a real-time AI study and work assistant on the user's desktop. "
        "Answer questions directly and completely. When you see exam questions or problems, "
        "solve them step-by-step. Be concise but thorough."
    )

    def __init__(self):
        self._buffer:  Deque[ContextEntry] = collections.deque(maxlen=self.MAX_BUFFER)
        self._system   = self.DEFAULT_SYSTEM
        self._active   = False
        self._started: Optional[float] = None
        self._lock     = threading.Lock()

    def start(self, system: str = "") -> None:
        with self._lock:
            self._buffer.clear()
            self._system  = system or self.DEFAULT_SYSTEM
            self._active  = True
            self._started = time.time()

    def end(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._active  = False
            self._started = None

    def set_system(self, system: str) -> None:
        """Hot-swap system prompt (e.g. on mode change)."""
        with self._lock:
            self._system = system

    def add(self, entry: ContextEntry) -> None:
        if not self._active:
            return
        with self._lock:
            cutoff = time.time() - self.MAX_AGE_H * 3600
            while self._buffer and self._buffer[0].timestamp < cutoff:
                self._buffer.popleft()
            self._buffer.append(entry)

    def add_user(self, content: str, source: str = "user") -> None:
        self.add(ContextEntry(role="user", content=content, source=source))

    def add_ai(self, content: str) -> None:
        self.add(ContextEntry(role="assistant", content=content, source="ai"))

    def build_messages(self) -> List[dict]:
        """Smart context window: system + first anchor + recent turns + pinned."""
        msgs = [{"role": "system", "content": self._system}]
        with self._lock:
            buf = list(self._buffer)
        if not buf:
            return msgs
        # Always include first entry (topic anchor)
        msgs.append(buf[0].to_chat_message())
        # Pinned entries
        for e in buf[1:]:
            if e.pinned:
                msgs.append(e.to_chat_message())
        # Recent window (deduplicate with pinned / anchor)
        anchor_content = buf[0].content
        recent = buf[-self.RECENT_TURNS:]
        for e in recent:
            m = e.to_chat_message()
            # Skip if content duplicates anchor or already pinned-added
            if e.content != anchor_content and not e.pinned:
                msgs.append(m)
        return msgs

    def pin_last(self) -> None:
        """Pin the most recent entry so it's always included in context."""
        with self._lock:
            if self._buffer:
                self._buffer[-1].pinned = True

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def entry_count(self) -> int:
        return len(self._buffer)

    @property
    def summary(self) -> str:
        if not self._active:
            return "No session"
        age = int(time.time() - (self._started or time.time()))
        m, s = divmod(age, 60)
        # Rough token estimate: average 6 chars/token
        approx_tokens = sum(len(e.content) for e in self._buffer) // 6
        return f"{m:02d}:{s:02d}  ·  {self.entry_count} entries  ·  ~{approx_tokens} tok"


# ─────────────────────────────────────────────────────────────────────────────
# STATE ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class StateEngine:
    ACTIVE_DEDUP_S     = 5
    AMBIENT_COOLDOWN_S = 30
    AMBIENT_MIN_CHARS  = 60
    GUIDED_MAX_TURNS   = 60

    def __init__(self, raw_query_fn: Callable[[List[dict]], None]):
        self._query    = raw_query_fn
        self.session   = SessionManager()
        self._mode     = ModeState.ACTIVE
        self._lock     = threading.Lock()
        self._listeners: List[Callable] = []
        self._active_last_text  = ""
        self._active_last_time  = 0.0
        self._ambient_last_fire = 0.0
        self._ambient_last_text = ""
        self.guided_turns = 0
        self.guided_topic = ""

    def set_mode(self, mode: ModeState) -> None:
        with self._lock:
            if mode == self._mode:
                return
            old = self._mode
            self._mode = mode
            self.guided_turns = 0
        # Update session system prompt for the new mode
        self.session.set_system(MODE_SYSTEMS[mode])
        self._emit("mode_changed", {"from": old.name, "to": mode.name})

    def handle_input(self, text: str, source: str = "ask") -> str:
        text = text.strip()
        if not text:
            return "suppressed:empty"
        if not self.session.is_active:
            self.session.start(MODE_SYSTEMS[self._mode])
        with self._lock:
            mode = self._mode
        if mode == ModeState.ACTIVE:
            return self._active(text, source)
        elif mode == ModeState.AMBIENT:
            return self._ambient(text, source)
        else:
            return self._guided(text, source)

    def store_ai_response(self, answer: str) -> None:
        self.session.add_ai(answer)

    def _active(self, text, source):
        now = time.time()
        if text == self._active_last_text and (now - self._active_last_time) < self.ACTIVE_DEDUP_S:
            return "suppressed:duplicate"
        self._active_last_text = text
        self._active_last_time = now
        self.session.add_user(text, source)
        self._query(self.session.build_messages())
        self._emit("fired", {"mode": "active", "source": source})
        return "fired"

    def _ambient(self, text, source):
        if source == "ask":
            self.session.add_user(text, source)
            self._query(self.session.build_messages())
            return "fired"
        now = time.time()
        if (now - self._ambient_last_fire) < self.AMBIENT_COOLDOWN_S:
            rem = int(self.AMBIENT_COOLDOWN_S - (now - self._ambient_last_fire))
            self._emit("suppressed", {"reason": "cooldown", "remaining": rem})
            return "suppressed:cooldown"
        old_lines = set(self._ambient_last_text.splitlines())
        new_chars  = sum(len(l) for l in (set(text.splitlines()) - old_lines))
        if new_chars < self.AMBIENT_MIN_CHARS:
            return "suppressed:low_delta"
        self._ambient_last_fire = now
        self._ambient_last_text = text
        obs = f"[AMBIENT] Screen content:\n\n{text}\n\nSurface anything useful."
        self.session.add_user(obs, source)
        self._query(self.session.build_messages())
        return "fired"

    def _guided(self, text, source):
        if self.guided_turns >= self.GUIDED_MAX_TURNS:
            return "suppressed:max_turns"
        if self.guided_turns == 0:
            self.guided_topic = text[:80]
        self.guided_turns += 1
        self.session.add_user(text, source)
        self._query(self.session.build_messages())
        self._emit("guided_step", {"turn": self.guided_turns})
        return "fired"

    def on_event(self, cb: Callable) -> None:
        self._listeners.append(cb)

    def _emit(self, t: str, p: dict = None) -> None:
        for cb in self._listeners:
            try: cb(t, p or {})
            except Exception: pass

    @property
    def mode(self) -> ModeState:
        return self._mode

    @property
    def mode_label(self) -> str:
        labels = {ModeState.ACTIVE: "ACTIVE", ModeState.AMBIENT: "AMBIENT",
                  ModeState.GUIDED: "GUIDED"}
        lbl = labels[self._mode]
        if self._mode == ModeState.GUIDED and self.guided_turns:
            lbl += f"  T{self.guided_turns}"
        return lbl


# ─────────────────────────────────────────────────────────────────────────────
# AUDIO ENGINE  — mic + speaker loopback + Interview Mode
# ─────────────────────────────────────────────────────────────────────────────
class AudioEngine:
    """
    Records microphone and WASAPI loopback (speaker) in separate threads.
    Uses a queue-based InputStream callback — reliable stop with no poll loops.

    Interview Mode
    ──────────────
    When interview_mode=True, transcripts are tagged and routed differently:
      SPEAKER transcript → AI generates answer talking points
      MIC transcript     → AI critiques the user's response
    """
    SAMPLE_RATE = 16_000
    CHUNK_SECS  = 8
    SILENCE_RMS = 0.004

    def __init__(
        self,
        on_transcript: Callable[[str, str], None],
        on_status:     Callable[[str], None],
    ):
        self.on_transcript  = on_transcript
        self.on_status      = on_status
        self.interview_mode = False

        self._mic_active  = False
        self._spk_active  = False
        self._mic_stop    = threading.Event()
        self._spk_stop    = threading.Event()
        self._mic_thread: Optional[threading.Thread] = None
        self._spk_thread: Optional[threading.Thread] = None

        self._mic_device_idx: Optional[int] = None
        self._spk_device_idx: Optional[int] = None
        self._mic_device_name = "Unknown"
        self._spk_device_name = "Not found"

        if HAS_AUDIO:
            self._mic_device_idx, self._mic_device_name = self._find_mic()
            self._spk_device_idx, self._spk_device_name = self._find_loopback()

    # ── Device discovery ──────────────────────────────────────────────────────
    @staticmethod
    def _find_mic() -> tuple[Optional[int], str]:
        try:
            idx  = sd.default.device[0]
            name = sd.query_devices(idx)["name"]
            return idx, name
        except Exception:
            return None, "Unknown"

    @staticmethod
    def _find_loopback() -> tuple[Optional[int], str]:
        """
        Find WASAPI loopback input device.
        Strategy 1: device named exactly like an output but in WASAPI input list
        Strategy 2: any WASAPI input with 'loopback' in its name
        Strategy 3: any WASAPI input that has output channels flagged
        """
        if not HAS_AUDIO:
            return None, "sounddevice not installed"
        try:
            devices  = sd.query_devices()
            hostapis = sd.query_hostapis()
            wasapi_idx = next(
                (i for i, h in enumerate(hostapis) if "WASAPI" in h["name"]), None)
            if wasapi_idx is None:
                return None, "No WASAPI host API"

            # Pass 1: explicit 'loopback' in name
            for i, d in enumerate(devices):
                if (d["hostapi"] == wasapi_idx
                        and d["max_input_channels"] > 0
                        and "loopback" in d["name"].lower()):
                    return i, d["name"]

            # Pass 2: WASAPI input whose name matches an output device name
            output_names = {
                d["name"].strip().lower()
                for d in devices
                if d["max_output_channels"] > 0 and d["hostapi"] == wasapi_idx
            }
            for i, d in enumerate(devices):
                if (d["hostapi"] == wasapi_idx
                        and d["max_input_channels"] > 0
                        and d["name"].strip().lower() in output_names
                        and i != sd.default.device[0]):
                    return i, d["name"]

            # Pass 3: any non-default WASAPI input
            for i, d in enumerate(devices):
                if (d["hostapi"] == wasapi_idx
                        and d["max_input_channels"] > 0
                        and i != sd.default.device[0]):
                    return i, d["name"] + " (fallback)"

        except Exception as exc:
            return None, f"Error: {exc}"
        return None, "Not found — enable Stereo Mix in Sound settings"

    @staticmethod
    def list_devices() -> str:
        if not HAS_AUDIO:
            return "sounddevice not installed.\nRun:  pip install sounddevice numpy"
        lines = []
        try:
            for i, d in enumerate(sd.query_devices()):
                io_tag  = ("IN " if d["max_input_channels"]  > 0 else "   ")
                io_tag += ("OUT" if d["max_output_channels"] > 0 else "   ")
                dflt = ""
                if i == sd.default.device[0]:
                    dflt += " ← default in"
                if i == sd.default.device[1]:
                    dflt += " ← default out"
                lines.append(f"[{i:2d}] {io_tag}  {d['name']}{dflt}")
        except Exception as e:
            return str(e)
        return "\n".join(lines)

    # ── Start / stop ──────────────────────────────────────────────────────────
    def start_mic(self) -> bool:
        if not HAS_AUDIO:
            self.on_status("⚠  sounddevice missing — pip install sounddevice numpy"); return False
        if not HAS_SF:
            self.on_status("⚠  soundfile missing  — pip install soundfile"); return False
        if self._mic_active:
            return True
        self._mic_stop.clear()
        self._mic_active = True
        self._mic_thread = threading.Thread(
            target=self._record_loop,
            args=("mic", self._mic_device_idx, self._mic_stop),
            daemon=True)
        self._mic_thread.start()
        return True

    def stop_mic(self) -> None:
        self._mic_active = False
        self._mic_stop.set()

    def start_speaker(self) -> bool:
        if not HAS_AUDIO:
            self.on_status("⚠  sounddevice missing — pip install sounddevice numpy"); return False
        if not HAS_SF:
            self.on_status("⚠  soundfile missing  — pip install soundfile"); return False
        if self._spk_device_idx is None:
            self.on_status(
                f"⚠  No loopback device: {self._spk_device_name}\n"
                "→ Enable 'Stereo Mix' in Windows Sound → Recording devices"); return False
        if self._spk_active:
            return True
        self._spk_stop.clear()
        self._spk_active = True
        self._spk_thread = threading.Thread(
            target=self._record_loop,
            args=("speaker", self._spk_device_idx, self._spk_stop),
            daemon=True)
        self._spk_thread.start()
        return True

    def stop_speaker(self) -> None:
        self._spk_active = False
        self._spk_stop.set()

    def stop_all(self) -> None:
        self.stop_mic()
        self.stop_speaker()

    @property
    def mic_active(self) -> bool:
        return self._mic_active

    @property
    def speaker_active(self) -> bool:
        return self._spk_active

    # ── Recording loop  (queue-based, reliable stop) ──────────────────────────
    def _record_loop(self, source: str, device_idx: Optional[int],
                     stop_event: threading.Event) -> None:
        label = "🎤 MIC" if source == "mic" else "🔊 SPEAKER"
        self.on_status(f"{label} listening…")
        q: "queue.Queue[Optional[np.ndarray]]" = queue.Queue()

        def _callback(indata, frames, time_info, status):
            if stop_event.is_set():
                raise sd.CallbackStop()
            q.put(indata.copy())

        n_frames = int(self.CHUNK_SECS * self.SAMPLE_RATE)

        while not stop_event.is_set():
            try:
                chunk_frames: List["np.ndarray"] = []
                collected = 0
                with sd.InputStream(
                    samplerate=self.SAMPLE_RATE, channels=1, dtype="float32",
                    device=device_idx, callback=_callback,
                    blocksize=int(self.SAMPLE_RATE * 0.5),   # 500 ms blocks
                    latency="low",
                ):
                    while collected < n_frames and not stop_event.is_set():
                        try:
                            block = q.get(timeout=1.0)
                            if block is not None:
                                chunk_frames.append(block)
                                collected += len(block)
                        except queue.Empty:
                            continue

                if stop_event.is_set() or not chunk_frames:
                    return

                audio = np.concatenate(chunk_frames, axis=0)[:n_frames]
                rms   = float(np.sqrt(np.mean(audio ** 2)))
                if rms < self.SILENCE_RMS:
                    continue

                transcript = self._transcribe(audio, source)
                if transcript and len(transcript.split()) >= MIN_TRANSCRIPT_WORDS:
                    self.on_transcript(transcript, source)

            except sd.CallbackStop:
                return
            except Exception as e:
                self.on_status(f"{label} error: {e}")
                time.sleep(3)

    # ── Transcription via Groq Whisper ────────────────────────────────────────
    def _transcribe(self, frames: "np.ndarray", source: str) -> str:
        if not HAS_SF:
            self.on_status("⚠  pip install soundfile")
            return ""
        try:
            buf = io.BytesIO()
            sf.write(buf, frames, self.SAMPLE_RATE, format="WAV", subtype="PCM_16")
            buf.seek(0)
            buf.name = "audio.wav"
            resp = groq_client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=buf,
                response_format="text",
            )
            return (resp if isinstance(resp, str) else resp.text).strip()
        except Exception as e:
            if "audio" in str(e).lower() or "transcri" in str(e).lower():
                self.on_status(f"Whisper error: {e}")
            return ""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────────────────────────────────────
class AceItApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AceIt")
        self.root.attributes("-topmost", True)
        self.root.geometry("480x940+1380+30")
        self.root.minsize(400, 600)
        self.root.resizable(True, True)

        # ── Theme state (must be declared before _build_ui) ───────────────────
        self.is_dark_mode = tk.BooleanVar(value=True)

        # Phase 1–3 state
        self.highlight_mode   = tk.BooleanVar(value=False)
        self.highlight_thread = None
        self.last_clipboard   = ""
        self._float_win       = None
        self._float_canvas    = None
        self._float_accent    = GOLD
        self._float_glyph_id  = None
        self._pill_win        = None
        self._pill_after_id   = None
        self._pill_current_w  = 0
        self._pill_label      = None
        self._drag_x = self._drag_y = 0
        self._drag_start_x = self._drag_start_y = 0
        self._float_did_drag  = False
        self.watch_active        = tk.BooleanVar(value=False)
        self._watch_thread       = None
        self._last_ocr_text      = ""
        self._last_ocr_hash      = ""
        self._ai_busy            = threading.Event()
        self._watch_interval     = tk.IntVar(value=5)
        self._sensitivity_var    = tk.StringVar(value="Medium")
        self._watcher_stop       = threading.Event()
        self._scan_count         = 0
        self._trigger_count      = 0
        self._watch_next_scan_at = 0.0
        self.opacity_var         = tk.IntVar(value=95)

        # Audio state
        self._interview_mode = tk.BooleanVar(value=False)
        self._transcript_log: collections.deque[str] = collections.deque(maxlen=6)

        # Engines
        self.engine = StateEngine(raw_query_fn=self._run_messages)
        self.engine.on_event(self._on_engine_event)
        self.audio  = AudioEngine(
            on_transcript=self._on_audio_transcript,
            on_status=self._set_status,
        )

        self._dark_titlebar()
        self._build_ui()
        self._apply_theme()          # initial paint (dark)
        self._bind_hotkeys()
        self._apply_opacity(95)

    # ── Dark Win32 titlebar ───────────────────────────────────────────────────
    def _dark_titlebar(self):
        try:
            import ctypes
            hwnd  = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # THEME ENGINE
    # ─────────────────────────────────────────────────────────────────────────
    def _apply_theme(self):
        """
        Swap the active palette and repaint every initialised widget in-place.

        Order: globals → root → header → mode bar → response area →
               dock → status bar → settings modal (if open).

        Every widget access is guarded with hasattr / winfo_exists so this
        is safe to call at any construction stage, including the very first
        call from __init__ before all widgets exist.
        """
        global BASE, SURFACE, PANEL, RAISED, BORDER
        global GOLD, GOLD_HI, GOLD_DIM, GOLD_SUB, CREAM, MUTED, DIM

        # ── 1. Rebind module-level colour aliases ─────────────────────────────
        t = THEMES["dark"] if self.is_dark_mode.get() else THEMES["light"]

        BASE      = t["BASE"]
        SURFACE   = t["SURFACE"]
        PANEL     = t["PANEL"]
        RAISED    = t["RAISED"]
        BORDER    = t["BORDER"]
        GOLD      = t["ACCENT"]
        GOLD_HI   = t["ACCENT_HI"]
        GOLD_DIM  = t["ACCENT_DIM"]
        GOLD_SUB  = t["ACCENT_SUB"]
        CREAM     = t["TEXT_MAIN"]
        MUTED     = t["MUTED"]
        DIM       = t["DIM"]

        # ── 2. Win32 titlebar tint ────────────────────────────────────────────
        self._dark_titlebar()

        # ── 3. Root window ────────────────────────────────────────────────────
        self.root.configure(bg=BASE)

        # ── 4. Header ─────────────────────────────────────────────────────────
        if hasattr(self, "_hdr_frame"):
            self._hdr_frame.configure(bg=BASE)
        if hasattr(self, "_hdr_accent_line"):
            self._hdr_accent_line.configure(bg=BORDER)
        if hasattr(self, "_hdr_icon_lbl"):
            self._hdr_icon_lbl.configure(bg=BASE, fg=GOLD)
        if hasattr(self, "_hdr_title_lbl"):
            self._hdr_title_lbl.configure(bg=BASE, fg=CREAM)
        if hasattr(self, "_hdr_sub_lbl"):
            self._hdr_sub_lbl.configure(bg=BASE, fg=MUTED)
        if hasattr(self, "_hdr_right"):
            self._hdr_right.configure(bg=BASE)
        if hasattr(self, "_mode_pill"):
            mode_clr = MODE_COLORS.get(self.engine.mode, GOLD)
            self._mode_pill.configure(bg=GOLD_SUB, fg=mode_clr)
        if hasattr(self, "_opacity_hdr_lbl"):
            self._opacity_hdr_lbl.configure(bg=BASE, fg=MUTED)
        if hasattr(self, "_opacity_val_lbl"):
            self._opacity_val_lbl.configure(bg=BASE, fg=CREAM)
        for attr in ("_gear_btn", "_float_btn"):
            if hasattr(self, attr):
                getattr(self, attr).configure(
                    bg=BASE, fg=MUTED,
                    activebackground=RAISED, activeforeground=GOLD)
        if hasattr(self, "_theme_btn"):
            self._theme_btn.configure(
                text="☀️" if self.is_dark_mode.get() else "🌙",
                bg=BASE, fg=MUTED,
                activebackground=RAISED, activeforeground=GOLD)

        # ── 5. Mode selector bar ──────────────────────────────────────────────
        if hasattr(self, "_mode_bar"):
            self._mode_bar.configure(bg=SURFACE)
        if hasattr(self, "_mode_btns"):
            self._refresh_mode_btns()

        # ── 6. Response area ──────────────────────────────────────────────────
        if hasattr(self, "response_box") and self.response_box.winfo_exists():
            # Structural frames
            resp_outer     = self.response_box.master   # 1-px BORDER ring
            resp_container = resp_outer.master           # BASE outer frame
            if resp_container.winfo_exists():
                resp_container.configure(bg=BASE)
            if resp_outer.winfo_exists():
                resp_outer.configure(bg=BORDER)

            # Header bar — first child of resp_container
            children = resp_container.winfo_children()
            if children:
                resp_hdr = children[0]
                if resp_hdr.winfo_exists():
                    resp_hdr.configure(bg=BASE)
                    for child in resp_hdr.winfo_children():
                        if not child.winfo_exists():
                            continue
                        cls = child.winfo_class()
                        if cls == "Label":
                            txt = str(child.cget("text"))
                            fg  = GOLD_DIM if "RESPONSE" in txt else MUTED
                            child.configure(bg=BASE, fg=fg)
                        elif cls == "Button":
                            child.configure(
                                bg=BASE, fg=MUTED,
                                activebackground=RAISED, activeforeground=GOLD)

            # The scrolled text widget itself
            self.response_box.configure(
                bg=PANEL, fg=CREAM,
                selectbackground=GOLD_DIM,
                insertbackground=GOLD)

        # ── 7. Action dock ────────────────────────────────────────────────────
        if hasattr(self, "_dock_frame") and self._dock_frame.winfo_exists():
            self._dock_frame.configure(bg=BASE)
            # Recursively repaint all plain Frame children of the dock
            def _paint_frames(widget):
                for child in widget.winfo_children():
                    if not child.winfo_exists():
                        continue
                    if child.winfo_class() == "Frame":
                        child.configure(bg=BASE)
                        _paint_frames(child)
            _paint_frames(self._dock_frame)

        # Ask entry
        if hasattr(self, "ask_entry") and self.ask_entry.winfo_exists():
            self.ask_entry.configure(
                bg=RAISED, fg=CREAM,
                insertbackground=GOLD,
                disabledforeground=MUTED)

        # Audio feed rolling label
        if hasattr(self, "_audio_feed") and self._audio_feed.winfo_exists():
            self._audio_feed.configure(bg=BASE, fg=MUTED)

        # Icon buttons — reset to idle state first, then restore active tints
        for attr in ("btn_capture", "btn_hl", "btn_watch",
                     "_dock_mic_btn", "_dock_spk_btn"):
            if hasattr(self, attr):
                btn = getattr(self, attr)
                if btn.winfo_exists():
                    btn.configure(
                        bg=RAISED, fg=CREAM,
                        activebackground=GOLD_DIM, activeforeground=GOLD)
        # Re-apply active-state tints (cyan/purple/gold) on top
        if hasattr(self, "btn_capture"):
            self._sync_dock_buttons()

        # ── 8. Status bar ──────────────────────────────────────────────────────
        if hasattr(self, "_status_bar") and self._status_bar.winfo_exists():
            self._status_bar.configure(bg=BASE, fg=MUTED)

        # ── 9. Settings modal (live repaint if currently open) ─────────────────
        if hasattr(self, "_settings_win") and self._settings_win and \
                self._settings_win.winfo_exists():
            self._repaint_widget_tree(self._settings_win)

    # ── Settings / generic recursive repainter ────────────────────────────────
    def _repaint_widget_tree(self, widget) -> None:
        """
        Depth-first walk of any Tkinter widget subtree.
        Applies the current global palette based on each widget's class.
        Silently skips destroyed or ttk-managed widgets.

        Widget → colour keys applied
        ─────────────────────────────
        Toplevel / Frame  → bg (semantic: BASE / PANEL / RAISED by current bg)
        Label             → bg, fg
        Button            → bg, fg, activebackground, activeforeground
        Entry             → bg, fg, insertbackground, disabledforeground
        Text              → bg, fg, selectbackground, insertbackground
        Radiobutton       → bg, fg, activebackground, activeforeground, selectcolor
        Canvas            → bg, highlightthickness=0
        """
        if not widget.winfo_exists():
            return

        cls = widget.winfo_class()

        try:
            if cls in ("Frame", "Toplevel"):
                try:
                    cur = widget.cget("bg").lower()
                except Exception:
                    cur = ""
                # Map old colour → new semantic slot
                _base_vals    = {"#050505", "#f5f5f7", "#0f0f0f", "#ffffff"}
                _surface_vals = {"#0f0f0f", "#ffffff"}
                _panel_vals   = {"#141414", "#f0f0f0"}
                _raised_vals  = {"#1a1a1a", "#e5e5e5"}
                if cur in _base_vals:
                    widget.configure(bg=BASE)
                elif cur in _panel_vals:
                    widget.configure(bg=PANEL)
                elif cur in _raised_vals:
                    widget.configure(bg=RAISED)
                else:
                    widget.configure(bg=PANEL)   # safe default for modal frames

            elif cls == "Label":
                try:
                    fg_cur = widget.cget("fg").lower()
                except Exception:
                    fg_cur = ""
                _gold_vals = {"#6b5a28", "#8b6914", GOLD_DIM.lower()}
                _muted_vals = {"#808080", "#666666", MUTED.lower()}
                fg = GOLD_DIM if fg_cur in _gold_vals else \
                     MUTED     if fg_cur in _muted_vals else CREAM
                try:
                    bg_cur = widget.cget("bg").lower()
                except Exception:
                    bg_cur = ""
                bg = BASE if bg_cur in {"#050505", "#f5f5f7"} else PANEL
                widget.configure(bg=bg, fg=fg)

            elif cls == "Button":
                try:
                    bg_cur = widget.cget("bg").lower()
                except Exception:
                    bg_cur = ""
                _gold_vals = {"#d4af37", "#b8860b"}
                _red_vals  = {"#b84040"}
                if bg_cur in _gold_vals:
                    widget.configure(
                        bg=GOLD, fg=BASE,
                        activebackground=GOLD_HI, activeforeground=BASE)
                elif bg_cur in _red_vals:
                    pass   # danger buttons keep their red
                else:
                    widget.configure(
                        bg=RAISED, fg=MUTED,
                        activebackground=GOLD_DIM, activeforeground=CREAM)

            elif cls == "Entry":
                widget.configure(
                    bg=RAISED, fg=CREAM,
                    insertbackground=GOLD,
                    disabledforeground=MUTED)

            elif cls == "Text":
                widget.configure(
                    bg=PANEL, fg=CREAM,
                    selectbackground=GOLD_DIM,
                    insertbackground=GOLD)

            elif cls == "Radiobutton":
                widget.configure(
                    bg=PANEL, fg=CREAM,
                    activebackground=PANEL, activeforeground=GOLD,
                    selectcolor=GOLD_DIM)

            elif cls == "Canvas":
                widget.configure(bg=PANEL, highlightthickness=0)

            # ttk.Scale / ttk.Scrollbar → style-engine managed; skip

        except Exception:
            pass   # never crash on a partially destroyed or ttk widget

        # Recurse
        try:
            for child in widget.winfo_children():
                self._repaint_widget_tree(child)
        except Exception:
            pass

    # ═════════════════════════════════════════════════════════════════════════
    # UI BUILD
    # ═════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        r = self.root
        self._build_header(r)
        self._build_mode_selector(r)      # Stage 2: persistent top-level mode bar
        self._build_response_area(r)      # response area expands to fill middle
        self._build_action_dock(r)        # unified bottom dock
        self._build_status_bar(r)

    # ── Header ────────────────────────────────────────────────────────────────
    def _build_header(self, r):
        # Outer bar — generous vertical padding for premium breathing room
        hdr = tk.Frame(r, bg=BASE, pady=12)
        hdr.pack(fill="x")
        self._hdr_frame = hdr

        # ── Left: wordmark ────────────────────────────────────────────────────
        self._hdr_icon_lbl = tk.Label(
            hdr, text="⚡", bg=BASE, fg=GOLD,
            font=("Segoe UI", 14, "bold"))
        self._hdr_icon_lbl.pack(side="left", padx=(16, 4))

        self._hdr_title_lbl = tk.Label(
            hdr, text="AceIt", bg=BASE, fg=CREAM,
            font=("Segoe UI", 13, "bold"))
        self._hdr_title_lbl.pack(side="left")

        self._hdr_sub_lbl = tk.Label(
            hdr, text=" Co-Pilot", bg=BASE, fg=MUTED,
            font=("Segoe UI", 13))
        self._hdr_sub_lbl.pack(side="left")

        # Mode pill — sits just right of the wordmark
        self._mode_pill = tk.Label(
            hdr, text="  ACTIVE  ",
            bg=GOLD_SUB, fg=GOLD,
            font=("Segoe UI", 8, "bold"),
            relief="flat", padx=6, pady=3)
        self._mode_pill.pack(side="left", padx=(8, 0))

        # ── Right: tool cluster ───────────────────────────────────────────────
        right = tk.Frame(hdr, bg=BASE)
        right.pack(side="right", padx=(0, 16))
        self._hdr_right = right

        # ⚙  Settings gear — opens Settings modal
        self._gear_btn = tk.Button(
            right, text="⚙",
            command=self._open_settings,
            bg=BASE, fg=MUTED,
            activebackground=RAISED, activeforeground=GOLD,
            relief="flat", font=("Segoe UI", 13),
            padx=6, cursor="hand2", bd=0)
        self._gear_btn.pack(side="right", padx=2)
        self._gear_btn.bind("<Enter>", lambda e: self._gear_btn.config(bg=RAISED, fg=GOLD))
        self._gear_btn.bind("<Leave>", lambda e: self._gear_btn.config(bg=BASE,  fg=MUTED))

        # Opacity: label + compact slider (72 px) + live % readout
        self._opacity_val_lbl = tk.Label(
            right, text="95%", bg=BASE, fg=CREAM,
            font=FONT_TINY, width=4)
        self._opacity_val_lbl.pack(side="right")

        self._opacity_hdr_lbl = tk.Label(
            right, text="opacity", bg=BASE, fg=MUTED,
            font=FONT_TINY)
        self._opacity_hdr_lbl.pack(side="right", padx=(4, 0))

        def _op_cmd(v):
            val = int(float(v))
            self._apply_opacity(val)
            self._opacity_val_lbl.config(text=f"{val}%")

        ttk.Scale(
            right, from_=20, to=100, orient="horizontal",
            variable=self.opacity_var,
            command=_op_cmd,
            length=72,
        ).pack(side="right", padx=(4, 6))

        # 🗗  Float button
        self._float_btn = tk.Button(
            right, text="🗗",
            command=self._enter_float,
            bg=BASE, fg=MUTED,
            activebackground=RAISED, activeforeground=GOLD,
            relief="flat", font=("Segoe UI", 12),
            padx=6, cursor="hand2", bd=0)
        self._float_btn.pack(side="right", padx=2)
        self._float_btn.bind("<Enter>", lambda e: self._float_btn.config(bg=RAISED, fg=GOLD))
        self._float_btn.bind("<Leave>", lambda e: self._float_btn.config(bg=BASE,  fg=MUTED))

        # ☀️ / 🌙  Theme toggle
        def _toggle_theme():
            self.is_dark_mode.set(not self.is_dark_mode.get())
            self._apply_theme()

        self._theme_btn = tk.Button(
            right, text="☀️",       # dark mode active → show sun to switch to light
            command=_toggle_theme,
            bg=BASE, fg=MUTED,
            activebackground=RAISED, activeforeground=GOLD,
            relief="flat", font=("Segoe UI", 11),
            padx=6, cursor="hand2", bd=0)
        self._theme_btn.pack(side="right", padx=2)
        self._theme_btn.bind("<Enter>", lambda e: self._theme_btn.config(bg=RAISED, fg=GOLD))
        self._theme_btn.bind("<Leave>", lambda e: self._theme_btn.config(bg=BASE,  fg=MUTED))

        # Structural accent divider — separates branding from workspace
        # Uses BORDER (#262626) for a clean, non-distracting workspace boundary
        self._hdr_accent_line = tk.Frame(r, bg=BORDER, height=1)
        self._hdr_accent_line.pack(fill="x")

    # ── Top-level Mode Selector ───────────────────────────────────────────────
    def _build_mode_selector(self, r):
        """
        Persistent horizontal tab row below the header.
        Four modes: Active · Ambient · Guided · Interview
        Interview is a first-class mode — clicking it calls _activate_interview.
        """
        bar = tk.Frame(r, bg=SURFACE, pady=6)
        bar.pack(fill="x", padx=12, pady=(4, 0))
        self._mode_bar = bar

        # (mode_key, label, accent_color)
        # None = Interview pseudo-mode handled separately
        _TABS = [
            (ModeState.ACTIVE,  "⚡  Active",    GOLD),
            (ModeState.AMBIENT, "🌙  Ambient",   CYAN),
            (ModeState.GUIDED,  "🎯  Guided",    IVEW_CLR),
            (None,              "🎙  Interview", MIC_CLR),
        ]

        self._mode_btns = {}
        for mode, label, clr in _TABS:
            key = mode if mode is not None else "interview"
            cmd = (lambda m=mode: self._switch_mode(m)) if mode is not None \
                  else self._activate_interview
            btn = tk.Button(
                bar, text=label, command=cmd,
                bg=RAISED, fg=MUTED,
                activebackground=SURFACE, activeforeground=CREAM,
                relief="flat", font=("Segoe UI", 9),
                padx=10, pady=6, cursor="hand2", bd=0)
            btn.pack(side="left", padx=(0, 3))
            self._mode_btns[key] = btn

        self._refresh_mode_btns()

        # Thin separator beneath the tab row
        tk.Frame(r, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(4, 0))

    # ── Unified Action Dock (bottom of window) ────────────────────────────────
    def _build_action_dock(self, r):
        """
        Single docked panel at the bottom:
          • Icon row — [📷] [🔍] [👁] [🎤] [🔊]
          • Text bar — ask_entry + Send button
        """
        # ── Outer dock frame (border top) ─────────────────────────────────────
        dock = tk.Frame(r, bg=BASE)
        dock.pack(fill="x", side="bottom")
        self._dock_frame = dock

        # thin top border
        tk.Frame(dock, bg=GOLD_DIM, height=1).pack(fill="x")

        inner = tk.Frame(dock, bg=BASE, pady=12)
        inner.pack(fill="x", padx=16)

        # ── Icon button row ────────────────────────────────────────────────────
        icon_row = tk.Frame(inner, bg=BASE)
        icon_row.pack(fill="x", pady=(0, 6))

        def _icon_btn(parent, icon, tip_text, cmd):
            btn = tk.Button(
                parent, text=icon, command=cmd,
                bg=RAISED, fg=CREAM,
                activebackground=GOLD_DIM, activeforeground=GOLD,
                relief="flat", font=("Segoe UI", 15),
                padx=10, pady=6, cursor="hand2", bd=0,
                width=2)
            btn.pack(side="left", padx=(0, 4))
            # Basic Tkinter tooltip
            tip = tk.Label(r, text=tip_text, bg=GOLD_SUB, fg=CREAM,
                           font=FONT_TINY, relief="flat", padx=6, pady=3)
            def _enter(e, t=tip, b=btn):
                x = b.winfo_rootx() - r.winfo_rootx()
                y = b.winfo_rooty() - r.winfo_rooty() - 28
                t.place(x=x, y=y)
                t.lift()
            def _leave(e, t=tip):
                t.place_forget()
            btn.bind("<Enter>", _enter)
            btn.bind("<Leave>", _leave)
            return btn

        self.btn_capture = _icon_btn(
            icon_row, "📷", "Capture Screen  (Ctrl+Shift+S)", self._do_capture)
        self.btn_hl = _icon_btn(
            icon_row, "🔍", "Highlight / Clipboard Watch  (Ctrl+Shift+H)", self._toggle_highlight)
        self.btn_watch = _icon_btn(
            icon_row, "👁", "Screen Watcher  (Ctrl+Shift+W)", self._toggle_watch)
        self._dock_mic_btn = _icon_btn(
            icon_row, "🎤", "Microphone", self._toggle_mic)
        self._dock_spk_btn = _icon_btn(
            icon_row, "🔊", "Speaker Loopback", self._toggle_speaker)

        # Spacer then audio-feed label (rolling transcript, right-aligned)
        self._audio_feed = tk.Label(
            icon_row, text="", bg=BASE, fg=MUTED,
            font=FONT_TINY, anchor="e", justify="right")
        self._audio_feed.pack(side="right", fill="x", expand=True)

        # ── Text bar ──────────────────────────────────────────────────────────
        ask_wrap = tk.Frame(inner, bg=BORDER, pady=1)
        ask_wrap.pack(fill="x")
        ask_inner = tk.Frame(ask_wrap, bg=RAISED)
        ask_inner.pack(fill="x")
        self.ask_entry = tk.Entry(
            ask_inner, bg=RAISED, fg=CREAM, font=FONT_UI,
            insertbackground=GOLD, relief="flat", bd=8)
        self.ask_entry.pack(side="left", fill="both", expand=True)
        self.ask_entry.insert(0, "Ask anything…")
        self.ask_entry.bind("<FocusIn>",  self._clear_placeholder)
        self.ask_entry.bind("<FocusOut>", self._restore_placeholder)
        self.ask_entry.bind("<Return>",   lambda e: self._ask_free())
        tk.Button(
            ask_inner, text="➤", command=self._ask_free,
            bg=GOLD, fg=BASE, activebackground=GOLD_HI, activeforeground=BASE,
            relief="flat", font=("Segoe UI", 12, "bold"),
            padx=10, pady=4, cursor="hand2", bd=0,
        ).pack(side="right")

    # ── Settings Modal ────────────────────────────────────────────────────────
    def _open_settings(self):
        """Open the Settings Toplevel. Raises it if already open."""
        if hasattr(self, "_settings_win") and self._settings_win and \
                self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_force()
            return

        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("440x540")
        win.minsize(380, 320)
        win.resizable(True, True)               # allow vertical resize
        win.configure(bg=BASE)
        win.attributes("-topmost", True)
        win.pack_propagate(True)                # ← explicit layout propagation
        try:
            import ctypes
            hwnd  = ctypes.windll.user32.GetParent(win.winfo_id())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass
        self._settings_win = win

        # ── Helper: gold-ruled section header ─────────────────────────────────
        def _section(parent, text):
            row = tk.Frame(parent, bg=PANEL)
            row.pack(fill="x", padx=16, pady=(14, 2))
            tk.Label(row, text=text, bg=PANEL, fg=GOLD_DIM,
                     font=("Segoe UI", 7, "bold")).pack(side="left")
            tk.Frame(row, bg=GOLD_DIM, height=1).pack(
                side="left", fill="x", expand=True, padx=(6, 0))

        # ── Helper: flat button with hover highlight ───────────────────────────
        def _flat_btn(parent, text, cmd, fg=MUTED, afg=CREAM, abg=None, **kw):
            _abg = abg or GOLD_DIM
            btn = tk.Button(
                parent, text=text, command=cmd,
                bg=RAISED, fg=fg,
                activebackground=_abg, activeforeground=afg,
                relief="flat", font=FONT_TINY,
                padx=10, pady=5, cursor="hand2", bd=0, **kw)
            btn.bind("<Enter>", lambda e: btn.config(bg=_abg, fg=afg))
            btn.bind("<Leave>", lambda e: btn.config(bg=RAISED, fg=fg))
            return btn

        # ══════════════════════════════════════════════════════════════════════
        # Outer chrome — fixed title-bar + scrollable body
        # ══════════════════════════════════════════════════════════════════════
        outer = tk.Frame(win, bg=PANEL)
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        outer.configure(highlightbackground=GOLD_DIM, highlightthickness=1)

        # ── Title bar (fixed, never scrolls) ──────────────────────────────────
        hdr = tk.Frame(outer, bg=RAISED, pady=8)
        hdr.pack(fill="x", side="top")
        tk.Label(hdr, text="⚙  Settings", bg=RAISED, fg=CREAM,
                 font=("Segoe UI", 12, "bold")).pack(side="left", padx=16)
        tk.Button(hdr, text="✕", command=win.destroy,
                  bg=RAISED, fg=MUTED, activebackground=RED,
                  activeforeground=CREAM, relief="flat",
                  font=("Segoe UI", 11), padx=8, cursor="hand2", bd=0
                  ).pack(side="right", padx=6)

        # Accent divider beneath settings title bar
        tk.Frame(outer, bg=BORDER, height=1).pack(fill="x", side="top")

        # ── Scrollable region: Canvas + Scrollbar ──────────────────────────────
        scroll_area = tk.Frame(outer, bg=PANEL)
        scroll_area.pack(fill="both", expand=True, side="top")
        scroll_area.pack_propagate(True)         # ← propagation on scroll host

        canvas = tk.Canvas(scroll_area, bg=PANEL, highlightthickness=0,
                           bd=0, relief="flat")
        vbar   = tk.Scrollbar(scroll_area, orient="vertical",
                              command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)

        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # Inner frame — all settings content lives here
        body = tk.Frame(canvas, bg=PANEL)
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_resize(e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_resize(e):
            canvas.itemconfig(body_id, width=e.width)

        body.bind("<Configure>", _on_body_resize)
        canvas.bind("<Configure>", _on_canvas_resize)

        # Mouse-wheel scroll (Windows + Linux)
        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        def _on_mousewheel_linux(e):
            canvas.yview_scroll(-1 if e.num == 4 else 1, "units")

        win.bind("<MouseWheel>",  _on_mousewheel)
        win.bind("<Button-4>",    _on_mousewheel_linux)
        win.bind("<Button-5>",    _on_mousewheel_linux)

        # ── Fixed footer (Done button, never scrolls) ──────────────────────────
        footer = tk.Frame(outer, bg=PANEL, pady=8)
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x")
        done_btn = tk.Button(
            footer, text="Done", command=win.destroy,
            bg=GOLD, fg=BASE, activebackground=GOLD_HI, activeforeground=BASE,
            relief="flat", font=("Segoe UI", 10, "bold"),
            padx=24, pady=7, cursor="hand2", bd=0)
        done_btn.pack(pady=(10, 4))
        done_btn.bind("<Enter>", lambda e: done_btn.config(bg=GOLD_HI))
        done_btn.bind("<Leave>", lambda e: done_btn.config(bg=GOLD))

        # ══════════════════════════════════════════════════════════════════════
        # SETTINGS CONTENT  (all packed into `body`, the scrollable inner frame)
        # ══════════════════════════════════════════════════════════════════════

        # ── AUDIO DEVICES ─────────────────────────────────────────────────────
        _section(body, "AUDIO DEVICES")
        dev_frame = tk.Frame(body, bg=PANEL)
        dev_frame.pack(fill="x", padx=16, pady=(6, 4))

        mic_name = self.audio._mic_device_name
        spk_name = self.audio._spk_device_name
        for icon, name in [("🎤  Mic", mic_name), ("🔊  Speaker", spk_name)]:
            row = tk.Frame(dev_frame, bg=RAISED, pady=6)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=icon, bg=RAISED, fg=MUTED,
                     font=FONT_SM, width=11, anchor="w").pack(side="left", padx=(10, 0))
            tk.Label(row, text=name or "(not found)",
                     bg=RAISED, fg=CREAM if name else AMBER,
                     font=FONT_TINY, wraplength=270, justify="left"
                     ).pack(side="left", padx=8)

        list_btn = _flat_btn(body, "📋  List All Devices", self._show_device_list)
        list_btn.pack(anchor="w", padx=16, pady=(6, 2))

        if not HAS_AUDIO:
            tk.Label(body, text="⚠  pip install sounddevice numpy soundfile",
                     bg=PANEL, fg=AMBER, font=FONT_TINY).pack(padx=16, pady=4, anchor="w")

        # ── SCREEN WATCHER ────────────────────────────────────────────────────
        _section(body, "SCREEN WATCHER")

        int_row = tk.Frame(body, bg=PANEL)
        int_row.pack(fill="x", padx=16, pady=(8, 4))
        tk.Label(int_row, text="Scan interval", bg=PANEL, fg=MUTED,
                 font=FONT_SM).pack(side="left")
        self._interval_lbl = tk.Label(int_row, text=f"{self._watch_interval.get()} s",
                                      bg=PANEL, fg=CREAM, font=FONT_SM, width=5)
        self._interval_lbl.pack(side="right")
        ttk.Scale(int_row, from_=3, to=15, orient="horizontal",
                  variable=self._watch_interval,
                  command=lambda v: self._interval_lbl.config(text=f"{int(float(v))} s"),
                  length=210
                  ).pack(side="left", padx=8)

        sens_row = tk.Frame(body, bg=PANEL)
        sens_row.pack(fill="x", padx=16, pady=(4, 4))
        tk.Label(sens_row, text="Sensitivity", bg=PANEL, fg=MUTED,
                 font=FONT_SM).pack(side="left")
        for lbl in ("Low", "Medium", "High"):
            tk.Radiobutton(
                sens_row, text=lbl, variable=self._sensitivity_var, value=lbl,
                bg=PANEL, fg=CREAM, selectcolor=GOLD_DIM,
                activebackground=PANEL, activeforeground=GOLD,
                font=FONT_SM, cursor="hand2").pack(side="left", padx=8)

        watcher_stat_row = tk.Frame(body, bg=PANEL)
        watcher_stat_row.pack(fill="x", padx=16, pady=(2, 4))
        self._watch_scans_lbl = tk.Label(watcher_stat_row, text="Scans: 0  Triggers: 0",
                                         bg=PANEL, fg=MUTED, font=FONT_TINY)
        self._watch_scans_lbl.pack(side="left")
        self._watch_diff_lbl = tk.Label(watcher_stat_row, text="",
                                        bg=PANEL, fg=CYAN, font=FONT_TINY)
        self._watch_diff_lbl.pack(side="right")

        # ── DISPLAY ───────────────────────────────────────────────────────────
        _section(body, "DISPLAY")

        op_row = tk.Frame(body, bg=PANEL)
        op_row.pack(fill="x", padx=16, pady=(8, 12))
        tk.Label(op_row, text="Opacity", bg=PANEL, fg=MUTED, font=FONT_SM).pack(side="left")
        self._opacity_lbl = tk.Label(op_row, text=f"{self.opacity_var.get()}%",
                                     bg=PANEL, fg=CREAM, font=FONT_SM, width=5)
        self._opacity_lbl.pack(side="right")
        ttk.Scale(op_row, from_=20, to=100, orient="horizontal",
                  variable=self.opacity_var,
                  command=lambda v: (self._apply_opacity(int(float(v))),
                                     self._opacity_lbl.config(text=f"{int(float(v))}%")),
                  length=210
                  ).pack(side="left", padx=8)

    # ── Response area ─────────────────────────────────────────────────────────
    def _build_response_area(self, r):
        # Outer container expands to fill all remaining vertical space
        resp_container = tk.Frame(r, bg=BASE)
        resp_container.pack(fill="both", expand=True, padx=16, pady=(4, 0))

        # ── Floating header bar (source label + action buttons) ───────────────
        resp_hdr = tk.Frame(resp_container, bg=BASE)
        resp_hdr.pack(fill="x", pady=(0, 2))

        tk.Label(resp_hdr, text="AI RESPONSE", bg=BASE, fg=GOLD_DIM,
                 font=("Segoe UI", 7, "bold")).pack(side="left")
        self._source_lbl = tk.Label(resp_hdr, text="", bg=BASE, fg=MUTED,
                                    font=FONT_TINY)
        self._source_lbl.pack(side="left", padx=8)

        # Action buttons float to the top-right of the response box
        tk.Button(resp_hdr, text="⎘ Copy", command=self._copy_response,
                  bg=BASE, fg=MUTED,
                  activebackground=RAISED, activeforeground=GOLD,
                  relief="flat", font=FONT_TINY, padx=6, pady=2,
                  cursor="hand2", bd=0
                  ).pack(side="right", padx=(2, 0))
        tk.Button(resp_hdr, text="🗑 Clear", command=self._clear,
                  bg=BASE, fg=MUTED,
                  activebackground=RAISED, activeforeground=RED,
                  relief="flat", font=FONT_TINY, padx=6, pady=2,
                  cursor="hand2", bd=0
                  ).pack(side="right")

        # ── Scrolled text — dominant element ─────────────────────────────────
        resp_outer = tk.Frame(resp_container, bg=BORDER, pady=1, padx=1)
        resp_outer.pack(fill="both", expand=True)
        self.response_box = scrolledtext.ScrolledText(
            resp_outer, bg=PANEL, fg=CREAM, font=FONT_MONO,
            relief="flat", wrap="word", padx=14, pady=12,
            state="disabled", insertbackground=GOLD,
            selectbackground=GOLD_DIM)
        self.response_box.pack(fill="both", expand=True)

    # ── Status bar ────────────────────────────────────────────────────────────
    def _build_status_bar(self, r):
        self._status_bar = tk.Label(
            r, text="  Ready", bg=BASE, fg=MUTED,
            font=FONT_TINY, anchor="w", pady=6)
        self._status_bar.pack(fill="x")

    def _section_label(self, parent, text):
        """Utility for settings modal section headers."""
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(row, text=text, bg=PANEL, fg=GOLD_DIM,
                 font=("Segoe UI", 7, "bold")).pack(side="left")
        tk.Frame(row, bg=GOLD_DIM, height=1).pack(
            side="left", fill="x", expand=True, padx=(6, 0))

    def _sync_dock_buttons(self):
        """Update dock icon button colours to reflect active/inactive state."""
        # Screen watcher
        if hasattr(self, "btn_watch"):
            active = self.watch_active.get()
            self.btn_watch.config(
                bg=CYAN if active else RAISED,
                fg=BASE if active else CREAM)
        # Mic
        if hasattr(self, "_dock_mic_btn"):
            active = self.audio.mic_active
            self._dock_mic_btn.config(
                bg=MIC_CLR if active else RAISED,
                fg=CREAM)
        # Speaker
        if hasattr(self, "_dock_spk_btn"):
            active = self.audio.speaker_active
            self._dock_spk_btn.config(
                bg=SPK_CLR if active else RAISED,
                fg=BASE if active else CREAM)
        # Highlight
        if hasattr(self, "btn_hl"):
            active = self.highlight_mode.get()
            self.btn_hl.config(
                bg=GOLD if active else RAISED,
                fg=BASE if active else CREAM)

    # ═════════════════════════════════════════════════════════════════════════
    # INTERVIEW MODE
    # ═════════════════════════════════════════════════════════════════════════
    def _toggle_interview(self):
        """Legacy toggle — delegates to the unified activate/deactivate helpers."""
        if self._interview_mode.get():
            self._deactivate_interview()
            self._refresh_mode_btns()
        else:
            self._activate_interview()

    # ═════════════════════════════════════════════════════════════════════════
    # AUDIO CONTROLS
    # ═════════════════════════════════════════════════════════════════════════
    def _set_mic(self, on: bool):
        if on and not self.audio.mic_active:
            ok = self.audio.start_mic()
            if ok:
                self.root.after(0, self._sync_dock_buttons)
        elif not on and self.audio.mic_active:
            self.audio.stop_mic()
            self.root.after(0, self._sync_dock_buttons)

    def _set_speaker(self, on: bool):
        if on and not self.audio.speaker_active:
            ok = self.audio.start_speaker()
            if ok:
                self.root.after(0, self._sync_dock_buttons)
        elif not on and self.audio.speaker_active:
            self.audio.stop_speaker()
            self.root.after(0, self._sync_dock_buttons)

    def _toggle_mic(self):
        self._set_mic(not self.audio.mic_active)
        status = "🎤 Mic ON — listening" if self.audio.mic_active else "🎤 Mic stopped"
        self._set_status(status)

    def _toggle_speaker(self):
        self._set_speaker(not self.audio.speaker_active)
        status = "🔊 Speaker capture ON" if self.audio.speaker_active else "🔊 Speaker stopped"
        self._set_status(status)

    def _show_device_list(self):
        """Show audio device list in the response box for debugging."""
        devs = AudioEngine.list_devices()
        self._show_response(f"─── Audio Devices ───\n{devs}\n─────────────────────")
        self._set_status("Device list shown in response box")

    def _on_audio_transcript(self, text: str, source: str):
        label  = "🎤 MIC" if source == "mic" else "🔊 SPEAKER"
        colour = MIC_CLR if source == "mic" else SPK_CLR
        short  = text[:70] + "…" if len(text) > 72 else text

        # Rolling transcript log
        self._transcript_log.append(f"{label}: {short}")
        feed_text = "\n".join(self._transcript_log)
        self.root.after(0, lambda: self._audio_feed.config(text=feed_text, fg=colour))

        # ── Interview Mode routing ─────────────────────────────────────────────
        if self._interview_mode.get():
            if source == "speaker":
                prompt = (
                    f"[SPEAKER — INTERVIEWER]\n"
                    f"The interviewer just said:\n\n\"{text}\"\n\n"
                    "Provide answer coaching as per your role."
                )
            else:  # mic = user
                prompt = (
                    f"[MIC — USER RESPONSE]\n"
                    f"The user (interviewee) just said:\n\n\"{text}\"\n\n"
                    "Evaluate and suggest improvements as per your role."
                )
        else:
            prompt = (
                f"[AUDIO — {label}]\n"
                f"Spoken/heard:\n\n\"{text}\"\n\n"
                "If this contains a question, answer it clearly. "
                "If it's general conversation, provide a brief relevant insight."
            )

        self._set_source(f"Source: {label}")
        self._set_status(f"{label} received — processing…")
        threading.Thread(
            target=self.engine.handle_input,
            args=(prompt,), kwargs={"source": source},
            daemon=True).start()

    # ═════════════════════════════════════════════════════════════════════════
    # MODE / SESSION
    # ═════════════════════════════════════════════════════════════════════════
    def _switch_mode(self, mode: ModeState):
        """Switch to a standard engine mode, deactivating Interview if needed."""
        if self._interview_mode.get():
            # Cleanly exit interview mode first
            self._deactivate_interview()
        self.engine.set_mode(mode)
        self._refresh_mode_btns()
        clr = MODE_COLORS[mode]
        self._mode_pill.config(text=f"  {mode.name}  ", fg=clr, bg=GOLD_SUB)
        self._set_status(f"Mode → {mode.name.capitalize()}")

    def _activate_interview(self):
        """Promote Interview to a first-class top-level mode."""
        self._interview_mode.set(True)
        self.audio.interview_mode = True
        # Inject specialist system prompt
        if not self.engine.session.is_active:
            self.engine.session.start(INTERVIEW_SYSTEM)
        else:
            self.engine.session.set_system(INTERVIEW_SYSTEM)
        # Auto-start both audio sources
        self._set_mic(True)
        self._set_speaker(True)
        self._refresh_mode_btns()
        self._mode_pill.config(text="  INTERVIEW  ", fg=MIC_CLR, bg=GOLD_SUB)
        self.root.after(0, self._sync_dock_buttons)
        self._set_status("🎙 Interview Mode ON — Mic + Speaker active")

    def _deactivate_interview(self):
        """Turn off interview mode and restore the current engine mode's prompt."""
        self._interview_mode.set(False)
        self.audio.interview_mode = False
        self.engine.session.set_system(MODE_SYSTEMS[self.engine.mode])
        self.root.after(0, self._sync_dock_buttons)
        self._set_status("🎙 Interview Mode OFF")

    def _refresh_mode_btns(self):
        """Repaint all four mode-selector tabs to reflect the active state."""
        current_engine_mode = self.engine.mode
        is_interview = self._interview_mode.get()

        _ACCENT = {
            ModeState.ACTIVE:  GOLD,
            ModeState.AMBIENT: CYAN,
            ModeState.GUIDED:  IVEW_CLR,
            "interview":       MIC_CLR,
        }

        for key, btn in self._mode_btns.items():
            if not btn.winfo_exists():
                continue
            active = (key == "interview" and is_interview) or \
                     (key is not None and key != "interview"
                      and key == current_engine_mode and not is_interview)
            if active:
                clr = _ACCENT.get(key, GOLD)
                btn.config(bg=GOLD_SUB, fg=clr, font=("Segoe UI", 9, "bold"))
            else:
                btn.config(bg=RAISED, fg=MUTED, font=("Segoe UI", 9))

    def _on_engine_event(self, event_type: str, payload: dict):
        if event_type == "mode_changed":
            self.root.after(0, lambda: self._mode_pill.config(
                text=f"  {self.engine.mode_label}  "))
        elif event_type == "suppressed" and payload.get("reason") == "cooldown":
            rem = payload.get("remaining", 30)
            self._set_status(f"🌙  Ambient cooldown — {rem}s")

    def _start_session(self):
        system = INTERVIEW_SYSTEM if self._interview_mode.get() \
                 else MODE_SYSTEMS[self.engine.mode]
        self.engine.session.start(system)
        self._set_status("Session started ✓  AI will remember context")

    def _end_session(self):
        self.engine.session.end()
        self._set_status("Session ended — memory cleared")

    # ═════════════════════════════════════════════════════════════════════════
    # AI QUERY
    # ═════════════════════════════════════════════════════════════════════════
    def _run_messages(self, messages: List[dict]):
        try:
            resp   = groq_client.chat.completions.create(
                model=GROQ_MODEL, messages=messages,
                max_tokens=1500, temperature=0.4)
            answer = resp.choices[0].message.content
        except Exception as exc:
            answer = f"[Groq error]\n{exc}"
        self.engine.store_ai_response(answer)
        self._show_response(answer)
        # ── Float-mode notification pill ──────────────────────────────────────
        # When the main window is hidden (float active), push a preview snippet
        # into the sliding pill instead of silently dropping the response.
        if getattr(self, "_float_win", None) and self._float_win.winfo_exists():
            snippet = answer.strip().replace("\n", " ")[:100]
            if len(answer.strip()) > 100:
                snippet += "…"
            self.float_notify(snippet)
        if self.watch_active.get():
            rem = max(0, int(self._watch_next_scan_at - time.time()))
            self._set_status(f"👁  Watching · next scan in {rem}s  [{self.engine.mode_label}]")
        else:
            self._set_status(f"Done ✓  [{self.engine.mode_label}]")

    # ═════════════════════════════════════════════════════════════════════════
    # CAPTURE / HIGHLIGHT / ASK
    # ═════════════════════════════════════════════════════════════════════════
    def _do_capture(self):
        self._set_status("Capturing screen…")
        self._set_source("📷 Screen Capture")
        self.root.withdraw()
        time.sleep(0.25)
        try:
            screenshot = pyautogui.screenshot()
        except Exception as e:
            self._show_response(f"[Capture error: {e}]")
            self.root.deiconify()
            return
        finally:
            self.root.deiconify()
        try:
            ocr_text = pytesseract.image_to_string(screenshot).strip()
        except Exception:
            ocr_text = ""
        if not ocr_text:
            ocr_text = "(No text found via OCR)"
        prompt = (
            "I captured the user's screen. Full screen text via OCR:\n\n"
            f"--- SCREEN ---\n{ocr_text}\n--- END ---\n\n"
            "Answer ALL questions and solve ALL problems visible above."
        )
        self._set_status("Thinking…")
        threading.Thread(target=self.engine.handle_input,
                         args=(prompt,), kwargs={"source": "capture"},
                         daemon=True).start()

    def _toggle_highlight(self):
        if self.highlight_mode.get():
            self.highlight_mode.set(False)
            self._sync_dock_buttons()
            self._set_status("Highlight OFF")
        else:
            self.highlight_mode.set(True)
            self._sync_dock_buttons()
            self._set_status("Highlight ON — select text anywhere")
            self.last_clipboard = ""
            if self.highlight_thread is None or not self.highlight_thread.is_alive():
                self.highlight_thread = threading.Thread(
                    target=self._highlight_loop, daemon=True)
                self.highlight_thread.start()

    def _highlight_loop(self):
        while self.highlight_mode.get():
            try:
                keyboard.send("ctrl+c")
                time.sleep(0.12)
                current = pyperclip.paste()
            except Exception:
                current = ""
            if current and current != self.last_clipboard and len(current.strip()) > 2:
                self.last_clipboard = current
                self._set_source("🔍 Highlighted text")
                self._set_status("Highlight detected — answering…")
                prompt = (
                    "The user highlighted this text:\n\n"
                    f"--- SELECTED ---\n{current.strip()}\n--- END ---\n\n"
                    "Answer, explain, or solve whatever is shown."
                )
                self.engine.handle_input(prompt, source="highlight")
            time.sleep(0.5)

    def _ask_free(self):
        question = self.ask_entry.get().strip()
        if not question or question == "Ask anything…":
            return
        self._set_source("✏ Manual question")
        self._set_status("Thinking…")
        threading.Thread(target=self.engine.handle_input,
                         args=(question,), kwargs={"source": "ask"},
                         daemon=True).start()

    # ═════════════════════════════════════════════════════════════════════════
    # SCREEN WATCHER
    # ═════════════════════════════════════════════════════════════════════════
    def _toggle_watch(self):
        if self.watch_active.get(): self._stop_watch()
        else: self._start_watch()

    def _start_watch(self):
        self.watch_active.set(True)
        self._watcher_stop.clear()
        self._last_ocr_text  = ""
        self._last_ocr_hash  = ""
        self._scan_count     = 0
        self._trigger_count  = 0
        self._sync_dock_buttons()
        self._set_status("👁  Watcher started")
        if self._watch_thread is None or not self._watch_thread.is_alive():
            self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
            self._watch_thread.start()
        self._watch_next_scan_at = time.time() + self._watch_interval.get()
        self._tick_countdown()

    def _stop_watch(self):
        self.watch_active.set(False)
        self._watcher_stop.set()
        self._sync_dock_buttons()
        self._set_status("⏸  Watcher paused")
        self.root.after(0, lambda: self._watch_diff_lbl.config(text="") if hasattr(self, "_watch_diff_lbl") and self._watch_diff_lbl.winfo_exists() else None)

    def _watch_loop(self):
        while not self._watcher_stop.is_set():
            interval = self._watch_interval.get()
            deadline = time.time() + interval
            while time.time() < deadline:
                if self._watcher_stop.is_set(): return
                time.sleep(0.3)
            if self._watcher_stop.is_set(): return
            try:
                screenshot = ImageGrab.grab()
                raw = pytesseract.image_to_string(screenshot)
            except Exception:
                continue
            text = raw.strip()
            self._scan_count += 1
            self._update_scan_stats()
            self._watch_next_scan_at = time.time() + self._watch_interval.get()
            h = hashlib.md5(text.encode()).hexdigest()
            if h == self._last_ocr_hash:
                self.root.after(0, lambda: self._watch_diff_lbl.config(text="Δ 0%"))
                continue
            changed, ratio = self._is_meaningful_change(self._last_ocr_text, text)
            pct = int((1 - ratio) * 100)
            self.root.after(0, lambda p=pct: self._watch_diff_lbl.config(text=f"Δ {p}%"))
            if not changed or self._ai_busy.is_set():
                continue
            self._last_ocr_text  = text
            self._last_ocr_hash  = h
            self._trigger_count += 1
            self._update_scan_stats()
            self._set_source("👁 Screen Watcher")
            self._set_status(f"👁  Change {pct}% — querying AI…")
            prompt = (
                "The user's screen just changed. Current screen content:\n\n"
                f"--- SCREEN ---\n{text}\n--- END ---\n\n"
                "Identify and answer any questions, problems or tasks visible."
            )
            threading.Thread(target=self._watch_query, args=(prompt,), daemon=True).start()

    def _is_meaningful_change(self, old: str, new: str) -> tuple[bool, float]:
        if not old:
            return len(new) > 100, 0.0
        ratio = difflib.SequenceMatcher(None, old, new, autojunk=True).quick_ratio()
        sens  = SENSITIVITY[self._sensitivity_var.get()]
        if ratio >= sens["sim"]:
            return False, ratio
        new_chars = sum(len(l) for l in (set(new.splitlines()) - set(old.splitlines())))
        return new_chars >= sens["chars"], ratio

    def _watch_query(self, prompt: str):
        self._ai_busy.set()
        try: self.engine.handle_input(prompt, source="watch")
        finally: self._ai_busy.clear()

    def _tick_countdown(self):
        if not self.watch_active.get(): return
        rem = max(0, int(self._watch_next_scan_at - time.time()))
        self._set_status(f"👁  Watching · next scan in {rem}s  [{self.engine.mode_label}]")
        self.root.after(1000, self._tick_countdown)

    def _update_scan_stats(self):
        s, t = self._scan_count, self._trigger_count
        self.root.after(0, lambda: self._watch_scans_lbl.config(text=f"Scans: {s}  Triggers: {t}"))

    # ═════════════════════════════════════════════════════════════════════════
    # FLOAT MODE  —  Circular Morph + Notification Pill
    # ═════════════════════════════════════════════════════════════════════════
    #
    # Architecture overview
    # ─────────────────────
    # _float_win          : frameless Toplevel — the 60×60 circle host
    # _float_canvas       : Canvas inside _float_win, draws the circle + glyph
    # _pill_win           : second frameless Toplevel anchored to the right of
    #                       the circle; slides in/out horizontally for toasts
    #
    # Transparency trick (Windows-safe)
    # ──────────────────────────────────
    # We set -transparentcolor to a specific chroma-key ("magenta" / #FF00FF).
    # The circle is drawn on the canvas; everything outside the arc stays chroma-
    # key so the OS compositing layer punches it to fully transparent.  Alpha
    # (-alpha 0.88) still applies to the circle region itself.
    #
    # Morph animation
    # ────────────────
    # Starting geometry 480×940 → target 60×60.
    # _float_morph_step() interpolates width/height/position over MORPH_STEPS
    # frames, each MORPH_DELAY ms apart, using a cosine ease-out curve.
    #
    # Pill animation
    # ───────────────
    # The pill is always zero-width at rest.  _pill_slide_out() expands it
    # rightward over PILL_STEPS frames, holds for 5 s, then _pill_slide_in()
    # collapses it.  Concurrent calls cancel the previous timer chain.

    _CHROMA_KEY   = "#FF00FF"   # transparent punch-out colour (avoid in palette)
    _CIRCLE_D     = 60          # final circle diameter (px)
    _MORPH_STEPS  = 18          # morph animation frame count
    _MORPH_DELAY  = 16          # ms between frames  (~60 fps)
    _PILL_MAX_W   = 280         # expanded pill width (px)
    _PILL_H       = 40          # pill height (px)
    _PILL_STEPS   = 16          # slide animation frame count
    _PILL_DELAY   = 14          # ms between pill frames
    _PILL_HOLD_MS = 5000        # how long pill stays fully open

    # ── Public entry/exit ────────────────────────────────────────────────────
    def _enter_float(self):
        """Shrink main window → 60×60 circle with morph animation."""
        # Snapshot geometry before hiding
        self.root.update_idletasks()
        start_x = self.root.winfo_x()
        start_y = self.root.winfo_y()
        start_w = self.root.winfo_width()
        start_h = self.root.winfo_height()

        # Determine accent colour for this session
        icon_color = IVEW_CLR if self._interview_mode.get() else (
            CYAN if self.watch_active.get() else GOLD)
        self._float_accent = icon_color

        # Target position: park circle near where the window left edge was
        target_x = start_x
        target_y = start_y + (start_h - self._CIRCLE_D) // 2

        # Build the frameless circle window — hidden until morph begins
        fw = tk.Toplevel(self.root)
        fw.overrideredirect(True)
        fw.attributes("-topmost", True)
        fw.attributes("-alpha", 0.0)       # start invisible; fades in during morph
        fw.geometry(f"{start_w}x{start_h}+{start_x}+{start_y}")
        fw.configure(bg=self._CHROMA_KEY)

        # Apply OS transparency layer — chroma key punches circle shape
        try:
            fw.wm_attributes("-transparentcolor", self._CHROMA_KEY)
        except tk.TclError:
            pass   # Linux/older macOS: no chroma key; graceful fallback

        # Canvas that draws the circle + glyph
        cv = tk.Canvas(fw, width=start_w, height=start_h,
                       bg=self._CHROMA_KEY, highlightthickness=0)
        cv.pack(fill="both", expand=True)

        self._float_win    = fw
        self._float_canvas = cv
        self._float_did_drag  = False
        self._float_glyph_id  = None
        self._pill_win        = None
        self._pill_after_id   = None
        self._pill_current_w  = 0

        # Main window hides once the overlay is positioned
        self.root.withdraw()

        # Kick off morph
        self._float_morph_step(
            step=0,
            sx=start_x, sy=start_y, sw=start_w, sh=start_h,
            tx=target_x, ty=target_y,
        )

    def _leave_float(self):
        """Collapse pill (if open) then restore main window."""
        self._pill_cancel()
        if self._float_win:
            try:
                self._float_win.destroy()
            except tk.TclError:
                pass
            self._float_win = None
        self._float_canvas = None
        self.root.deiconify()
        self.root.lift()

    # ── Morph animation ──────────────────────────────────────────────────────
    def _float_morph_step(self, step, sx, sy, sw, sh, tx, ty):
        """
        Cosine ease-out interpolation from full window size → 60×60 circle.
        Each frame redraws the canvas circle at the interpolated size/position.
        """
        fw = self._float_win
        if fw is None or not fw.winfo_exists():
            return

        n  = self._MORPH_STEPS
        t  = step / n                              # 0.0 → 1.0
        # Cosine ease-out:  fast start, gentle landing
        ease = 1 - ((1 - t) ** 3)

        d   = self._CIRCLE_D
        cw  = int(sw + (d - sw) * ease)           # current window width
        ch  = int(sh + (d - sh) * ease)           # current window height
        cx  = int(sx + (tx - sx) * ease)
        cy  = int(sy + (ty - sy) * ease)
        alpha = min(0.88, 0.1 + 0.78 * ease)

        # Reposition & resize the window
        fw.geometry(f"{max(cw, d)}x{max(ch, d)}+{cx}+{cy}")
        fw.attributes("-alpha", alpha)

        # Resize canvas to match
        cv = self._float_canvas
        cv.config(width=max(cw, d), height=max(ch, d))

        # Draw interpolated circle centred in window
        cx_c = max(cw, d) // 2
        cy_c = max(ch, d) // 2
        r    = max(4, int(d / 2 * ease))          # radius grows from tiny → 30
        self._float_draw_circle(cv, cx_c, cy_c, r, step, n)

        if step < n:
            fw.after(self._MORPH_DELAY,
                     lambda: self._float_morph_step(
                         step + 1, sx, sy, sw, sh, tx, ty))
        else:
            # Morph complete — lock window to exact 60×60 and wire interactions
            fw.geometry(f"{d}x{d}+{tx}+{ty}")
            cv.config(width=d, height=d)
            self._float_draw_circle(cv, d // 2, d // 2, d // 2 - 2, n, n)
            self._float_wire_events()

    def _float_draw_circle(self, cv, cx, cy, r, step, total_steps):
        """Redraw the circle arc and brand glyph on the canvas."""
        accent = self._float_accent
        cv.delete("all")
        if r < 2:
            return

        # Outer glow ring (semi-transparent look via darker ring)
        glow_r = r + 3
        cv.create_oval(cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
                       fill="", outline=GOLD_DIM, width=1)

        # Filled circle
        cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                       fill=accent, outline=accent, width=0)

        # Brand glyph — only render when circle is large enough
        progress = step / total_steps if total_steps > 0 else 1.0
        if progress > 0.6:
            font_size = max(6, int(22 * ((progress - 0.6) / 0.4)))
            self._float_glyph_id = cv.create_text(
                cx, cy, text="⚡",
                font=("Segoe UI", font_size),
                fill=BASE)

    def _float_wire_events(self):
        """Attach drag / click bindings after morph completes."""
        fw = self._float_win
        cv = self._float_canvas
        if fw is None or cv is None:
            return
        for w in (fw, cv):
            w.bind("<ButtonPress-1>",   self._float_drag_start)
            w.bind("<B1-Motion>",       self._float_drag_move)
            w.bind("<ButtonRelease-1>", self._float_click_or_snap)

    # ── Drag / snap ──────────────────────────────────────────────────────────
    def _float_drag_start(self, e):
        self._drag_x = e.x_root - self._float_win.winfo_x()
        self._drag_y = e.y_root - self._float_win.winfo_y()
        self._drag_start_x = e.x_root
        self._drag_start_y = e.y_root
        self._float_did_drag = False

    def _float_drag_move(self, e):
        if abs(e.x_root - self._drag_start_x) > 4 or \
           abs(e.y_root - self._drag_start_y) > 4:
            self._float_did_drag = True
        self._float_win.geometry(
            f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")
        # Keep pill anchored to circle during drag
        self._pill_reanchor()

    def _float_click_or_snap(self, e):
        if self._float_did_drag:
            self._float_snap_edge()
        else:
            self._leave_float()

    def _float_snap_edge(self):
        fw = self._float_win
        sw, sh = fw.winfo_screenwidth(), fw.winfo_screenheight()
        x,  y  = fw.winfo_x(), fw.winfo_y()
        w,  h  = fw.winfo_width(), fw.winfo_height()
        dists  = {"left": x, "right": sw-x-w, "top": y, "bottom": sh-y-h}
        coords = {"left": (0, y), "right": (sw-w, y),
                  "top": (x, 0), "bottom": (x, sh-h)}
        nx, ny = coords[min(dists, key=dists.get)]
        fw.geometry(f"+{nx}+{ny}")
        self._pill_reanchor()

    # ── Notification pill ────────────────────────────────────────────────────
    def float_notify(self, text: str):
        """
        Public hook — call from any thread to push a notification into the pill.
        If the float window is not active this is a no-op.
        """
        if self._float_win is None or not self._float_win.winfo_exists():
            return
        # Safely schedule on the main thread
        self.root.after(0, lambda: self._pill_show(text))

    def _pill_show(self, text: str):
        """Create/reset the pill window and trigger its slide-out animation."""
        self._pill_cancel()   # abort any running animation

        fw = self._float_win
        if fw is None or not fw.winfo_exists():
            return

        # Position pill immediately to the right of the circle
        cx, cy = fw.winfo_x(), fw.winfo_y()
        d      = self._CIRCLE_D
        px     = cx + d + 6      # 6px gap between circle and pill
        py     = cy + (d - self._PILL_H) // 2

        # Create pill window if it doesn't exist yet
        if self._pill_win is None or not self._pill_win.winfo_exists():
            pw = tk.Toplevel(self.root)
            pw.overrideredirect(True)
            pw.attributes("-topmost", True)
            pw.attributes("-alpha", 0.93)
            pw.configure(bg=SURFACE)
            pw.geometry(f"0x{self._PILL_H}+{px}+{py}")

            # Inner frame for padding + rounded-feel border
            pill_frame = tk.Frame(pw, bg=SURFACE,
                                  highlightbackground=self._float_accent,
                                  highlightthickness=1)
            pill_frame.pack(fill="both", expand=True, padx=1, pady=1)

            # Accent left-bar (colour stripe)
            stripe = tk.Frame(pill_frame, bg=self._float_accent, width=3)
            stripe.pack(side="left", fill="y")

            # Text label (truncates gracefully if very long)
            self._pill_label = tk.Label(
                pill_frame,
                text="",
                bg=SURFACE, fg=CREAM,
                font=("Segoe UI", 9),
                anchor="w",
                padx=10, pady=0,
                wraplength=self._PILL_MAX_W - 30,
                justify="left",
            )
            self._pill_label.pack(side="left", fill="both", expand=True)

            self._pill_win = pw
        else:
            self._pill_win.geometry(f"0x{self._PILL_H}+{px}+{py}")

        # Truncate very long text to a single readable line
        display = text if len(text) <= 120 else text[:117] + "…"
        self._pill_label.config(text=display)
        self._pill_current_w = 0

        # Slide out
        self._pill_slide_out(step=0, px=px, py=py)

    def _pill_slide_out(self, step: int, px: int, py: int):
        """Expand pill width from 0 → _PILL_MAX_W using ease-out."""
        pw = self._pill_win
        if pw is None or not pw.winfo_exists():
            return
        if step > self._PILL_STEPS:
            # Fully open — schedule retract after hold period
            self._pill_current_w = self._PILL_MAX_W
            self._pill_after_id = self.root.after(
                self._PILL_HOLD_MS,
                lambda: self._pill_slide_in(step=0, px=px, py=py))
            return

        t    = step / self._PILL_STEPS
        ease = 1 - (1 - t) ** 3               # cubic ease-out
        w    = int(self._PILL_MAX_W * ease)
        w    = max(w, 1)

        # Recalculate anchor in case circle was dragged
        if self._float_win and self._float_win.winfo_exists():
            px = self._float_win.winfo_x() + self._CIRCLE_D + 6
            py = self._float_win.winfo_y() + (self._CIRCLE_D - self._PILL_H) // 2

        pw.geometry(f"{w}x{self._PILL_H}+{px}+{py}")
        self._pill_current_w = w

        self._pill_after_id = self.root.after(
            self._PILL_DELAY,
            lambda: self._pill_slide_out(step + 1, px, py))

    def _pill_slide_in(self, step: int, px: int, py: int):
        """Collapse pill width from _PILL_MAX_W → 0 using ease-in."""
        pw = self._pill_win
        if pw is None or not pw.winfo_exists():
            return
        if step > self._PILL_STEPS:
            # Fully retracted — hide the window
            try:
                pw.geometry(f"0x{self._PILL_H}+{px}+{py}")
            except tk.TclError:
                pass
            return

        t    = step / self._PILL_STEPS
        ease = t ** 2                          # quadratic ease-in
        w    = int(self._PILL_MAX_W * (1 - ease))
        w    = max(w, 1)

        if self._float_win and self._float_win.winfo_exists():
            px = self._float_win.winfo_x() + self._CIRCLE_D + 6
            py = self._float_win.winfo_y() + (self._CIRCLE_D - self._PILL_H) // 2

        pw.geometry(f"{w}x{self._PILL_H}+{px}+{py}")
        self._pill_current_w = w

        self._pill_after_id = self.root.after(
            self._PILL_DELAY,
            lambda: self._pill_slide_in(step + 1, px, py))

    def _pill_cancel(self):
        """Abort any in-flight pill animation timer."""
        if self._pill_after_id is not None:
            try:
                self.root.after_cancel(self._pill_after_id)
            except Exception:
                pass
            self._pill_after_id = None

    def _pill_reanchor(self):
        """Reposition the pill window when the circle is dragged."""
        pw = getattr(self, "_pill_win", None)
        if pw is None or not pw.winfo_exists():
            return
        fw = self._float_win
        if fw is None or not fw.winfo_exists():
            return
        px = fw.winfo_x() + self._CIRCLE_D + 6
        py = fw.winfo_y() + (self._CIRCLE_D - self._PILL_H) // 2
        w  = getattr(self, "_pill_current_w", 0)
        if w > 0:
            pw.geometry(f"{w}x{self._PILL_H}+{px}+{py}")

    # ═════════════════════════════════════════════════════════════════════════
    # HOTKEYS / UTILITY
    # ═════════════════════════════════════════════════════════════════════════
    def _bind_hotkeys(self):
        for combo, fn in [
            ("ctrl+shift+s", self._do_capture),
            ("ctrl+shift+h", self._toggle_highlight),
            ("ctrl+shift+w", self._toggle_watch),
        ]:
            try: keyboard.add_hotkey(combo, fn, suppress=False)
            except Exception: pass
        self.root.bind("<Control-r>", lambda e: self._hot_reload())

    def _hot_reload(self):
        self._set_status("Reloading…")
        self.root.after(300, self._do_reload)

    def _do_reload(self):
        self._stop_watch()
        self.audio.stop_all()
        try:
            self.root.destroy()
            os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        except Exception:
            subprocess.Popen([sys.executable, os.path.abspath(__file__)])
            self.root.destroy()

    def _apply_opacity(self, value: int):
        value = max(20, min(100, int(value)))
        self.root.attributes("-alpha", value / 100.0)
        if hasattr(self, "_opacity_lbl"):
            self._opacity_lbl.config(text=f"{value}%")

    def _copy_response(self):
        text = self.response_box.get("1.0", "end").strip()
        if text:
            pyperclip.copy(text)
            self._set_status("Copied to clipboard ✓")

    def _show_response(self, text: str):
        def _u():
            self.response_box.config(state="normal")
            self.response_box.delete("1.0", "end")
            self.response_box.insert("end", text)
            self.response_box.config(state="disabled")
            self.response_box.see("1.0")
        self.root.after(0, _u)

    def _set_status(self, msg: str):
        self.root.after(0, lambda: self._status_bar.config(text=f"  {msg}"))

    def _set_source(self, msg: str):
        self.root.after(0, lambda: self._source_lbl.config(text=msg))

    def _clear(self):
        self._show_response("")
        self._set_source("")
        self.engine.session.end()          # silently reset context memory
        self._set_status("Cleared  ·  session memory reset")

    def _clear_placeholder(self, event):
        if self.ask_entry.get() == "Ask anything…":
            self.ask_entry.delete(0, "end")
            self.ask_entry.config(fg=CREAM)

    def _restore_placeholder(self, event):
        if not self.ask_entry.get():
            self.ask_entry.insert(0, "Ask anything…")
            self.ask_entry.config(fg=MUTED)

    def on_close(self):
        self._stop_watch()
        self.audio.stop_all()
        self.highlight_mode.set(False)
        self.engine.session.end()
        self._pill_cancel()
        if self._pill_win:
            try: self._pill_win.destroy()
            except Exception: pass
        if self._float_win:
            try: self._float_win.destroy()
            except Exception: pass
        try: keyboard.unhook_all_hotkeys()
        except Exception: pass
        self.root.destroy()


# ─────────────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    app  = AceItApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()

if __name__ == "__main__":
    main()
