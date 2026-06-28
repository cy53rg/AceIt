"""
atlas_learning.py — Continual learning loops (facts, skills, persona drift).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

from atlas_memory import UserMemory

log = logging.getLogger("atlas_learning")

_default_fast_model = "llama-3.1-8b-instant"
ATLAS_FAST_MODEL = (
    os.environ.get("ATLAS_FAST_MODEL") or _default_fast_model
).strip() or _default_fast_model

PERSONA_BASELINE = (
    "Atlas should sound like a trusted senior technical mentor: approachable and "
    "human without being chatty or performative. Lead with the answer; keep replies "
    "concise but not cold. Adapt register to the moment — exploratory when someone "
    "is learning, surgical when they're under pressure. Plain-spoken and confident "
    "without arrogance. No filler affirmations ('Great question!'), no rambling, "
    "no apology for brevity, no documentation-style walls of text."
)
PERSONA_DRIFT_THRESHOLD = 4  # 0–10 match score; correct when below this
DRIFT_CHECK_EVERY = 10
DRIFT_SAMPLE_SIZE = 20


class LearningEngine:
    """Orchestrates fact reinforcement, skill warnings, and persona drift correction."""

    def __init__(self, memory: UserMemory, user_id: int) -> None:
        self.memory = memory
        self.user_id = user_id
        self._turn_count = 0
        self._recent_responses: list[str] = []
        self._pending_correction: Optional[str] = None
        self._lock = threading.Lock()

    def _get_groq(self) -> Any:
        return self.memory._get_groq()

    def on_turn_complete(self, user_text: str, ai_text: str) -> None:
        """
        Single post-turn cognition pass: extract durable facts (reinforcing
        known ones, learning new ones) and run periodic persona-drift checks.

        This is the ONLY fact-extraction path per turn — StateEngine no longer
        also calls extract_and_store_facts_async, so each turn costs one Groq
        extraction instead of two.
        """
        facts = self.memory.extract_facts_from_turn(user_text, ai_text)
        existing = {
            (str(f["category"]).lower(), str(f["key"]).lower()): f
            for f in self.memory.recall(self.user_id, min_confidence=0.0)
        }
        for fact in facts:
            cat = str(fact.get("category", "general")).lower()
            key = str(fact.get("key", "")).lower()
            if (cat, key) in existing:
                self.memory.reinforce(self.user_id, cat, key)
            else:
                self.memory.remember(
                    self.user_id,
                    cat,
                    key,
                    str(fact.get("value", "")),
                    confidence=0.6,
                    source="learned",
                )
        # Preserve the Qt signal so UI indicators can still react to learning.
        if facts and getattr(self.memory, "signals", None) is not None:
            try:
                self.memory.signals.facts_extracted.emit(self.user_id, facts)
            except Exception:
                pass

        with self._lock:
            self._recent_responses.append(ai_text)
            if len(self._recent_responses) > DRIFT_SAMPLE_SIZE:
                self._recent_responses = self._recent_responses[-DRIFT_SAMPLE_SIZE:]
            self._turn_count += 1
            if self._turn_count % DRIFT_CHECK_EVERY == 0:
                correction = self.check_persona_drift(list(self._recent_responses))
                if correction:
                    self._pending_correction = correction

    def run_decay(self) -> None:
        """Daemon entry: decay stale facts once at startup."""
        try:
            count = self.memory.decay_stale_facts()
            if count:
                log.info("Decayed %d stale facts", count)
        except Exception as exc:
            log.warning("decay_stale_facts failed: %s", exc)

    def check_persona_drift(self, recent_responses: list[str]) -> str | None:
        """Compare sampled tone to baseline; return correction string or None."""
        client = self._get_groq()
        if client is None or not recent_responses:
            return None
        sample = "\n---\n".join(recent_responses[-DRIFT_SAMPLE_SIZE:])
        prompt = (
            "You evaluate whether an AI assistant's recent replies match a target persona.\n\n"
            f"TARGET PERSONA:\n{PERSONA_BASELINE}\n\n"
            "Read the assistant samples below. Rate how well they match the target persona "
            "on a 0–10 scale (10 = excellent match, 0 = completely off).\n"
            'Return JSON only: {"score": int, "brief_reason": "one sentence"}\n\n'
            f"ASSISTANT SAMPLES:\n{sample[:4000]}"
        )
        try:
            resp = client.chat.completions.create(
                model=ATLAS_FAST_MODEL,
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=120,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            raw_score = data.get("score")
            if raw_score is None:
                return None
            score = int(raw_score)
            if score >= PERSONA_DRIFT_THRESHOLD:
                return None
            reason = str(data.get("brief_reason", "")).strip()
            correction = (
                "Persona correction: realign with your Atlas voice — lead with the answer, "
                "stay concise and plain-spoken, warm but not chatty, no filler or rambling."
            )
            if reason:
                return f"{correction} ({reason})"
            return correction
        except Exception:
            return None

    def get_correction(self) -> str | None:
        """Return pending drift correction and clear it."""
        with self._lock:
            corr = self._pending_correction
            self._pending_correction = None
        return corr

    def get_learning_report(self) -> dict[str, Any]:
        facts = self.memory.recall(self.user_id, min_confidence=0.0)
        high = [f for f in facts if float(f.get("confidence", 0)) >= 0.7]
        sessions = self.memory._recent_sessions(self.user_id, limit=100)
        with self._lock:
            turns = self._turn_count
        return {
            "total_facts": len(facts),
            "high_confidence_facts": len(high),
            "recent_session_count": len(sessions),
            "turn_count": turns,
            "last_drift_check": self._turn_count // DRIFT_CHECK_EVERY * DRIFT_CHECK_EVERY,
        }

    def get_skill_warning(self, skill_name: str) -> str | None:
        perf = self.memory.get_skill_performance(self.user_id, skill_name)
        uses = int(perf.get("uses", 0))
        rate = float(perf.get("success_rate", 1.0))
        if uses >= 5 and rate < 0.3:
            return (
                f"Warning: skill '{skill_name}' success rate is {rate:.0%} "
                f"over {uses} uses — verify triggers and permissions."
            )
        return None
