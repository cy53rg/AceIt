"""Phase 5 — goals, killswitch, workflows, SKILL.md, audit log."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas_do.killswitch import KillSwitch
from atlas_do.workflows import import_workflow, playbook_to_workflow, routine_to_workflow
from atlas_memory import UserMemory
from atlas_skills import SkillRegistry, _parse_skill_md_frontmatter


@pytest.fixture
def memory(tmp_path):
    return UserMemory(str(tmp_path / "phase5.sqlite3"))


def test_killswitch_invokes_handlers():
    ks = KillSwitch()
    called: list[str] = []
    ks.register(lambda: called.append("a"))
    ks.register(lambda: called.append("b"))
    ks.engage()
    assert called == ["a", "b"]


def test_goal_lifecycle(memory):
    uid = memory.create_or_login("goal-user")
    gid = memory.goal_create(uid, "Organize desktop", steps=["Sort downloads", "Archive old files"])
    row = memory.goal_get(uid, gid)
    assert row
    assert len(row["steps"]) == 2
    memory.goal_checkpoint(uid, gid, 1, "Archive old files")
    memory.goal_update_status(uid, gid, "done")
    active = memory.goal_get_active(uid)
    assert active is None
    goals = memory.goal_list(uid)
    assert goals[0]["status"] == "done"


def test_audit_log(memory):
    uid = memory.create_or_login("audit-user")
    memory.audit_log(uid, "goal", "Started backup", {"step": 1})
    entries = memory.audit_list(uid, limit=5)
    assert entries
    assert entries[0]["category"] == "goal"
    assert entries[0]["detail"]["step"] == 1


def test_workflow_roundtrip(tmp_path):
    wf = playbook_to_workflow({
        "goal": "Deploy app",
        "steps": [{"action": "click", "target": "Deploy"}],
        "success_count": 2,
    })
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(wf), encoding="utf-8")
    loaded = import_workflow(path)
    assert loaded["type"] == "playbook"
    assert loaded["goal"] == "Deploy app"

    routine = routine_to_workflow({"name": "morning", "events": [{"type": "click"}]})
    assert routine["type"] == "routine"


def test_skill_md_frontmatter():
    text = """---
name: demo-skill
triggers:
  - run demo
---
Do the demo thing.
"""
    manifest, body = _parse_skill_md_frontmatter(text)
    assert manifest["name"] == "demo-skill"
    assert "run demo" in manifest["triggers"]
    assert "demo thing" in body


def test_skill_md_scan(tmp_path, monkeypatch):
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo-skill
triggers:
  - hello demo
---
Always greet warmly.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("atlas_skills.SKILL_DIR", skill_root)
    reg = SkillRegistry()
    assert reg.get("demo-skill")
    result = reg.execute("demo-skill", "hello demo", [], None, None, None, None)
    assert result["success"]
    assert "greet warmly" in result["inject_context"]


def test_goal_engine_start(monkeypatch, memory):
    from atlas_do.goal_engine import GoalEngine

    state = MagicMock()
    state.memory = memory
    state.user_id = memory.create_or_login("goal-engine")
    state._emit = MagicMock()
    state._task_running = False
    state.run_task = MagicMock()
    state.stop_task = MagicMock()
    state.killswitch = KillSwitch()

    monkeypatch.setattr(
        "atlas_do.goal_engine.GoalEngine._plan_steps",
        lambda self, text: ["step one", "step two"],
    )
    monkeypatch.setattr("atlas_do.goal_engine.GoalEngine._run_step", lambda self, s: None)

    engine = GoalEngine(state)
    gid = engine.start("Test goal")
    assert gid
    engine._thread.join(timeout=3.0)
    row = memory.goal_get(state.user_id, gid)
    assert row["status"] in ("done", "active", "killed")

