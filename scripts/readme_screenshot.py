#!/usr/bin/env python3
"""Render the README screenshot offscreen, with a macOS-style window frame.

Uses the offscreen Qt platform, so it runs on Linux CI or a headless box.
Settings go to a temp folder, so the shot shows the app's defaults.

    uv run python scripts/readme_screenshot.py
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / "resources" / "screenshots" / "exr_converter_screenshot.png"
TITLE_BAR = 28
RADIUS = 10
SS = 4  # supersampling for anti-aliased shapes


def render_window(width: int, height: int, tab: int, out: Path) -> None:
    from PySide6.QtCore import QEventLoop, QSettings, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    import src.rc_resources  # noqa: F401
    from src.core.constants import APP_NAME, APP_ORG, APP_VERSION
    from src.gui.style import load_stylesheet
    from src.gui.window import MainWindow

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
    app = QApplication([sys.argv[0], "-platform", "offscreen"])
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    font = QFont("Inter")
    font.setPointSizeF(10.0)
    app.setFont(font)
    app.setStyleSheet(load_stylesheet())

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    win = MainWindow()
    win.resize(width, height)
    win.show()
    pump(1500)
    win._tabs.setCurrentIndex(tab)
    pump(300)
    if not win.grab().save(str(out), "PNG"):
        raise RuntimeError(f"Could not write {out}.")
    win.close()


def add_frame(raw_path: Path, out: Path, title: str, font_path: str | None) -> None:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    raw = Image.open(raw_path).convert("RGBA")
    w, h = raw.width, raw.height + TITLE_BAR
    win = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    win.paste(raw, (0, TITLE_BAR))
    draw = ImageDraw.Draw(win)
    draw.rectangle([0, 0, w, TITLE_BAR - 1], fill=(54, 54, 54, 255))
    draw.line([0, TITLE_BAR - 1, w, TITLE_BAR - 1], fill=(20, 20, 20, 255))

    lights = Image.new("RGBA", (80 * SS, TITLE_BAR * SS), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lights)
    colors = (
        ((255, 95, 87), (226, 70, 63)),
        ((254, 188, 46), (223, 160, 35)),
        ((40, 200, 64), (29, 173, 43)),
    )
    for i, (fill, edge) in enumerate(colors):
        cx, cy, r = (20 + i * 20) * SS, TITLE_BAR * SS // 2, 6 * SS
        ld.ellipse(
            [cx - r, cy - r, cx + r, cy + r], fill=fill + (255,), outline=edge + (255,), width=3
        )
    win.alpha_composite(lights.resize((80, TITLE_BAR), Image.LANCZOS), (0, 0))

    try:
        font = ImageFont.truetype(font_path or "Inter-SemiBold.otf", 13)
    except OSError:
        font = ImageFont.load_default()
    tw = draw.textlength(title, font=font)
    draw.text(
        ((w - tw) / 2, TITLE_BAR / 2), title, font=font, fill=(220, 220, 220, 255), anchor="lm"
    )

    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], RADIUS * SS, fill=255)
    win.putalpha(mask.resize((w, h), Image.LANCZOS))
    border = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(
        [0, 0, w * SS - 1, h * SS - 1], RADIUS * SS, outline=(110, 110, 110, 160), width=SS
    )
    win.alpha_composite(border.resize((w, h), Image.LANCZOS))

    px, pt, pb = 44, 34, 56
    canvas = Image.new("RGBA", (w + 2 * px, h + pt + pb), (0, 0, 0, 0))
    shadow_mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(shadow_mask).rounded_rectangle(
        [px, pt + 14, px + w, pt + h + 14], RADIUS, fill=120
    )
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_mask.filter(ImageFilter.GaussianBlur(20)))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(win, (px, pt))
    canvas.save(out, optimize=True)
    print(f"Wrote {out} ({canvas.width}x{canvas.height})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--width", type=int, default=812)
    parser.add_argument("--height", type=int, default=826)
    parser.add_argument("--tab", type=int, default=0, help="Tab index (0 = Video to EXR).")
    parser.add_argument(
        "--font", default=os.environ.get("README_SHOT_FONT"), help="Title bar font file."
    )
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.png"
        render_window(args.width, args.height, args.tab, raw)
        add_frame(raw, args.output, "EXR Converter", args.font)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
