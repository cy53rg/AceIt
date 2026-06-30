"""
atlas_skills.py — Skill engine: load and run user skills from ~/.atlas/skills/.
"""
from __future__ import annotations

import importlib.util
import logging
import re
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("atlas_skills")

SKILL_DIR = Path.home() / ".atlas" / "skills"
EXECUTE_TIMEOUT_S = 30

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_skill_md_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, (text or "").strip()
    raw_fm = match.group(1)
    body = text[match.end():].strip()
    manifest: dict[str, Any] = {}
    current_key = ""
    for line in raw_fm.splitlines():
        if not line.strip():
            continue
        key_m = re.match(r"^([\w-]+):\s*(.*)$", line)
        if key_m:
            current_key = key_m.group(1).strip().lower()
            val = key_m.group(2).strip()
            if val:
                manifest[current_key] = val.strip('"').strip("'")
            else:
                manifest[current_key] = []
            continue
        list_m = re.match(r"^\s*-\s+(.+)$", line)
        if list_m and current_key:
            item = list_m.group(1).strip().strip('"').strip("'")
            cur = manifest.get(current_key)
            if not isinstance(cur, list):
                cur = []
            cur.append(item)
            manifest[current_key] = cur
    if "triggers" in manifest and isinstance(manifest["triggers"], str):
        manifest["triggers"] = [manifest["triggers"]]
    return manifest, body


class SkillRegistry:
    """Discover, install, and execute Python skill modules."""

    def __init__(self) -> None:
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, dict[str, Any]] = {}
        self.scan()

    def scan(self) -> None:
        """Walk SKILL_DIR for *.py and SKILL.md, register manifests."""
        self._skills.clear()
        for path in sorted(SKILL_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"atlas_skill_{path.stem}", path)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                manifest = getattr(mod, "SKILL_MANIFEST", None)
                run_fn = getattr(mod, "run", None)
                if not isinstance(manifest, dict) or not callable(run_fn):
                    log.warning("Skill %s missing SKILL_MANIFEST or run()", path.name)
                    continue
                name = str(manifest.get("name", path.stem)).strip()
                if not name:
                    continue
                self._skills[name] = {
                    "manifest": manifest,
                    "run": run_fn,
                    "path": path,
                    "kind": "python",
                }
            except Exception as exc:
                log.warning("Failed to load skill %s: %s", path.name, exc)
        for path in sorted(SKILL_DIR.rglob("SKILL.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                manifest, body = _parse_skill_md_frontmatter(text)
                name = str(manifest.get("name") or path.parent.name).strip()
                if not name:
                    continue
                manifest.setdefault("name", name)
                manifest.setdefault("display", name)
                manifest.setdefault("version", "1.0.0")
                manifest.setdefault("triggers", [])
                self._skills[name] = {
                    "manifest": manifest,
                    "path": path,
                    "kind": "markdown",
                    "body": body,
                }
            except Exception as exc:
                log.warning("Failed to load SKILL.md %s: %s", path, exc)

    def get(self, name: str) -> dict[str, Any] | None:
        return self._skills.get(name)

    def list_skills(self) -> list[dict[str, Any]]:
        return [entry["manifest"] for entry in self._skills.values()]

    def match_triggers(self, text: str) -> str | None:
        """Return first skill name whose trigger appears in lowercase text."""
        lower = (text or "").lower()
        for name, entry in self._skills.items():
            triggers = entry["manifest"].get("triggers", [])
            if not isinstance(triggers, list):
                continue
            for trig in triggers:
                t = str(trig).lower().strip()
                if t and t in lower:
                    return name
        return None

    def install_from_file(self, path: str | Path) -> tuple[bool, str]:
        """Validate skill file, copy to SKILL_DIR, re-scan."""
        src = Path(path)
        if not src.is_file():
            return False, "File not found"
        try:
            spec = importlib.util.spec_from_file_location("atlas_skill_validate", src)
            if spec is None or spec.loader is None:
                return False, "Cannot load module"
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            manifest = getattr(mod, "SKILL_MANIFEST", None)
            run_fn = getattr(mod, "run", None)
            if not isinstance(manifest, dict) or not callable(run_fn):
                return False, "Missing SKILL_MANIFEST or run()"
            name = str(manifest.get("name", src.stem))
            version = str(manifest.get("version", "1.0.0"))
            dest = SKILL_DIR / f"{name}.py"
            shutil.copy2(src, dest)
            self.scan()
            display = str(manifest.get("display", name))
            return True, f"Installed {display} v{version}"
        except Exception as exc:
            return False, str(exc)

    def uninstall(self, name: str) -> None:
        entry = self._skills.get(name)
        if entry:
            try:
                Path(entry["path"]).unlink(missing_ok=True)
            except Exception as exc:
                log.warning("Uninstall %s failed: %s", name, exc)
        self.scan()

    def execute(
        self,
        name: str,
        user_text: str,
        history: list[dict[str, Any]],
        memory: Any,
        atlas_fs: Any,
        atlas_hands: Any,
        groq_client: Any,
    ) -> dict[str, Any]:
        """Run skill with 30s timeout; never crash caller."""
        entry = self.get(name)
        if not entry:
            return {"response": "Skill not found.", "facts": [], "success": False}

        if entry.get("kind") == "markdown":
            body = str(entry.get("body") or "").strip()
            manifest = entry.get("manifest") or {}
            display = str(manifest.get("display") or name)
            inject = f"<Skill name=\"{display}\">\n{body}\n</Skill>" if body else ""
            return {
                "response": "",
                "facts": [],
                "success": True,
                "inject_context": inject,
            }

        context = {
            "user_text": user_text,
            "history": history,
            "memory": memory,
            "atlas_fs": atlas_fs,
            "atlas_hands": atlas_hands,
            "groq_client": groq_client,
        }
        result_box: dict[str, Any] = {}
        error_box: list[Exception] = []

        def _target() -> None:
            try:
                result_box["value"] = entry["run"](context)
            except Exception as exc:
                error_box.append(exc)

        thread = threading.Thread(target=_target, daemon=True, name=f"skill-{name}")
        thread.start()
        thread.join(timeout=EXECUTE_TIMEOUT_S)
        if thread.is_alive():
            return {"response": "Skill timed out.", "facts": [], "success": False}
        if error_box:
            return {"response": f"Skill failed: {error_box[0]}", "facts": [], "success": False}
        raw = result_box.get("value", {})
        if not isinstance(raw, dict):
            return {"response": "Skill returned invalid result.", "facts": [], "success": False}
        return {
            "response": str(raw.get("response", "")),
            "facts": raw.get("facts", []) if isinstance(raw.get("facts"), list) else [],
            "success": bool(raw.get("success", True)),
            "inject_context": str(raw.get("inject_context", "") or ""),
        }
