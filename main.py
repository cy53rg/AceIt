"""
AceIt Co-Pilot — Enhanced main.py
Improvements:
  1. Fixed Highlight Mode: now intercepts selected text on the screen properly
  2. Fixed Capture Mode: captures the full visible screen, sends ALL content to AI
  3. Hot Reload: press the Refresh button (or Ctrl+R) to reload main.py without restarting
  4. Floating Icon Mode: minimize to a small draggable floating button
  5. Opacity Control: slider to adjust transparency of the entire window
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
import sys
import os
import importlib
import subprocess
import pyperclip
import keyboard
import pyautogui
import pytesseract
from PIL import ImageGrab, Image, ImageTk
import io
import base64
import textwrap

# ── API / Model ────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Tesseract path from env (Windows)
_tess_cmd = os.getenv("TESSERACT_CMD", "")
if _tess_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tess_cmd

# Groq is PRIMARY — used whenever a key is present
try:
    from groq import Groq
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except ImportError:
    groq_client = None

# Gemini is FALLBACK only (used for vision when Groq not available)
try:
    import google.generativeai as genai
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
    USE_GEMINI = bool(GEMINI_API_KEY) and groq_client is None
except ImportError:
    USE_GEMINI = False

DEFAULT_MODEL = os.getenv("ACEIT_MODEL", "llama-3.3-70b-versatile")  # groq default

# ── Colours ────────────────────────────────────────────────────────────────────
BG        = "#1a1a2e"
PANEL     = "#16213e"
ACCENT    = "#0f3460"
HIGHLIGHT = "#e94560"
TEXT_CLR  = "#eaeaea"
MUTED     = "#888"

FONT_MONO = ("Consolas", 10)
FONT_UI   = ("Segoe UI", 10)


# ══════════════════════════════════════════════════════════════════════════════
class AceItApp:
    # ── Init ──────────────────────────────────────────────────────────────────
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AceIt  Co-Pilot")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)

        # State
        self.highlight_mode  = tk.BooleanVar(value=False)
        self.highlight_thread = None
        self.last_clipboard   = ""
        self._floating        = False
        self._float_win       = None
        self._drag_x = self._drag_y = 0

        # Geometry
        self.root.geometry("420x680+1400+100")
        self.root.minsize(300, 400)

        self._build_ui()
        self._bind_hotkeys()

        # Opacity (must be set after window exists)
        self.opacity_var.set(95)
        self._apply_opacity(95)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg=ACCENT, pady=6)
        header.pack(fill="x")

        tk.Label(header, text="⚡ AceIt  Co-Pilot",
                 bg=ACCENT, fg=TEXT_CLR,
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=12)

        # Model label (right side)
        self._model_label = tk.Label(header, text=DEFAULT_MODEL,
                                     bg=ACCENT, fg=MUTED, font=FONT_UI)
        self._model_label.pack(side="right", padx=12)

        # ── Controls row 1 ────────────────────────────────────────────────────
        row1 = tk.Frame(self.root, bg=BG, pady=4)
        row1.pack(fill="x", padx=8)

        self.btn_capture = self._btn(row1, "📷  Capture Screen",
                                     self._do_capture, ACCENT)
        self.btn_capture.pack(side="left", padx=4)

        self.btn_highlight = self._toggle_btn(row1, "🔍  Highlight",
                                              self._toggle_highlight)
        self.btn_highlight.pack(side="left", padx=4)

        # ── Controls row 2 ────────────────────────────────────────────────────
        row2 = tk.Frame(self.root, bg=BG, pady=2)
        row2.pack(fill="x", padx=8)

        self._btn(row2, "🔄  Refresh",  self._hot_reload,   "#2d6a4f").pack(side="left", padx=4)
        self._btn(row2, "🗗  Float",    self._enter_float,  "#4a4e69").pack(side="left", padx=4)
        self._btn(row2, "🗑  Clear",    self._clear,        "#555").pack(side="left",  padx=4)

        # ── Opacity slider ─────────────────────────────────────────────────────
        opacity_row = tk.Frame(self.root, bg=BG, pady=2)
        opacity_row.pack(fill="x", padx=12)
        tk.Label(opacity_row, text="Opacity:", bg=BG, fg=MUTED,
                 font=FONT_UI).pack(side="left")
        self.opacity_var = tk.IntVar(value=95)
        self.opacity_slider = ttk.Scale(
            opacity_row, from_=20, to=100,
            orient="horizontal", variable=self.opacity_var,
            command=lambda v: self._apply_opacity(int(float(v))))
        self.opacity_slider.pack(side="left", fill="x", expand=True, padx=6)
        self._opacity_lbl = tk.Label(opacity_row, text="95%", bg=BG, fg=MUTED,
                                     font=FONT_UI, width=4)
        self._opacity_lbl.pack(side="left")

        # ── Source label ───────────────────────────────────────────────────────
        self._source_lbl = tk.Label(self.root, text="",
                                    bg=BG, fg=MUTED, font=FONT_UI, anchor="w")
        self._source_lbl.pack(fill="x", padx=12, pady=(4, 0))

        # ── Ask box ────────────────────────────────────────────────────────────
        ask_frame = tk.Frame(self.root, bg=PANEL, bd=1, relief="flat")
        ask_frame.pack(fill="x", padx=8, pady=4)

        self.ask_entry = tk.Entry(ask_frame, bg=PANEL, fg=TEXT_CLR,
                                  font=FONT_UI, insertbackground=TEXT_CLR,
                                  relief="flat", bd=4)
        self.ask_entry.pack(side="left", fill="both", expand=True)
        self.ask_entry.insert(0, "Ask anything…")
        self.ask_entry.bind("<FocusIn>",  self._clear_placeholder)
        self.ask_entry.bind("<FocusOut>", self._restore_placeholder)
        self.ask_entry.bind("<Return>",   lambda e: self._ask_free())

        self._btn(ask_frame, "🎤", self._voice_stub, "#333").pack(side="right", padx=2)

        # ── Response area ──────────────────────────────────────────────────────
        resp_frame = tk.Frame(self.root, bg=PANEL, bd=0)
        resp_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        tk.Label(resp_frame, text="AI RESPONSE", bg=PANEL, fg=HIGHLIGHT,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=6, pady=(6, 2))

        self.response_box = scrolledtext.ScrolledText(
            resp_frame, bg=PANEL, fg=TEXT_CLR,
            font=FONT_MONO, relief="flat", wrap="word",
            padx=8, pady=8, state="disabled")
        self.response_box.pack(fill="both", expand=True)

        # Status bar
        self._status = tk.Label(self.root, text="Ready", bg=ACCENT,
                                fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self._status.pack(fill="x", padx=0, pady=0)

    # ── Helper: create styled button ──────────────────────────────────────────
    def _btn(self, parent, text, cmd, bg=ACCENT):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=bg, fg=TEXT_CLR, activebackground=HIGHLIGHT,
                      activeforeground="white", relief="flat",
                      font=FONT_UI, padx=8, pady=4, cursor="hand2")
        return b

    def _toggle_btn(self, parent, text, cmd):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=ACCENT, fg=TEXT_CLR, activebackground=HIGHLIGHT,
                      relief="flat", font=FONT_UI, padx=8, pady=4, cursor="hand2")
        return b

    # ── Hotkeys ───────────────────────────────────────────────────────────────
    def _bind_hotkeys(self):
        # Ctrl+Shift+S → capture
        try:
            keyboard.add_hotkey("ctrl+shift+s", self._do_capture, suppress=False)
        except Exception:
            pass
        # Ctrl+Shift+H → toggle highlight
        try:
            keyboard.add_hotkey("ctrl+shift+h", self._toggle_highlight, suppress=False)
        except Exception:
            pass
        # Ctrl+R inside window → reload
        self.root.bind("<Control-r>", lambda e: self._hot_reload())

    # ══════════════════════════════════════════════════════════════════════════
    # CAPTURE MODE (FIXED)
    # Captures FULL screen (minus this window if possible), sends image + OCR text
    # ══════════════════════════════════════════════════════════════════════════
    def _do_capture(self):
        self._set_status("Capturing screen…")
        self._set_source("Source: Screen Capture")

        # Hide our window briefly so it doesn't appear in screenshot
        self.root.withdraw()
        time.sleep(0.25)

        try:
            screenshot = pyautogui.screenshot()
        except Exception as e:
            screenshot = None
            self._show_response(f"[Capture error: {e}]")
        finally:
            self.root.deiconify()

        if screenshot is None:
            return

        # OCR the full screenshot to get ALL text
        try:
            ocr_text = pytesseract.image_to_string(screenshot)
        except Exception:
            ocr_text = ""

        ocr_text = ocr_text.strip()
        if not ocr_text:
            ocr_text = "(No text found on screen via OCR)"

        # Also pass the image to the model if using Gemini (vision capable)
        user_prompt = f"""I have captured the user's screen. Here is all the text found on screen via OCR:

