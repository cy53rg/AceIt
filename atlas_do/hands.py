"""Permission-gated desktop automation (pyautogui)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from atlas_fs_v2 import AtlasFileSystemV2, FSPermission
from atlas_logging import get_logger

log = get_logger("do.hands")

import pyautogui as _pyautogui_mod

_pyautogui_mod.FAILSAFE = True


class AtlasHands:
    """Permission-gated physical automation wrapper around pyautogui."""

    def __init__(self, fs: AtlasFileSystemV2) -> None:
        self._fs = fs
        self.auto_approve = False

    def _request_action(self, label: str, proceed: Callable[[], None]) -> None:
        if self.auto_approve:
            try:
                proceed()
            except Exception as exc:
                log.error("AtlasHands auto-approved %s failed: %s", label, exc)
            return
        action_path = Path(f"atlas-hands://{label}")
        self._fs._request_permission(FSPermission.EXECUTE, action_path, proceed)

    def click(self, x: int, y: int) -> None:
        def _do_click() -> None:
            import pyautogui

            pyautogui.click(int(x), int(y))
            log.info("AtlasHands: clicked %s,%s", x, y)

        self._request_action(f"click/{int(x)}/{int(y)}", _do_click)

    def type_text(self, text: str, interval: float = 0.01) -> None:
        clean = str(text)

        def _do_type() -> None:
            import pyautogui

            pyautogui.write(clean, interval=max(0.0, float(interval)))
            log.info("AtlasHands: typed %d chars", len(clean))

        self._request_action("type_text", _do_type)

    def press_key(self, key: str) -> None:
        clean_key = str(key).strip()
        if not clean_key:
            return

        def _do_press() -> None:
            import pyautogui

            pyautogui.press(clean_key)
            log.info("AtlasHands: pressed %s", clean_key)

        self._request_action(f"press_key/{clean_key}", _do_press)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        def _do_drag() -> None:
            import pyautogui

            pyautogui.moveTo(int(x1), int(y1))
            pyautogui.dragTo(int(x2), int(y2), duration=max(0.0, float(duration)), button="left")
            log.info("AtlasHands: drag %s,%s -> %s,%s", x1, y1, x2, y2)

        self._request_action(f"drag/{int(x1)}/{int(y1)}", _do_drag)

    def scroll(self, x: int, y: int, clicks: int) -> None:
        def _do_scroll() -> None:
            import pyautogui

            pyautogui.scroll(int(clicks), x=int(x), y=int(y))
            log.info("AtlasHands: scroll %s at %s,%s", clicks, x, y)

        self._request_action(f"scroll/{int(x)}/{int(y)}", _do_scroll)

    def hotkey(self, *keys: str) -> None:
        clean = [str(k).strip() for k in keys if str(k).strip()]
        if not clean:
            return

        def _do_hotkey() -> None:
            import pyautogui

            pyautogui.hotkey(*clean)
            log.info("AtlasHands: hotkey %s", "+".join(clean))

        self._request_action("hotkey/" + "+".join(clean), _do_hotkey)
