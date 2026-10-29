#!/usr/bin/env python3
"""Render Microsoft Store screenshots of the real Windows app.

Run on windows-latest (or any Windows desktop session) with the native
Windows Qt platform. The images are 1920x1080 PNG captures of the window,
including the Windows title bar. Sample clips come from
``tests/fixtures/manifest.json`` when those files exist. Missing clips are
written with the same synthetic writers the integration tests use.

Do not set ``QT_QPA_PLATFORM=offscreen``. This script forces the ``windows``
platform plugin and a Windows style (Windows 11, Vista, or classic Windows).
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QImage  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QMainWindow,
    QSplitter,
    QStyleFactory,
)

import src.rc_resources  # noqa: E402, F401  # register :/style.qss and icons
from src.core.constants import APP_NAME, APP_ORG, APP_VERSION  # noqa: E402
from src.gui import style as style_mod  # noqa: E402
from src.gui.color_widgets import ColorSpaceButton  # noqa: E402
from src.gui.ocio_gpu_plane import configure_default_gl_format  # noqa: E402
from src.gui.style import load_stylesheet  # noqa: E402
from src.gui.window import PROXY_TAB_INDEX, TRANSCODE_TAB_INDEX, MainWindow  # noqa: E402
from tests.support.integration import (  # noqa: E402
    load_manifest,
    write_synthetic_exr_sequence,
    write_synthetic_video,
)

TARGET_W = 1920
TARGET_H = 1080
_MIN_W = 1366
_MIN_H = 768
_MAC_FONT_MARKERS = ("sf pro", "apple", "lucida grande", "menlo", "monaco", "geneva")
_VIDEO_NAMES = (
    "shot_010_plate.mov",
    "shot_020_plate.mov",
    "shot_030_comp.mov",
    "daily_ep010.mov",
)
_SRC_VIDEO = "sRGB Encoded Rec.709 (sRGB)"
_DST_EXR = "ACEScg"
_DST_VIDEO = "Rec.1886 Rec.709 - Display"


class _RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class _DEVMODEW(ctypes.Structure):
    _fields_ = (
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_uint),
        ("dmPositionX", ctypes.c_int),
        ("dmPositionY", ctypes.c_int),
        ("dmDisplayOrientation", ctypes.c_uint),
        ("dmDisplayFixedOutput", ctypes.c_uint),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_uint),
        ("dmPelsWidth", ctypes.c_uint),
        ("dmPelsHeight", ctypes.c_uint),
        ("dmDisplayFlags", ctypes.c_uint),
        ("dmDisplayFrequency", ctypes.c_uint),
        ("dmICMMethod", ctypes.c_uint),
        ("dmICMIntent", ctypes.c_uint),
        ("dmMediaType", ctypes.c_uint),
        ("dmDitherType", ctypes.c_uint),
        ("dmReserved1", ctypes.c_uint),
        ("dmReserved2", ctypes.c_uint),
        ("dmPanningWidth", ctypes.c_uint),
        ("dmPanningHeight", ctypes.c_uint),
    )


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = (
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    )


def store_aspect_ok(width: int, height: int) -> bool:
    """True when a capture is 16:9 and at least the Store minimum."""
    if width < _MIN_W or height < _MIN_H or height <= 0:
        return False
    return abs((width / height) - (16 / 9)) / (16 / 9) <= 0.02


def _shots_root() -> Path:
    for candidate in (Path(r"C:\shots\ep010"), Path.home() / "shots" / "ep010"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        return candidate
    raise RuntimeError("No writable folder for sample media.")


def prepare_sample_media() -> tuple[list[Path], Path]:
    """Fixture clips when the manifest has files, plus generated shots to fill the queue."""
    videos, sequences = load_manifest()
    paths = [item.path for item in videos if item.path.is_file()]
    root = _shots_root()
    for name in _VIDEO_NAMES:
        if len(paths) >= 4:
            break
        path = root / name
        if not path.is_file():
            write_synthetic_video(path, width=640, height=360, frames=12, fps=24)
        paths.append(path)
    if len(paths) < 4:
        raise RuntimeError(f"Need at least 4 sample clips, found {len(paths)}.")

    for item in sequences:
        parent = item.path.parent
        if any(parent.glob("*.exr")):
            return paths, item.path

    exr = write_synthetic_exr_sequence(
        root / "shot_010_beauty",
        stem="shot_010_beauty",
        frames=(1001, 1002, 1003, 1004),
        width=320,
        height=180,
    )
    return paths, exr


def _require_windows_session() -> None:
    if sys.platform != "win32":
        raise SystemExit("Store screenshots must be rendered on Windows.")
    platform = os.environ.get("QT_QPA_PLATFORM", "").strip().lower()
    if platform in {"offscreen", "minimal", "cocoa", "wayland", "xcb", "vnc"}:
        raise SystemExit(
            f"QT_QPA_PLATFORM={platform!r} cannot produce Windows Store screenshots. "
            "Use the windows platform plugin."
        )
    os.environ["QT_QPA_PLATFORM"] = "windows"
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    os.environ["QT_SCALE_FACTOR"] = "1"


def _user32() -> ctypes.WinDLL:
    library = ctypes.WinDLL("user32", use_last_error=True)
    library.EnumDisplaySettingsW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p]
    library.EnumDisplaySettingsW.restype = ctypes.c_int
    library.ChangeDisplaySettingsW.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    library.ChangeDisplaySettingsW.restype = ctypes.c_long
    library.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.GetWindowRect.restype = ctypes.c_int
    library.GetWindowDC.argtypes = [ctypes.c_void_p]
    library.GetWindowDC.restype = ctypes.c_void_p
    library.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.ReleaseDC.restype = ctypes.c_int
    library.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    library.PrintWindow.restype = ctypes.c_int
    return library


def _gdi32() -> ctypes.WinDLL:
    library = ctypes.WinDLL("gdi32", use_last_error=True)
    library.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    library.CreateCompatibleDC.restype = ctypes.c_void_p
    library.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    library.CreateCompatibleBitmap.restype = ctypes.c_void_p
    library.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.SelectObject.restype = ctypes.c_void_p
    library.DeleteObject.argtypes = [ctypes.c_void_p]
    library.DeleteObject.restype = ctypes.c_int
    library.DeleteDC.argtypes = [ctypes.c_void_p]
    library.DeleteDC.restype = ctypes.c_int
    library.GetDIBits.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    library.GetDIBits.restype = ctypes.c_int
    return library


def _set_display_1920x1080() -> None:
    user32 = _user32()
    mode = _DEVMODEW()
    mode.dmSize = ctypes.sizeof(_DEVMODEW)
    if not user32.EnumDisplaySettingsW(None, -1, ctypes.byref(mode)):
        raise RuntimeError("EnumDisplaySettingsW failed.")
    current = (int(mode.dmPelsWidth), int(mode.dmPelsHeight))
    print(f"Display before: {current[0]}x{current[1]}", flush=True)
    if current == (TARGET_W, TARGET_H):
        return
    mode.dmPelsWidth = TARGET_W
    mode.dmPelsHeight = TARGET_H
    mode.dmFields = 0x00080000 | 0x00100000  # DM_PELSWIDTH | DM_PELSHEIGHT
    result = int(user32.ChangeDisplaySettingsW(ctypes.byref(mode), 0))
    if result != 0:
        raise RuntimeError(
            f"Could not set the display to {TARGET_W}x{TARGET_H} "
            f"(ChangeDisplaySettingsW returned {result}, was {current[0]}x{current[1]})."
        )
    print(f"Display now: {TARGET_W}x{TARGET_H}", flush=True)


def _app_stylesheet() -> str:
    """The same dark theme ``main.py`` applies, loaded after the Qt app exists."""
    src.rc_resources.qInitResources()
    style_mod._cache = None
    text = load_stylesheet()
    if len(text) < 500 or "282828" not in text:
        raw = (ROOT / "resources" / "style.qss").read_text(encoding="utf-8")
        text = style_mod._VAR_RE.sub(
            lambda match: style_mod._PALETTE.get(match.group(1), match.group(0)),
            raw,
        )
    if len(text) < 500 or "282828" not in text:
        raise RuntimeError(
            "Could not load the app stylesheet. Screenshots would not match the app."
        )
    print(f"stylesheet chars: {len(text)}", flush=True)
    return text


def _apply_windows_style(app: QApplication) -> str:
    available = {name.lower(): name for name in QStyleFactory.keys()}
    chosen = ""
    for key in ("windows11", "windowsvista", "windows"):
        if key in available:
            app.setStyle(available[key])
            chosen = available[key]
            break
    if not chosen:
        raise RuntimeError(f"No Windows Qt style. Factory keys: {sorted(available)}")
    if not QFontDatabase.hasFamily("Segoe UI"):
        raise RuntimeError("Segoe UI is not installed. This session is not a Windows desktop.")
    app.setFont(QFont("Segoe UI", 9))
    family = QFontInfo(app.font()).family()
    lowered = family.casefold()
    if any(marker in lowered for marker in _MAC_FONT_MARKERS):
        raise RuntimeError(f"UI font is {family}, which is a macOS font.")
    print(f"Qt style: {chosen}; font: {family}", flush=True)
    return chosen


def _pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _set_combo_data(combo: QComboBox, value: str) -> None:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return
    raise RuntimeError(f"Combo has no value {value!r}.")


def _select_space(button: ColorSpaceButton, *names: str) -> str:
    for name in names:
        if button.try_select(name, auto=False):
            return name
    shown = button.displayed_space()
    if shown and shown != "(none)":
        return shown
    raise RuntimeError(f"None of {names!r} are in the OCIO menu.")


def _fit_window(win: QMainWindow) -> None:
    """Size the visible frame to 1920x1080 and give the form most of the height."""
    page = win._tabs.currentWidget()
    if page is not None:
        bar_h = win._tabs.tabBar().sizeHint().height()
        # Keep the blank stretch inside the form from eating the window.
        # The log pane takes the leftover height.
        win._tabs.setMaximumHeight(max(page.sizeHint().height() + bar_h + 12, 420))
    win.show()
    _pump(200)
    frame = win.frameGeometry()
    client = win.geometry()
    extra_w = max(0, frame.width() - client.width())
    extra_h = max(0, frame.height() - client.height())
    win.resize(TARGET_W - extra_w, TARGET_H - extra_h)
    win.move(0, 0)
    _pump(200)
    frame = win.frameGeometry()
    client = win.geometry()
    extra_w = max(0, frame.width() - client.width())
    extra_h = max(0, frame.height() - client.height())
    win.resize(max(900, TARGET_W - extra_w), max(700, TARGET_H - extra_h))
    win.move(0, 0)
    splitter = win.findChild(QSplitter, "logSplitter")
    if splitter is not None:
        top_h = min(760, win._tabs.maximumHeight() + 200)
        splitter.setSizes([top_h, max(220, TARGET_H - top_h)])
    win.repaint()
    _pump(100)


def _visible_frame(hwnd: int) -> _RECT | None:
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    dwmapi.DwmGetWindowAttribute.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    rect = _RECT()
    # DWMWA_EXTENDED_FRAME_BOUNDS: visible window without the DWM shadow.
    hr = int(dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)))
    if hr != 0:
        return None
    return rect


def _grab_window(win: QMainWindow) -> QImage:
    user32 = _user32()
    gdi32 = _gdi32()
    hwnd = int(win.winId())
    outer = _RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(outer)):
        raise RuntimeError("GetWindowRect failed.")
    width = int(outer.right - outer.left)
    height = int(outer.bottom - outer.top)
    if width < 100 or height < 100:
        raise RuntimeError(f"Window rect is {width}x{height}.")

    hwnd_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    previous = gdi32.SelectObject(mem_dc, bitmap)
    # PW_RENDERFULLCONTENT asks DWM for the real window, including the frame.
    printed = bool(user32.PrintWindow(hwnd, mem_dc, 0x00000002))
    if not printed:
        printed = bool(user32.PrintWindow(hwnd, mem_dc, 0))
    if not printed:
        gdi32.SelectObject(mem_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)
        raise RuntimeError("PrintWindow failed.")

    header = _BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = height
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0
    buffer = ctypes.create_string_buffer(width * height * 4)
    # The bitmap must not stay selected into a DC for GetDIBits.
    gdi32.SelectObject(mem_dc, previous)
    rows = gdi32.GetDIBits(hwnd_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hwnd_dc)
    if rows == 0:
        raise RuntimeError("GetDIBits failed.")

    image = QImage(buffer, width, height, width * 4, QImage.Format.Format_ARGB32).copy()
    image = image.mirrored(False, True)
    visible = _visible_frame(hwnd)
    if visible is not None:
        crop_x = max(0, int(visible.left - outer.left))
        crop_y = max(0, int(visible.top - outer.top))
        crop_w = min(image.width() - crop_x, int(visible.right - visible.left))
        crop_h = min(image.height() - crop_y, int(visible.bottom - visible.top))
        if crop_w > 100 and crop_h > 100:
            image = image.copy(crop_x, crop_y, crop_w, crop_h)
    return image


def _to_store_png(image: QImage) -> QImage:
    if image.isNull():
        raise RuntimeError("Screenshot capture was empty.")
    if not store_aspect_ok(image.width(), image.height()):
        raise RuntimeError(
            f"Capture is {image.width()}x{image.height()}, "
            f"need 16:9 and at least {_MIN_W}x{_MIN_H}."
        )
    scaled = image.scaled(
        TARGET_W,
        TARGET_H,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - TARGET_W) // 2)
    y = max(0, (scaled.height() - TARGET_H) // 2)
    frame = scaled.copy(x, y, TARGET_W, TARGET_H)
    if frame.width() != TARGET_W or frame.height() != TARGET_H:
        raise RuntimeError(f"Store frame is {frame.width()}x{frame.height()}.")
    return frame


def _assert_looks_like_windows_ui(image: QImage) -> None:
    buckets: set[tuple[int, int, int]] = set()
    dark = 0
    samples = 0
    reds = yellows = greens = 0
    step_x = max(1, image.width() // 48)
    step_y = max(1, image.height() // 48)
    for y in range(60, image.height() - 20, step_y):
        for x in range(12, image.width() - 12, step_x):
            color = image.pixelColor(x, y)
            samples += 1
            if color.red() < 80 and color.green() < 80 and color.blue() < 80:
                dark += 1
    if samples == 0 or dark * 4 < samples:
        raise RuntimeError(
            f"Screenshot is missing the app's dark theme ({dark}/{samples} dark samples)."
        )
    for y in range(0, image.height(), step_y):
        for x in range(0, image.width(), step_x):
            color = image.pixelColor(x, y)
            buckets.add((color.red() // 16, color.green() // 16, color.blue() // 16))
    for y in range(0, min(36, image.height())):
        for x in range(0, min(80, image.width())):
            color = image.pixelColor(x, y)
            if color.red() > 200 and color.green() < 120 and color.blue() < 120:
                reds += 1
            elif color.red() > 200 and 140 < color.green() < 220 and color.blue() < 90:
                yellows += 1
            elif color.green() > 150 and color.red() < 120 and color.blue() < 130:
                greens += 1
    if len(buckets) < 6:
        raise RuntimeError(f"Screenshot looks blank ({len(buckets)} color groups).")
    if reds > 2 and yellows > 2 and greens > 2:
        raise RuntimeError("Screenshot has macOS traffic-light window buttons.")


def _save(win: QMainWindow, path: Path) -> None:
    _fit_window(win)
    image = _to_store_png(_grab_window(win))
    _assert_looks_like_windows_ui(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"Could not write {path}.")
    print(f"Wrote {path} ({image.width()}x{image.height()})", flush=True)


def _load_inputs(win: QMainWindow, video: Path, exr: Path) -> None:
    v2e = win._v2e_tab
    e2v = win._e2v_tab
    win._tabs.setCurrentIndex(0)
    if not v2e.set_input(str(video)):
        raise RuntimeError(f"Video to EXR could not open {video}.")
    _pump(1300)
    _select_space(v2e.src_btn, _SRC_VIDEO)
    _select_space(v2e.dst_btn, _DST_EXR)
    _pump(200)

    win._tabs.setCurrentIndex(1)
    if not e2v.set_input(str(exr)):
        raise RuntimeError(f"EXR to Video could not open {exr}.")
    _pump(1300)
    _select_space(e2v.src_btn, _DST_EXR)
    _select_space(e2v.dst_btn, _DST_VIDEO)
    _pump(200)


def _fill_queues(win: QMainWindow, videos: list[Path], output_dir: Path) -> None:
    names = [str(path) for path in videos]
    proxy = win._proxy_tab
    added = proxy.add_paths(names)
    if added < 4 and proxy.clip_list.count() < 4:
        raise RuntimeError(f"Proxy queue has {proxy.clip_list.count()} clips.")

    transcode = win._transcode_tab
    added = transcode.add_paths(names)
    if added < 4 and transcode.clip_list.count() < 4:
        raise RuntimeError(f"Transcode queue has {transcode.clip_list.count()} clips.")
    _set_combo_data(transcode.resolution_combo, "1080")
    _set_combo_data(transcode.location_combo, "custom")
    output_dir.mkdir(parents=True, exist_ok=True)
    transcode.output_dir.setText(str(output_dir))
    transcode._extras.set_expanded(True)
    transcode.color_cb.setChecked(True)
    _select_space(transcode.src_btn, _SRC_VIDEO)
    _select_space(transcode.dst_btn, _DST_VIDEO)
    _pump(200)


def capture(output: Path) -> None:
    _require_windows_session()
    _set_display_1920x1080()
    videos, exr = prepare_sample_media()
    print("Sample video:", videos[0], flush=True)
    print("Sample sequence:", exr, flush=True)

    configure_default_gl_format()
    app = QApplication([sys.argv[0], "-platform", "windows"])
    if app.platformName() != "windows":
        raise RuntimeError(f"Qt platform is {app.platformName()!r}, expected 'windows'.")
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    settings_dir = output / "_settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_dir))
    _apply_windows_style(app)
    sheet = _app_stylesheet()
    app.setStyleSheet(sheet)

    win = MainWindow()
    win.setStyleSheet(sheet)
    print(f"applied stylesheet chars: {len(app.styleSheet())}", flush=True)
    screen = win.screen()
    if screen is not None:
        size = screen.size()
        print(
            f"Qt screen: {size.width()}x{size.height()} dpr {screen.devicePixelRatio()}",
            flush=True,
        )
    _load_inputs(win, videos[0], exr)
    win._tabs.setCurrentIndex(0)
    _save(win, output / "01-video-to-exr.png")
    win._tabs.setCurrentIndex(1)
    _save(win, output / "02-exr-to-video.png")

    names = [str(path) for path in videos]
    delivery = videos[0].parent / "delivery"
    proxy_added = win._proxy_tab.add_paths(names)
    if proxy_added < 4 and win._proxy_tab.clip_list.count() < 4:
        raise RuntimeError(f"Proxy queue has {win._proxy_tab.clip_list.count()} clips.")
    win._tabs.setCurrentIndex(PROXY_TAB_INDEX)
    _save(win, output / "03-video-proxy.png")

    _fill_queues(win, videos, delivery)
    win._tabs.setCurrentIndex(TRANSCODE_TAB_INDEX)
    _save(win, output / "04-transcode-queue.png")

    win.close()
    app.quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("store-screenshots"),
        help="Directory for the 1920x1080 PNG files.",
    )
    args = parser.parse_args(argv)
    try:
        capture(args.output)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
