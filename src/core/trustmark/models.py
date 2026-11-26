"""Locate TrustMark ONNX models: bundled with the app, else a per-user cache.

``encoder_Q.onnx`` is committed under ``resources/trustmark/`` and packaged
into release builds, so watermarking works offline. If that copy is missing
or fails its hash, it is downloaded once into a per-user cache. ``decoder_Q.onnx`` is for
``scripts/trustmark_detect.py`` and is never packaged with a release.
Every file is checked against a SHA256 before use.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..app_paths import is_under_install_dir, outside_install_dir, runtime_exe_dirs
from ..constants import APP_ORG, APP_VERSION

MODEL_BASE_URL = "https://cai-watermark.adobe.net/watermarking/trustmark-models/"
ENCODER_FILENAME = "encoder_Q.onnx"
DECODER_FILENAME = "decoder_Q.onnx"

# SHA256 of the files published at MODEL_BASE_URL (sizes 17,312,208 and 47,401,222).
_ENCODER_SHA256 = "19b3d1b25836130ffd78775a8f61539f993375d1823ef0e59ba5b8dffb4f892d"
_DECODER_SHA256 = "ee3268f057c9dabef680e169302f5973d0589feea86189ed229a896cc3aa88df"

_SHA256 = {
    ENCODER_FILENAME: _ENCODER_SHA256,
    DECODER_FILENAME: _DECODER_SHA256,
}

LogCallback = Callable[[str], None]

# Where release builds keep the bundled encoder, relative to the app root.
BUNDLED_REL_DIR = Path("resources") / "trustmark"


def _default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / APP_ORG / "EXRConverter" / "Cache" / "trustmark"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_ORG / "EXRConverter" / "trustmark"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / "exr-converter" / "trustmark"


def _fallback_cache_dir() -> Path:
    """Home-relative cache when ``LOCALAPPDATA`` / ``XDG_CACHE_HOME`` point at the install tree."""
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / APP_ORG / "EXRConverter" / "Cache" / "trustmark"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_ORG / "EXRConverter" / "trustmark"
    return Path.home() / ".cache" / "exr-converter" / "trustmark"


def cache_dir() -> Path:
    """Per-user model cache. ``EXR_TRUSTMARK_CACHE`` overrides it.

    The directory is never inside the application install folder. An MSIX
    package root is read-only, so a download next to ``exr_converter.exe``
    would fail.
    """
    override = os.environ.get("EXR_TRUSTMARK_CACHE", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute() or is_under_install_dir(path):
            raise RuntimeError(
                "EXR_TRUSTMARK_CACHE must be an absolute directory outside the "
                "application install folder. An MSIX install directory is read-only."
            )
        return path
    return outside_install_dir(_default_cache_dir(), _fallback_cache_dir())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundled_dirs() -> list[Path]:
    """Folders that may hold a model shipped with the app (or a source checkout)."""
    dirs: list[Path] = []
    # Source checkout: <repo>/resources/trustmark (this file is src/core/trustmark/).
    dirs.append(Path(__file__).resolve().parents[3] / BUNDLED_REL_DIR)
    # Nuitka standalone / .app: beside the executable, or Contents/Resources.
    for exe_dir in runtime_exe_dirs():
        dirs.append(exe_dir / BUNDLED_REL_DIR)
    seen: set[Path] = set()
    unique: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def bundled_model(filename: str) -> Path | None:
    """Return the shipped copy of *filename* when it is present and intact."""
    expected = _SHA256.get(filename)
    if expected is None:
        return None
    for d in bundled_dirs():
        cand = d / filename
        try:
            if cand.is_file() and _sha256(cand) == expected:
                return cand
        except OSError:
            continue
    return None


def download_model(filename: str, dest: Path, *, log: LogCallback | None = None) -> Path:
    """Download *filename* to *dest*, verify its SHA256, and return *dest*."""
    expected = _SHA256.get(filename)
    if expected is None:
        raise ValueError(f"Unknown TrustMark model {filename!r}.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(filename + ".partial")
    url = MODEL_BASE_URL + filename
    if log:
        log(f"Downloading TrustMark {filename} (once) to {dest.parent}")
    request = urllib.request.Request(url, headers={"User-Agent": f"exr-converter/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Could not download TrustMark {filename} from {url} ({exc}). "
            "Check the network and try again."
        ) from exc

    actual = _sha256(tmp)
    if actual != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"TrustMark {filename} failed the SHA256 check (expected {expected}, got {actual})."
        )
    os.replace(tmp, dest)
    if log:
        log(f"Saved TrustMark model {dest}")
    return dest


def ensure_model(filename: str, *, log: LogCallback | None = None) -> Path:
    """Return the bundled or cached model, downloading it once if neither is present."""
    expected = _SHA256.get(filename)
    if expected is None:
        raise ValueError(f"Unknown TrustMark model {filename!r}.")
    shipped = bundled_model(filename)
    if shipped is not None:
        return shipped
    dest = cache_dir() / filename
    if dest.is_file() and _sha256(dest) == expected:
        return dest
    return download_model(filename, dest, log=log)
