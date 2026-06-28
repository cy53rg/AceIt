"""
atlas_audio.py — Speech capture (Whisper STT), passive audio watching, and TTS routing.

Extracted from atlas_core.py (AudioWatcher, AudioEngine, Kokoro/ElevenLabs voice matrix).
"""
from __future__ import annotations

import io
import os
import queue
import re
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from dotenv import load_dotenv
from groq import Groq

from atlas_logging import get_logger

load_dotenv()
log = get_logger("audio")

# ── Optional Voice Activity Detection backend ─────────────────────────────────
try:
    import webrtcvad  # type: ignore
    _HAS_WEBRTCVAD = True
except Exception:  # pragma: no cover - optional dependency
    webrtcvad = None  # type: ignore
    _HAS_WEBRTCVAD = False

_default_whisper = "whisper-large-v3-turbo"
ATLAS_WHISPER_MODEL = (
    os.environ.get("ATLAS_WHISPER_MODEL") or _default_whisper
).strip() or _default_whisper

def _endpoint_silence_s() -> float:
    """Trailing silence (seconds) after speech before Atlas transcribes and replies."""
    raw = (os.environ.get("ATLAS_ENDPOINT_SILENCE_S") or "1.5").strip()
    try:
        return max(0.8, min(4.0, float(raw)))
    except ValueError:
        return 1.5

_VOICE_ALWAYS_ON = os.environ.get("ATLAS_VOICE_ALWAYS_ON", "").strip().lower() in (
    "1", "true", "yes", "on",
)
_CAPTURE_SYSTEM_AUDIO = os.environ.get("ATLAS_CAPTURE_SYSTEM_AUDIO", "").strip().lower() in (
    "1", "true", "yes", "on",
)

groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))


class AudioWatcher:
    """
    Passive voice + optional system-audio context for copilot mode.
    Reuses AudioEngine capture loops — never duplicates mic/speaker code.
    """

    _USER_TYPED_GRACE_S = 5.0
    _BUFFER_MAX_S = 60.0

    def __init__(
        self,
        on_voice_input: Callable[[str], None],
    ) -> None:
        self._on_voice_input = on_voice_input
        self._audio: Optional["AudioEngine"] = None
        self._copilot_active = False
        self._voice_always_on = _VOICE_ALWAYS_ON
        self._system_audio = _CAPTURE_SYSTEM_AUDIO
        self._buffer: list[tuple[float, str]] = []
        self._user_typed_at = 0.0
        self._mic_started = False
        self._spk_started = False
        self._lock = threading.Lock()

    def bind_audio(self, engine: "AudioEngine") -> None:
        self._audio = engine

    @property
    def voice_listening(self) -> bool:
        return bool(self._copilot_active and self._voice_always_on)

    @property
    def system_audio_active(self) -> bool:
        return bool(self._copilot_active and self._system_audio and self._spk_started)

    def set_copilot(self, active: bool) -> None:
        self._copilot_active = bool(active)
        if active:
            self._start_passive_audio()
        else:
            self._stop_passive_audio()

    def mark_user_typed(self) -> None:
        self._user_typed_at = time.time()

    def route_transcript(self, text: str, source: str) -> str:
        """
        Decide how a transcript should be handled.

        Returns
        -------
        ``"buffer"``  — consumed silently (system-audio context buffer)
        ``"drop"``    — consumed and discarded (user typed recently)
        ``"forward"`` — caller should continue normal routing
        """
        src = (source or "").lower()
        if src == "speaker" and self._copilot_active and self._system_audio:
            self._append_buffer(text)
            return "buffer"
        if src == "mic" and self._copilot_active and self._voice_always_on:
            if time.time() - self._user_typed_at < self._USER_TYPED_GRACE_S:
                return "drop"
            threading.Thread(
                target=self._on_voice_input,
                args=(text,),
                daemon=True,
                name="atlas-voice-always-on",
            ).start()
            return "consumed"
        return "forward"

    def get_audio_context(self, seconds: float = 20.0) -> str:
        """Last *seconds* of system-audio transcript (for prompt injection)."""
        cutoff = time.time() - max(1.0, float(seconds))
        with self._lock:
            parts = [t for ts, t in self._buffer if ts >= cutoff]
        return " ".join(parts).strip()

    def _append_buffer(self, text: str) -> None:
        now = time.time()
        with self._lock:
            self._buffer.append((now, text.strip()))
            cutoff = now - self._BUFFER_MAX_S
            self._buffer = [(ts, t) for ts, t in self._buffer if ts >= cutoff and t]

    def _start_passive_audio(self) -> None:
        if not self._audio:
            return
        if self._voice_always_on and not self._audio.mic_active:
            self._audio.start_mic()
            self._mic_started = True
        if self._system_audio and not self._audio.speaker_active:
            self._audio.start_speaker()
            self._spk_started = True

    def _stop_passive_audio(self) -> None:
        if not self._audio:
            return
        if self._mic_started and self._audio.mic_active:
            self._audio.stop_mic()
        self._mic_started = False
        if self._spk_started and self._audio.speaker_active:
            self._audio.stop_speaker()
        self._spk_started = False
        with self._lock:
            self._buffer.clear()


