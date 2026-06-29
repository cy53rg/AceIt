"""
atlas_vision.py — Screen capture, spatial UI location, and passive screen watching.

Extracted from atlas_core.py (vision backend + ScreenWatcher).
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Callable, Optional

from groq import Groq

from atlas_logging import get_logger, log_outcome_json

log = get_logger("vision")

_default_vision = "qwen/qwen3.6-27b"
GROQ_VISION_MODEL = (
    os.environ.get("ATLAS_VISION_MODEL") or _default_vision
).strip() or _default_vision


def base64_encode(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("utf-8")


def base64_decode(text: str) -> bytes:
    import base64

    return base64.b64decode(text)


# Legacy module-level capture metadata — updated by capture_screen_b64(); prefer
# ScreenCapture.scale / .screen_size on the returned object instead.
_LAST_CAPTURE_SCALE: float = 1.0
_LAST_CAPTURE_SIZE: tuple[int, int] = (0, 0)   # (w, h) of image sent to the vision model
_LAST_SCREEN_SIZE: tuple[int, int] = (0, 0)    # native desktop pixels for the grab
_LAST_CAPTURE_MONO: float = 0.0
_LEGACY_CAPTURE_STALE_S = 2.0


@dataclass
class ScreenCapture:
    """One screen grab with scale metadata bound to that specific frame."""

    b64: str
    scale: float
    capture_size: tuple[int, int]
    screen_size: tuple[int, int]


def _publish_legacy_capture_metadata(cap: ScreenCapture) -> None:
    global _LAST_CAPTURE_SCALE, _LAST_CAPTURE_SIZE, _LAST_SCREEN_SIZE, _LAST_CAPTURE_MONO
    _LAST_CAPTURE_SCALE = cap.scale
    _LAST_CAPTURE_SIZE = cap.capture_size
    _LAST_SCREEN_SIZE = cap.screen_size
    _LAST_CAPTURE_MONO = time.monotonic()


def _warn_if_stale_legacy_read(caller: str) -> None:
    if _LAST_CAPTURE_MONO <= 0.0:
        log.warning(
            "%s called before any capture_screen_b64() — returning defaults; "
            "pass ScreenCapture through call chains instead.",
            caller,
        )
        return
    if time.monotonic() - _LAST_CAPTURE_MONO > _LEGACY_CAPTURE_STALE_S:
        warnings.warn(
            f"{caller}() read module-level capture metadata without a recent "
            "capture_screen_b64() call — migrate to ScreenCapture.scale",
            DeprecationWarning,
            stacklevel=3,
        )


def desktop_geometry() -> tuple[int, int, int, int]:
    """
    Virtual desktop bounds as (left, top, width, height).

    Matches ``mss.monitors[0]`` — the same coordinate space used by screen
    capture and Point-and-Talk mapping.  Falls back to the Qt primary screen.
    """
    try:
        import mss  # type: ignore

        with mss.mss() as sct:
            m = sct.monitors[0]
            return int(m["left"]), int(m["top"]), int(m["width"]), int(m["height"])
    except Exception:
        pass
    try:
        from PySide6.QtWidgets import QApplication

        screen = QApplication.primaryScreen()
        if screen:
            g = screen.geometry()
            return g.x(), g.y(), g.width(), g.height()
    except Exception:
        pass
    return 0, 0, 1920, 1080


def last_capture_scale() -> float:
    """Return the pixel scale for the last downscaled screen capture (legacy)."""
    _warn_if_stale_legacy_read("last_capture_scale")
    return _LAST_CAPTURE_SCALE


def last_screen_size() -> tuple[int, int]:
    """Native desktop width/height of the last screen capture (legacy)."""
    _warn_if_stale_legacy_read("last_screen_size")
    return _LAST_SCREEN_SIZE


# Trailing coordinate tag emitted by the vision LLM (Point-and-Talk / HeyClicky style).
TARGET_COORDINATE_RE = re.compile(
    r"\[TARGET_COORDINATE:\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]\s*",
    re.IGNORECASE,
)

_ATLAS_POINT_AND_TALK_VISION = """\
POINT-AND-TALK — SCREEN-AWARE DESKTOP AGENT (screenshot attached):

