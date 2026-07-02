"""Regression: plain Groq content must not be swallowed by HarmonyStreamFilter."""
from atlas_recorder import HarmonyStreamFilter


def test_plain_groq_content_emits_when_not_waiting_for_final_channel():
    filt = HarmonyStreamFilter(wait_for_final=False)
    out = filt.feed("Hello") + filt.feed(" Atlas") + filt.flush()
    assert out == "Hello Atlas"


def test_wait_for_final_blocks_plain_content_without_harmony_markers():
    filt = HarmonyStreamFilter(wait_for_final=True)
    out = filt.feed("Hello Atlas") + filt.flush()
    assert out == ""