class AudioEngine:
    """
    Microphone and speaker-loopback capture using Groq Whisper for
    speech-to-text transcription.

    Both capture loops run as daemon threads.  Silence is detected via RMS
    threshold before any API call is made, keeping costs minimal.

    Callbacks
    ---------
    on_transcript(text: str, source: str)
        Fired with the transcribed text and its origin ("mic" or "speaker").

    on_status(message: str)
        Fired with human-readable status updates (start, stop, errors).
    """

    _MIC_RATE         = 16_000
    _MIC_CHUNK_S      = 3
    _MIC_SILENCE_RMS  = 0.02   # raised from 0.01 — more aggressive silence gate
    _SPK_RATE         = 16_000
    _SPK_CHUNK_S      = 3
    _SPK_SILENCE_RMS  = 0.015  # raised from 0.005 — more aggressive silence gate

    # ── Anti-hallucination voice gate ─────────────────────────────────────────
    _VAD_AGGRESSIVENESS = 2      # 0 (lenient) .. 3 (very aggressive)
    _VAD_FRAME_MS       = 30     # webrtcvad accepts 10 / 20 / 30 ms frames
    _VAD_VOICED_RATIO   = 0.55   # fraction of frames that must register speech
    _VAD_RMS_FLOOR      = 0.012  # hard energy gate that runs ahead of the VAD

    _PTT_MIN_RMS        = 0.006

    _LISTEN_RATE         = 16_000
    _LISTEN_FRAME_MS     = 30      # 480 samples @ 16 kHz — valid webrtcvad frame
    _ENDPOINT_SILENCE_S  = 1.5     # trailing silence before transcribe (override via env)
    _LISTEN_MIN_SPEECH_S = 0.25    # ignore sub-250 ms blips (clicks, taps)
    _LISTEN_MAX_S        = 30.0    # hard safety cap on a single utterance
    _LISTEN_PREROLL_S    = 0.20    # audio kept just before speech onset
    _LISTEN_TAIL_KEEP_S  = 0.12    # minimal trailing silence sent to Whisper
    _LISTEN_ABS_FLOOR    = 0.010   # absolute RMS floor for the fallback detector

    def __init__(
        self,
        on_transcript: Callable[[str, str], None],
        on_status:     Callable[[str], None],
        on_state:      Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_transcript = on_transcript
        self._on_status     = on_status
        self._on_state      = on_state or (lambda _s: None)
        self.mic_active     = False
        self.speaker_active = False
        self._mic_stop      = threading.Event()
        self._spk_stop      = threading.Event()
        self.ptt_active     = False
        self._ptt_lock      = threading.Lock()
        self._ptt_frames: list[np.ndarray] = []
        self._ptt_stream    = None

        self.is_listening   = False
        self._listen_stop   = threading.Event()
        self._finalize_now  = threading.Event()
        self._listen_cancel = threading.Event()
        self._listen_thread: Optional[threading.Thread] = None
        self._listen_floor  = 0.005

        self._vad = None
        if _HAS_WEBRTCVAD:
            try:
                self._vad = webrtcvad.Vad(self._VAD_AGGRESSIVENESS)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("webrtcvad init failed (%s); using RMS noise floor.", exc)
                self._vad = None

        self._noise_floor = 0.005
        self._noise_lock  = threading.Lock()

    def _passes_voice_gate(self, audio: "np.ndarray", rate: int) -> bool:
        flat = np.asarray(audio, dtype=np.float32).flatten()
        if flat.size == 0:
            return False

        rms = float(np.sqrt(np.mean(flat ** 2)))
        if rms < self._VAD_RMS_FLOOR:
            return False

        if self._vad is None:
            return self._dynamic_floor_pass(rms)

        pcm = (np.clip(flat, -1.0, 1.0) * 32_767.0).astype(np.int16).tobytes()
        frame_bytes = int(rate * (self._VAD_FRAME_MS / 1000.0)) * 2
        if frame_bytes <= 0:
            return self._dynamic_floor_pass(rms)

        voiced = 0
        total  = 0
        for offset in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
            frame = pcm[offset:offset + frame_bytes]
            total += 1
            try:
                if self._vad.is_speech(frame, rate):
                    voiced += 1
            except Exception:
                continue

        if total == 0:
            return self._dynamic_floor_pass(rms)
        return (voiced / total) >= self._VAD_VOICED_RATIO

    def _dynamic_floor_pass(self, rms: float) -> bool:
        with self._noise_lock:
            if rms < self._noise_floor * 1.5:
                self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms
            threshold = max(self._VAD_RMS_FLOOR, self._noise_floor * 3.0)
        return rms >= threshold

    def start_listening(self) -> None:
        if self.is_listening:
            return
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            self._on_status("🎧 sounddevice not installed")
            return

        self.is_listening = True
        self._listen_stop.clear()
        self._finalize_now.clear()
        self._listen_cancel.clear()
        self._listen_floor = 0.005
        self._endpoint_silence_s = _endpoint_silence_s()
        self._on_state("listening")
        self._on_status("🎧 Listening…")
        self._listen_thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="atlas-listen"
        )
        self._listen_thread.start()

    def finalize_listening(self) -> None:
        if self.is_listening:
            self._finalize_now.set()

    def cancel_listening(self) -> None:
        if self.is_listening:
            self._listen_cancel.set()
            self._listen_stop.set()

    def _frame_is_voiced(self, frame: "np.ndarray", rate: int) -> bool:
        rms = float(np.sqrt(np.mean(frame ** 2)))
        if self._vad is not None:
            pcm = (np.clip(frame, -1.0, 1.0) * 32_767.0).astype(np.int16).tobytes()
            try:
                return bool(self._vad.is_speech(pcm, rate))
            except Exception:
                pass
        with self._noise_lock:
            if rms < self._listen_floor * 1.5:
                self._listen_floor = 0.9 * self._listen_floor + 0.1 * rms
            threshold = max(self._LISTEN_ABS_FLOOR, self._listen_floor * 2.5)
        return rms > threshold

    def _listen_loop(self) -> None:
        import queue as _queue

        import sounddevice as sd

        rate      = self._LISTEN_RATE
        frame_len = int(rate * self._LISTEN_FRAME_MS / 1000.0)
        frame_dur = self._LISTEN_FRAME_MS / 1000.0
        preroll_frames = max(1, int(self._LISTEN_PREROLL_S / frame_dur))

        audio_q: "_queue.Queue[np.ndarray]" = _queue.Queue()

        def _cb(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log.debug("listen stream status: %s", status)
            audio_q.put(indata[:, 0].copy())

        collected: list[np.ndarray] = []
        preroll:   list[np.ndarray] = []
        speech_started   = False
        trailing_silence = 0.0
        speech_dur       = 0.0
        start_t          = time.time()
        done             = False
        buf = np.empty(0, dtype=np.float32)

        try:
            with sd.InputStream(
                samplerate = rate,
                channels   = 1,
                dtype      = "float32",
                blocksize  = frame_len,
                callback   = _cb,
            ):
                while not self._listen_stop.is_set() and not done:
                    if self._finalize_now.is_set():
                        break
                    try:
                        data = audio_q.get(timeout=0.1)
                    except _queue.Empty:
                        if (time.time() - start_t) > self._LISTEN_MAX_S:
                            break
                        continue

                    buf = np.concatenate([buf, data]) if buf.size else data
                    while len(buf) >= frame_len:
                        frame = buf[:frame_len]
                        buf   = buf[frame_len:]

                        if self._frame_is_voiced(frame, rate):
                            if not speech_started:
                                collected.extend(preroll)
                                preroll = []
                                speech_started = True
                            trailing_silence = 0.0
                            speech_dur += frame_dur
                            collected.append(frame)
                        else:
                            if speech_started:
                                trailing_silence += frame_dur
                                collected.append(frame)
                                if trailing_silence >= self._endpoint_silence_s:
                                    done = True
                                    break
                            else:
                                preroll.append(frame)
                                if len(preroll) > preroll_frames:
                                    preroll.pop(0)

                    if (time.time() - start_t) > self._LISTEN_MAX_S:
                        done = True
        except Exception as exc:
            self._on_status(f"🎧 listen error: {exc}")
        finally:
            self.is_listening = False

        if self._listen_cancel.is_set():
            self._on_state("idle")
            return

        if not speech_started or speech_dur < self._LISTEN_MIN_SPEECH_S:
            self._on_state("idle")
            self._on_status("🎧 No speech detected")
            return

        keep_tail = int(self._LISTEN_TAIL_KEEP_S / frame_dur)
        drop = max(0, int(trailing_silence / frame_dur) - keep_tail)
        if drop and drop < len(collected):
            collected = collected[:-drop]

        if not collected:
            self._on_state("idle")
            return

        self._on_state("processing")
        self._on_status("📝 Transcribing…")
        audio = np.concatenate(collected).astype(np.float32)
        self._transcribe_listen(audio, rate)

    def _transcribe_listen(self, audio: "np.ndarray", rate: int) -> None:
        try:
            wav_buf = self._to_wav_buffer(audio, rate)
            result = groq_client.audio.transcriptions.create(
                model           = ATLAS_WHISPER_MODEL,
                file            = wav_buf,
                response_format = "text",
                language        = "en",
                temperature     = 0.0,
            )
            txt = result.strip() if isinstance(result, str) else result.text.strip()
            if self._is_hallucination(txt):
                self._on_state("idle")
                self._on_status("🎧 No speech detected")
                return
            self._on_transcript(txt, "ptt")
        except Exception as exc:
            self._on_state("idle")
            self._on_status(f"📝 Transcription error: {exc}")

    def start_ptt_recording(self) -> None:
        if self.ptt_active:
            return
        try:
            import sounddevice as sd
        except ImportError:
            self._on_status("PTT: sounddevice not installed")
            return

        with self._ptt_lock:
            self._ptt_frames = []
            self.ptt_active = True

        def _callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log.debug("PTT stream status: %s", status)
            with self._ptt_lock:
                self._ptt_frames.append(indata.copy())

        try:
            self._ptt_stream = sd.InputStream(
                samplerate=self._MIC_RATE,
                channels=1,
                dtype="float32",
                callback=_callback,
            )
            self._ptt_stream.start()
            self._on_status("PTT recording...")
        except Exception as exc:
            with self._ptt_lock:
                self.ptt_active = False
                self._ptt_frames = []
            self._on_status(f"PTT start failed: {exc}")

    def stop_ptt_recording(self) -> None:
        if not self.ptt_active:
            return

        stream = self._ptt_stream
        self._ptt_stream = None
        try:
            if stream is not None:
                stream.stop()
                stream.close()
        except Exception as exc:
            log.debug("PTT stream close error: %s", exc)

        with self._ptt_lock:
            frames = list(self._ptt_frames)
            self._ptt_frames = []
            self.ptt_active = False

        if not frames:
            self._on_status("PTT: no audio captured")
            return

        audio = np.concatenate(frames, axis=0)
        rms = float(np.sqrt(np.mean(np.asarray(audio, dtype=np.float32) ** 2)))
        if rms < self._PTT_MIN_RMS:
            self._on_status("PTT: no speech detected")
            return

        threading.Thread(
            target=self._transcribe_ptt_audio,
            args=(audio,),
            daemon=True,
            name="atlas-ptt-transcribe",
        ).start()

    def _transcribe_ptt_audio(self, audio: "np.ndarray") -> None:
        try:
            wav_buf = self._to_wav_buffer(audio, self._MIC_RATE)
            result = groq_client.audio.transcriptions.create(
                model=ATLAS_WHISPER_MODEL,
                file=wav_buf,
                response_format="text",
                language="en",
                temperature=0.0,
            )
            txt = result.strip() if isinstance(result, str) else result.text.strip()
            if self._is_hallucination(txt):
                self._on_status("PTT: no speech detected")
                return
            self._on_status("PTT transcribed")
            self._on_transcript(txt, "ptt")
        except Exception as exc:
            self._on_status(f"PTT transcription error: {exc}")

    def start_mic(self) -> None:
        if self.mic_active:
            return
        self.mic_active = True
        self._mic_stop.clear()
        threading.Thread(target=self._mic_loop, daemon=True, name="atlas-mic").start()
        self._on_status("🎤 Mic active")

    def stop_mic(self) -> None:
        self.mic_active = False
        self._mic_stop.set()
        self._on_status("🎤 Mic stopped")

    def _mic_loop(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            self._on_status("🎤 sounddevice not installed")
            self.mic_active = False
            return

        chunk_samples = self._MIC_RATE * self._MIC_CHUNK_S

        while not self._mic_stop.is_set():
            try:
                audio = sd.rec(
                    chunk_samples,
                    samplerate = self._MIC_RATE,
                    channels   = 1,
                    dtype      = "float32",
                )
                sd.wait()
                if not self._passes_voice_gate(audio, self._MIC_RATE):
                    continue

                wav_buf = self._to_wav_buffer(audio, self._MIC_RATE)
                result  = groq_client.audio.transcriptions.create(
                    model           = ATLAS_WHISPER_MODEL,
                    file            = wav_buf,
                    response_format = "text",
                    language        = "en",
                    temperature     = 0.0,
                )
                txt = result.strip() if isinstance(result, str) else result.text.strip()
                if self._is_hallucination(txt):
                    log.debug("Mic: dropped hallucination %r", txt)
                    continue
                self._on_transcript(txt, "mic")

            except Exception as exc:
                log.debug("Mic loop error: %s", exc)
                time.sleep(1)

    def start_speaker(self) -> None:
        if self.speaker_active:
            return
        self.speaker_active = True
        self._spk_stop.clear()
        threading.Thread(target=self._spk_loop, daemon=True, name="atlas-spk").start()
        self._on_status("🔊 Speaker capture active")

    def stop_speaker(self) -> None:
        self.speaker_active = False
        self._spk_stop.set()
        self._on_status("🔊 Speaker capture stopped")

    def _spk_loop(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            self._on_status("🔊 sounddevice not installed")
            self.speaker_active = False
            return

        chunk_samples = self._SPK_RATE * self._SPK_CHUNK_S

        loopback: Optional[int] = None
        for idx, dev in enumerate(sd.query_devices()):
            name_lower = dev["name"].lower()
            if any(k in name_lower for k in ("loopback", "stereo mix", "what u hear", "monitor")):
                if dev["max_input_channels"] > 0:
                    loopback = idx
                    break

        if loopback is None:
            self.speaker_active = False
            self._on_status(
                "🔊 ERROR: No loopback/monitor audio device found. "
                "Speaker capture is unavailable. "
                "Enable 'Stereo Mix' or install a virtual audio cable "
                "(e.g. VB-Cable on Windows, BlackHole on macOS)."
            )
            log.warning(
                "AudioEngine._spk_loop: no loopback device discovered — "
                "speaker capture thread exiting cleanly."
            )
            return

        while not self._spk_stop.is_set():
            try:
                audio = sd.rec(
                    chunk_samples,
                    samplerate = self._SPK_RATE,
                    channels   = 1,
                    dtype      = "float32",
                    device     = loopback,
                )
                sd.wait()
                if not self._passes_voice_gate(audio, self._SPK_RATE):
                    continue

                wav_buf = self._to_wav_buffer(audio, self._SPK_RATE)
                result  = groq_client.audio.transcriptions.create(
                    model           = ATLAS_WHISPER_MODEL,
                    file            = wav_buf,
                    response_format = "text",
                    language        = "en",
                    temperature     = 0.0,
                )
                txt = result.strip() if isinstance(result, str) else result.text.strip()
                if self._is_hallucination(txt):
                    log.debug("Speaker: dropped hallucination %r", txt)
                    continue
                self._on_transcript(txt, "speaker")

            except Exception as exc:
                log.debug("Speaker loop error: %s", exc)
                time.sleep(1)

    _HALLUCINATION_EXACT: frozenset[str] = frozenset({
        "thank you",
        "thanks",
        "bye",
        "bye bye",
        "you",
        "the",
        ".",
        "...",
        "okay",
        "ok",
        "mm-hmm",
        "uh-huh",
        "hmm",
        "um",
        "uh",
        "ah",
        "mhm",
    })

    _HALLUCINATION_SUBSTRINGS: tuple[str, ...] = (
        "amara.org",
        "stavros",
        "hiátlan szórakoz",
        "storbritannia",
        "subtitles by",
        "subtitle by",
        "transcribed by",
        "translated by",
        "www.",
        ".com",
        ".org",
        ".net",
        "subscribe",
        "like and subscribe",
        "patreon",
        "this video",
        "this episode",
        "tune in",
        "stay tuned",
        "captions by",
        "captioned by",
    )

    @classmethod
    def _is_hallucination(cls, text: str) -> bool:
        if not text or not text.strip():
            return True

        stripped = text.strip()

        if len(stripped) <= 2:
            return True

        lower = stripped.lower()

        if lower in cls._HALLUCINATION_EXACT:
            return True

        for fragment in cls._HALLUCINATION_SUBSTRINGS:
            if fragment in lower:
                return True

        return False

    @staticmethod
    def _to_wav_buffer(audio: "np.ndarray", rate: int) -> io.BytesIO:
        buf = io.BytesIO()
        buf.name = "audio.wav"
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes((audio * 32_767).astype("int16").tobytes())
        buf.seek(0)
        return buf


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "code block omitted.", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.+?)_{1,3}", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", "", text)
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class KokoroVoiceEngine:
    _instance: Optional["KokoroVoiceEngine"] = None

    _FALLBACK_VOICES: tuple[str, ...] = (
        "af_sarah", "af_bella", "af_nicole", "af", "am_adam", "am_michael",
    )

    def __init__(
        self,
        voice:      str   = "af_sarah",
        speed:      float = 1.0,
        model_path: str   = "kokoro-v0_19.onnx",
        voices_path: str  = "voices.bin",
    ) -> None:
        self.voice       = voice
        self.speed       = speed
        self._model_path  = model_path
        self._voices_path = voices_path
        self._muted       = False
        self.last_error   = ""

        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._skip_event  = threading.Event()
        self._ready       = False
        self._kokoro      = None
        self._sd          = None
        self._cur_stream  = None
        self._is_speaking = False

        self._worker_thread = threading.Thread(
            target = self._worker,
            daemon = True,
            name   = "atlas-kokoro",
        )
        self._worker_thread.start()
        KokoroVoiceEngine._instance = self

    def preload(self) -> None:
        """Load Kokoro models in the background so the first speak() is instant."""
        if self._ready:
            return
        threading.Thread(
            target=self._ensure_kokoro_loaded,
            daemon=True,
            name="atlas-kokoro-preload",
        ).start()

    def speak(self, text: str) -> None:
        if self._muted or not text or not text.strip():
            return
        clean = _strip_markdown(text)
        if clean:
            self._queue.put(clean)

    def skip(self) -> None:
        self._skip_event.set()
        stream = self._cur_stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
        if self._sd is not None:
            try:
                self._sd.stop()
            except Exception:
                pass

    def flush(self) -> None:
        self.skip()
        old_queue = self._queue
        self._queue = queue.Queue()
        while True:
            try:
                old_queue.get_nowait()
            except queue.Empty:
                break

    def mute(self) -> None:
        self._muted = True
        self.skip()

    def unmute(self) -> None:
        self._muted = False

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def set_voice(self, voice: str) -> None:
        voice = (voice or "").strip()
        if not voice:
            return
        if self._kokoro is not None:
            try:
                if voice not in self._kokoro.voices:
                    log.warning("set_voice: %r unavailable; keeping %r", voice, self.voice)
                    return
            except Exception:
                pass
        self.voice = voice

    def available_voices(self) -> list[str]:
        if self._kokoro is None:
            return []
        try:
            return sorted(self._kokoro.voices.keys())
        except Exception:
            return []

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.5, min(2.0, speed))

    def shutdown(self) -> None:
        self._queue.put(None)

    @staticmethod
    def _instance_running() -> bool:
        inst = KokoroVoiceEngine._instance
        return inst is not None and inst._worker_thread.is_alive()

    @staticmethod
    def models_present(model_path: str = "kokoro-v0_19.onnx",
                       voices_path: str = "voices.bin") -> bool:
        return Path(model_path).exists() and Path(voices_path).exists()

    def _ensure_kokoro_loaded(self) -> bool:
        if self._ready:
            return True
        if self._kokoro is not None and not self._ready:
            return False
        try:
            from kokoro_onnx import Kokoro  # type: ignore
            import sounddevice as _sd

            self._kokoro = Kokoro(self._model_path, self._voices_path)
            self._sd = _sd

            try:
                available = set(self._kokoro.voices.keys())
            except Exception:
                available = set()
            if available and self.voice not in available:
                fallback = next(
                    (v for v in self._FALLBACK_VOICES if v in available), None
                )
                if fallback is None:
                    fallback = sorted(available)[0]
                log.warning(
                    "Kokoro voice %r not in voices.bin; falling back to %r. "
                    "Available voices: %s",
                    self.voice, fallback, sorted(available),
                )
                self.voice = fallback

            self._ready = True
            log.info("Kokoro voice engine ready — voice=%s speed=%.1f", self.voice, self.speed)
            return True
        except Exception as exc:
            self._kokoro = None
            log.warning(
                "KokoroVoiceEngine: load failed (%s). TTS disabled.",
                exc,
            )
            return False

    def _worker(self) -> None:
        load_failed_logged = False
        while True:
            item = self._queue.get()
            if item is None:
                break

            if self._muted:
                continue

            if not self._ready:
                if not self._ensure_kokoro_loaded():
                    if not load_failed_logged:
                        load_failed_logged = True
                    continue

            self._skip_event.clear()
            self._synthesise_and_play(item)

    def _synthesise_and_play(self, text: str) -> None:
        try:
            self._is_speaking = True
            samples, sample_rate = self._kokoro.create(
                text,
                voice = self.voice,
                speed = self.speed,
                lang  = "en-us",
            )

            audio = np.ascontiguousarray(np.asarray(samples, dtype=np.float32))
            peak  = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak > 1.0:
                audio = audio / peak

            block_size = max(256, int(sample_rate * 0.08))

            stream = self._sd.OutputStream(
                samplerate = sample_rate,
                channels   = 1,
                dtype      = "float32",
                blocksize  = block_size,
            )
            stream.start()
            self._cur_stream = stream
            try:
                offset = 0
                total  = len(audio)
                while offset < total:
                    if self._skip_event.is_set():
                        break
                    chunk = audio[offset: offset + block_size]
                    stream.write(chunk)
                    offset += block_size
            finally:
                self._cur_stream = None
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Kokoro synthesis/playback error: %s", exc)
        finally:
            self._is_speaking = False


