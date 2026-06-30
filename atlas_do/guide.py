"""Guided walkthrough verification helpers."""
from __future__ import annotations

from atlas_vision import SpatialBrain


def verify_step_completion(
    expected_state: str,
    screen_b64: str,
    spatial: SpatialBrain,
) -> dict:
    """
    Generic screenshot-vs-expected-state check (guided user steps and future
    autonomous self-steps).

    Returns ``{"completed", "confidence", "observed", "discrepancy"}``.
    """
    return spatial.verify(expected_state, screen_b64=screen_b64 or None)