--- SCREEN CONTENT ---
{ocr_text}
--- END SCREEN CONTENT ---

Please answer ALL questions or solve ALL problems visible above. Be thorough — address every question, problem, or exercise you can identify in the screen content."""

        self._set_status("Thinking…")
        threading.Thread(target=self._query_ai,
                         args=(user_prompt, screenshot),
                         daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════════
    # HIGHLIGHT MODE (FIXED)
    # Watches clipboard; when text changes → auto-query AI
    # ══════════════════════════════════════════════════════════════════════════
    def _toggle_highlight(self):
        if self.highlight_mode.get():
            # Turn OFF
            self.highlight_mode.set(False)
            self.btn_highlight.config(bg=ACCENT, text="🔍  Highlight")
            self._set_status("Highlight mode OFF")
            self._set_source("")
        else:
            # Turn ON
            self.highlight_mode.set(True)
            self.btn_highlight.config(bg=HIGHLIGHT, text="✅  Highlight ON")
            self._set_status("Highlight mode ON — select text anywhere")
            self._set_source("Highlight mode ON — select text anywhere on screen")
            self.last_clipboard = ""
            if self.highlight_thread is None or not self.highlight_thread.is_alive():
                self.highlight_thread = threading.Thread(
                    target=self._highlight_loop, daemon=True)
                self.highlight_thread.start()

    def _highlight_loop(self):
        """
        Poll for selected text every 0.4 s.
        Simulates Ctrl+C to copy the current selection, then checks if
        clipboard changed — if so, sends the new text to AI.
        """
        import ctypes

        def _try_copy():
            """Simulate Ctrl+C via keyboard module to copy selection."""
            try:
                keyboard.send("ctrl+c")
                time.sleep(0.12)   # give clipboard time to update
            except Exception:
                pass

        while self.highlight_mode.get():
            try:
                _try_copy()
                current = pyperclip.paste()
            except Exception:
                current = ""

            if current and current != self.last_clipboard and len(current.strip()) > 2:
                self.last_clipboard = current
                self._set_source("Source: Highlighted text")
                self._set_status("Highlight detected — answering…")

                prompt = f"""The user has highlighted/selected the following text on their screen:

