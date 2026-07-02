"""Kokoro TTS should fail open to silent mode when kokoro-onnx is missing."""
from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def _import_without_kokoro(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "kokoro_onnx":
            raise ImportError("kokoro-onnx is not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("atlas_audio", None)
    return importlib.import_module("atlas_audio")


def test_kokoro_voice_engine_marks_unavailable_on_import_error(monkeypatch):
    mod = _import_without_kokoro(monkeypatch)
    engine = mod.KokoroVoiceEngine()

    assert engine._kokoro_available is False
    assert engine.is_ready is False

    events: list[str] = []
    engine.on_event(lambda event_type, _payload: events.append(event_type))
    engine.speak("Atlas ready.")

    assert events == ["speak_complete"]
    assert engine._queue.empty()

    engine.shutdown()


def test_voice_router_launches_without_kokoro(monkeypatch):
    mod = _import_without_kokoro(monkeypatch)

    router = mod.VoiceRouter(voice="af_sarah", speed=1.0)

    assert router.kokoro._kokoro_available is False

    events: list[str] = []
    router.kokoro.on_event(lambda event_type, _payload: events.append(event_type))
    router.speak("Hello from Atlas.")

    assert "speak_complete" in events
    assert router.kokoro._queue.empty()
    assert router.is_speaking is False

    router.shutdown()


def test_preload_noops_when_kokoro_missing(monkeypatch):
    mod = _import_without_kokoro(monkeypatch)
    engine = mod.KokoroVoiceEngine()

    engine.preload()

    assert engine.is_ready is False
    engine.shutdown()
