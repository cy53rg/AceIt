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
# PALETTE  — deep obsidian, warm gold
# ─────────────────────────────────────────────────────────────────────────────
BASE      = "#060606"   # near-black — window background
SURFACE   = "#0d0d0d"   # primary surface
PANEL     = "#111111"   # control panels
RAISED    = "#1c1c1c"   # buttons, inputs
BORDER    = "#282828"   # subtle dividers
GOLD      = "#c9a84c"   # primary accent
GOLD_HI   = "#e8c96a"   # gold hover
GOLD_DIM  = "#6b5a28"   # muted gold
GOLD_SUB  = "#1e190a"   # very dark gold tint background
CREAM     = "#ede8d8"   # readable body text
MUTED     = "#555555"   # secondary labels
DIM       = "#2e2e2e"   # hairlines
GREEN     = "#4caf7d"   # success / session active
CYAN      = "#3ab5c4"   # watcher / speaker
AMBER     = "#e8a020"   # warnings
RED       = "#b84040"   # danger / stop
MIC_CLR   = "#9b59e6"   # purple — microphone
SPK_CLR   = "#3ab5c4"   # cyan  — speaker loopback
IVEW_CLR  = "#e07040"   # orange — interview mode

FONT_UI   = ("Segoe UI", 10)
FONT_MONO = ("Consolas",  11)   # ← enlarged for readability
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
        self.root.configure(bg=BASE)
        self.root.attributes("-topmost", True)
        self.root.geometry("480x940+1380+30")
        self.root.minsize(400, 600)
        self.root.resizable(True, True)

        # Phase 1–3 state
        self.highlight_mode   = tk.BooleanVar(value=False)
        self.highlight_thread = None
        self.last_clipboard   = ""
        self._float_win       = None
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

    # ═════════════════════════════════════════════════════════════════════════
    # UI BUILD
    # ═════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        r = self.root
        self._build_header(r)
        self._build_primary_actions(r)
        # Controls wrapper — always in the pack order, content toggles
        self._ctrl_wrapper = tk.Frame(r, bg=BASE)
        self._ctrl_wrapper.pack(fill="x")
        self._ctrl_frame = tk.Frame(self._ctrl_wrapper, bg=PANEL)
        # NOT packed until user clicks ⚙
        self._build_controls_panel()
        self._build_ask_bar(r)
        self._build_response_area(r)
        self._build_status_bar(r)

    # ── Header ────────────────────────────────────────────────────────────────
    def _build_header(self, r):
        hdr = tk.Frame(r, bg=BASE, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="⚡", bg=BASE, fg=GOLD,
                 font=("Segoe UI", 14, "bold")).pack(side="left", padx=(12, 4))
        tk.Label(hdr, text="AceIt", bg=BASE, fg=CREAM,
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        tk.Label(hdr, text=" Co-Pilot", bg=BASE, fg=MUTED,
                 font=("Segoe UI", 13)).pack(side="left")

        # Right controls
        right = tk.Frame(hdr, bg=BASE)
        right.pack(side="right", padx=8)

        tk.Button(right, text="✕", command=self.on_close,
                  bg=BASE, fg=MUTED, activebackground=RED, activeforeground=CREAM,
                  relief="flat", font=("Segoe UI", 10), padx=6, cursor="hand2", bd=0
                  ).pack(side="right", padx=2)
        tk.Button(right, text="⚙", command=self._toggle_controls,
                  bg=BASE, fg=MUTED, activebackground=RAISED, activeforeground=GOLD,
                  relief="flat", font=("Segoe UI", 13), padx=6, cursor="hand2", bd=0
                  ).pack(side="right", padx=2)

        self._mode_pill = tk.Label(
            hdr, text="  ACTIVE  ", bg=GOLD_SUB, fg=GOLD,
            font=("Segoe UI", 8, "bold"), relief="flat", padx=6, pady=3)
        self._mode_pill.pack(side="right", padx=4)

        tk.Frame(r, bg=GOLD_DIM, height=1).pack(fill="x")

    # ── Primary action row ────────────────────────────────────────────────────
    def _build_primary_actions(self, r):
        act = tk.Frame(r, bg=SURFACE, pady=10)
        act.pack(fill="x", padx=12)
        act.columnconfigure(0, weight=3)
        act.columnconfigure(1, weight=2)

        self.btn_capture = tk.Button(
            act, text="  📷  Capture Screen", command=self._do_capture,
            bg=GOLD, fg=BASE, activebackground=GOLD_HI, activeforeground=BASE,
            relief="flat", font=("Segoe UI", 11, "bold"),
            pady=10, cursor="hand2", bd=0)
        self.btn_capture.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_hl = tk.Button(
            act, text="  🔍  Highlight", command=self._toggle_highlight,
            bg=RAISED, fg=MUTED, activebackground=GOLD_DIM, activeforeground=CREAM,
            relief="flat", font=("Segoe UI", 11),
            pady=10, cursor="hand2", bd=0)
        self.btn_hl.grid(row=0, column=1, sticky="ew")

        tk.Label(r, text="Ctrl+Shift+S  capture  ·  Ctrl+Shift+H  highlight",
                 bg=SURFACE, fg=DIM, font=FONT_TINY).pack(pady=(0, 2))

    # ── Ask bar ───────────────────────────────────────────────────────────────
    def _build_ask_bar(self, r):
        ask_wrap = tk.Frame(r, bg=BORDER, pady=1)
        ask_wrap.pack(fill="x", padx=12, pady=(4, 0))
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

    # ── Response area ─────────────────────────────────────────────────────────
    def _build_response_area(self, r):
        resp_hdr = tk.Frame(r, bg=SURFACE)
        resp_hdr.pack(fill="x", padx=12, pady=(6, 0))
        tk.Label(resp_hdr, text="AI RESPONSE", bg=SURFACE, fg=GOLD_DIM,
                 font=("Segoe UI", 7, "bold")).pack(side="left")
        self._source_lbl = tk.Label(resp_hdr, text="", bg=SURFACE, fg=MUTED,
                                    font=FONT_TINY)
        self._source_lbl.pack(side="left", padx=8)
        tk.Button(resp_hdr, text="⎘ Copy", command=self._copy_response,
                  bg=SURFACE, fg=MUTED, activebackground=RAISED, activeforeground=GOLD,
                  relief="flat", font=FONT_TINY, padx=4, cursor="hand2", bd=0
                  ).pack(side="right")
        tk.Button(resp_hdr, text="🗑", command=self._clear,
                  bg=SURFACE, fg=MUTED, activebackground=RAISED, activeforeground=RED,
                  relief="flat", font=FONT_TINY, padx=4, cursor="hand2", bd=0
                  ).pack(side="right", padx=2)

        resp_outer = tk.Frame(r, bg=BORDER, pady=1, padx=1)
        resp_outer.pack(fill="both", expand=True, padx=12, pady=(2, 0))
        self.response_box = scrolledtext.ScrolledText(
            resp_outer, bg=PANEL, fg=CREAM, font=FONT_MONO,
            relief="flat", wrap="word", padx=14, pady=12,
            state="disabled", insertbackground=GOLD,
            selectbackground=GOLD_DIM, height=14)   # min height guarantee
        self.response_box.pack(fill="both", expand=True)

    # ── Status bar ────────────────────────────────────────────────────────────
    def _build_status_bar(self, r):
        self._status_bar = tk.Label(
            r, text="  Ready", bg=BASE, fg=MUTED,
            font=FONT_TINY, anchor="w", pady=6)
        self._status_bar.pack(fill="x")

    # ── Controls panel (inside _ctrl_wrapper, toggled) ────────────────────────
    def _build_controls_panel(self):
        f = self._ctrl_frame
        f.configure(highlightbackground=GOLD_DIM, highlightthickness=1)
        tk.Frame(f, bg=GOLD_DIM, height=1).pack(fill="x")

        # MODE ─────────────────────────────────────────────────────────────────
        self._section_label(f, "MODE")
        mode_row = tk.Frame(f, bg=PANEL)
        mode_row.pack(fill="x", padx=10, pady=(2, 6))
        self._mode_btns = {}
        for mode, label, clr in [
            (ModeState.ACTIVE,  "⚡ Active",  GOLD),
            (ModeState.AMBIENT, "🌙 Ambient", CYAN),
            (ModeState.GUIDED,  "🎯 Guided",  IVEW_CLR),
        ]:
            btn = tk.Button(
                mode_row, text=label,
                command=lambda m=mode: self._switch_mode(m),
                bg=RAISED, fg=MUTED, activebackground=GOLD_DIM, activeforeground=CREAM,
                relief="flat", font=FONT_SM, padx=8, pady=4, cursor="hand2", bd=0)
            btn.pack(side="left", padx=(0, 4))
            self._mode_btns[mode] = btn
        self._refresh_mode_btns()

        # SESSION ──────────────────────────────────────────────────────────────
        self._section_label(f, "SESSION MEMORY")
        sess_row = tk.Frame(f, bg=PANEL)
        sess_row.pack(fill="x", padx=10, pady=(2, 2))
        self._btn_sess_start = tk.Button(
            sess_row, text="▶ Start", command=self._start_session,
            bg=RAISED, fg=GOLD, activebackground=GREEN, activeforeground=CREAM,
            relief="flat", font=FONT_SM, padx=8, pady=3, cursor="hand2", bd=0)
        self._btn_sess_start.pack(side="left", padx=(0, 4))
        self._btn_sess_end = tk.Button(
            sess_row, text="⏹ End", command=self._end_session,
            bg=RAISED, fg=MUTED, activebackground=RED, activeforeground=CREAM,
            relief="flat", font=FONT_SM, padx=8, pady=3, cursor="hand2", bd=0)
        self._btn_sess_end.pack(side="left", padx=(0, 8))
        self._session_lbl = tk.Label(sess_row, text="No session",
                                     bg=PANEL, fg=MUTED, font=FONT_TINY)
        self._session_lbl.pack(side="left")
        self.root.after(2000, self._tick_session_label)

        # AUDIO ────────────────────────────────────────────────────────────────
        self._section_label(f, "AUDIO LISTENER")

        # Device info
        dev_row = tk.Frame(f, bg=PANEL)
        dev_row.pack(fill="x", padx=10, pady=(0, 2))
        mic_name = self.audio._mic_device_name[:28] + "…" \
            if len(self.audio._mic_device_name) > 30 else self.audio._mic_device_name
        spk_name = self.audio._spk_device_name[:28] + "…" \
            if len(self.audio._spk_device_name) > 30 else self.audio._spk_device_name
        tk.Label(dev_row,
                 text=f"🎤 {mic_name}   🔊 {spk_name}",
                 bg=PANEL, fg=MUTED, font=FONT_TINY,
                 wraplength=380, justify="left").pack(side="left")

        audio_row = tk.Frame(f, bg=PANEL)
        audio_row.pack(fill="x", padx=10, pady=(2, 4))

        self._btn_mic = tk.Button(
            audio_row, text="🎤  Mic  OFF",
            command=self._toggle_mic,
            bg=RAISED, fg=MUTED,
            activebackground=MIC_CLR, activeforeground=CREAM,
            relief="flat", font=FONT_SM, padx=8, pady=4, cursor="hand2", bd=0)
        self._btn_mic.pack(side="left", padx=(0, 6))

        self._btn_spk = tk.Button(
            audio_row, text="🔊  Speaker  OFF",
            command=self._toggle_speaker,
            bg=RAISED, fg=MUTED,
            activebackground=SPK_CLR, activeforeground=BASE,
            relief="flat", font=FONT_SM, padx=8, pady=4, cursor="hand2", bd=0)
        self._btn_spk.pack(side="left", padx=(0, 6))

        tk.Button(audio_row, text="📋 Devices",
                  command=self._show_device_list,
                  bg=RAISED, fg=MUTED, activebackground=RAISED, activeforeground=GOLD,
                  relief="flat", font=FONT_TINY, padx=4, pady=4, cursor="hand2", bd=0
                  ).pack(side="left")

        # Interview mode toggle
        ivw_row = tk.Frame(f, bg=PANEL)
        ivw_row.pack(fill="x", padx=10, pady=(2, 4))
        self._btn_ivw = tk.Button(
            ivw_row, text="🎙 Interview Mode  OFF",
            command=self._toggle_interview,
            bg=RAISED, fg=MUTED,
            activebackground=IVEW_CLR, activeforeground=CREAM,
            relief="flat", font=FONT_SM, padx=8, pady=4, cursor="hand2", bd=0)
        self._btn_ivw.pack(side="left")
        tk.Label(ivw_row,
                 text=" Speaker=interviewer  ·  Mic=you",
                 bg=PANEL, fg=MUTED, font=FONT_TINY).pack(side="left", padx=6)

        # Live transcript feed
        feed_row = tk.Frame(f, bg=RAISED)
        feed_row.pack(fill="x", padx=10, pady=(0, 4))
        self._audio_feed = tk.Label(
            feed_row, text="Audio off — enable Mic or Speaker above",
            bg=RAISED, fg=MUTED, font=FONT_TINY,
            anchor="w", justify="left", wraplength=380, pady=4)
        self._audio_feed.pack(fill="x", padx=6)

        if not HAS_AUDIO:
            tk.Label(f, text="⚠  pip install sounddevice numpy soundfile",
                     bg=PANEL, fg=AMBER, font=FONT_TINY).pack(padx=10, pady=(0,4), anchor="w")

        # SCREEN WATCHER ───────────────────────────────────────────────────────
        self._section_label(f, "SCREEN WATCHER  ·  Ctrl+Shift+W")
        watch_row = tk.Frame(f, bg=PANEL)
        watch_row.pack(fill="x", padx=10, pady=(2, 2))
        self.btn_watch = tk.Button(
            watch_row, text="▶ Start Watching", command=self._toggle_watch,
            bg=RAISED, fg=CYAN, activebackground=CYAN, activeforeground=BASE,
            relief="flat", font=FONT_SM, padx=8, pady=3, cursor="hand2", bd=0)
        self.btn_watch.pack(side="left", padx=(0, 8))
        self._watch_scans_lbl = tk.Label(watch_row, text="Scans: 0  Triggers: 0",
                                         bg=PANEL, fg=MUTED, font=FONT_TINY)
        self._watch_scans_lbl.pack(side="left")
        self._watch_diff_lbl = tk.Label(watch_row, text="",
                                        bg=PANEL, fg=CYAN, font=FONT_TINY)
        self._watch_diff_lbl.pack(side="right")

        int_row = tk.Frame(f, bg=PANEL)
        int_row.pack(fill="x", padx=10, pady=(4, 2))
        tk.Label(int_row, text="Scan every", bg=PANEL, fg=MUTED,
                 font=FONT_TINY).pack(side="left")
        self._interval_lbl = tk.Label(int_row, text="5 s", bg=PANEL,
                                      fg=CREAM, font=FONT_TINY, width=4)
        self._interval_lbl.pack(side="right")
        ttk.Scale(int_row, from_=3, to=15, orient="horizontal",
                  variable=self._watch_interval,
                  command=lambda v: self._interval_lbl.config(text=f"{int(float(v))} s")
                  ).pack(side="left", fill="x", expand=True, padx=6)

        sens_row = tk.Frame(f, bg=PANEL)
        sens_row.pack(fill="x", padx=10, pady=(2, 2))
        tk.Label(sens_row, text="Sensitivity", bg=PANEL, fg=MUTED,
                 font=FONT_TINY).pack(side="left")
        for lbl in ("Low", "Medium", "High"):
            tk.Radiobutton(
                sens_row, text=lbl, variable=self._sensitivity_var, value=lbl,
                bg=PANEL, fg=CREAM, selectcolor=GOLD_DIM,
                activebackground=PANEL, activeforeground=GOLD,
                font=FONT_TINY, cursor="hand2").pack(side="left", padx=6)

        # TOOLS ────────────────────────────────────────────────────────────────
        self._section_label(f, "TOOLS")
        util_row = tk.Frame(f, bg=PANEL)
        util_row.pack(fill="x", padx=10, pady=(2, 4))
        for lbl, cmd in [("🔄 Refresh", self._hot_reload),
                          ("🗗 Float",   self._enter_float),
                          ("🗑 Clear",   self._clear)]:
            tk.Button(util_row, text=lbl, command=cmd,
                      bg=RAISED, fg=MUTED, activebackground=GOLD_DIM, activeforeground=CREAM,
                      relief="flat", font=FONT_TINY, padx=6, pady=3, cursor="hand2", bd=0
                      ).pack(side="left", padx=(0, 4))

        op_row = tk.Frame(f, bg=PANEL)
        op_row.pack(fill="x", padx=10, pady=(2, 8))
        tk.Label(op_row, text="Opacity", bg=PANEL, fg=MUTED, font=FONT_TINY).pack(side="left")
        self._opacity_lbl = tk.Label(op_row, text="95%", bg=PANEL,
                                     fg=CREAM, font=FONT_TINY, width=4)
        self._opacity_lbl.pack(side="right")
        ttk.Scale(op_row, from_=20, to=100, orient="horizontal",
                  variable=self.opacity_var,
                  command=lambda v: self._apply_opacity(int(float(v)))
                  ).pack(side="left", fill="x", expand=True, padx=6)

    def _section_label(self, parent, text):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(row, text=text, bg=PANEL, fg=GOLD_DIM,
                 font=("Segoe UI", 7, "bold")).pack(side="left")
        tk.Frame(row, bg=GOLD_DIM, height=1).pack(
            side="left", fill="x", expand=True, padx=(6, 0))

    def _toggle_controls(self):
        if self._ctrl_frame.winfo_viewable():
            self._ctrl_frame.pack_forget()
        else:
            self._ctrl_frame.pack(fill="x", in_=self._ctrl_wrapper)

    # ═════════════════════════════════════════════════════════════════════════
    # INTERVIEW MODE
    # ═════════════════════════════════════════════════════════════════════════
    def _toggle_interview(self):
        on = not self._interview_mode.get()
        self._interview_mode.set(on)
        self.audio.interview_mode = on

        if on:
            self._btn_ivw.config(bg=IVEW_CLR, fg=CREAM, text="🎙 Interview Mode  ON")
            # Inject specialist system prompt into session
            if not self.engine.session.is_active:
                self.engine.session.start(INTERVIEW_SYSTEM)
            else:
                self.engine.session.set_system(INTERVIEW_SYSTEM)
            self._set_status("🎙 Interview Mode ON — start Mic + Speaker")
            # Auto-start both audio sources
            self._set_mic(True)
            self._set_speaker(True)
        else:
            self._btn_ivw.config(bg=RAISED, fg=MUTED, text="🎙 Interview Mode  OFF")
            # Restore mode system prompt
            self.engine.session.set_system(MODE_SYSTEMS[self.engine.mode])
            self._set_status("🎙 Interview Mode OFF")

    # ═════════════════════════════════════════════════════════════════════════
    # AUDIO CONTROLS
    # ═════════════════════════════════════════════════════════════════════════
    def _set_mic(self, on: bool):
        if on and not self.audio.mic_active:
            ok = self.audio.start_mic()
            if ok:
                self._btn_mic.config(bg=MIC_CLR, fg=CREAM, text="🎤  Mic  ON")
        elif not on and self.audio.mic_active:
            self.audio.stop_mic()
            self._btn_mic.config(bg=RAISED, fg=MUTED, text="🎤  Mic  OFF")

    def _set_speaker(self, on: bool):
        if on and not self.audio.speaker_active:
            ok = self.audio.start_speaker()
            if ok:
                self._btn_spk.config(bg=SPK_CLR, fg=BASE, text="🔊  Speaker  ON")
        elif not on and self.audio.speaker_active:
            self.audio.stop_speaker()
            self._btn_spk.config(bg=RAISED, fg=MUTED, text="🔊  Speaker  OFF")

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
        # Don't allow mode switch while interview is active
        if self._interview_mode.get():
            self._set_status("⚠  End Interview Mode before switching modes")
            return
        self.engine.set_mode(mode)
        self._refresh_mode_btns()
        clr = MODE_COLORS[mode]
        self._mode_pill.config(
            text=f"  {mode.name}  ",
            fg=clr, bg=GOLD_SUB)
        self._set_status(f"Mode → {mode.name.capitalize()}")

    def _refresh_mode_btns(self):
        current = self.engine.mode
        for mode, btn in self._mode_btns.items():
            if mode == current:
                clr = MODE_COLORS[mode]
                btn.config(bg=GOLD_SUB, fg=clr, font=("Segoe UI", 9, "bold"))
            else:
                btn.config(bg=RAISED, fg=MUTED, font=FONT_SM)

    def _on_engine_event(self, event_type: str, payload: dict):
        if event_type == "mode_changed":
            self.root.after(0, lambda: self._mode_pill.config(
                text=f"  {self.engine.mode_label}  "))
        elif event_type == "suppressed" and payload.get("reason") == "cooldown":
            rem = payload.get("remaining", 30)
            self._set_status(f"🌙  Ambient cooldown — {rem}s")

    def _start_session(self):
        system = INTERVIEW_SYSTEM if self._interview_mode.get() else MODE_SYSTEMS[self.engine.mode]
        self.engine.session.start(system)
        self._btn_sess_start.config(bg=GOLD_SUB, fg=GREEN)
        self._set_status("Session started ✓  AI will remember context")

    def _end_session(self):
        self.engine.session.end()
        self._btn_sess_start.config(bg=RAISED, fg=GOLD)
        self._set_status("Session ended — memory cleared")

    def _tick_session_label(self):
        s = self.engine.session
        if s.is_active:
            self._session_lbl.config(text=f"● {s.summary}", fg=GREEN)
        else:
            self._session_lbl.config(text="No session", fg=MUTED)
        self.root.after(2000, self._tick_session_label)

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
            self.btn_hl.config(bg=RAISED, fg=MUTED, text="  🔍  Highlight")
            self._set_status("Highlight OFF")
        else:
            self.highlight_mode.set(True)
            self.btn_hl.config(bg=GOLD_DIM, fg=GOLD, text="  ✅  Highlight ON")
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
        self.btn_watch.config(text="⏸ Pause Watching", bg=AMBER, fg=BASE)
        self._set_status("👁  Watcher started")
        if self._watch_thread is None or not self._watch_thread.is_alive():
            self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
            self._watch_thread.start()
        self._watch_next_scan_at = time.time() + self._watch_interval.get()
        self._tick_countdown()

    def _stop_watch(self):
        self.watch_active.set(False)
        self._watcher_stop.set()
        self.btn_watch.config(text="▶ Start Watching", bg=RAISED, fg=CYAN)
        self._set_status("⏸  Watcher paused")
        self.root.after(0, lambda: self._watch_diff_lbl.config(text=""))

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
    # FLOAT MODE
    # ═════════════════════════════════════════════════════════════════════════
    def _enter_float(self):
        self.root.withdraw()
        fw = tk.Toplevel(self.root)
        fw.overrideredirect(True)
        fw.attributes("-topmost", True)
        fw.attributes("-alpha", 0.88)
        fw.geometry("56x56+20+300")
        icon_color = IVEW_CLR if self._interview_mode.get() else (
            CYAN if self.watch_active.get() else GOLD)
        fw.configure(bg=icon_color)
        self._float_win = fw
        self._float_did_drag = False
        lbl = tk.Label(fw, text="⚡", font=("Segoe UI", 22),
                       bg=icon_color, fg=BASE, cursor="hand2")
        lbl.pack(fill="both", expand=True)
        for w in (fw, lbl):
            w.bind("<ButtonPress-1>",   self._float_drag_start)
            w.bind("<B1-Motion>",       self._float_drag_move)
            w.bind("<ButtonRelease-1>", self._float_click_or_snap)

    def _leave_float(self):
        if self._float_win:
            self._float_win.destroy()
            self._float_win = None
        self.root.deiconify()
        self.root.lift()

    def _float_drag_start(self, e):
        self._drag_x = e.x_root - self._float_win.winfo_x()
        self._drag_y = e.y_root - self._float_win.winfo_y()
        self._drag_start_x = e.x_root
        self._drag_start_y = e.y_root
        self._float_did_drag = False

    def _float_drag_move(self, e):
        if abs(e.x_root - self._drag_start_x) > 5 or abs(e.y_root - self._drag_start_y) > 5:
            self._float_did_drag = True
        self._float_win.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

    def _float_click_or_snap(self, e):
        if self._float_did_drag: self._float_snap_edge()
        else: self._leave_float()

    def _float_snap_edge(self):
        fw = self._float_win
        sw, sh = fw.winfo_screenwidth(), fw.winfo_screenheight()
        x, y = fw.winfo_x(), fw.winfo_y()
        w, h = fw.winfo_width(), fw.winfo_height()
        dists  = {"left": x, "right": sw-x-w, "top": y, "bottom": sh-y-h}
        coords = {"left": (0,y), "right": (sw-w,y), "top": (x,0), "bottom": (x,sh-h)}
        nx, ny = coords[min(dists, key=dists.get)]
        fw.geometry(f"+{nx}+{ny}")

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
        self._set_status("Cleared")

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
