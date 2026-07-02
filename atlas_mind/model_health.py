"""Groq model health probes and availability validation."""
from __future__ import annotations

import os
import time
from typing import Any, Optional

from atlas_logging import get_logger

log = get_logger("mind.model_health")

# Minimal 1x1 PNG for vision-model health probes.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAD0lEQVQImWP4"
    "DwABBAEAAP//AAAAAH0CQQAAAABJRU5ErkJggg=="
)

_MODEL_LIST_CACHE: Optional[frozenset[str]] = None
_MODEL_LIST_CACHE_AT: float = 0.0
_MODEL_LIST_CACHE_TTL_S: float = 3600.0


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


def get_available_groq_models(
    groq_client,
    *,
    force_refresh: bool = False,
) -> frozenset[str]:
    """
    Return Groq model ids from the API (cached for one hour).

    Returns an empty set when the API key is missing or the list call fails.
    """
    global _MODEL_LIST_CACHE, _MODEL_LIST_CACHE_AT

    now = time.time()
    if (
        not force_refresh
        and _MODEL_LIST_CACHE is not None
        and now - _MODEL_LIST_CACHE_AT < _MODEL_LIST_CACHE_TTL_S
    ):
        return _MODEL_LIST_CACHE

    if not groq_client or not (os.environ.get("GROQ_API_KEY") or "").strip():
        return frozenset()

    try:
        listing = groq_client.models.list()
        data = getattr(listing, "data", None) or []
        ids: set[str] = set()
        for item in data:
            model_id = getattr(item, "id", None)
            if model_id is None and isinstance(item, dict):
                model_id = item.get("id")
            if model_id:
                ids.add(str(model_id))
        _MODEL_LIST_CACHE = frozenset(ids)
        _MODEL_LIST_CACHE_AT = now
        return _MODEL_LIST_CACHE
    except Exception as exc:
        log.debug("Groq models.list failed: %s", exc)
        return frozenset()


def resolve_groq_model(
    model: str,
    *,
    fallback: str,
    groq_client,
) -> str:
    """
    Return *model* when Groq lists it; otherwise log and return *fallback*.

    When the model list is unavailable, the requested model is returned unchanged.
    """
    requested = (model or "").strip() or fallback
    available = get_available_groq_models(groq_client)
    if not available:
        return requested
    if requested not in available:
        log.warning(
            "Model %s not available; falling back to %s",
            requested,
            fallback,
        )
        return fallback
    return requested


def groq_completions_create(
    groq_client,
    *,
    model: str,
    fallback: str,
    **kwargs: Any,
):
    """Validate *model* against Groq's catalog, then call chat.completions.create."""
    resolved = resolve_groq_model(model, fallback=fallback, groq_client=groq_client)
    return groq_client.chat.completions.create(model=resolved, **kwargs)


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
