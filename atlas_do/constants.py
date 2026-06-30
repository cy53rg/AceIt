"""Task-loop and guide constants (extracted from atlas_core)."""
from __future__ import annotations

import os
import re

_TASK_MAX_STEPS_DEFAULT = 40
TASK_MAX_STEPS = max(1, int(os.environ.get("ATLAS_MAX_STEPS") or _TASK_MAX_STEPS_DEFAULT))
TASK_PHYSICAL_ACTIONS = frozenset({
    "click", "left_click", "tap",
    "double_click", "doubleclick", "double",
    "type", "type_text", "write", "input",
    "press", "key", "keypress",
    "hotkey", "combo", "shortcut",
    "scroll",
})
TASK_VERIFY_ACTIONS = frozenset({
    "click", "left_click", "tap",
    "double_click", "doubleclick", "double",
    "type", "type_text", "write", "input",
    "press", "key", "keypress",
    "hotkey", "combo", "shortcut",
    "scroll",
})
TASK_ACTION_MAX_RETRIES = 2

GUIDE_ADVANCE_RE = re.compile(
    r"^(?:"
    r"done|ok(?:ay)?|next|continue|finished|got it|did it|yep|yes|ready|k"
    r"|i(?:['']ve| am|'m)?\s+(?:done|finished|ready)"
    r"|move on|step (?:done|complete)|that(?:'s| is)? (?:done|it)"
    r")\s*[.!?]*$",
    re.IGNORECASE,
)

GUIDE_VERIFY_CONF_PASS = 0.65
GUIDE_VERIFY_CONF_LOW = 0.55

# Backward-compatible private aliases used by atlas_core and tests.
_TASK_MAX_STEPS = TASK_MAX_STEPS
_TASK_PHYSICAL_ACTIONS = TASK_PHYSICAL_ACTIONS
_TASK_VERIFY_ACTIONS = TASK_VERIFY_ACTIONS
_TASK_ACTION_MAX_RETRIES = TASK_ACTION_MAX_RETRIES
_GUIDE_ADVANCE_RE = GUIDE_ADVANCE_RE
_GUIDE_VERIFY_CONF_PASS = GUIDE_VERIFY_CONF_PASS
_GUIDE_VERIFY_CONF_LOW = GUIDE_VERIFY_CONF_LOW
