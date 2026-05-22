"""Quick check: Python version, AceIt imports, Tesseract, stray easyocr."""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path


def main() -> None:
    print("Python:", sys.version.replace("\n", " "))
    print()

    for name, mod in (
        ("pyautogui", "pyautogui"),
        ("Pillow", "PIL"),
        ("pytesseract", "pytesseract"),
        ("python-dotenv", "dotenv"),
        ("google-generativeai", "google.generativeai"),
        ("keyboard", "keyboard"),
    ):
        try:
            importlib.import_module(mod)
            print(f"[OK] {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[MISSING] {name}: {exc}")

    try:
        import easyocr  # noqa: F401

        print()
        print(
            "[WARN] easyocr is installed. AceIt uses pytesseract only. "
            "Uninstall to avoid python-bidi / torch builds:",
        )
        print("       python -m pip uninstall -y easyocr")
    except ImportError:
        pass

    print()
    exe = shutil.which("tesseract")
    if exe:
        print(f"[OK] tesseract on PATH: {exe}")
    else:
        for cand in (
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ):
            if cand.is_file():
                print(f"[OK] tesseract found: {cand}")
                break
        else:
            print(
                "[MISSING] tesseract.exe - install Tesseract (see INSTALL_WINDOWS.txt) "
                "or set TESSERACT_CMD.",
            )


if __name__ == "__main__":
    main()
