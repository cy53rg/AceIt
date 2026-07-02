"""
atlas_research.py — Web search + doc extraction for [[RESEARCH:]] grounding.

Results are session-cached by normalized query. Extracted text is meant as
grounding context (summarize in dialogue — do not dump verbatim).
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Optional
from urllib.parse import urlparse

import requests

log = logging.getLogger("atlas_research")

MAX_EXTRACT_CHARS = 2500
MAX_GROUNDING_CHARS = 6000
DEFAULT_RESEARCH_TOKEN_BUDGET = 5000
SEARCH_TIMEOUT_S = 12
FETCH_TIMEOUT_S = 15

_search_cache: dict[str, list[dict[str, str]]] = {}
_grounding_cache: dict[str, str] = {}
_cache_lock = threading.Lock()
_research_engine: Optional["ResearchEngine"] = None
_research_engine_lock = threading.Lock()


def research_token_budget() -> int:
    """Total Groq token budget for research summarization in this session."""
    raw = (os.environ.get("ATLAS_RESEARCH_TOKEN_BUDGET") or "").strip()
    if not raw:
        return DEFAULT_RESEARCH_TOKEN_BUDGET
    try:
        return max(1000, int(raw))
    except ValueError:
        return DEFAULT_RESEARCH_TOKEN_BUDGET


def normalize_query(query: str) -> str:
    q = re.sub(r"\s+", " ", (query or "").strip().lower())
    return q[:240]


def clear_research_cache() -> None:
    """Drop session-scoped search/grounding caches (call on new session)."""
    with _cache_lock:
        _search_cache.clear()
        _grounding_cache.clear()
    get_research_engine()._research_token_usage = 0


def get_research_engine() -> "ResearchEngine":
    global _research_engine
    with _research_engine_lock:
        if _research_engine is None:
            _research_engine = ResearchEngine()
        return _research_engine


def search_task_docs(query: str, *, max_results: int = 5) -> list[dict[str, str]]:
    """
    Search the web for task/how-to documentation.

    Returns ``[{"title", "url", "snippet"}, ...]``.

    Uses ``SEARCH_API_KEY`` with ``SEARCH_PROVIDER`` (brave, bing, serpapi).
    Falls back to DuckDuckGo when no API key is configured (best-effort).
    """
    key = normalize_query(query)
    if not key:
        return []

    with _cache_lock:
        if key in _search_cache:
            return list(_search_cache[key][:max_results])

    api_key = (os.environ.get("SEARCH_API_KEY") or "").strip()
    provider = (os.environ.get("SEARCH_PROVIDER") or "brave").strip().lower()
    results: list[dict[str, str]] = []

    if api_key:
        try:
            if provider == "serpapi":
                results = _search_serpapi(key, api_key, max_results)
            elif provider == "bing":
                results = _search_bing(key, api_key, max_results)
            else:
                results = _search_brave(key, api_key, max_results)
        except Exception as exc:
            log.warning("search API (%s) failed: %s", provider, exc)

    if not results:
        try:
            results = _search_duckduckgo(key, max_results)
            if not api_key:
                log.info(
                    "Using DuckDuckGo fallback for research — set SEARCH_API_KEY "
                    "for a supported API (brave/bing/serpapi)."
                )
        except Exception as exc:
            log.warning("DuckDuckGo search fallback failed: %s", exc)

    with _cache_lock:
        _search_cache[key] = list(results)
    return list(results[:max_results])


def fetch_and_extract(url: str, *, max_chars: int = MAX_EXTRACT_CHARS) -> str:
    """Fetch a URL and return readable main text (capped), or \"\" on failure."""
    url = (url or "").strip()
    if not url or not _url_allowed(url):
        return ""
    try:
        import trafilatura

        downloaded = trafilatura.fetch_url(url, no_ssl=False)
        if not downloaded:
            return ""
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if not text:
            return ""
        text = re.sub(r"\n{3,}", "\n\n", text.strip())
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        return text
    except Exception as exc:
        log.debug("fetch_and_extract failed for %s: %s", url, exc)
        return ""


