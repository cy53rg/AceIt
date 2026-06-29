"""
atlas_playbooks.py — Distilled TASK/GUIDE procedures stored in UserMemory.

Playbooks capture reusable step sequences (target, action, expected_state) without
screenshots or chat transcripts. Size is capped to keep storage in the low-KB range.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import Any, Callable, Optional

from atlas_learning import task_type_key
from atlas_logging import get_logger
from atlas_memory import ATLAS_FAST_MODEL, UserMemory

log = get_logger("playbooks")

PLAYBOOK_MAX_BYTES = 50 * 1024
MIN_SUCCESS_FOR_PROPOSAL = 1
SignatureFn = Callable[[str], str]
DistillFn = Callable[[str, list[dict[str, Any]]], list[dict[str, Any]]]

_FORBIDDEN_STEP_KEYS = frozenset({
    "screen_b64", "screenshot", "b64", "image", "frame", "transcript", "chat",
    "raw_html", "pixels",
})

_DISTILLED_STEP_KEYS = frozenset({
    "target", "action", "instruction", "expected_state",
})


def keyword_signature(goal: str) -> str:
    """Offline fallback when Groq is unavailable."""
    key = task_type_key(goal, max_words=6)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"kw:{digest[:20]}"


def sanitize_raw_steps(raw_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip non-procedural fields before distillation or storage."""
    cleaned: list[dict[str, Any]] = []
    for item in raw_steps or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").lower().strip()
        if action in ("done", "done_rejected", "stuck", "plan", "step_limit", "fail", "abort", "error"):
            continue
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else item
        if not isinstance(detail, dict):
            detail = {}
        step = {
            "target": str(detail.get("target") or detail.get("text") or item.get("target") or "").strip(),
            "action": action or str(detail.get("action") or "guide").strip(),
            "instruction": str(
                detail.get("instruction")
                or detail.get("say")
                or detail.get("thought")
                or item.get("instruction")
                or ""
            ).strip(),
            "expected_state": str(
                detail.get("expected_state")
                or detail.get("expect")
                or item.get("expected_state")
                or ""
            ).strip(),
        }
        step = {k: v for k, v in step.items() if v}
        if not step.get("action") and not step.get("target"):
            continue
        cleaned.append(step)
    return cleaned


