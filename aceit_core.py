"""
aceit_core.py — AceIt Backend Core (PySide6 / UI-Free)
"""
from __future__ import annotations

import collections
import io
import queue
import threading
import time
import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Deque, List, Optional

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ── Groq client ───────────────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
groq_client  = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL   = os.getenv("ACEIT_MODEL", "llama-3.3-70b-versatile")

# Available models for the model selector
GROQ_MODELS: list[str] = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]

GROQ_MODEL_LABELS: dict[str, str] = {
    "llama-3.3-70b-versatile": "Llama 3.3 70B",
    "llama-3.1-8b-instant":    "Llama 3.1 8B",
    "llama3-70b-8192":         "Llama3 70B",
    "llama3-8b-8192":          "Llama3 8B",
    "mixtral-8x7b-32768":      "Mixtral 8x7B",
    "gemma2-9b-it":            "Gemma2 9B",
}

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

# ── Constants ─────────────────────────────────────────────────────────────────
SENSITIVITY = {
    "Low":    {"sim": 0.60, "chars": 80},
    "Medium": {"sim": 0.75, "chars": 30},
    "High":   {"sim": 0.88, "chars": 10},
}
MIN_TRANSCRIPT_WORDS = 4

class ModeState(Enum):
    ACTIVE    = auto()
    AMBIENT   = auto()
    GUIDED    = auto()
    INTERVIEW = auto()

MODE_SYSTEMS: dict[ModeState, str] = {
    ModeState.ACTIVE: "You are AceIt in Active Mode — a razor-sharp real-time task assistant. Immediately solve any questions, debug errors, or complete explicit tasks visible on screen. Be direct, thorough, and fast. Use numbered steps for multi-step problems.",
    ModeState.AMBIENT: "You are AceIt in Ambient Mode — a silent, observant co-pilot. Only speak when you notice something genuinely actionable: a mistake, efficiency tip, or important pattern. If nothing is noteworthy, respond with exactly: NOTHING\nOtherwise prefix with '💡 Tip:' / '⚠️ Notice:' and use at most 2 sentences.",
    ModeState.GUIDED: "You are AceIt in Guided Mode — an expert interactive mentor. Guide the user step-by-step. Break every explanation into numbered steps. After each response, ask one focused follow-up question. Never skip steps. Acknowledge completed milestones.",
    ModeState.INTERVIEW: (
        "You are AceIt in Interview Coach Mode. You receive live transcripts from two sources:\n"
        "[SPEAKER] = the interviewer speaking\n"
        "[MIC]     = the user (interviewee) speaking\n\n"
        "When you receive [SPEAKER] content:\n"
        "• If it contains a question: provide 3 concise bullet-point talking points the user should hit, "
        "then a 2-sentence model answer example.\n"
        "• If it's context/statement: note it briefly and suggest how to build on it.\n\n"
        "When you receive [MIC] content:\n"
        "• Evaluate the response in 1 sentence (what was strong).\n"
        "• Suggest 1-2 specific improvements or stronger phrasings.\n"
        "• Keep feedback constructive, brief, and actionable.\n\n"
        "If USER CONTEXT (résumé / job description) is available in the session, tailor every coaching "
        "response to the candidate's stated background and target role.\n\n"
        "Be fast — the conversation is live. Lead every response with the source label."
    ),
}

# Backwards-compat alias (referenced by AudioEngine interview_mode flag elsewhere)
INTERVIEW_SYSTEM = MODE_SYSTEMS[ModeState.INTERVIEW]

# ── Response Style Definitions ────────────────────────────────────────────────
RESPONSE_STYLES = ["Terse", "Direct", "Balanced", "Detailed"]

STYLE_SUFFIXES: dict[str, str] = {
    "Terse":    "\n\n[RESPONSE STYLE: Terse] Reply in 1–3 sentences maximum. No preamble, no caveats, no filler. Raw signal only.",
    "Direct":   "\n\n[RESPONSE STYLE: Direct] Be concise and clear. Lead with the answer. Skip pleasantries. Use short paragraphs or bullets only when they genuinely aid clarity.",
    "Balanced": "\n\n[RESPONSE STYLE: Balanced] Provide a complete answer with appropriate context. Use structure where helpful. Neither too brief nor verbose.",
    "Detailed": "\n\n[RESPONSE STYLE: Detailed] Give a thorough, comprehensive answer. Include relevant context, edge cases, examples, and reasoning. Prefer numbered steps for procedures.",
}

@dataclass
class ContextEntry:
    role:      str
    content:   str
    source:    str
    timestamp: float = field(default_factory=time.time)
    pinned:    bool  = False

    def to_chat_message(self) -> dict:
        role   = "assistant" if self.role == "assistant" else "user"
        prefix = f"[{self.source.upper()}] " if self.role not in ("user", "assistant") else ""
        return {"role": role, "content": f"{prefix}{self.content}"}

