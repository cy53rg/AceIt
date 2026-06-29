"""StreamBracketFilter + research token dispatch wiring."""
from __future__ import annotations

from atlas_recorder import ActionTokenPatterns, StreamBracketFilter


def test_research_token_regex_matches():
    raw = "[[RESEARCH: export PDF in Word 365]]"
    m = ActionTokenPatterns._RESEARCH_TOKEN_RE.match(raw)
    assert m is not None
    assert m.group(1).strip() == "export PDF in Word 365"


def test_stream_filter_strips_research_token():
    filt = StreamBracketFilter()
    visible, tokens = filt.feed(
        "Let me check. [[RESEARCH: Photoshop crop tool 2024]] Step one:"
    )
    tail, tail_tokens = filt.flush()
    assert tokens == ["[[RESEARCH: Photoshop crop tool 2024]]"]
    assert tail_tokens == []
    assert "RESEARCH" not in visible + tail
    assert "Step one:" in visible + tail


def test_stream_filter_holds_partial_research_prefix():
    filt = StreamBracketFilter()
    v1, t1 = filt.feed("Before [[RESE")
    assert v1 == "Before "
    assert t1 == []
    v2, t2 = filt.feed("ARCH: query here]] after")
    tail, tt = filt.flush()
    assert t2 == ["[[RESEARCH: query here]]"]
    assert "RESEARCH" not in v2 + tail
    assert "after" in v2 + tail
