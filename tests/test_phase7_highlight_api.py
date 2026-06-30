"""Daemon routes highlight to Glass interview path when Focus is active."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_highlight_uses_interview_path_when_glass_active():
  from atlas_daemon import create_app
  from fastapi.testclient import TestClient

  import atlas_daemon as mod

  state = MagicMock()
  state.glass.active = True
  state.handle_glass_interview_question = MagicMock()
  state.handle_input = MagicMock()
  state.inject_screen_capture = MagicMock()

  old_state = mod._state
  mod._state = state
  try:
    client = TestClient(create_app())
    resp = client.post(
      "/api/handle_input",
      json={"text": "What is polymorphism?", "source": "highlight"},
    )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True
  finally:
    mod._state = old_state

  import time
  deadline = time.time() + 2.0
  while time.time() < deadline:
    if state.handle_glass_interview_question.called:
      break
    time.sleep(0.05)

  state.handle_glass_interview_question.assert_called_once()
  args, kwargs = state.handle_glass_interview_question.call_args
  assert args[0] == "What is polymorphism?"
  assert kwargs.get("source") == "highlight"
  state.handle_input.assert_not_called()
