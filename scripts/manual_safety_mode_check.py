#!/usr/bin/env python3
"""Manual Safety Mode checks for Tier 1.1 (no GUI harness required).

Run from repo root:
  python scripts/manual_safety_mode_check.py

Exercises task-goal gating and auto_approve mapping without starting Atlas UI.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import atlas_core
from atlas_core import StateEngine
from atlas_task_safety import check_task_goal_allowed


def _engine(mode: str) -> StateEngine:
    eng = StateEngine(
        on_chunk=lambda _c: None,
        on_complete=lambda _t: None,
        on_error=lambda _e: None,
        on_coordinates=lambda _d: None,
        on_token_usage=lambda _u: None,
        user_name="safety-manual",
    )
    eng.safety_mode = mode
    eng._task_confirm_cb = lambda goal: True
    return eng


def main() -> int:
    print("=== Task goal allowlist ===")
    ok, _ = check_task_goal_allowed("open Spotify and play music")
    bad, reason = check_task_goal_allowed("delete all files in Downloads")
    assert ok and not bad, (ok, bad, reason)
    print("  benign goal: allowed")
    print(f"  destructive goal: blocked ({reason})")

    print("\n=== Safety mode auto_approve mapping ===")
    for mode, expect_auto in (("off", True), ("trusted", True), ("always", False)):
        eng = _engine(mode)
        eng._task_trusted_ok = mode == "trusted"
        auto = eng._task_auto_approve_for_mode()
        assert auto == expect_auto, f"{mode}: expected auto_approve={expect_auto}, got {auto}"
        print(f"  {mode}: auto_approve={auto} (expected {expect_auto})")

    print("\n=== Task start gate ===")
    eng = _engine("trusted")
    assert eng._prepare_task_start("open notepad") is True
    assert eng._prepare_task_start("delete all files in Downloads") is False
    print("  trusted: benign starts, destructive blocked")

    print("\nAll manual safety checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
