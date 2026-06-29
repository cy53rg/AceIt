"""Pre-action region stability checks (pixel fingerprint around locate coords)."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from atlas_vision import (
    ScreenCapture,
    region_changed_since_capture,
    region_fingerprint,
    region_mean_distance,
    base64_encode,
)


def _cap_from_color(rgb: tuple[int, int, int], size=(200, 200)) -> ScreenCapture:
    img = Image.new("RGB", size, color=rgb)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return ScreenCapture(
        b64=base64_encode(buf.getvalue()),
        scale=1.0,
        capture_size=size,
        screen_size=size,
    )


def test_region_fingerprint_stable_for_same_frame():
    cap = _cap_from_color((40, 80, 120))
    fp1 = region_fingerprint(cap, 100, 100, 40, 40)
    fp2 = region_fingerprint(cap, 100, 100, 40, 40)
    assert fp1 is not None and fp2 is not None
    assert region_mean_distance(fp1, fp2) == 0.0


def test_region_changed_detects_different_viewport():
    locate = _cap_from_color((255, 0, 0))
    fresh = _cap_from_color((0, 0, 255))
    changed, dist = region_changed_since_capture(
        locate, 100, 100, 40, 40, fresh_cap=fresh, threshold=5.0,
    )
    assert changed is True
    assert dist > 5.0


def test_region_unchanged_when_fresh_matches_locate():
    cap = _cap_from_color((10, 20, 30))
    changed, dist = region_changed_since_capture(
        cap, 100, 100, 40, 40, fresh_cap=cap, threshold=5.0,
    )
    assert changed is False
    assert dist == 0.0
