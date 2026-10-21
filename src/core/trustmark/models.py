"""Download TrustMark ONNX models into a per-user cache.

The app only fetches ``encoder_Q.onnx``. ``decoder_Q.onnx`` is for
``scripts/trustmark_detect.py`` and is never packaged with a release.
Both files are checked against a SHA256 before use.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..app_paths import is_under_install_dir, outside_install_dir
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


def ensure_model(filename: str, *, log: LogCallback | None = None) -> Path:
    """Return a cached model, downloading it once when the hash does not match."""
    expected = _SHA256.get(filename)
    if expected is None:
        raise ValueError(f"Unknown TrustMark model {filename!r}.")
    dest = cache_dir() / filename
    if dest.is_file() and _sha256(dest) == expected:
        return dest

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
            f"Could not download TrustMark {filename} from {url}. Check the network and try again."
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