_ELEVEN_MODEL       = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
_ELEVEN_PCM_RATE    = 24_000

ELEVEN_VOICES: dict = {
    "Brian":  "nPczCjzI2devNBz1zQrb",
    "George": "JBFqnCBsd6RMkjVDRZzb",
    "Sarah":  "EXAVITQu4vr4xnSDxMaL",
    "Laura":  "FGY2WhTYpPnrIDTdsKH5",
}
ELEVEN_DEFAULT_VOICE_ID = ELEVEN_VOICES["Brian"]


def _eleven_configured() -> bool:
    return bool((os.environ.get("ELEVENLABS_API_KEY") or "").strip())


class ElevenLabsVoiceEngine:
    def __init__(
        self,
        fallback: "KokoroVoiceEngine",
        speed: float = 1.0,
    ) -> None:
        self._fallback   = fallback
        self.speed       = speed
        self._muted      = False
        self.last_error  = ""
        self._available  = False
        self._disabled   = False
        self._client     = None
        self._sd         = None
        self._voice_id   = (os.environ.get("ELEVENLABS_VOICE_ID")
                            or os.environ.get("VOICE_ID") or "").strip() \
                            or ELEVEN_DEFAULT_VOICE_ID

        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._skip_event  = threading.Event()
        self._cur_stream  = None
        self._is_speaking = False

        self._worker_thread = threading.Thread(
            target=self._worker, daemon=True, name="atlas-elevenlabs"
        )
        self._worker_thread.start()

    def _ensure_client(self) -> bool:
        if self._disabled:
            return False
        if self._available:
            return True
        if not _eleven_configured():
            self._disabled = True
            return False
        try:
            from elevenlabs.client import ElevenLabs  # type: ignore
            import sounddevice as _sd

            key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
            self._client = ElevenLabs(api_key=key)
            self._sd = _sd
            self._available = True
            log.info("ElevenLabs streaming voice ready — model=%s voice=%s",
                     _ELEVEN_MODEL, self._voice_id)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self._disabled  = True
            log.warning("ElevenLabs unavailable (%s); using Kokoro fallback.", exc)
            return False

    @property
    def is_active(self) -> bool:
        return _eleven_configured() and not self._disabled

    def speak(self, text: str) -> None:
        if self._muted or not text or not text.strip():
            return
        clean = _strip_markdown(text)
        if clean:
            self._queue.put(clean)

    def skip(self) -> None:
        self._skip_event.set()
        stream = self._cur_stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass

    def flush(self) -> None:
        self.skip()
        old = self._queue
        self._queue = queue.Queue()
        while True:
            try:
                old.get_nowait()
            except queue.Empty:
                break

    def mute(self) -> None:
        self._muted = True
        self.skip()

    def unmute(self) -> None:
        self._muted = False

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.5, min(2.0, speed))

    @property
    def voice_id(self) -> str:
        return self._voice_id

    def set_voice_id(self, voice_id: str) -> None:
        voice_id = (voice_id or "").strip()
        if not voice_id or voice_id == self._voice_id:
            return
        self._voice_id  = voice_id
        self._disabled  = False
        self.last_error = ""

    def shutdown(self) -> None:
        self._queue.put(None)

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            if self._muted:
                continue
            self._skip_event.clear()
            self._stream_one(item)

    def _stream_one(self, text: str) -> None:
        if not self._ensure_client():
            self._fallback.speak(text)
            return
        try:
            self._is_speaking = True
            audio_iter = self._client.text_to_speech.convert(
                voice_id      = self._voice_id,
                model_id      = _ELEVEN_MODEL,
                text          = text,
                output_format = "pcm_24000",
            )
            stream = self._sd.OutputStream(
                samplerate=_ELEVEN_PCM_RATE, channels=1, dtype="int16",
            )
            stream.start()
            self._cur_stream = stream
            leftover = b""
            try:
                for chunk in audio_iter:
                    if self._skip_event.is_set():
                        break
                    if not chunk:
                        continue
                    buf = leftover + chunk
                    usable = len(buf) - (len(buf) % 2)
                    leftover = buf[usable:]
                    if usable:
                        stream.write(
                            np.frombuffer(buf[:usable], dtype=np.int16)
                        )
            finally:
                self._cur_stream = None
                try:
                    stream.stop(); stream.close()
                except Exception:
                    pass
        except Exception as exc:
            self.last_error = str(exc)
            self._disabled  = True
            log.warning("ElevenLabs stream failed (%s); falling back to Kokoro.", exc)
            self._fallback.speak(text)
        finally:
            self._is_speaking = False


