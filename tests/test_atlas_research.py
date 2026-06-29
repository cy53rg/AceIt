"""Tests for atlas_research search/cache/extract helpers."""
from __future__ import annotations

import atlas_research as ar


def test_normalize_query_collapses_whitespace():
    assert ar.normalize_query("  Export   PDF  Word  ") == "export pdf word"


def test_search_task_docs_uses_session_cache(monkeypatch):
    ar.clear_research_cache()
    calls: list[str] = []

    def fake_ddg(query: str, max_results: int):
        calls.append(query)
        return [{"title": "T", "url": "https://example.com/a", "snippet": "S"}]

    monkeypatch.setattr(ar, "_search_duckduckgo", fake_ddg)
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)

    first = ar.search_task_docs("Export PDF Word")
    second = ar.search_task_docs("  export   pdf word  ")

    assert first == second
    assert len(calls) == 1
    assert first[0]["url"] == "https://example.com/a"


def test_fetch_and_extract_caps_length(monkeypatch):
    long_text = "word " * 2000

    def fake_fetch(url, no_ssl=False):
        return "<html>ok</html>"

    def fake_extract(downloaded, **kwargs):
        return long_text.strip()

    monkeypatch.setattr("trafilatura.fetch_url", fake_fetch)
    monkeypatch.setattr("trafilatura.extract", fake_extract)

    out = ar.fetch_and_extract("https://example.com/guide", max_chars=100)
    assert len(out) <= 100
    assert len(out) > 0


def test_ground_query_pins_and_caches(monkeypatch):
    ar.clear_research_cache()

    monkeypatch.setattr(
        ar,
        "search_task_docs",
        lambda q, max_results=5: [
            {"title": "Guide", "url": "https://example.com/g", "snippet": "fallback"},
        ],
    )
    monkeypatch.setattr(
        ar,
        "fetch_and_extract",
        lambda url, max_chars=2500: "Step one: open File menu.",
    )

    g1 = ar.ground_query("how to export pdf")
    g2 = ar.ground_query("HOW TO EXPORT PDF")

    assert g1 == g2
    assert "Step one" in g1
    assert "Research query:" in g1
