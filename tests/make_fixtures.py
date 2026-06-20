"""Generate deterministic UI fixture PNGs for vision tests."""
from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("Pillow required: pip install Pillow")

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURES.mkdir(parents=True, exist_ok=True)

SPECS = [
    {
        "name": "save_button.png",
        "size": (640, 400),
        "draw": lambda img, d: (
            d.rectangle((180, 140, 300, 190), fill="#2563eb", outline="#1e40af"),
            d.text((205, 155), "Save", fill="white"),
        ),
        "target": "Save button",
        "expected": (180, 140),
    },
    {
        "name": "settings_icon.png",
        "size": (480, 320),
        "draw": lambda img, d: d.ellipse((360, 40, 410, 90), fill="#64748b"),
        "target": "settings gear icon",
        "expected": (360, 40),
    },
    {
        "name": "search_field.png",
        "size": (800, 480),
        "draw": lambda img, d: (
            d.rectangle((120, 80, 520, 120), fill="#f1f5f9", outline="#94a3b8"),
            d.text((130, 92), "Search...", fill="#64748b"),
        ),
        "target": "search field",
        "expected": (120, 80),
    },
]

meta_lines = []
for spec in SPECS:
    img = Image.new("RGB", spec["size"], "#ffffff")
    draw = ImageDraw.Draw(img)
    spec["draw"](img, draw)
    out = FIXTURES / spec["name"]
    img.save(out, format="PNG")
    meta_lines.append(
        f"{spec['name']}|{spec['target']}|{spec['expected'][0]}|{spec['expected'][1]}"
    )

(FIXTURES / "targets.txt").write_text("\n".join(meta_lines) + "\n", encoding="utf-8")
print(f"wrote {len(SPECS)} fixtures to {FIXTURES}")
