"""Quick check: Python version, Atlas imports, Tesseract, optional packages."""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path


def main() -> None:
    print("Python:", sys.version.replace("\n", " "))
    print()

    packages = (
        ("pyautogui", "pyautogui"),
        ("Pillow", "PIL"),
        ("pytesseract", "pytesseract"),
        ("python-dotenv", "dotenv"),
        ("groq", "groq"),
        ("keyboard", "keyboard"),
        ("PySide6", "PySide6"),
        ("opencv-python", "cv2"),
        ("sounddevice", "sounddevice"),
        ("kokoro-onnx", "kokoro_onnx"),
        ("mss", "mss"),
        ("pyperclip", "pyperclip"),
        ("markdown-it-py", "markdown_it"),
        ("pymupdf", "fitz"),
        ("numpy", "numpy"),
    )
    for name, mod in packages:
        try:
            importlib.import_module(mod)
            print(f"[OK] {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[MISSING] {name}: {exc}")

    print()
    for label, mod in (
        ("atlas_data", "atlas_data"),
        ("atlas_memory", "atlas_memory"),
        ("atlas_memory_manager", "atlas_memory_manager"),
        ("atlas_interaction", "atlas_interaction"),
        ("atlas_skills", "atlas_skills"),
        ("atlas_learning", "atlas_learning"),
        ("atlas_policy", "atlas_policy"),
        ("atlas_fs_v2", "atlas_fs_v2"),
        ("atlas_shell", "atlas_shell"),
        ("atlas_playbooks", "atlas_playbooks"),
        ("atlas_task_safety", "atlas_task_safety"),
        ("atlas_recorder", "atlas_recorder"),
        ("atlas_apscheduler", "atlas_apscheduler"),
        ("atlas_connectors", "atlas_connectors"),
        ("atlas_research", "atlas_research"),
        ("atlas_daemon", "atlas_daemon"),
        ("atlas_ipc", "atlas_ipc"),
        ("atlas_scheduler", "atlas_scheduler"),
        ("atlas_state_proxy", "atlas_state_proxy"),
        ("atlas_core", "atlas_core"),
        ("atlas_overlay", "atlas_overlay"),
        ("atlas_ui", "atlas_ui"),
    ):
        try:
            importlib.import_module(mod)
            print(f"[OK] {label}")
        except Exception as exc:  # noqa: BLE001
            print(f"[MISSING] {label}: {exc}")

    try:
        import easyocr  # noqa: F401

        print()
        print(
            "[WARN] easyocr is installed. Atlas uses pytesseract only. "
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

    groq_key = Path(".env")
    if groq_key.is_file():
        print("[OK] .env present")
    else:
        print("[WARN] .env not found — set GROQ_API_KEY for AI features")


if __name__ == "__main__":
    main()
