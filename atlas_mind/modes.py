"""Atlas runtime modes and system-prompt mapping."""
from __future__ import annotations

from enum import Enum, auto

from atlas_mind.prompts import (
    _ATLAS_ACTIVE,
    _ATLAS_AMBIENT,
    _ATLAS_GUIDED,
    _ATLAS_INTERVIEW,
)


class ModeState(Enum):
    ACTIVE    = auto()
    AMBIENT   = auto()
    GUIDED    = auto()
    INTERVIEW = auto()


MODE_SYSTEMS = {
    ModeState.ACTIVE:    _ATLAS_ACTIVE,
    ModeState.AMBIENT:   _ATLAS_AMBIENT,
    ModeState.GUIDED:    _ATLAS_GUIDED,
    ModeState.INTERVIEW: _ATLAS_INTERVIEW,
}