def ground_query(query: str, *, max_pages: int = 2) -> str:
    """
    Search + fetch top pages into one pinned-context block (session-cached).
    """
    key = normalize_query(query)
    if not key:
        return ""

    with _cache_lock:
        cached = _grounding_cache.get(key)
        if cached is not None:
            return cached

    hits = search_task_docs(query, max_results=max(3, max_pages + 1))
    blocks: list[str] = []
    for hit in hits:
        if len(blocks) >= max_pages:
            break
        url = hit.get("url", "")
        if not url:
            continue
        body = fetch_and_extract(url)
        if not body:
            snippet = (hit.get("snippet") or "").strip()
            if snippet:
                body = snippet
            else:
                continue
        title = hit.get("title") or url
        blocks.append(f"Source: {title}\nURL: {url}\n{body}")

    if not blocks:
        grounding = (
            f"Research query: {query}\n"
            "No usable documentation excerpts were retrieved."
        )
    else:
        grounding = (
            f"Research query: {query}\n\n"
            + "\n\n---\n\n".join(blocks)
            + "\n\n(Grounding notes — summarize in your own words; do not reproduce "
            "large verbatim passages in chat.)"
        )

    if len(grounding) > MAX_GROUNDING_CHARS:
        grounding = grounding[: MAX_GROUNDING_CHARS - 1].rstrip() + "…"

    with _cache_lock:
        _grounding_cache[key] = grounding
    return grounding


class ResearchEngine:
    """
    Multi-step research with Groq summarization and a session token budget.

    Token usage is tracked per thread so overlapping research jobs do not
    corrupt each other's counters.
    """

    def __init__(self) -> None:
        self._research_token_local = threading.local()
        self._research_token_usage = 0

    @property
    def _research_token_usage(self) -> int:
        return int(getattr(self._research_token_local, "value", 0))

    @_research_token_usage.setter
    def _research_token_usage(self, value: int) -> None:
        self._research_token_local.value = int(value)

    def _remaining_budget(self) -> int:
        return research_token_budget() - self._research_token_usage

    def _groq_research_call(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int = 800,
    ) -> tuple[Optional[str], Optional[dict[str, str]]]:
        """Run one budget-checked Groq completion for research summarization."""
        budget = research_token_budget()
        remaining = budget - self._research_token_usage
        if remaining < 1000:
            return None, {"error": "Research budget exhausted."}

        from atlas_core import GROQ_MODEL, _default_chat, groq_client
        from atlas_mind.model_health import groq_completions_create

        resp = groq_completions_create(
            groq_client,
            model=GROQ_MODEL,
            fallback=_default_chat,
            messages=messages,
            temperature=0.2,
            max_tokens=min(max_tokens, 1200),
        )
        usage = getattr(resp, "usage", None)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        self._research_token_usage += total_tokens
        remaining_after = budget - self._research_token_usage
        log.info(
            "Research call used %d tokens; remaining: %d / %d",
            total_tokens,
            max(remaining_after, 0),
            budget,
        )
        content = (resp.choices[0].message.content or "").strip()
        return content, None

    @staticmethod
    def _search_queries_for_topic(topic: str, max_searches: int) -> list[str]:
        topic = (topic or "").strip()
        if max_searches <= 1:
            return [topic]
        return [
            topic,
            f"{topic} official documentation",
        ][:max_searches]

    @staticmethod
    def _format_hits_for_groq(
        hits: list[dict[str, str]],
        query: str,
        *,
        max_pages: int = 2,
    ) -> str:
        blocks: list[str] = []
        for hit in hits:
            if len(blocks) >= max_pages:
                break
            url = hit.get("url", "")
            if not url:
                continue
            body = fetch_and_extract(url)
            if not body:
                snippet = (hit.get("snippet") or "").strip()
                if snippet:
                    body = snippet
                else:
                    continue
            title = hit.get("title") or url
            blocks.append(f"Source: {title}\nURL: {url}\n{body}")
        if blocks:
            return "\n\n---\n\n".join(blocks)
        if hits:
            lines = [f"Search hits for {query!r} (no full page text retrieved):"]
            for hit in hits[:max_pages]:
                lines.append(
                    f"- {hit.get('title') or 'Untitled'}: "
                    f"{(hit.get('snippet') or '')[:220]}"
                )
            return "\n".join(lines)
        return f"No web results found for {query!r}."

    @staticmethod
    def _merge_summaries(topic: str, summaries: list[dict[str, str]]) -> str:
        if not summaries:
            return (
                f"Research query: {topic}\n"
                "No usable documentation excerpts were retrieved."
            )
        parts = [f"Research query: {topic}", ""]
        for item in summaries:
            parts.append(f"Search: {item.get('query', topic)}")
            parts.append(str(item.get("summary") or "").strip())
            parts.append("")
        parts.append(
            "(Grounding notes — summarize in your own words; do not reproduce "
            "large verbatim passages in chat.)"
        )
        grounding = "\n".join(parts).strip()
        if len(grounding) > MAX_GROUNDING_CHARS:
            grounding = grounding[: MAX_GROUNDING_CHARS - 1].rstrip() + "…"
        return grounding

    def explore_topic(self, topic: str, *, max_searches: int = 2) -> dict[str, Any]:
        """
        Search the web and summarize findings with Groq under a token budget.

        Returns a dict with ``grounding`` on success or ``error`` when the
        budget is exhausted.
        """
        topic = (topic or "").strip()
        if not topic:
            return {"error": "Empty research topic."}

        budget = research_token_budget()
        remaining = budget - self._research_token_usage
        if remaining < 1000:
            return {"error": "Research budget exhausted."}

        summaries: list[dict[str, str]] = []
        for query in self._search_queries_for_topic(topic, max_searches):
            remaining = budget - self._research_token_usage
            if remaining < 1000:
                if summaries:
                    return {
                        "topic": topic,
                        "summaries": summaries,
                        "grounding": self._merge_summaries(topic, summaries),
                        "usage": self._research_token_usage,
                        "error": "Research budget exhausted.",
                    }
                return {"error": "Research budget exhausted."}

            hits = search_task_docs(query, max_results=3)
            excerpt = self._format_hits_for_groq(hits, query)
            summary, err = self._groq_research_call(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize task documentation for guided desktop help. "
                            "Be concise and actionable."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Topic: {topic}\n"
                            f"Search query: {query}\n\n"
                            f"Sources:\n{excerpt}"
                        ),
                    },
                ],
            )
            if err:
                if summaries:
                    return {
                        "topic": topic,
                        "summaries": summaries,
                        "grounding": self._merge_summaries(topic, summaries),
                        "usage": self._research_token_usage,
                        **err,
                    }
                return err
            summaries.append({"query": query, "summary": summary or ""})

        grounding = self._merge_summaries(topic, summaries)
        return {
            "topic": topic,
            "summaries": summaries,
            "grounding": grounding,
            "usage": self._research_token_usage,
        }