class SessionManager:
    MAX_BUFFER   = 200
    RECENT_TURNS = 24
    MAX_AGE_H    = 2
    DEFAULT_SYSTEM = "You are AceIt, a real-time AI study and work assistant on the user's desktop. Answer questions directly and completely."

    def __init__(self):
        self._buffer:  Deque[ContextEntry] = collections.deque(maxlen=self.MAX_BUFFER)
        self._system   = self.DEFAULT_SYSTEM
        self._active   = False
        self._started: Optional[float] = None
        self._lock     = threading.Lock()
        self.response_style: str = "Balanced"   # Terse | Direct | Balanced | Detailed

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
        with self._lock:
            self._system = system

    def add(self, entry: ContextEntry) -> None:
        if not self._active: return
        with self._lock:
            cutoff = time.time() - self.MAX_AGE_H * 3600
            while self._buffer and self._buffer[0].timestamp < cutoff:
                self._buffer.popleft()
            self._buffer.append(entry)

    def add_user(self, content: str, source: str = "user") -> None:
        self.add(ContextEntry(role="user", content=content, source=source))

    def add_ai(self, content: str) -> None:
        self.add(ContextEntry(role="assistant", content=content, source="ai"))

    def add_pinned_context(self, content: str, source: str = "context") -> None:
        """
        Inject a permanently-pinned entry (e.g. résumé / job description) that
        survives buffer rotation and is always included near the top of every
        build_messages() call.  Calling this again with the same source label
        replaces the previous pinned entry for that source to avoid duplication.
        """
        # Remove any existing pinned entry from the same source so re-uploads
        # don't pile up.
        with self._lock:
            to_remove = [e for e in self._buffer if e.pinned and e.source == source]
            for e in to_remove:
                try:
                    self._buffer.remove(e)
                except ValueError:
                    pass
        entry = ContextEntry(role="user", content=content, source=source, pinned=True)
        # Bypass the is_active guard — context can be injected before the first
        # live query arrives.
        with self._lock:
            self._buffer.append(entry)
            if not self._active:
                self._active  = True
                self._started = time.time()

    def build_messages(self) -> List[dict]:
        style_suffix = STYLE_SUFFIXES.get(self.response_style, "")
        msgs = [{"role": "system", "content": self._system + style_suffix}]
        with self._lock: buf = list(self._buffer)
        if not buf: return msgs

        seen_ids: set[int] = set()

        def _add(entry: ContextEntry) -> None:
            eid = id(entry)
            if eid in seen_ids:
                return
            seen_ids.add(eid)
            msgs.append(entry.to_chat_message())

        # Always include the oldest (anchor) entry first
        _add(buf[0])

        # Pinned entries: walk the full buffer so nothing is missed
        for e in buf[1:]:
            if e.pinned:
                _add(e)

        # Recent tail: skip the anchor and any pinned entries already added
        for e in buf[-self.RECENT_TURNS:]:
            if not e.pinned:
                _add(e)

        return msgs

    @property
    def is_active(self) -> bool: return self._active
    @property
    def summary(self) -> str:
        if not self._active: return "No session"
        m, s = divmod(int(time.time() - (self._started or time.time())), 60)
        toks = sum(len(e.content) for e in self._buffer) // 6
        return f"{m:02d}:{s:02d}  ·  {len(self._buffer)} entries  ·  ~{toks} tok"

