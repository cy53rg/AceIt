"""Point-and-Talk coordinate tag extraction and desktop mapping."""

from __future__ import annotations

import atlas_vision as av
from atlas_recorder import StreamCoordinateFilter


def test_extract_target_coordinate_strips_trailing_tag():
    raw = "Click the Save button in the toolbar. [TARGET_COORDINATE: 842, 127]"
    clean, coord = av.extract_target_coordinate(raw)
    assert coord is not None
    assert coord["x"] == 842.0
    assert coord["y"] == 127.0
    assert "[TARGET_COORDINATE" not in clean
    assert clean.endswith("toolbar.")


def test_normalized_coord_to_desktop():
    av._LAST_SCREEN_SIZE = (1920, 1080)  # noqa: SLF001
    px, py = av.normalized_coord_to_desktop(500, 500)
    assert px == 960
    assert py == 540


def test_stream_coordinate_filter_holds_partial_tag():
    filt = StreamCoordinateFilter()
    out1 = filt.feed("Answer here. [TARGET_COORDINATE:")
    assert "[TARGET" not in out1
    out2 = filt.feed(" 100, 200]")
    remainder, coord = filt.flush()
    assert coord is not None
    assert coord["x"] == 100.0
    assert coord["y"] == 200.0
    assert "[TARGET_COORDINATE" not in (out1 + out2 + remainder)