You are a spatial, screen-aware desktop co-pilot. The attached image is the user's \
live desktop — treat it as ground truth.

STRUCTURAL ANALYSIS (do this silently before answering):
- Map the layout: windows, panes, toolbars, dialogs, menus, text fields, buttons, icons.
- Note what is foreground vs background and which controls look active or focused.
- Ground every claim in what is actually visible — never invent UI that is not on screen.

RESPONSE STYLE:
- Concise, clear, human dialogue. Lead with the answer.
- Name on-screen elements by their visible labels (e.g. "the blue Save button, top-right").
- Never say you cannot see the screen when an image is attached.

COORDINATE TAG — machine-only metadata (HeyClicky-style Point-and-Talk):
- When your answer refers to ONE specific control, field, button, icon, or region the \
user should notice or interact with, append EXACTLY ONE tag as the absolute final \
characters of your raw response (after all prose; nothing may follow it):
  [TARGET_COORDINATE: X, Y]
- X and Y use normalized screenshot space: 0 = left/top edge, 1000 = right/bottom edge \
of the attached image (not raw pixels).
- Put the point at the visual center of the element you mean.
- Omit the tag when no specific on-screen target is needed.
- Never mention, describe, or speak the coordinate tag — the user must not see or hear it.

Example ending: "...tap the gear icon in the toolbar. [TARGET_COORDINATE: 842, 127]"
"""


def extract_target_coordinate(text: str) -> tuple[str, dict | None]:
    """
    Strip a trailing ``[TARGET_COORDINATE: X, Y]`` tag from *text*.

    Returns (clean_text, coord_dict|None).  Coordinates are normalized 0–1000.
    """
    if not text:
        return text, None
    match = TARGET_COORDINATE_RE.search(text)
    if not match:
        return text, None
    clean = TARGET_COORDINATE_RE.sub("", text).rstrip()
    return clean, {
        "x": float(match.group(1)),
        "y": float(match.group(2)),
        "normalized": True,
    }


def normalized_coord_to_desktop(
    nx: float,
    ny: float,
    screen_size: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Map normalized 0–1000 vision coords to absolute desktop pixels."""
    sw, sh = screen_size or _LAST_SCREEN_SIZE
    if sw <= 0 or sh <= 0:
        sw, sh = 1920, 1080
    px = int(round(max(0.0, min(1000.0, nx)) / 1000.0 * sw))
    py = int(round(max(0.0, min(1000.0, ny)) / 1000.0 * sh))
    return px, py


def record_capture_from_b64(b64: str) -> None:
    """
    Update desktop/capture dimension metadata from a base64 PNG or JPEG frame.

    Called when the UI injects a user-attached screenshot so Point-and-Talk
    coordinate tags map correctly even if ``capture_screen_b64()`` was not used.
    """
    global _LAST_CAPTURE_SCALE, _LAST_CAPTURE_SIZE, _LAST_SCREEN_SIZE
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(base64_decode(b64))).convert("RGB")
        cap = ScreenCapture(
            b64=b64,
            scale=1.0,
            capture_size=(img.width, img.height),
            screen_size=(img.width, img.height),
        )
        _publish_legacy_capture_metadata(cap)
    except Exception as exc:
        log.debug("record_capture_from_b64 failed: %s", exc)


def capture_screen_b64(max_width: int = 1280) -> Optional[ScreenCapture]:
    """
    Grab the primary display silently and return capture metadata, or None.

    Prefers ``mss`` (fast, headless) and falls back to Pillow ImageGrab.  The
    frame is downscaled to ``max_width`` so vision calls stay quick.

    Vision models return coordinates in *image* pixel space; multiply by
    ``ScreenCapture.scale`` to map clicks and overlay markers to the desktop.
    """
    try:
        from PIL import Image
        try:
            import mss  # type: ignore

            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[0])
                img = Image.frombytes("RGB", shot.size, shot.rgb)
        except Exception:
            from PIL import ImageGrab

            img = ImageGrab.grab().convert("RGB")

        orig_w, orig_h = img.width, img.height
        scale = 1.0

        if img.width > max_width:
            ratio = max_width / float(orig_w)
            img = img.resize((max_width, int(img.height * ratio)))
            scale = orig_w / float(max_width)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        cap = ScreenCapture(
            b64=base64_encode(buf.getvalue()),
            scale=scale,
            capture_size=(img.width, img.height),
            screen_size=(orig_w, orig_h),
        )
        _publish_legacy_capture_metadata(cap)
        return cap
    except Exception as exc:  # pragma: no cover - capture is best-effort
        log.debug("capture_screen_b64 failed: %s", exc)
        return None


