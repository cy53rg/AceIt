"""AudioWatcher speaker context must not grow without bound."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from atlas_audio import AudioWatcher, _AUDIO_CONTEXT_MAXLEN, _audio_context_ttl_s


def test_audio_context_ttl_default_is_five_minutes(monkeypatch):
    monkeypatch.delenv("ATLAS_AUDIO_CONTEXT_TTL_SECONDS", raising=False)
    assert _audio_context_ttl_s() == 300.0


def test_audio_context_ttl_from_env(monkeypatch):
    monkeypatch.setenv("ATLAS_AUDIO_CONTEXT_TTL_SECONDS", "120")
    assert _audio_context_ttl_s() == 120.0


def test_audio_buffer_respects_maxlen():
    watcher = AudioWatcher(on_voice_input=lambda _t: None)
    base = 1_000_000.0

    for i in range(_AUDIO_CONTEXT_MAXLEN + 50):
        with patch("time.time", return_value=base + i * 0.01):
            watcher._append_buffer(f"chunk-{i}")

    assert len(watcher._buffer) <= _AUDIO_CONTEXT_MAXLEN


def test_audio_buffer_prunes_by_ttl():
    watcher = AudioWatcher(on_voice_input=lambda _t: None)
    watcher._buffer_ttl_s = 300.0
    base = 2_000_000.0

    with patch("time.time", return_value=base):
        watcher._append_buffer("old meeting notes")

    with patch("time.time", return_value=base + 301):
        watcher._append_buffer("fresh notes")

    assert len(watcher._buffer) == 1
    assert watcher._buffer[-1][1] == "fresh notes"


def test_audio_buffer_plateaus_during_two_hour_session():
    """
    Simulate a 2-hour passive capture session (1 chunk/sec) and sample every
    10s — buffer size should plateau well before the session ends.
    """
    watcher = AudioWatcher(on_voice_input=lambda _t: None)
    watcher._buffer_ttl_s = 300.0
    base = 3_000_000.0
    samples: list[int] = []

    for second in range(7200):
        ts = base + second
        with patch("time.time", return_value=ts):
            watcher._append_buffer("speaker fragment")
            if second % 10 == 0:
                with watcher._lock:
                    watcher._prune_buffer(ts)
                samples.append(len(watcher._buffer))

    assert samples[-1] <= _AUDIO_CONTEXT_MAXLEN
    # After ~10 minutes the sampled size should stop climbing.
    first_ten_min_peak = max(samples[:60]) if len(samples) >= 60 else max(samples)
    after_ten_min_peak = max(samples[60:])
    assert after_ten_min_peak <= _AUDIO_CONTEXT_MAXLEN
    assert after_ten_min_peak <= first_ten_min_peak + 1


def test_get_audio_context_does_not_resurrect_expired_entries():
    watcher = AudioWatcher(on_voice_input=lambda _t: None)
    watcher._buffer_ttl_s = 300.0
    base = 4_000_000.0

    with patch("time.time", return_value=base):
        watcher._append_buffer("ancient context")

    with patch("time.time", return_value=base + 400):
        assert watcher.get_audio_context(20.0) == ""


@pytest.mark.skipif(sys.platform != "win32", reason="psutil handle check is Windows-oriented")
def test_audio_buffer_memory_plateau_windows(monkeypatch):
    psutil = pytest.importorskip("psutil")
    watcher = AudioWatcher(on_voice_input=lambda _t: None)
    watcher._buffer_ttl_s = 300.0
    proc = psutil.Process()
    base = 5_000_000.0
    rss_samples: list[int] = []

    for second in range(600):
        ts = base + second
        with patch("time.time", return_value=ts):
            watcher._append_buffer("x" * 256)
            if second % 10 == 0:
                rss_samples.append(proc.memory_info().rss)

    assert len(watcher._buffer) <= _AUDIO_CONTEXT_MAXLEN
    if len(rss_samples) >= 12:
        early = max(rss_samples[:6])
        late = max(rss_samples[6:])
        assert late <= early * 1.25 + 512_000