def enforce_playbook_size(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """
    Ensure serialized steps stay under PLAYBOOK_MAX_BYTES.
    Returns (steps, byte_size). Truncates tail steps if needed and logs a warning.
    """
    steps = [s for s in steps if isinstance(s, dict)]
    while steps:
        payload = json.dumps(steps, separators=(",", ":"), ensure_ascii=False)
        size = len(payload.encode("utf-8"))
        if size <= PLAYBOOK_MAX_BYTES:
            return steps, size
        log.warning(
            "playbook steps exceed %d bytes (%d) — truncating tail step",
            PLAYBOOK_MAX_BYTES,
            size,
        )
        steps = steps[:-1]
    return [], 0


def _strip_distilled_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        row = {k: step[k] for k in _DISTILLED_STEP_KEYS if k in step and step[k]}
        for bad in _FORBIDDEN_STEP_KEYS:
            row.pop(bad, None)
        if row:
            out.append(row)
    return out


class PlaybookManager:
    """Orchestrates signature matching, distillation, and playbook lifecycle."""

    def __init__(
        self,
        memory: UserMemory,
        user_id: int,
        *,
        signature_fn: Optional[SignatureFn] = None,
        distill_fn: Optional[DistillFn] = None,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self._signature_fn = signature_fn
        self._distill_fn = distill_fn
        self._lock = threading.Lock()

    def compute_signature(self, goal: str) -> str:
        if self._signature_fn is not None:
            return self._signature_fn(goal)
        client = self.memory._get_groq()
        if client is None:
            return keyword_signature(goal)
        prompt = (
            "Return a short canonical task signature (2-6 lowercase hyphenated words) "
            "that identifies the TYPE of task, ignoring minor wording differences.\n"
            "Examples:\n"
            '- "deploy our React app to staging" → deploy-staging\n'
            '- "push the latest build to staging server" → deploy-staging\n'
            "Reply with ONLY the signature string, no punctuation besides hyphens.\n\n"
            f"Task: {goal.strip()}"
        )
        try:
            resp = client.chat.completions.create(
                model=ATLAS_FAST_MODEL,
                messages=[
                    {"role": "system", "content": "Reply with one signature string only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=32,
            )
            sig = (resp.choices[0].message.content or "").strip().lower()
            sig = re.sub(r"[^\w-]+", "-", sig).strip("-")
            if sig and len(sig) <= 64:
                return sig
        except Exception as exc:
            log.debug("signature groq failed: %s", exc)
        return keyword_signature(goal)

    def distill_steps(self, goal: str, raw_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sanitized = sanitize_raw_steps(raw_steps)
        if self._distill_fn is not None:
            distilled = self._distill_fn(goal, sanitized)
            return enforce_playbook_size(_strip_distilled_steps(distilled))[0]
        client = self.memory._get_groq()
        if client is None or not sanitized:
            steps, _ = enforce_playbook_size(_strip_distilled_steps(sanitized))
            return steps
        prompt = (
            "Condense this task procedure into reusable JSON steps ONLY.\n"
            "Each step: target, action (click|type|guide|press|hotkey|wait|navigate), "
            "instruction, expected_state.\n"
            "Do NOT include screenshots, transcripts, or chat text.\n"
            f"Goal: {goal}\n\nRaw steps:\n{json.dumps(sanitized)[:6000]}\n\n"
            'Return JSON: {"steps":[...]}'
        )
        try:
            resp = client.chat.completions.create(
                model=ATLAS_FAST_MODEL,
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=1200,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            steps = data.get("steps", [])
            if isinstance(steps, list):
                steps, size = enforce_playbook_size(_strip_distilled_steps(steps))
                if size > PLAYBOOK_MAX_BYTES:
                    log.warning("distilled playbook still over cap after enforcement")
                return steps
        except Exception as exc:
            log.warning("playbook distillation failed: %s", exc)
        steps, _ = enforce_playbook_size(_strip_distilled_steps(sanitized))
        return steps

    def check_proposal(self, goal: str) -> Optional[dict[str, Any]]:
        """If a proven playbook exists, return a reuse proposal for the user."""
        sig = self.compute_signature(goal)
        pb = self.memory.get_playbook(self.user_id, sig)
        if not pb:
            return None
        if int(pb.get("success_count") or 0) < MIN_SUCCESS_FOR_PROPOSAL:
            return None
        if float(pb.get("confidence") or 0) < 0.35:
            return None
        steps = pb.get("steps") or []
        return {
            "playbook_id": int(pb["id"]),
            "task_signature": sig,
            "goal": pb.get("goal") or goal,
            "success_count": int(pb.get("success_count") or 0),
            "steps": steps,
            "message": (
                f"I've done this before ({pb.get('success_count', 0)} successful runs) — "
                "want me to follow the same steps, or should I look it up fresh "
                "in case something's changed?"
            ),
        }

    def on_sequence_completed(
        self,
        goal: str,
        raw_steps: list[dict[str, Any]],
        *,
        source: str = "task",
        duration_s: float = 0.0,
        had_corrections: bool = False,
        used_playbook: bool = False,
        task_signature: str | None = None,
    ) -> Optional[dict[str, Any]]:
        """
        Record a successful TASK/GUIDE run. Synthesizes/updates playbook on 2nd+ success.
        Returns playbook info if created/updated.
        """
        sig = task_signature or self.compute_signature(goal)
        with self._lock:
            if used_playbook:
                self.memory.record_playbook_outcome(
                    self.user_id,
                    sig,
                    success=True,
                    duration_s=duration_s,
                    had_corrections=had_corrections,
                )
                return self.memory.get_playbook(self.user_id, sig)

            prior = self.memory.count_successful_playbook_runs(self.user_id, sig)
            sanitized = sanitize_raw_steps(raw_steps)
            self.memory.record_playbook_run(
                self.user_id,
                task_signature=sig,
                goal=goal,
                steps=sanitized,
                success=True,
                had_corrections=had_corrections,
                duration_s=duration_s,
                source=source,
            )

            if prior >= 1:
                distilled = self.distill_steps(goal, sanitized)
                steps, size = enforce_playbook_size(distilled)
                if not steps:
                    log.warning("playbook write rejected — no steps after size enforcement")
                    return None
                if size > PLAYBOOK_MAX_BYTES:
                    log.warning("playbook write rejected — %d bytes exceeds cap", size)
                    return None
                payload = json.dumps(steps, separators=(",", ":"), ensure_ascii=False)
                pid = self.memory.upsert_playbook(
                    self.user_id,
                    task_signature=sig,
                    goal=goal,
                    steps_json=payload,
                    duration_s=duration_s,
                )
                self.memory.record_playbook_outcome(
                    self.user_id,
                    sig,
                    success=True,
                    duration_s=duration_s,
                    had_corrections=had_corrections,
                )
                log.info(
                    "playbook upserted id=%s sig=%s size=%d bytes steps=%d",
                    pid, sig, size, len(steps),
                )
                return {"id": pid, "task_signature": sig, "size_bytes": size, "steps": steps}

            return None

    def on_sequence_failed(
        self,
        goal: str,
        *,
        task_signature: str | None = None,
        had_corrections: bool = True,
        used_playbook: bool = False,
    ) -> None:
        sig = task_signature or self.compute_signature(goal)
        if used_playbook:
            self.memory.record_playbook_outcome(
                self.user_id,
                sig,
                success=False,
                had_corrections=had_corrections,
            )
        self.memory.record_playbook_run(
            self.user_id,
            task_signature=sig,
            goal=goal,
            steps=[],
            success=False,
            had_corrections=had_corrections,
            source="task",
        )

    def run_maintenance(self) -> dict[str, int]:
        """Decay stale playbooks and prune consistently bad ones."""
        decayed = self.memory.decay_stale_playbooks()
        pruned = self.memory.prune_bad_playbooks()
        if decayed or pruned:
            log.info("playbook maintenance: decayed=%d pruned=%d", decayed, pruned)
        return {"decayed": decayed, "pruned": pruned}

    def playbook_steps_json_size(self, goal: str) -> int:
        sig = self.compute_signature(goal)
        pb = self.memory.get_playbook(self.user_id, sig)
        if not pb:
            return 0
        return len((pb.get("steps_json") or "[]").encode("utf-8"))
