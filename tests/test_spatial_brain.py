"""SpatialBrain.locate() tests against fixture screenshots."""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest

from atlas_vision import SpatialBrain

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