def capture_screen_b64_str(max_width: int = 1280) -> Optional[str]:
    """Thin wrapper when only the PNG base64 string is needed."""
    cap = capture_screen_b64(max_width=max_width)
    return cap.b64 if cap else None


_REGION_PAD = 48
_REGION_HASH_SIZE = 16
REGION_CHANGE_THRESHOLD = 14.0


def _desktop_to_capture_xy(cap: ScreenCapture, desktop_x: int, desktop_y: int) -> tuple[int, int]:
    scale = cap.scale if cap.scale > 0 else 1.0
    return int(desktop_x / scale), int(desktop_y / scale)


def region_fingerprint(
    cap: ScreenCapture,
    desktop_cx: int,
    desktop_cy: int,
    desktop_w: int = 0,
    desktop_h: int = 0,
    *,
    pad: int = _REGION_PAD,
) -> tuple[int, ...] | None:
    """16×16 grayscale fingerprint of the UI region around a desktop coordinate."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(base64_decode(cap.b64))).convert("RGB")
        ix, iy = _desktop_to_capture_xy(cap, desktop_cx, desktop_cy)
        half_desktop = max(pad, (desktop_w or 0) // 2, (desktop_h or 0) // 2, 24)
        half = max(8, int(half_desktop / cap.scale)) if cap.scale > 0 else half_desktop
        left = max(0, ix - half)
        top = max(0, iy - half)
        right = min(img.width, ix + half)
        bottom = min(img.height, iy + half)
        if right - left < 4 or bottom - top < 4:
            return None
        crop = img.crop((left, top, right, bottom))
        small = crop.resize((_REGION_HASH_SIZE, _REGION_HASH_SIZE)).convert("L")
        return tuple(small.getdata())
    except Exception as exc:
        log.debug("region_fingerprint failed: %s", exc)
        return None


def region_mean_distance(
    fp1: tuple[int, ...] | None,
    fp2: tuple[int, ...] | None,
) -> float:
    if not fp1 or not fp2 or len(fp1) != len(fp2):
        return 999.0
    diffs = [abs(int(a) - int(b)) for a, b in zip(fp1, fp2)]
    return sum(diffs) / max(len(diffs), 1)


def region_changed_since_capture(
    locate_cap: ScreenCapture,
    desktop_cx: int,
    desktop_cy: int,
    desktop_w: int = 0,
    desktop_h: int = 0,
    *,
    fresh_cap: ScreenCapture | None = None,
    threshold: float = REGION_CHANGE_THRESHOLD,
) -> tuple[bool, float]:
    """
    Compare the target region in *locate_cap* to a fresh screenshot.

    Returns ``(changed_significantly, mean_pixel_distance)``.
    """
    fp_locate = region_fingerprint(
        locate_cap, desktop_cx, desktop_cy, desktop_w, desktop_h,
    )
    fresh = fresh_cap or capture_screen_b64()
    if fresh is None:
        return False, 0.0
    fp_fresh = region_fingerprint(
        fresh, desktop_cx, desktop_cy, desktop_w, desktop_h,
    )
    dist = region_mean_distance(fp_locate, fp_fresh)
    return dist >= threshold, dist


class SpatialBrain:
    """
    Vision-based locator that resolves textual UI targets to absolute pixels.

    locate() returns:
        {"found": true|false, "x": int, "y": int, "w": int, "h": int}
    """

    _JSON_RE = re.compile(r"\{[\s\S]*\}")
    _FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

    @classmethod
    def _parse_coordinate_json(cls, raw: str) -> dict:
        """
        Parse locate() model output — handles bare JSON and ```json fences.
        """
        text = (raw or "").strip()
        if not text:
            return {"found": False, "x": 0, "y": 0, "w": 0, "h": 0}
        fence = cls._FENCE_RE.search(text)
        if fence:
            text = fence.group(1).strip()
        match = cls._JSON_RE.search(text)
        blob = match.group(0) if match else text
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            log.warning("SpatialBrain: could not parse locate JSON: %r", raw[:240])
            return {"found": False, "x": 0, "y": 0, "w": 0, "h": 0}
        if not isinstance(data, dict):
            return {"found": False, "x": 0, "y": 0, "w": 0, "h": 0, "confidence": 0.0}
        return {
            "found": bool(data.get("found", False)),
            "x": int(data.get("x", 0) or 0),
            "y": int(data.get("y", 0) or 0),
            "w": int(data.get("w", 0) or 0),
            "h": int(data.get("h", 0) or 0),
            "confidence": float(data.get("confidence", 0.0) or 0.0),
        }

    def __init__(self, client: Groq, model: str = GROQ_VISION_MODEL) -> None:
        self.client = client
        self.model = model

    def capture_screen_png_b64(self) -> str:
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise RuntimeError("Pillow ImageGrab is required for spatial location") from exc

        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64_encode(buf.getvalue())

    def locate(
        self,
        target: str,
        screen: ScreenCapture | None = None,
        screen_b64: Optional[str] = None,
        scale: Optional[float] = None,
        *,
        refine: bool = True,
    ) -> dict:
        clean_target = (target or "").strip()
        if not clean_target:
            return {"found": False, "x": 0, "y": 0, "w": 0, "h": 0, "confidence": 0.0}

        frame_b64, scale_val = self._resolve_frame_and_scale(
            screen, screen_b64, scale,
        )
        result = self._locate_once(clean_target, frame_b64, scale_val)
        if not refine or not result.get("found"):
            self._log_locate_outcome(clean_target, result)
            return result

        conf = float(result.get("confidence", 0.0) or 0.0)
        if conf >= 0.85 and not self._is_small_target(clean_target):
            return result

        refined = self._locate_crop_refine(
            clean_target, result, screen_b64=frame_b64, scale=scale_val,
        )
        final = refined if refined.get("found") else result
        self._log_locate_outcome(clean_target, final)
        return final

    @classmethod
    def _parse_verify_json(cls, raw: str) -> dict:
        text = (raw or "").strip()
        if not text:
            return {
                "completed": False,
                "confidence": 0.0,
                "observed": "",
                "discrepancy": "Empty verification response.",
            }
        fence = cls._FENCE_RE.search(text)
        if fence:
            text = fence.group(1).strip()
        match = cls._JSON_RE.search(text)
        blob = match.group(0) if match else text
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            log.warning("SpatialBrain: could not parse verify JSON: %r", raw[:240])
            return {
                "completed": False,
                "confidence": 0.0,
                "observed": "",
                "discrepancy": "Could not parse verification response.",
            }
        if not isinstance(data, dict):
            return {
                "completed": False,
                "confidence": 0.0,
                "observed": "",
                "discrepancy": "Invalid verification response.",
            }
        completed = data.get("completed", False)
        if isinstance(completed, str):
            completed = completed.strip().lower() in ("true", "yes", "1")
        disc = data.get("discrepancy")
        return {
            "completed": bool(completed),
            "confidence": float(data.get("confidence", 0.0) or 0.0),
            "observed": str(data.get("observed") or "").strip(),
            "discrepancy": None if disc in (None, "null", "") else str(disc).strip(),
        }

    def verify(
        self,
        expected_state: str,
        screen: ScreenCapture | None = None,
        screen_b64: Optional[str] = None,
    ) -> dict:
        """
        Vision check: does the screenshot match *expected_state*?

        Returns ``{"completed", "confidence", "observed", "discrepancy"}``.
        """
        expected = (expected_state or "").strip()
        if not expected:
            return {
                "completed": False,
                "confidence": 0.0,
                "observed": "",
                "discrepancy": "No expected state provided.",
            }
        frame_b64, _scale = self._resolve_frame_and_scale(screen, screen_b64, None)
        if not frame_b64:
            try:
                frame_b64 = self.capture_screen_png_b64()
            except Exception as exc:
                log.warning("SpatialBrain.verify capture failed: %s", exc)
                return {
                    "completed": False,
                    "confidence": 0.0,
                    "observed": "",
                    "discrepancy": "No screenshot available for verification.",
                }
        instruction = (
            "You verify whether a UI step succeeded. Compare the screenshot to the "
            "expected outcome. Return strict JSON only: "
            '{"completed": true|false, "confidence": 0.0-1.0, '
            '"observed": "<what is actually visible now, 1 sentence>", '
            '"discrepancy": "<null or what is wrong, 1 sentence>"}. '
            f"Expected outcome after the step: {expected}"
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{frame_b64}"},
                            },
                            {"type": "text", "text": instruction},
                        ],
                    }
                ],
                temperature=0,
                max_tokens=220,
            )
            raw = resp.choices[0].message.content or "{}"
            result = self._parse_verify_json(raw)
            try:
                log_outcome_json({
                    "verify_expected": expected[:120],
                    "completed": bool(result.get("completed")),
                    "confidence": float(result.get("confidence", 0.0) or 0.0),
                    "model": self.model,
                })
            except Exception:
                pass
            return result
        except Exception as exc:
            log.warning("SpatialBrain.verify failed: %s", exc)
            return {
                "completed": False,
                "confidence": 0.0,
                "observed": "",
                "discrepancy": "Verification check failed.",
            }

    @staticmethod
    def _resolve_frame_and_scale(
        screen: ScreenCapture | None,
        screen_b64: Optional[str],
        scale: Optional[float],
    ) -> tuple[Optional[str], float]:
        if screen is not None:
            return screen.b64, screen.scale
        if screen_b64 is not None:
            return screen_b64, float(scale if scale is not None else last_capture_scale())
        return None, 1.0

    def _log_locate_outcome(self, target: str, result: dict) -> None:
        try:
            log_outcome_json({
                "target": target,
                "found": bool(result.get("found", False)),
                "confidence": float(result.get("confidence", 0.0) or 0.0),
                "coords": [
                    int(result.get("x", 0) or 0),
                    int(result.get("y", 0) or 0),
                ],
                "model": self.model,
            })
        except Exception:
            pass

    _SMALL_TARGET_HINTS = (
        "icon", "button", "tab", "toolbar", "menu", "arrow", "glyph",
        "close", "minimize", "maximize", "×", "✕", "pin", "chip",
    )

    @classmethod
    def _is_small_target(cls, target: str) -> bool:
        t = (target or "").lower()
        if len(t.split()) <= 3:
            return True
        return any(h in t for h in cls._SMALL_TARGET_HINTS)

    def _locate_once(
        self,
        clean_target: str,
        screen_b64: Optional[str],
        scale: Optional[float],
    ) -> dict:
        if screen_b64 is None:
            frame = self.capture_screen_png_b64()
            scale = 1.0
        else:
            frame = screen_b64
            if scale is None:
                scale = last_capture_scale()
        instruction = (
            "You are a UI coordinate locator. Find the requested target in the screenshot. "
            "Return strict JSON only with pixel coordinates IN THIS IMAGE (top-left origin): "
            '{"found":true,"x":int,"y":int,"w":int,"h":int,"confidence":0.0-1.0}. '
            'If not visible, return {"found":false,"x":0,"y":0,"w":0,"h":0,"confidence":0}. '
            f"Target: {clean_target}"
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{frame}"},
                        },
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            temperature=0,
            max_tokens=180,
        )
        raw = resp.choices[0].message.content or "{}"
        data = self._parse_coordinate_json(raw)
        sf = float(scale or 1.0)
        return {
            "found": bool(data.get("found", False)),
            "x": int(round(int(data.get("x", 0) or 0) * sf)),
            "y": int(round(int(data.get("y", 0) or 0) * sf)),
            "w": int(round(int(data.get("w", 0) or 0) * sf)),
            "h": int(round(int(data.get("h", 0) or 0) * sf)),
            "confidence": float(data.get("confidence", 0.0) or 0.0),
        }

    def _locate_crop_refine(
        self,
        target: str,
        first: dict,
        *,
        screen_b64: Optional[str],
        scale: Optional[float],
        crop_size: int = 300,
    ) -> dict:
        """Second-pass locate on a native-resolution crop around the first hit."""
        try:
            from PIL import Image
        except ImportError:
            return first

        if screen_b64 is None:
            try:
                import mss
                with mss.mss() as sct:
                    shot = sct.grab(sct.monitors[0])
                    img = Image.frombytes("RGB", shot.size, shot.rgb)
                sf = 1.0
            except Exception:
                from PIL import ImageGrab
                img = ImageGrab.grab().convert("RGB")
                sf = 1.0
        else:
            img = Image.open(io.BytesIO(base64_decode(screen_b64))).convert("RGB")
            sf = float(scale or last_capture_scale())

        cx = int(first.get("x", 0) + first.get("w", 0) / 2)
        cy = int(first.get("y", 0) + first.get("h", 0) / 2)
        half = crop_size // 2
        left = max(0, cx - half)
        top = max(0, cy - half)
        right = min(img.width, left + crop_size)
        bottom = min(img.height, top + crop_size)
        crop = img.crop((left, top, right, bottom))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        crop_b64 = base64_encode(buf.getvalue())

        inner = self._locate_once(
            target, crop_b64, scale=1.0,
        )
        if not inner.get("found"):
            return first

        ox = int(round(left * sf))
        oy = int(round(top * sf))
        return {
            "found": True,
            "x": ox + int(inner.get("x", 0)),
            "y": oy + int(inner.get("y", 0)),
            "w": int(inner.get("w", 0)),
            "h": int(inner.get("h", 0)),
            "confidence": max(float(first.get("confidence", 0.0)), float(inner.get("confidence", 0.0))),
        }


# ── Screen capture dependencies ────────────────────────────────────────────────

try:
    from PIL import Image  # noqa: F401 — availability probe
    import mss as _mss_probe  # noqa: F401
    _HAS_SCREEN_DEPS = True
except Exception:
    _HAS_SCREEN_DEPS = False

try:
    import imagehash as _imagehash  # type: ignore
    _HAS_IMAGEHASH = True
except Exception:
    _imagehash = None  # type: ignore
    _HAS_IMAGEHASH = False

_SCREEN_POLL_INTERVAL = float(os.environ.get("ATLAS_SCREEN_POLL_INTERVAL") or 5)

_ERROR_DETECT_PROMPT = (
    "Look at this screenshot. Is there any visible error, crash dialog, warning "
    "popup, or broken state? Reply only with: "
    "{found: true/false, type: 'error_type', summary: 'one sentence'}."
)


class ScreenWatcher:
    """
    Passive screen polling with perceptual-hash change detection and proactive
    error observation.  On-demand ``take_and_analyze`` works even when polling
    is off.
    """

    _RING_MAX = 10
    _JSON_RE = re.compile(r"\{[\s\S]*\}")
    _SENS_THRESHOLDS = {
        "low":    {"phash": 14, "pixels": 12.0},
        "medium": {"phash": 10, "pixels": 6.0},
        "high":   {"phash": 6,  "pixels": 3.0},
    }

    def __init__(
        self,
        client: Groq,
        model: str = GROQ_VISION_MODEL,
        on_proactive: Optional[Callable[[dict], None]] = None,
        poll_interval: float = _SCREEN_POLL_INTERVAL,
    ) -> None:
        self._client = client
        self._model = model
        self._on_proactive = on_proactive or (lambda _p: None)
        self._interval = max(1.0, float(poll_interval))
        self._sensitivity = "medium"
        self._ring: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.available = _HAS_SCREEN_DEPS
        if not self.available:
            log.warning(
                "ScreenWatcher unavailable — install mss and Pillow for screen awareness."
            )

    @property
    def is_watching(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def set_sensitivity(self, level: str) -> None:
        lvl = (level or "medium").strip().lower()
        if lvl not in self._SENS_THRESHOLDS:
            lvl = "medium"
        self._sensitivity = lvl

    def start_watching(self) -> None:
        if not self.available:
            log.warning("ScreenWatcher.start_watching: capture deps missing — no-op.")
            return
        if self.is_watching:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="atlas-screen-watcher",
        )
        self._thread.start()

    def stop_watching(self) -> None:
        self._stop.set()

    def take_and_analyze(self, prompt: str) -> Optional[str]:
        if not self.available:
            log.warning("ScreenWatcher.take_and_analyze: unavailable — no-op.")
            return None
        b64 = self._capture_b64()
        if not b64:
            return None
        try:
            return self._vision_query(b64, prompt or "Describe what you see on this screen.")
        except Exception as exc:
            log.warning("ScreenWatcher.take_and_analyze failed: %s", exc)
            return None

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                log.warning("ScreenWatcher poll error: %s", exc)
            if self._stop.wait(self._interval):
                break

    def _poll_once(self) -> None:
        b64 = self._capture_b64()
        if not b64:
            return
        h = self._compute_hash(b64)
        if not self._hash_changed(h):
            return
        with self._lock:
            self._ring.append({"b64": b64, "hash": h})
            if len(self._ring) > self._RING_MAX:
                self._ring.pop(0)
        try:
            raw = self._vision_query(b64, _ERROR_DETECT_PROMPT)
            parsed = self._parse_error_json(raw)
            if parsed.get("found"):
                summary = str(parsed.get("summary") or "something on your screen").strip()
                err_type = str(parsed.get("type") or "error").strip()
                self._on_proactive({
                    "found": True,
                    "type": err_type,
                    "summary": summary,
                    "message": (
                        f"I noticed something — {summary}. "
                        "Want me to walk you through fixing it?"
                    ),
                })
        except Exception as exc:
            log.warning("ScreenWatcher error detection failed: %s", exc)

    def _capture_b64(self) -> Optional[str]:
        return capture_screen_b64_str()

    def _compute_hash(self, b64: str) -> tuple:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(base64_decode(b64))).convert("RGB")
            if _HAS_IMAGEHASH and _imagehash is not None:
                return ("phash", str(_imagehash.phash(img)))
            small = img.resize((16, 16)).convert("L")
            return ("pixels", tuple(small.getdata()))
        except Exception as exc:
            log.debug("ScreenWatcher hash failed: %s", exc)
            return ("pixels", ())

    def _hash_changed(self, new_hash: tuple) -> bool:
        with self._lock:
            if not self._ring:
                return True
            prev = self._ring[-1]["hash"]
        dist = self._hash_distance(prev, new_hash)
        thresh = self._SENS_THRESHOLDS.get(self._sensitivity, self._SENS_THRESHOLDS["medium"])
        if new_hash[0] == "phash":
            return dist >= thresh["phash"]
        return dist >= thresh["pixels"]

    @staticmethod
    def _hash_distance(h1: tuple, h2: tuple) -> float:
        if h1[0] == "phash" and h2[0] == "phash" and _HAS_IMAGEHASH and _imagehash is not None:
            try:
                return float(_imagehash.hex_to_hash(h1[1]) - _imagehash.hex_to_hash(h2[1]))
            except Exception:
                return 999.0
        if h1[0] == "pixels" and h2[0] == "pixels" and h1[1] and h2[1]:
            diffs = [abs(int(a) - int(b)) for a, b in zip(h1[1], h2[1])]
            return sum(diffs) / max(len(diffs), 1)
        return 999.0

    def _vision_query(self, b64: str, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
            temperature=0,
            max_tokens=256,
        )
        if not resp.choices:
            return ""
        return (resp.choices[0].message.content or "").strip()

    def _parse_error_json(self, raw: str) -> dict:
        text = (raw or "").strip()
        match = self._JSON_RE.search(text)
        blob = match.group(0) if match else text
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            low = text.lower()
            if "found" in low and "true" in low:
                return {"found": True, "type": "unknown", "summary": text[:200]}
            return {"found": False, "type": "", "summary": ""}
        if not isinstance(data, dict):
            return {"found": False, "type": "", "summary": ""}
        return {
            "found": bool(data.get("found", False)),
            "type": str(data.get("type", "") or ""),
            "summary": str(data.get("summary", "") or ""),
        }


__all__ = [
    "GROQ_VISION_MODEL",
    "ScreenCapture",
    "SpatialBrain",
    "ScreenWatcher",
    "_ERROR_DETECT_PROMPT",
    "_HAS_SCREEN_DEPS",
    "_SCREEN_POLL_INTERVAL",
    "base64_decode",
    "base64_encode",
    "capture_screen_b64",
    "capture_screen_b64_str",
    "region_changed_since_capture",
    "region_fingerprint",
    "REGION_CHANGE_THRESHOLD",
    "last_capture_scale",
    "last_screen_size",
]
