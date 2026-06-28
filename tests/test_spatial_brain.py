"""SpatialBrain.locate() tests against fixture screenshots."""
from __future__ import annotations

import base64
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from atlas_vision import ScreenCapture, SpatialBrain, capture_screen_b64

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOLERANCE_PX = 50


def _load_fixture_b64(name: str) -> str:
    return base64.b64encode((FIXTURES / name).read_bytes()).decode("ascii")


def _parse_targets():
    meta = FIXTURES / "targets.txt"
    if not meta.exists():
        pytest.skip("Run tests/make_fixtures.py to generate PNG fixtures")
    rows = []
    for line in meta.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fname, target, ex, ey = line.split("|")
        rows.append((fname, target, int(ex), int(ey)))
    return rows


@pytest.mark.parametrize("fname,target,expected_x,expected_y", _parse_targets())
def test_spatial_brain_locate_fixture(
    mock_groq_client,
    vision_response_factory,
    fname,
    target,
    expected_x,
    expected_y,
):
    mock_groq_client.chat.completions.create.return_value = vision_response_factory(
        found=True,
        x=expected_x,
        y=expected_y,
        w=80,
        h=40,
        confidence=0.95,
    )
    brain = SpatialBrain(mock_groq_client, model="test-vision-model")
    screen_b64 = _load_fixture_b64(fname)

    with patch.object(brain, "_locate_crop_refine", side_effect=lambda *_a, **_k: {}):
        result = brain.locate(target, screen_b64=screen_b64, scale=1.0, refine=False)

    assert result["found"] is True
    assert abs(result["x"] - expected_x) <= TOLERANCE_PX
    assert abs(result["y"] - expected_y) <= TOLERANCE_PX


def test_spatial_brain_locate_logs_outcome(mock_groq_client, vision_response_factory):
    mock_groq_client.chat.completions.create.return_value = vision_response_factory(
        found=True, x=10, y=20, w=5, h=5,
    )
    brain = SpatialBrain(mock_groq_client)
    with patch("atlas_vision.log_outcome_json") as log_fn:
        with patch.object(brain, "_locate_crop_refine", side_effect=lambda *_a, **_k: {}):
            brain.locate("button", screen_b64=_load_fixture_b64("save_button.png"), refine=False)
    assert log_fn.called
    record = log_fn.call_args[0][0]
    assert record["target"] == "button"
    assert record["found"] is True
    assert record["model"] == brain.model


def _patch_big_screen(monkeypatch):
    """Simulate a 2000×1000 desktop for capture_screen_b64()."""
    from PIL import Image

    img = Image.new("RGB", (2000, 1000), color=(255, 0, 0))

    class FakeShot:
        size = (2000, 1000)
        rgb = img.tobytes()

    class FakeSCT:
        monitors = [None, {"left": 0, "top": 0, "width": 2000, "height": 1000}]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def grab(self, _mon):
            return FakeShot()

    monkeypatch.setattr("mss.mss", lambda: FakeSCT())
    monkeypatch.setattr(
        "PIL.ImageGrab.grab",
        lambda: img.copy(),
        raising=False,
    )


def test_concurrent_capture_scales_isolated(
    monkeypatch,
    mock_groq_client,
    vision_response_factory,
):
    """
    Two threads capture with different max_width values; each locate() must
    scale coordinates with its own ScreenCapture, not a shared global.
    """
    _patch_big_screen(monkeypatch)
    mock_groq_client.chat.completions.create.return_value = vision_response_factory(
        found=True,
        x=100,
        y=50,
        w=10,
        h=10,
        confidence=0.9,
    )

    results: dict[str, tuple[float, int, int]] = {}
    barrier = threading.Barrier(2)

    def worker(key: str, max_width: int) -> None:
        barrier.wait()
        cap = capture_screen_b64(max_width=max_width)
        assert cap is not None
        brain = SpatialBrain(mock_groq_client, model="test-vision-model")
        with patch.object(brain, "_locate_crop_refine", side_effect=lambda *_a, **_k: {}):
            loc = brain.locate("button", screen=cap, refine=False)
        results[key] = (cap.scale, loc["x"], loc["y"])

    t_narrow = threading.Thread(target=worker, args=("narrow", 500))
    t_wide = threading.Thread(target=worker, args=("wide", 1000))
    t_narrow.start()
    t_wide.start()
    t_narrow.join()
    t_wide.join()

    narrow_scale, narrow_x, narrow_y = results["narrow"]
    wide_scale, wide_x, wide_y = results["wide"]

    assert narrow_scale == pytest.approx(4.0)
    assert wide_scale == pytest.approx(2.0)
    assert narrow_x == 400
    assert wide_x == 200
    assert narrow_y == 200
    assert wide_y == 100
