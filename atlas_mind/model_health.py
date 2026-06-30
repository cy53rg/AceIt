"""Groq model health probes."""
from __future__ import annotations

import os

from atlas_logging import get_logger

log = get_logger("mind.model_health")

# Minimal 1x1 PNG for vision-model health probes.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAD0lEQVQImWP4"
    "DwABBAEAAP//AAAAAH0CQQAAAABJRU5ErkJggg=="
)


def groq_model_retired(exc: BaseException) -> bool:
    """True when Groq reports a model id is gone or decommissioned."""
    msg = str(exc).lower()
    return any(
        k in msg
        for k in (
            "model_decommissioned",
            "model_not_found",
            "decommissioned",
            "does not exist",
            "no longer supported",
        )
    )


def probe_groq_model(groq_client, model: str, *, vision: bool = False) -> bool:
    """
    Return True if the model id is retired / not found.

    Makes a minimal max_tokens=1 call. Other errors are ignored.
    """
    if not model or not os.environ.get("GROQ_API_KEY"):
        return False
    try:
        if vision:
            messages = [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{TINY_PNG_B64}"},
                    },
                    {"type": "text", "text": "ping"},
                ],
            }]
        else:
            messages = [{"role": "user", "content": "ping"}]
        groq_client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=1,
            temperature=0,
        )
        return False
    except Exception as exc:
        if groq_model_retired(exc):
            return True
        log.debug("Model health probe non-fatal for %r: %s", model, exc)
        return False