--- SELECTED TEXT ---
{current.strip()}
--- END ---

Please answer, explain, or solve whatever is asked or shown in the selected text above. Be concise but complete."""

                self._query_ai(prompt)
            time.sleep(0.5)

    # ══════════════════════════════════════════════════════════════════════════
    # FREE-FORM ASK
    # ══════════════════════════════════════════════════════════════════════════
    def _ask_free(self):
        question = self.ask_entry.get().strip()
        if not question or question == "Ask anything…":
            return
        self._set_source("Source: Manual question")
        self._set_status("Thinking…")
        threading.Thread(target=self._query_ai, args=(question,), daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════════
    # AI QUERY
    # ══════════════════════════════════════════════════════════════════════════
    def _query_ai(self, prompt: str, image=None):
        """Send prompt (+ optional PIL image) to AI and display result."""
        try:
            if groq_client:
                # ── Groq (primary) — text; OCR text is embedded in prompt ──
                resp = groq_client.chat.completions.create(
                    model=DEFAULT_MODEL,
                    messages=[
                        {"role": "system",
                         "content": "You are AceIt, a smart study assistant. Answer questions clearly and completely."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=1500,
                    temperature=0.4,
                )
                answer = resp.choices[0].message.content
            elif USE_GEMINI and image is not None:
                # ── Gemini vision fallback ─────────────────────────────────
                model = genai.GenerativeModel("gemini-1.5-flash")
                response = model.generate_content([prompt, image])
                answer = response.text
            elif USE_GEMINI:
                model = genai.GenerativeModel("gemini-1.5-flash")
                response = model.generate_content(prompt)
                answer = response.text
            else:
                answer = "⚠️  No API key configured.\nSet GROQ_API_KEY in your .env file."

        except Exception as exc:
            answer = f"[Error querying AI]\n{exc}"

        self._show_response(answer)
        self._set_status("Done ✓")

    # ══════════════════════════════════════════════════════════════════════════
    # HOT RELOAD
    # ══════════════════════════════════════════════════════════════════════════
    def _hot_reload(self):
        """Restart the process in-place, reloading main.py."""
        self._set_status("Reloading…")
        self.root.after(300, self._do_reload)

    def _do_reload(self):
        try:
            python = sys.executable
            script = os.path.abspath(__file__)
            self.root.destroy()
            os.execv(python, [python, script])
        except Exception as e:
            # If exec fails, fall back to subprocess restart
            subprocess.Popen([sys.executable, os.path.abspath(__file__)])
            self.root.destroy()

    # ══════════════════════════════════════════════════════════════════════════
    # FLOATING ICON MODE
    # ══════════════════════════════════════════════════════════════════════════
    def _enter_float(self):
        """Shrink to a small floating icon. Drag to move; click to restore."""
        self.root.withdraw()
        self._floating = True
        self._float_did_drag = False   # track whether user dragged vs clicked

        fw = tk.Toplevel(self.root)
        fw.overrideredirect(True)          # no title bar
        fw.attributes("-topmost", True)
        fw.attributes("-alpha", 0.85)
        fw.geometry("60x60+20+300")
        fw.configure(bg=HIGHLIGHT)
        self._float_win = fw

        btn = tk.Label(fw, text="⚡", font=("Segoe UI", 22),
                       bg=HIGHLIGHT, fg="white", cursor="hand2")
        btn.pack(fill="both", expand=True)

        # Dragging — bound on both window and label
        for w in (fw, btn):
            w.bind("<ButtonPress-1>",   self._float_drag_start)
            w.bind("<B1-Motion>",       self._float_drag_move)
            w.bind("<ButtonRelease-1>", self._float_click_or_snap)

    def _leave_float(self):
        if self._float_win:
            self._float_win.destroy()
            self._float_win = None
        self._floating = False
        self.root.deiconify()
        self.root.lift()

    def _float_drag_start(self, event):
        self._drag_x = event.x_root - self._float_win.winfo_x()
        self._drag_y = event.y_root - self._float_win.winfo_y()
        self._drag_start_x = event.x_root
        self._drag_start_y = event.y_root
        self._float_did_drag = False

    def _float_drag_move(self, event):
        dx = abs(event.x_root - self._drag_start_x)
        dy = abs(event.y_root - self._drag_start_y)
        if dx > 5 or dy > 5:
            self._float_did_drag = True
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self._float_win.geometry(f"+{x}+{y}")

    def _float_click_or_snap(self, event):
        """On release: if it was a click (no real drag) → open main window;
        otherwise snap to nearest edge."""
        if self._float_did_drag:
            self._float_snap_edge(event)
        else:
            self._leave_float()

    def _float_snap_edge(self, event):
        """Snap to nearest screen edge when dropped."""
        fw = self._float_win
        sw = fw.winfo_screenwidth()
        sh = fw.winfo_screenheight()
        x  = fw.winfo_x()
        y  = fw.winfo_y()
        w  = fw.winfo_width()
        h  = fw.winfo_height()

        # Snap to closest edge
        edges = {
            "left":   (0,       y),
            "right":  (sw - w,  y),
            "top":    (x,       0),
            "bottom": (x,       sh - h),
        }
        dist = {
            "left":   x,
            "right":  sw - x - w,
            "top":    y,
            "bottom": sh - y - h,
        }
        nearest = min(dist, key=dist.get)
        nx, ny = edges[nearest]
        fw.geometry(f"+{nx}+{ny}")

    # ══════════════════════════════════════════════════════════════════════════
    # OPACITY
    # ══════════════════════════════════════════════════════════════════════════
    def _apply_opacity(self, value: int):
        value = max(20, min(100, int(value)))
        self.root.attributes("-alpha", value / 100.0)
        self._opacity_lbl.config(text=f"{value}%")

    # ══════════════════════════════════════════════════════════════════════════
    # UI Helpers
    # ══════════════════════════════════════════════════════════════════════════
    def _show_response(self, text: str):
        def _update():
            self.response_box.config(state="normal")
            self.response_box.delete("1.0", "end")
            self.response_box.insert("end", text)
            self.response_box.config(state="disabled")
            self.response_box.see("end")
        self.root.after(0, _update)

    def _set_status(self, msg: str):
        self.root.after(0, lambda: self._status.config(text=f"  {msg}"))

    def _set_source(self, msg: str):
        self.root.after(0, lambda: self._source_lbl.config(text=msg))

    def _clear(self):
        self._show_response("")
        self._set_source("")
        self._set_status("Cleared")

    def _clear_placeholder(self, event):
        if self.ask_entry.get() == "Ask anything…":
            self.ask_entry.delete(0, "end")
            self.ask_entry.config(fg=TEXT_CLR)

    def _restore_placeholder(self, event):
        if not self.ask_entry.get():
            self.ask_entry.insert(0, "Ask anything…")
            self.ask_entry.config(fg=MUTED)

    def _voice_stub(self):
        self._set_status("Voice input coming soon…")

    # ── Clean shutdown ────────────────────────────────────────────────────────
    def on_close(self):
        self.highlight_mode.set(False)
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        self.root.destroy()


# ══════════════════════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    app  = AceItApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
