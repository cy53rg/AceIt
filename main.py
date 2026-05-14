"""AceIt — capture center screen region, OCR (Tesseract), Gemini 3 Flash via google-genai (Windows)."""

from __future__ import annotations

import os
import shutil
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
import keyboard
import pyautogui
import pytesseract
from PIL import Image

# Avoid aborting when the cursor hits a screen corner during normal use.
pyautogui.FAILSAFE = False

ROOT = Path(__file__).resolve().parent
ASSETS_DIR = ROOT / "assets"
OVERLAY_BANNER = (
    (ROOT / "image_a04a23.png")
    if (ROOT / "image_a04a23.png").is_file()
    else (ASSETS_DIR / "image_a04a23.png")
)
RECORDINGS_DIR = ROOT / "recordings"

# Window: compact always-on-top overlay with room for output.
WINDOW_W, WINDOW_H = 380, 520

# Center capture: size as fraction of primary monitor, then centered.
CAPTURE_WIDTH_FRAC = 0.65
CAPTURE_HEIGHT_FRAC = 0.38

GEMINI_MODEL = "gemini-3-flash"
GEMINI_ENV_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

COACH_PROMPT = (
    "You are a professional interview coach. Provide a concise, 3-bullet point response "
    "to this interview prompt."
)

_TESSERACT_EXE_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)
_tesseract_ready = False


def _load_env() -> None:
    """Load GEMINI_API_KEY (etc.) from project .env without overriding existing env vars."""
    load_dotenv(ROOT / ".env", override=False)


def _configure_tesseract() -> None:
    """Point pytesseract at tesseract.exe (PATH, TESSERACT_CMD, or common install dirs)."""
    global _tesseract_ready
    if _tesseract_ready:
        return
    override = os.environ.get("TESSERACT_CMD", "").strip()
    if override:
        pytesseract.pytesseract.tesseract_cmd = override
        _tesseract_ready = True
        return
    found = shutil.which("tesseract")
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
        _tesseract_ready = True
        return
    for path in _TESSERACT_EXE_CANDIDATES:
        exe = Path(path)
        if exe.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(exe)
            _tesseract_ready = True
            return
    raise RuntimeError(
        "Tesseract OCR not found. Install from "
        "https://github.com/UB-Mannheim/tesseract/wiki "
        "or set TESSERACT_CMD to the full path of tesseract.exe. "
        "See INSTALL_WINDOWS.txt."
    )


def _ocr_image(image_path: Path) -> str:
    _configure_tesseract()
    raw = pytesseract.image_to_string(Image.open(image_path), lang="eng")
    return "\n".join(line.strip() for line in raw.splitlines() if line.strip())