def explore_topic(topic: str, *, max_searches: int = 2) -> dict[str, Any]:
    """Module-level helper using the shared ResearchEngine instance."""
    return get_research_engine().explore_topic(topic, max_searches=max_searches)


def _url_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False
    return True


def _search_brave(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": max_results},
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
        timeout=SEARCH_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    web = data.get("web") or {}
    rows = web.get("results") or []
    out: list[dict[str, str]] = []
    for row in rows[:max_results]:
        if not isinstance(row, dict):
            continue
        out.append({
            "title": str(row.get("title") or ""),
            "url": str(row.get("url") or ""),
            "snippet": str(row.get("description") or row.get("snippet") or ""),
        })
    return out


def _search_bing(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    resp = requests.get(
        "https://api.bing.microsoft.com/v7.0/search",
        params={"q": query, "count": max_results, "textDecorations": False},
        headers={"Ocp-Apim-Subscription-Key": api_key},
        timeout=SEARCH_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("webPages", {}).get("value") or []
    out: list[dict[str, str]] = []
    for row in rows[:max_results]:
        if not isinstance(row, dict):
            continue
        out.append({
            "title": str(row.get("name") or ""),
            "url": str(row.get("url") or ""),
            "snippet": str(row.get("snippet") or ""),
        })
    return out


def _search_serpapi(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    resp = requests.get(
        "https://serpapi.com/search.json",
        params={"engine": "google", "q": query, "api_key": api_key, "num": max_results},
        timeout=SEARCH_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("organic_results") or []
    out: list[dict[str, str]] = []
    for row in rows[:max_results]:
        if not isinstance(row, dict):
            continue
        out.append({
            "title": str(row.get("title") or ""),
            "url": str(row.get("link") or ""),
            "snippet": str(row.get("snippet") or ""),
        })
    return out


def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, str]]:
    try:
        from duckduckgo_search import DDGS
    except ImportError as exc:
        raise RuntimeError(
            "duckduckgo-search is not installed; set SEARCH_API_KEY instead"
        ) from exc

    out: list[dict[str, str]] = []
    with DDGS() as ddgs:
        for row in ddgs.text(query, max_results=max_results):
            if not isinstance(row, dict):
                continue
            out.append({
                "title": str(row.get("title") or ""),
                "url": str(row.get("href") or row.get("url") or ""),
                "snippet": str(row.get("body") or row.get("snippet") or ""),
            })
    return out
