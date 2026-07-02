"""Research token budget enforcement for explore_topic()."""
from __future__ import annotations

from types import SimpleNamespace

import atlas_research as ar


def _fake_groq_factory(tokens_per_call: int):
    calls: list[dict] = []

    def _fake_groq(_client, *, model, fallback, **kwargs):
        calls.append({"messages": kwargs.get("messages"), "max_tokens": kwargs.get("max_tokens")})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=f"Summary {len(calls)}"))],
            usage=SimpleNamespace(total_tokens=tokens_per_call),
        )

    return _fake_groq, calls


def test_explore_topic_stops_when_budget_exhausted(monkeypatch):
    monkeypatch.setenv("ATLAS_RESEARCH_TOKEN_BUDGET", "5000")
    ar.clear_research_cache()
    engine = ar.ResearchEngine()

    fake_groq, groq_calls = _fake_groq_factory(2500)
    monkeypatch.setattr("atlas_mind.model_health.groq_completions_create", fake_groq)
    monkeypatch.setattr(
        ar,
        "search_task_docs",
        lambda *_a, **_k: [{"title": "Guide", "url": "https://example.com/g", "snippet": "S"}],
    )
    monkeypatch.setattr(ar, "fetch_and_extract", lambda *_a, **_k: "Step one.")

    first = engine.explore_topic("docker install", max_searches=2)
    second = engine.explore_topic("nginx config", max_searches=2)

    assert first.get("error") is None
    assert len(first["summaries"]) == 2
    assert first["usage"] == 5000
    assert len(groq_calls) == 2

    assert second == {"error": "Research budget exhausted."}
    assert len(groq_calls) == 2


def test_second_search_skipped_when_remaining_below_threshold(monkeypatch):
    monkeypatch.setenv("ATLAS_RESEARCH_TOKEN_BUDGET", "3400")
    engine = ar.ResearchEngine()

    fake_groq, groq_calls = _fake_groq_factory(2500)
    monkeypatch.setattr("atlas_mind.model_health.groq_completions_create", fake_groq)
    monkeypatch.setattr(
        ar,
        "search_task_docs",
        lambda q, max_results=3: [
            {"title": q, "url": "https://example.com/q", "snippet": "S"},
        ],
    )
    monkeypatch.setattr(ar, "fetch_and_extract", lambda *_a, **_k: "Body text.")

    result = engine.explore_topic("export pdf", max_searches=2)

    assert len(groq_calls) == 1
    assert len(result["summaries"]) == 1
    assert result["error"] == "Research budget exhausted."
    assert result["usage"] == 2500


def test_budget_check_runs_before_each_groq_call(monkeypatch):
    monkeypatch.setenv("ATLAS_RESEARCH_TOKEN_BUDGET", "5000")
    engine = ar.ResearchEngine()
    engine._research_token_usage = 4100

    fake_groq, groq_calls = _fake_groq_factory(2500)
    monkeypatch.setattr("atlas_mind.model_health.groq_completions_create", fake_groq)
    monkeypatch.setattr(
        ar,
        "search_task_docs",
        lambda *_a, **_k: [{"title": "T", "url": "https://example.com/t", "snippet": "S"}],
    )
    monkeypatch.setattr(ar, "fetch_and_extract", lambda *_a, **_k: "Body.")

    result = engine.explore_topic("single topic", max_searches=2)

    assert result["error"] == "Research budget exhausted."
    assert groq_calls == []


def test_research_call_logs_remaining_tokens(monkeypatch, caplog):
    monkeypatch.setenv("ATLAS_RESEARCH_TOKEN_BUDGET", "5000")
    engine = ar.ResearchEngine()

    monkeypatch.setattr(
        "atlas_mind.model_health.groq_completions_create",
        _fake_groq_factory(2500)[0],
    )
    monkeypatch.setattr(
        ar,
        "search_task_docs",
        lambda *_a, **_k: [{"title": "T", "url": "https://example.com/t", "snippet": "S"}],
    )
    monkeypatch.setattr(ar, "fetch_and_extract", lambda *_a, **_k: "Body.")

    with caplog.at_level("INFO", logger="atlas_research"):
        engine.explore_topic("topic", max_searches=1)

    assert any(
        "Research call used 2500 tokens; remaining: 2500 / 5000" in rec.message
        for rec in caplog.records
    )


def test_clear_research_cache_resets_token_usage(monkeypatch):
    monkeypatch.setenv("ATLAS_RESEARCH_TOKEN_BUDGET", "5000")
    engine = ar.get_research_engine()
    engine._research_token_usage = 4200

    ar.clear_research_cache()

    assert engine._research_token_usage == 0