def _capture_center_region() -> Image.Image:
    sw, sh = pyautogui.size()
    rw = max(120, int(sw * CAPTURE_WIDTH_FRAC))
    rh = max(80, int(sh * CAPTURE_HEIGHT_FRAC))
    left = max(0, (sw - rw) // 2)
    top = max(0, (sh - rh) // 2)
    return pyautogui.screenshot(region=(left, top, rw, rh))


def _gemini_api_key() -> Optional[str]:
    for key in GEMINI_ENV_KEYS:
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None


def _gemini_answer(ocr_text: str) -> str:
    api_key = _gemini_api_key()
    if not api_key:
        return (
            "[AI skipped] Add GEMINI_API_KEY to your .env file (see project root) or set it "
            "in the environment."
        )
    client = genai.Client(api_key=api_key)
    contents = (
        f"{COACH_PROMPT}\n\n"
        f"Interview prompt (from screen OCR):\n{ocr_text or '(no text captured)'}"
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
    )
    try:
        text = (response.text or "").strip()
    except ValueError:
        return "(Gemini blocked or empty response — no text returned.)"
    if not text:
        return (
            "(Gemini returned no text — check API key, model id, or safety filters.)"
        )
    return text


def _append_output(text_widget: tk.Text, message: str) -> None:
    text_widget.configure(state=tk.NORMAL)
    text_widget.insert(tk.END, message + "\n\n")
    text_widget.see(tk.END)
    text_widget.configure(state=tk.DISABLED)


class AceItApp:
    def __init__(self) -> None:
        self._busy = False
        self._overlay_photo: Optional[tk.PhotoImage] = None

        self.root = tk.Tk()
        self.root.title("AceIt")
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.root.minsize(WINDOW_W, WINDOW_H)
        self.root.resizable(True, True)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        frame = tk.Frame(self.root, bg="#1e1e1e")
        frame.pack(fill=tk.BOTH, expand=True)

        self.btn = tk.Button(
            frame,
            text="Capture Question",
            command=self._schedule_capture,
            bg="#3c3c3c",
            fg="#f0f0f0",
            activebackground="#505050",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            font=("Segoe UI", 10, "bold"),
        )
        self.btn.pack(fill=tk.X, padx=8, pady=(8, 4))

        hint = tk.Label(
            frame,
            text="Ctrl+Shift+S capture · Esc quit",
            bg="#1e1e1e",
            fg="#888888",
            font=("Segoe UI", 8),
        )
        hint.pack()

        # Banner overlay (image_a04a23.png): shows "Thinking..." then the coach reply.
        self.overlay = tk.Label(
            frame,
            text="",
            bg="#2a2a30",
            fg="#f5f5f5",
            font=("Segoe UI", 10),
            justify=tk.CENTER,
            wraplength=350,
            padx=10,
            pady=14,
        )
        if OVERLAY_BANNER.is_file():
            try:
                self._overlay_photo = tk.PhotoImage(file=str(OVERLAY_BANNER))
                self.overlay.configure(
                    image=self._overlay_photo,
                    compound=tk.CENTER,
                    bg="#1e1e1e",
                )
            except tk.TclError:
                self._overlay_photo = None
        self.overlay.pack(fill=tk.X, padx=8, pady=(0, 6))

        self.output = tk.Text(
            frame,
            height=14,
            wrap=tk.WORD,
            bg="#252526",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
            font=("Consolas", 9),
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.output.configure(state=tk.DISABLED)

        self.root.bind("<Escape>", lambda _e: self._quit())

        self._register_hotkey()

    def _set_overlay_message(self, message: str) -> None:
        self.overlay.configure(text=message)
        self.root.update_idletasks()

    def _register_hotkey(self) -> None:
        def on_hotkey() -> None:
            self.root.after(0, self._schedule_capture)

        try:
            keyboard.add_hotkey("ctrl+shift+s", on_hotkey)
            print("Global hotkey: Ctrl+Shift+S", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"Hotkey not registered ({exc}). Try running as Administrator.", flush=True)

    def _quit(self) -> None:
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self.root.destroy()

    def _schedule_capture(self) -> None:
        if self._busy:
            print("(capture already running — skipped)", flush=True)
            return
        self._busy = True
        self._set_overlay_message("")
        self.btn.configure(state=tk.DISABLED)
        threading.Thread(target=self._capture_pipeline, daemon=True).start()

    def _capture_pipeline(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"========== {stamp} =========="
        lines: list[str] = [header]
        ai_text = ""

        try:
            RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
            image = _capture_center_region()
            file_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = RECORDINGS_DIR / f"question_{file_stamp}.png"
            image.save(out_path)
            msg = f"Saved: {out_path}"
            print(msg, flush=True)
            lines.append(msg)

            try:
                ocr_text = _ocr_image(out_path)
            except Exception as exc:  # noqa: BLE001
                ocr_text = ""
                err = f"OCR failed: {exc}"
                print(err, flush=True)
                lines.append(err)

            print("--- OCR text ---", flush=True)
            print(ocr_text if ocr_text else "(no text detected)", flush=True)
            print("--- end OCR ---", flush=True)
            lines.append("--- OCR ---")
            lines.append(ocr_text if ocr_text else "(no text detected)")

            self.root.after(0, lambda: self._set_overlay_message("Thinking..."))
            try:
                ai_text = _gemini_answer(ocr_text)
            except Exception as exc:  # noqa: BLE001
                ai_text = f"[Gemini error] {exc}"

            self.root.after(0, lambda t=ai_text: self._set_overlay_message(t))

            print("--- Gemini ---", flush=True)
            print(ai_text, flush=True)
            print("--- end Gemini ---", flush=True)
            lines.append("--- Gemini ---")
            lines.append(ai_text)

        except Exception as exc:  # noqa: BLE001
            err = f"Capture failed: {exc}"
            print(err, flush=True)
            lines.append(err)
            self.root.after(0, lambda m=str(exc): self._set_overlay_message(f"Error: {m}"))

        block = "\n".join(lines)
        self.root.after(0, lambda b=block: self._finish_capture(b))

    def _finish_capture(self, block: str) -> None:
        _append_output(self.output, block)
        self.btn.configure(state=tk.NORMAL)
        self._busy = False

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    _load_env()
    AceItApp().run()


if __name__ == "__main__":
    main()
