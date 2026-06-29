"""
atlas_learning.py — Continual learning loops (facts, skills, persona drift).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Optional

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
TEACHING_CRITIQUE_EVERY = DRIFT_CHECK_EVERY

_CONFUSION_PATTERNS = (
    r"(?:don't|do not|can't|cannot|cant)\s+(?:see|find|locate)",
    r"\bwhere\s+(?:is|are|do|should)",
    r"^where\??$",
    r"(?:huh|what\?|i'm lost|im lost|confused|stuck|that didn't work)",
    r"(?:doesn't look|does not look|not right|wrong place)",
)
_CONFUSION_RE = re.compile("|".join(_CONFUSION_PATTERNS), re.IGNORECASE)
_SHORT_NEGATIVE = frozenset({"no", "nope", "nah", "wrong", "naw", "ugh"})


def task_type_key(goal: str, *, max_words: int = 4) -> str:
    """Loose key for per-task-type teaching rollups in memory."""
    words = re.sub(r"[^\w\s]", " ", (goal or "").lower()).split()
    words = [w for w in words if w and w not in (
        "the", "a", "an", "to", "for", "me", "my", "through", "guide", "how", "in",
    )]
    if not words:
        return "general"
    return " ".join(words[:max_words])


class TeachingPerformanceTracker:
    """Session-scoped counters for guided teaching and self-action verification."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset_session()

    def reset_session(self) -> None:
        with self._lock:
            self.task_key: str = ""
            self.counters: dict[str, int] = {
                "steps_total": 0,
                "steps_verified_first_try": 0,
                "steps_needed_correction": 0,
                "steps_user_confused": 0,
                "guide_targets_not_found": 0,
                "self_actions_failed_after_retries": 0,
                "research_lookups_performed": 0,
            }
            self._recent_discrepancies: list[str] = []

    def set_task_context(self, goal: str) -> None:
        key = task_type_key(goal)
        with self._lock:
            if key:
                self.task_key = key

    @staticmethod
    def is_user_confused(text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        if _CONFUSION_RE.search(t):
            return True
        if len(t) <= 24 and t.lower().rstrip(".!?") in _SHORT_NEGATIVE:
            return True
        if t.endswith("?") and len(t.split()) <= 6:
            return True
        return False

    def record_guide_step(self) -> None:
        with self._lock:
            self.counters["steps_total"] += 1

    def record_verified_first_try(self) -> None:
        with self._lock:
            self.counters["steps_verified_first_try"] += 1

    def record_correction(self, discrepancy: str = "", observed: str = "") -> None:
        with self._lock:
            self.counters["steps_needed_correction"] += 1
            note = (discrepancy or observed or "").strip()
            if note:
                self._recent_discrepancies.append(note[:240])
                self._recent_discrepancies = self._recent_discrepancies[-5:]

    def record_user_confused(self) -> None:
        with self._lock:
            self.counters["steps_user_confused"] += 1

    def record_target_not_found(self) -> None:
        with self._lock:
            self.counters["guide_targets_not_found"] += 1

    def record_self_action_failed(self, discrepancy: str = "", observed: str = "") -> None:
        with self._lock:
            self.counters["self_actions_failed_after_retries"] += 1
            note = (discrepancy or observed or "").strip()
            if note:
                self._recent_discrepancies.append(note[:240])
                self._recent_discrepancies = self._recent_discrepancies[-5:]

    def record_research(self) -> None:
        with self._lock:
            self.counters["research_lookups_performed"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "task_key": self.task_key,
                "counters": dict(self.counters),
                "recent_discrepancies": list(self._recent_discrepancies),
            }

    def format_user_summary(self) -> str:
        diag = self._compute_rates(self.snapshot()["counters"])
        total = int(diag.get("steps_total") or 0)
        if total == 0 and not diag.get("research_lookups_performed"):
            return (
                "No guided steps recorded this session yet. "
                "Start a walkthrough in Guided mode and I'll track how well I'm teaching."
            )
        parts = [f"Out of {total} guided step{'s' if total != 1 else ''} this session,"]
        ok = int(diag.get("steps_verified_first_try") or 0)
        parts.append(f"{ok} were confirmed correct on the first try")
        corr = int(diag.get("steps_needed_correction") or 0)
        if corr:
            parts.append(f"{corr} needed a correction")
        confused = int(diag.get("steps_user_confused") or 0)
        if confused:
            parts.append(f"you seemed stuck or confused {confused} time{'s' if confused != 1 else ''}")
        missed = int(diag.get("guide_targets_not_found") or 0)
        if missed:
            parts.append(f"I couldn't locate a target {missed} time{'s' if missed != 1 else ''}")
        failed = int(diag.get("self_actions_failed_after_retries") or 0)
        if failed:
            parts.append(
                f"{failed} autonomous action{'s' if failed != 1 else ''} failed after retries"
            )
        research = int(diag.get("research_lookups_performed") or 0)
        if research:
            parts.append(
                f"I looked up documentation {research} time{'s' if research != 1 else ''}"
            )
        text = ", ".join(parts) + "."
        rate = float(diag.get("first_try_rate") or 0.0)
        if total >= 3:
            text += f" First-try success rate: {rate:.0%}."
        return text

    @staticmethod
    def _compute_rates(counters: dict[str, int]) -> dict[str, Any]:
        total = int(counters.get("steps_total") or 0)
        verified = int(counters.get("steps_verified_first_try") or 0)
        correction = int(counters.get("steps_needed_correction") or 0)
        out = dict(counters)
        out["first_try_rate"] = (verified / total) if total else 0.0
        out["correction_rate"] = (correction / total) if total else 0.0
        confused = int(counters.get("steps_user_confused") or 0)
        out["confusion_rate"] = (confused / total) if total else 0.0
        failed = int(counters.get("self_actions_failed_after_retries") or 0)
        actions = verified + correction + failed
        out["self_action_fail_rate"] = (failed / actions) if actions else 0.0
        return out


class LearningEngine:
    """Orchestrates fact reinforcement, skill warnings, and persona drift correction."""

    def __init__(self, memory: UserMemory, user_id: int) -> None:
        self.memory = memory
        self.user_id = user_id
        self.teaching = TeachingPerformanceTracker()
        self._playbook_persist_hook: Optional[Callable[[bool], None]] = None
        self._turn_count = 0
        self._recent_responses: list[str] = []
        self._pending_correction: Optional[str] = None
        self._pending_teaching_correction: Optional[str] = None
        self._lock = threading.Lock()

    def _get_groq(self) -> Any:
        return self.memory._get_groq()

    def on_turn_complete(self, user_text: str, ai_text: str) -> None:
        """
        Single post-turn cognition pass: extract durable facts (reinforcing
        known ones, learning new ones) and run periodic persona-drift checks.

        This is the ONLY fact-extraction path per turn — StateEngine schedules
        this via ``_schedule_learning_on_turn_complete`` (one Groq extraction
        per turn, with reinforcement for known facts and persona-drift checks).
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
            if self._turn_count % TEACHING_CRITIQUE_EVERY == 0:
                critique = self._maybe_teaching_self_critique()
                if critique:
                    self._pending_teaching_correction = critique

    def get_self_diagnosis(self) -> dict[str, Any]:
        """Structured teaching-performance summary with simple rates."""
        snap = self.teaching.snapshot()
        rates = TeachingPerformanceTracker._compute_rates(snap["counters"])
        return {
            **rates,
            "task_key": snap["task_key"],
            "recent_discrepancies": snap["recent_discrepancies"],
        }

    def format_teaching_summary_for_user(self) -> str:
        return self.teaching.format_user_summary()

    def persist_teaching_rollup(self) -> None:
        """Save session teaching stats under teach:{task_key} in skill_outcomes."""
        snap = self.teaching.snapshot()
        key = snap.get("task_key") or ""
        counters = snap.get("counters") or {}
        if not key or int(counters.get("steps_total") or 0) == 0:
            self._invoke_playbook_hook(success=False)
            return
        diag = self.get_self_diagnosis()
        success = (
            float(diag.get("correction_rate") or 0.0) < 0.35
            and int(diag.get("self_actions_failed_after_retries") or 0) == 0
        )
        try:
            self.memory.log_skill_outcome(
                self.user_id,
                f"teach:{key}",
                success,
                json.dumps({k: diag[k] for k in diag if k != "recent_discrepancies"}),
            )
        except Exception as exc:
            log.warning("persist_teaching_rollup failed: %s", exc)
        self._invoke_playbook_hook(success=success)

    def set_playbook_persist_hook(self, hook: Optional[Callable[[bool], None]]) -> None:
        self._playbook_persist_hook = hook

    def _invoke_playbook_hook(self, *, success: bool) -> None:
        hook = getattr(self, "_playbook_persist_hook", None)
        if hook is None:
            return
        try:
            hook(success)
        except Exception as exc:
            log.warning("playbook persist hook failed: %s", exc)

    def get_teaching_hint(self, goal: str) -> str | None:
        """Past-session hint when this task type struggled before."""
        key = task_type_key(goal)
        if not key or key == "general":
            return None
        perf = self.memory.get_skill_performance(self.user_id, f"teach:{key}")
        uses = int(perf.get("uses", 0))
        rate = float(perf.get("success_rate", 1.0))
        if uses >= 2 and rate < 0.6:
            return (
                f"Teaching note: past walkthroughs of '{key}' needed extra corrections "
                f"({rate:.0%} smooth first-try rate over {uses} sessions) — "
                "slow down, verify each step, and use clearer target descriptions."
            )
        return None

    def _maybe_teaching_self_critique(self) -> str | None:
        diag = self.get_self_diagnosis()
        if int(diag.get("steps_total") or 0) < 3:
            return None
        client = self._get_groq()
        if client is None:
            return None
        discrepancies = diag.get("recent_discrepancies") or []
        disc_block = ""
        if discrepancies:
            disc_block = "Recent verification mismatches:\n" + "\n".join(
                f"- {d}" for d in discrepancies[-5:]
            )
        stats = (
            f"steps_total={diag.get('steps_total', 0)}, "
            f"first_try_rate={diag.get('first_try_rate', 0):.2f}, "
            f"correction_rate={diag.get('correction_rate', 0):.2f}, "
            f"confusion_rate={diag.get('confusion_rate', 0):.2f}, "
            f"targets_not_found={diag.get('guide_targets_not_found', 0)}, "
            f"self_action_failures={diag.get('self_actions_failed_after_retries', 0)}, "
            f"research_lookups={diag.get('research_lookups_performed', 0)}"
        )
        prompt = (
            "You are Atlas reviewing your own teaching/automation performance this session.\n"
            f"Stats: {stats}\n\n"
            f"{disc_block}\n\n"
            "In 2-3 sentences, self-critique: what kind of step are you struggling with "
            "(vague instructions, bad target descriptions, wrong app assumptions?) and "
            "what you should change for the REST of this session. Be specific and actionable."
        )
        try:
            resp = client.chat.completions.create(
                model=ATLAS_FAST_MODEL,
                messages=[
                    {"role": "system", "content": "Reply in 2-3 plain sentences only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=180,
            )
            critique = (resp.choices[0].message.content or "").strip()
            if not critique:
                return None
            return f"Teaching self-critique: {critique}"
        except Exception as exc:
            log.debug("teaching self-critique failed: %s", exc)
            return None

    def reset_teaching_session(self) -> None:
        """Clear session counters (call before starting a fresh session)."""
        self.persist_teaching_rollup()
        self.teaching.reset_session()

    def run_decay(self) -> None:
        """Daemon entry: decay stale facts once at startup."""
        try:
            count = self.memory.decay_stale_facts()
            if count:
                log.info("Decayed %d stale facts", count)
        except Exception as exc:
            log.warning("decay_stale_facts failed: %s", exc)

    def run_weekly_diagnostics(self, recent_responses: list[str] | None = None) -> dict[str, Any]:
        """Weekly persona-drift + teaching self-check (not gated on turn count)."""
        responses = list(recent_responses if recent_responses is not None else self._recent_responses)
        persona_drift = self.check_persona_drift(responses) if responses else None
        teaching_critique = None
        if responses:
            teaching_critique = self._weekly_teaching_critique(responses)
        return {
            "persona_drift": persona_drift,
            "teaching_diagnosis": self.get_self_diagnosis(),
            "teaching_critique": teaching_critique,
        }

    def _weekly_teaching_critique(self, recent_responses: list[str]) -> str | None:
        client = self._get_groq()
        if client is None or not recent_responses:
            return None
        snap = self.teaching.snapshot()
        diag = self.get_self_diagnosis()
        prompt = (
            "Weekly teaching review. Based on session stats and recent assistant replies, "
            "give 2-3 sentences on what to improve next week.\n\n"
            f"STATS: {json.dumps({k: diag.get(k) for k in diag if k != 'recent_discrepancies'})}\n"
            f"RECENT:\n" + "\n---\n".join(recent_responses[-8:])[:3000]
        )
        try:
            resp = client.chat.completions.create(
                model=ATLAS_FAST_MODEL,
                messages=[
                    {"role": "system", "content": "Reply in 2-3 plain sentences only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=180,
            )
            text = (resp.choices[0].message.content or "").strip()
            return f"Weekly teaching note: {text}" if text else None
        except Exception as exc:
            log.debug("weekly teaching critique failed: %s", exc)
            return None

    def check_persona_drift(self, recent_responses: list[str]) -> str | None:
        """Compare sampled tone to baseline; return correction string or None."""
        style = (
            str((self.memory.get_prefs(self.user_id) or {}).get("response_style") or "")
            .strip()
            .lower()
        )
        if style in ("terse", "brief", "concise", "short", "minimal"):
            return None
        client = self._get_groq()
        if client is None or not recent_responses:
            return None
        joined = "\n---\n".join(recent_responses[-DRIFT_SAMPLE_SIZE:])
        if len(joined) > 4000:
            third = max(1, len(joined) // 3)
            sample = "\n---\n".join([
                joined[:third],
                joined[third: third * 2],
                joined[third * 2: third * 2 + 1300],
            ])
        else:
            sample = joined
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
        """Return pending persona + teaching corrections and clear them."""
        with self._lock:
            parts: list[str] = []
            if self._pending_correction:
                parts.append(self._pending_correction)
                self._pending_correction = None
            if self._pending_teaching_correction:
                parts.append(self._pending_teaching_correction)
                self._pending_teaching_correction = None
        return "\n\n".join(parts) if parts else None

    def get_learning_report(self) -> dict[str, Any]:
        facts = self.memory.recall(self.user_id, min_confidence=0.0)
        high = [f for f in facts if float(f.get("confidence", 0)) >= 0.7]
        sessions = self.memory._recent_sessions(self.user_id, limit=100)
        with self._lock:
            turns = self._turn_count
            teaching = self.teaching.snapshot()
        return {
            "total_facts": len(facts),
            "high_confidence_facts": len(high),
            "recent_session_count": len(sessions),
            "turn_count": turns,
            "last_drift_check": self._turn_count // DRIFT_CHECK_EVERY * DRIFT_CHECK_EVERY,
            "teaching": teaching,
            "teaching_diagnosis": TeachingPerformanceTracker._compute_rates(
                teaching.get("counters") or {},
            ),
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
