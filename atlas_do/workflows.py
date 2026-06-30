"""
atlas_do/workflows.py — Unified workflow JSON for playbooks and recorded routines.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WORKFLOW_VERSION = 1


def playbook_to_workflow(playbook: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": WORKFLOW_VERSION,
        "type": "playbook",
        "goal": str(playbook.get("goal") or playbook.get("task") or ""),
        "task_signature": str(playbook.get("task_signature") or ""),
        "steps": list(playbook.get("steps") or []),
        "meta": {
            "success_count": int(playbook.get("success_count") or 0),
            "source": str(playbook.get("source") or "playbook"),
        },
    }


def routine_to_workflow(routine: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": WORKFLOW_VERSION,
        "type": "routine",
        "name": str(routine.get("name") or ""),
        "events": list(routine.get("events") or routine.get("steps") or []),
        "meta": {
            "created": routine.get("created"),
            "description": str(routine.get("description") or ""),
        },
    }


def export_workflow(path: str | Path, workflow: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(workflow, indent=2), encoding="utf-8")


def import_workflow(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("workflow must be a JSON object")
    if int(data.get("version") or 0) != WORKFLOW_VERSION:
        raise ValueError(f"unsupported workflow version: {data.get('version')}")
    return data


def workflow_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    wtype = str(workflow.get("type") or "")
    if wtype == "playbook":
        return [s for s in (workflow.get("steps") or []) if isinstance(s, dict)]
    if wtype == "routine":
        events = workflow.get("events") or []
        out: list[dict[str, Any]] = []
        for ev in events:
            if isinstance(ev, dict):
                out.append(ev)
        return out
    return []
