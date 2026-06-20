"""Shared pytest fixtures for Atlas tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--mock-groq",
        action="store_true",
        default=False,
        help="Use mocked Groq clients (recommended for CI/local runs).",
    )


@pytest.fixture
def mock_groq_client(request: pytest.FixtureRequest) -> MagicMock:
    """Groq client stub whose chat completions return configurable JSON."""
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"found": false}'))]
    )
    client.audio.transcriptions.create.return_value = SimpleNamespace(text="")
    if request.config.getoption("--mock-groq"):
        request.node.mock_groq = True  # marker for tests that inspect the flag
    return client


@pytest.fixture
def vision_response_factory():
    """Build a Groq chat completion that returns coordinate JSON."""

    def _factory(
        *,
        found: bool = True,
        x: int = 0,
        y: int = 0,
        w: int = 40,
        h: int = 30,
        confidence: float = 0.92,
    ) -> SimpleNamespace:
        payload = (
            f'{{"found": {str(found).lower()}, "x": {x}, "y": {y}, '
            f'"w": {w}, "h": {h}, "confidence": {confidence}}}'
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
        )

    return _factory