class VoiceRouter:
    def __init__(self, voice: str = "af_sarah", speed: float = 1.0) -> None:
        self.kokoro = KokoroVoiceEngine(voice=voice, speed=speed)
        self.eleven = ElevenLabsVoiceEngine(fallback=self.kokoro, speed=speed)

    def _active(self):
        return self.eleven if self.eleven.is_active else self.kokoro

    def speak(self, text: str) -> None:
        self._active().speak(text)

    def skip(self) -> None:
        self.eleven.skip(); self.kokoro.skip()

    def flush(self) -> None:
        self.eleven.flush(); self.kokoro.flush()

    def mute(self) -> None:
        self.eleven.mute(); self.kokoro.mute()

    def unmute(self) -> None:
        self.eleven.unmute(); self.kokoro.unmute()

    @property
    def is_muted(self) -> bool:
        return self.kokoro.is_muted

    @property
    def is_ready(self) -> bool:
        return self.eleven.is_active or self.kokoro.is_ready

    @property
    def is_speaking(self) -> bool:
        return self.eleven.is_speaking or self.kokoro.is_speaking

    @property
    def last_error(self) -> str:
        return self.eleven.last_error or self.kokoro.last_error

    @property
    def active_engine(self) -> str:
        return "elevenlabs" if self.eleven.is_active else "kokoro"

    def set_voice(self, voice: str) -> None:
        self.kokoro.set_voice(voice)

    @property
    def eleven_voice_id(self) -> str:
        return self.eleven.voice_id

    def set_eleven_voice(self, voice_id: str) -> None:
        self.eleven.set_voice_id(voice_id)

    def available_voices(self) -> list[str]:
        return self.kokoro.available_voices()

    def set_speed(self, speed: float) -> None:
        self.kokoro.set_speed(speed)
        self.eleven.set_speed(speed)

    def preload(self) -> None:
        """Warm up TTS backends before the first utterance."""
        self.kokoro.preload()
        if self.eleven.is_active:
            threading.Thread(
                target=self.eleven._ensure_client,
                daemon=True,
                name="atlas-eleven-preload",
            ).start()

    def shutdown(self) -> None:
        self.eleven.shutdown(); self.kokoro.shutdown()


voice_engine = VoiceRouter(voice="af_sarah", speed=1.0)


__all__ = [
    "ATLAS_WHISPER_MODEL",
    "AudioEngine",
    "AudioWatcher",
    "ElevenLabsVoiceEngine",
    "ELEVEN_DEFAULT_VOICE_ID",
    "ELEVEN_VOICES",
    "KokoroVoiceEngine",
    "VoiceRouter",
    "_CAPTURE_SYSTEM_AUDIO",
    "_VOICE_ALWAYS_ON",
    "groq_client",
    "voice_engine",
]