class StateEngine:
    ACTIVE_DEDUP_S = 5; AMBIENT_COOLDOWN_S = 30; AMBIENT_MIN_CHARS = 60; GUIDED_MAX_TURNS = 60
    INTERVIEW_MAX_TURNS = 200   # effectively unlimited for a real interview session

    def __init__(self, raw_query_fn: Callable[[List[dict]], None]):
        self._query = raw_query_fn
        self.session = SessionManager()
        self._mode = ModeState.ACTIVE
        self._lock = threading.Lock()
        self._listeners: List[Callable] = []
        self._active_last_text = ""; self._active_last_time = 0.0
        self._ambient_last_fire = 0.0; self._ambient_last_text = ""
        self.guided_turns = 0
        self.interview_turns = 0
        # When True the ambient cooldown is skipped (set automatically in Interview mode)
        self.interview_cooldown_disabled: bool = False

    def set_mode(self, mode: ModeState) -> None:
        with self._lock:
            if mode == self._mode: return
            old, self._mode = self._mode, mode
            self.guided_turns = 0
            self.interview_turns = 0
            self.interview_cooldown_disabled = (mode == ModeState.INTERVIEW)
        self.session.set_system(MODE_SYSTEMS[mode])
        self._emit("mode_changed", {"from": old.name, "to": mode.name})

    def handle_input(self, text: str, source: str = "ask") -> str:
        text = text.strip()
        if not text: return "suppressed:empty"
        if not self.session.is_active: self.session.start(MODE_SYSTEMS[self._mode])
        mode = self._mode
        if mode == ModeState.ACTIVE:    return self._active(text, source)
        elif mode == ModeState.AMBIENT: return self._ambient(text, source)
        elif mode == ModeState.GUIDED:  return self._guided(text, source)
        else:                           return self._interview(text, source)

    def store_ai_response(self, answer: str) -> None: self.session.add_ai(answer)

    def _active(self, text: str, source: str) -> str:
        now = time.time()
        if text == self._active_last_text and (now - self._active_last_time) < self.ACTIVE_DEDUP_S: return "suppressed:duplicate"
        self._active_last_text, self._active_last_time = text, now
        self.session.add_user(text, source)
        self._query(self.session.build_messages())
        return "fired"

    def _ambient(self, text: str, source: str) -> str:
        if source == "ask":
            self.session.add_user(text, source); self._query(self.session.build_messages()); return "fired"
        now = time.time()
        if not self.interview_cooldown_disabled:
            if (now - self._ambient_last_fire) < self.AMBIENT_COOLDOWN_S: return "suppressed:cooldown"
        if sum(len(l) for l in (set(text.splitlines()) - set(self._ambient_last_text.splitlines()))) < self.AMBIENT_MIN_CHARS: return "suppressed:low_delta"
        self._ambient_last_fire, self._ambient_last_text = now, text
        self.session.add_user(f"[AMBIENT] Screen content:\n\n{text}\n\nSurface anything useful.", source)
        self._query(self.session.build_messages())
        return "fired"

    def _guided(self, text: str, source: str) -> str:
        if self.guided_turns >= self.GUIDED_MAX_TURNS: return "suppressed:max_turns"
        self.guided_turns += 1
        self.session.add_user(text, source)
        self._query(self.session.build_messages())
        return "fired"

    def _interview(self, text: str, source: str) -> str:
        """
        Interview Mode dispatcher.

        Audio sources ("mic" / "speaker") are prefixed with the canonical
        [MIC] / [SPEAKER] labels the interview system prompt expects.
        Direct "ask" messages (typed by the user) are passed through untagged
        so the user can inject ad-hoc questions to the coach mid-interview.
        """
        if self.interview_turns >= self.INTERVIEW_MAX_TURNS:
            return "suppressed:max_turns"
        self.interview_turns += 1

        if source == "mic":
            labelled = f"[MIC] {text}"
        elif source == "speaker":
            labelled = f"[SPEAKER] {text}"
        else:
            labelled = text   # typed "ask" — no label needed

        self.session.add_user(labelled, source)
        self._query(self.session.build_messages())
        return "fired"

    def get_debug_state(self) -> str:
        """
        Compile a formatted diagnostic report of the current engine state.
        Returned as a plain string ready to be printed into the UI response area.
        """
        import aceit_core as _self_mod   # runtime reference for the live GROQ_MODEL value

        # ── Session buffer stats ──────────────────────────────────────────────
        with self.session._lock:
            buf_snapshot = list(self.session._buffer)
        total_entries = len(buf_snapshot)
        total_tokens  = sum(len(e.content) for e in buf_snapshot) // 6

        # ── Audio status ──────────────────────────────────────────────────────
        # StateEngine holds no direct reference to AudioEngine; we report what
        # we know from the interview_cooldown_disabled flag as a proxy.
        audio_hint = "Enabled (Interview cooldown bypassed)" if self.interview_cooldown_disabled else "Standard"

        # ── Watcher sensitivity — stored on the session via SENSITIVITY keys ──
        # We expose the raw SENSITIVITY dict keys for reference.
        sens_keys = ", ".join(SENSITIVITY.keys())

        lines = [
            "╔══════════════════════════════════════╗",
            "║         AceIt  Debug State           ║",
            "╚══════════════════════════════════════╝",
            "",
            f"  Active Mode        : {self.mode_label}",
            f"  Current Groq Model : {_self_mod.GROQ_MODEL}",
            f"  Response Style     : {self.session.response_style}",
            f"  Watcher Sensitivity: (available levels: {sens_keys})",
            f"  Audio Status       : {audio_hint}",
            "",
            "  ── Session Buffer ──────────────────",
            f"  Total Entries      : {total_entries}",
            f"  Approx Tokens      : ~{total_tokens}",
            f"  Session Active     : {self.session.is_active}",
            f"  Session Summary    : {self.session.summary}",
            "",
        ]
        return "\n".join(lines)

    def on_event(self, cb: Callable) -> None: self._listeners.append(cb)
    def _emit(self, t: str, p: dict | None = None) -> None:
        for cb in self._listeners:
            try: cb(t, p or {})
            except Exception: pass
    @property
    def mode(self) -> ModeState: return self._mode
    @property
    def mode_label(self) -> str:
        if self._mode == ModeState.GUIDED:
            return f"GUIDED T{self.guided_turns}"
        if self._mode == ModeState.INTERVIEW:
            return f"INTERVIEW T{self.interview_turns}"
        return self._mode.name

