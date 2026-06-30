"""
atlas_mind/provider_router.py — Groq primary, OpenRouter/Gemini fallback, Ollama offline.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterator, Optional

import requests

log = logging.getLogger("atlas_mind.provider_router")

_DEFAULT_GEMINI_MODEL = (os.environ.get("ATLAS_GEMINI_MODEL") or "gemini-2.0-flash").strip()
_DEFAULT_OLLAMA_MODEL = (os.environ.get("ATLAS_OLLAMA_MODEL") or "llama3.2").strip()
_OLLAMA_BASE = (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_DEFAULT_OPENROUTER_MODEL = (
    os.environ.get("ATLAS_OPENROUTER_MODEL") or "meta-llama/llama-3.3-70b-instruct:free"
).strip()


def resolve_groq_api_key(engine: Any = None) -> str:
    key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if key:
        return key
    if engine is not None:
        try:
            prefs = engine.get_user_prefs()
            key = str(prefs.get("groq_api_key") or "").strip()
            if key:
                os.environ["GROQ_API_KEY"] = key
                return key
        except Exception:
            pass
    return ""


def resolve_gemini_api_key() -> str:
    return (
        (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    )


def resolve_openrouter_api_key() -> str:
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip()


class ProviderRouter:
    """Try providers in order until one streams successfully."""

    def __init__(self) -> None:
        self._last_provider = "groq"

    @property
    def last_provider(self) -> str:
        return self._last_provider

    def stream_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        vision: bool = False,
        engine: Any = None,
        reasoning_effort: Optional[str] = None,
    ) -> Iterator[str]:
        errors: list[str] = []
        if resolve_groq_api_key(engine):
            try:
                for chunk in self._stream_groq(
                    messages,
                    model=model,
                    reasoning_effort=reasoning_effort,
                ):
                    self._last_provider = "groq"
                    yield chunk
                return
            except Exception as exc:
                errors.append(f"Groq: {exc}")
                log.warning("Groq stream failed, trying fallback: %s", exc)

        if resolve_openrouter_api_key() and not vision:
            try:
                for chunk in self._stream_openrouter(messages, model=model):
                    self._last_provider = "openrouter"
                    yield chunk
                return
            except Exception as exc:
                errors.append(f"OpenRouter: {exc}")
                log.warning("OpenRouter stream failed: %s", exc)

        gemini_key = resolve_gemini_api_key()
        if gemini_key and (vision or not self._messages_have_image(messages)):
            try:
                for chunk in self._stream_gemini(messages, vision=vision):
                    self._last_provider = "gemini"
                    yield chunk
                return
            except Exception as exc:
                errors.append(f"Gemini: {exc}")
                log.warning("Gemini stream failed, trying Ollama: %s", exc)
        elif vision and self._messages_have_image(messages):
            errors.append("Gemini: no API key for vision fallback")

        if not vision:
            try:
                for chunk in self._stream_ollama(messages):
                    self._last_provider = "ollama"
                    yield chunk
                return
            except Exception as exc:
                errors.append(f"Ollama: {exc}")

        detail = "; ".join(errors) or "no providers configured"
        raise RuntimeError(f"All AI providers failed ({detail})")

    @staticmethod
    def _messages_have_image(messages: list[dict[str, Any]]) -> bool:
        for msg in messages or []:
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False

    def _stream_groq(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        reasoning_effort: Optional[str],
    ) -> Iterator[str]:
        from groq import Groq

        client = Groq(api_key=resolve_groq_api_key())
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if str(model).startswith("openai/gpt-oss") and reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        stream = client.chat.completions.create(**kwargs)
        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            delta_obj = chunk.choices[0].delta
            if getattr(delta_obj, "reasoning", None):
                continue
            text = delta_obj.content or ""
            if text:
                yield text

    def _stream_gemini(
        self,
        messages: list[dict[str, Any]],
        *,
        vision: bool,
    ) -> Iterator[str]:
        import google.generativeai as genai

        genai.configure(api_key=resolve_gemini_api_key())
        model_name = _DEFAULT_GEMINI_MODEL
        if vision:
            model_name = os.environ.get("ATLAS_GEMINI_VISION_MODEL") or model_name
        model = genai.GenerativeModel(model_name)

        system_parts: list[str] = []
        history: list[dict[str, Any]] = []
        last_user: Any = ""
        for msg in messages:
            role = str(msg.get("role") or "user")
            content = msg.get("content")
            if role == "system":
                system_parts.append(self._content_to_text(content))
                continue
            if role == "assistant":
                history.append({"role": "model", "parts": [self._content_to_text(content)]})
            else:
                if vision and isinstance(content, list):
                    parts = self._gemini_parts_from_content(content)
                    last_user = parts
                else:
                    last_user = self._content_to_text(content)
                history.append({"role": "user", "parts": [last_user] if isinstance(last_user, str) else last_user})

        prompt = "\n\n".join(system_parts).strip()
        chat = model.start_chat(history=history[:-1] if history else [])
        user_msg = history[-1]["parts"] if history else [prompt or "Hello"]
        if prompt and history:
            user_msg = [f"{prompt}\n\n{self._content_to_text(last_user)}"] if isinstance(last_user, str) else user_msg
        stream = chat.send_message(user_msg, stream=True)
        for event in stream:
            text = getattr(event, "text", None) or ""
            if text:
                yield text

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            bits = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    bits.append(str(part.get("text") or ""))
            return "\n".join(bits)
        return str(content or "")

    @staticmethod
    def _gemini_parts_from_content(content: list) -> list:
        import base64
        import re

        parts: list[Any] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif item.get("type") == "image_url":
                url = (item.get("image_url") or {}).get("url") or ""
                match = re.match(r"data:image/[^;]+;base64,(.+)", url)
                if match:
                    raw = base64.b64decode(match.group(1))
                    parts.append({"mime_type": "image/png", "data": raw})
        return parts or [""]

    def _stream_openrouter(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
    ) -> Iterator[str]:
        key = resolve_openrouter_api_key()
        if not key:
            raise RuntimeError("OpenRouter API key not configured")
        or_model = _DEFAULT_OPENROUTER_MODEL
        if model and ":free" in model:
            or_model = model
        payload = {
            "model": or_model,
            "messages": [],
            "stream": True,
        }
        for m in messages:
            role = str(m.get("role") or "user")
            text = self._content_to_text(m.get("content"))
            if role in ("system", "user", "assistant"):
                payload["messages"].append({"role": role, "content": text})
        resp = requests.post(
            f"{_OPENROUTER_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "https://github.com/cy53rg/AceIt",
                "X-Title": "Atlas",
            },
            json=payload,
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = data.get("choices") or []
            if not choices:
                continue
            delta = (choices[0].get("delta") or {}).get("content") or ""
            if delta:
                yield delta

    def _stream_ollama(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        payload = {
            "model": _DEFAULT_OLLAMA_MODEL,
            "messages": [],
            "stream": True,
        }
        ollama_msgs = []
        for m in messages:
            role = str(m.get("role") or "user")
            text = self._content_to_text(m.get("content"))
            if role == "system":
                ollama_msgs.insert(0, {"role": "system", "content": text})
            elif role in ("user", "assistant"):
                ollama_msgs.append({"role": role, "content": text})
        payload["messages"] = ollama_msgs
        resp = requests.post(
            f"{_OLLAMA_BASE}/api/chat",
            json=payload,
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = data.get("message") or {}
            text = msg.get("content") or ""
            if text:
                yield text
            if data.get("done"):
                break


_router: Optional[ProviderRouter] = None


def get_provider_router() -> ProviderRouter:
    global _router
    if _router is None:
        _router = ProviderRouter()
    return _router