class AudioEngine:
    SAMPLE_RATE = 16_000; CHUNK_SECS = 8; SILENCE_RMS = 0.004
    def __init__(self, on_transcript: Callable[[str, str], None], on_status: Callable[[str], None]):
        self.on_transcript = on_transcript; self.on_status = on_status
        self.interview_mode = False; self._mic_active = False; self._spk_active = False
        self._mic_stop = threading.Event(); self._spk_stop = threading.Event()
        self._mic_device_idx, self._mic_device_name = self._find_mic() if HAS_AUDIO else (None, "Unknown")
        self._spk_device_idx, self._spk_device_name = self._find_loopback() if HAS_AUDIO else (None, "Not found")

    @staticmethod
    def _find_mic() -> tuple:
        try:
            idx = sd.default.device[0]
            return idx, sd.query_devices(idx)["name"]
        except: return None, "Unknown"

    @staticmethod
    def _find_loopback() -> tuple:
        try:
            apis = sd.query_hostapis(); wasapi = next((i for i, h in enumerate(apis) if "WASAPI" in h["name"]), None)
            if wasapi is None: return None, "No WASAPI API"
            devs = sd.query_devices()
            for i, d in enumerate(devs):
                if d["hostapi"] == wasapi and d["max_input_channels"] > 0 and "loopback" in d["name"].lower(): return i, d["name"]
            for i, d in enumerate(devs):
                if d["hostapi"] == wasapi and d["max_input_channels"] > 0 and i != sd.default.device[0]: return i, d["name"]
        except Exception as e: return None, str(e)
        return None, "Not found"

    def start_mic(self) -> bool:
        if not HAS_AUDIO or not HAS_SF: return False
        self._mic_stop.clear(); self._mic_active = True
        threading.Thread(target=self._record_loop, args=("mic", self._mic_device_idx, self._mic_stop), daemon=True).start()
        return True

    def stop_mic(self) -> None: self._mic_active = False; self._mic_stop.set()
    def start_speaker(self) -> bool:
        if not HAS_AUDIO or not HAS_SF or self._spk_device_idx is None: return False
        self._spk_stop.clear(); self._spk_active = True
        threading.Thread(target=self._record_loop, args=("speaker", self._spk_device_idx, self._spk_stop), daemon=True).start()
        return True

    def stop_speaker(self) -> None: self._spk_active = False; self._spk_stop.set()
    def stop_all(self) -> None: self.stop_mic(); self.stop_speaker()

    # ── Public property accessors ─────────────────────────────────────────────
    @property
    def mic_device_name(self) -> str:   return self._mic_device_name
    @property
    def spk_device_name(self) -> str:   return self._spk_device_name
    @property
    def mic_active(self) -> bool:       return self._mic_active
    @property
    def speaker_active(self) -> bool:   return self._spk_active

    def _record_loop(self, source: str, dev_idx: int, stop_event: threading.Event) -> None:
        q = queue.Queue()
        def cb(indata, frames, time_info, status):
            if stop_event.is_set(): raise sd.CallbackStop()
            q.put(indata.copy())
        n_frames = int(self.CHUNK_SECS * self.SAMPLE_RATE)
        while not stop_event.is_set():
            try:
                chunks = []; col = 0
                with sd.InputStream(samplerate=self.SAMPLE_RATE, channels=1, dtype="float32", device=dev_idx, callback=cb, blocksize=int(self.SAMPLE_RATE*0.5)):
                    while col < n_frames and not stop_event.is_set():
                        try:
                            b = q.get(timeout=1.0)
                            if b is not None: chunks.append(b); col += len(b)
                        except queue.Empty: continue
                if stop_event.is_set() or not chunks: return
                audio = np.concatenate(chunks, axis=0)[:n_frames]
                if float(np.sqrt(np.mean(audio**2))) < self.SILENCE_RMS: continue
                tx = self._transcribe(audio, source)
                if tx and len(tx.split()) >= MIN_TRANSCRIPT_WORDS: self.on_transcript(tx, source)
            except sd.CallbackStop: return
            except Exception as e: self.on_status(f"Error: {e}"); time.sleep(3)

    def _transcribe(self, frames, source) -> str:
        try:
            buf = io.BytesIO()
            sf.write(buf, frames, self.SAMPLE_RATE, format="WAV", subtype="PCM_16")
            buf.seek(0); buf.name = "audio.wav"
            resp = groq_client.audio.transcriptions.create(model="whisper-large-v3", file=buf, response_format="text")
            return (resp if isinstance(resp, str) else resp.text).strip()
        except: return ""