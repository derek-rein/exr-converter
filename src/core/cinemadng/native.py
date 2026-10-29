"""ctypes loader for the CinemaDNG frame reader."""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path

import numpy as np

from .color import FrameMeta
from .paths import library_candidates

log = logging.getLogger(__name__)

_lib: ctypes.CDLL | None = None
_load_attempted = False
_load_error = ""


class _Meta(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("bits", ctypes.c_uint32),
        ("compression", ctypes.c_uint32),
        ("cfa", ctypes.c_uint8 * 4),
        ("has_forward", ctypes.c_int32),
        ("has_color_matrix", ctypes.c_int32),
        ("illuminant", ctypes.c_int32),
        ("orientation", ctypes.c_int32),
        ("has_neutral", ctypes.c_int32),
        ("black", ctypes.c_double * 4),
        ("white", ctypes.c_double),
        ("neutral", ctypes.c_double * 3),
        ("color_matrix", ctypes.c_double * 9),
        ("forward_matrix", ctypes.c_double * 9),
        ("baseline_exposure", ctypes.c_double),
        ("frame_rate", ctypes.c_double),
        ("make", ctypes.c_char * 96),
        ("model", ctypes.c_char * 96),
        ("unique_model", ctypes.c_char * 128),
    ]


def _bind(lib: ctypes.CDLL) -> None:
    lib.cinemadng_last_error.restype = ctypes.c_char_p
    lib.cinemadng_last_error.argtypes = []
    lib.cinemadng_meta_size.restype = ctypes.c_size_t
    lib.cinemadng_meta_size.argtypes = []
    lib.cinemadng_free.restype = None
    lib.cinemadng_free.argtypes = [ctypes.c_void_p]
    lib.cinemadng_probe.restype = ctypes.c_int
    lib.cinemadng_probe.argtypes = [ctypes.c_char_p, ctypes.POINTER(_Meta)]
    lib.cinemadng_decode.restype = ctypes.POINTER(ctypes.c_float)
    lib.cinemadng_decode.argtypes = [ctypes.c_char_p, ctypes.POINTER(_Meta)]


def load_library() -> ctypes.CDLL:
    """Load the reader once. Raises RuntimeError when it is missing or mismatched."""
    global _lib, _load_attempted, _load_error
    if _lib is not None:
        return _lib
    if _load_attempted and _load_error:
        raise RuntimeError(_load_error)
    _load_attempted = True
    for path in library_candidates():
        if not path.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(path))
        except OSError as exc:
            log.debug("Failed to load CinemaDNG reader %s: %s", path, exc)
            continue
        _bind(lib)
        size = int(lib.cinemadng_meta_size())
        if size != ctypes.sizeof(_Meta):
            _load_error = (
                f"CinemaDNG reader ABI mismatch at {path} "
                f"(C struct {size} bytes, Python {ctypes.sizeof(_Meta)} bytes)"
            )
            raise RuntimeError(_load_error)
        lib._library_path = path  # type: ignore[attr-defined]
        _lib = lib
        log.info("Loaded CinemaDNG reader: %s", path)
        return lib
    tried = ", ".join(str(path) for path in library_candidates()[:8])
    _load_error = (
        "CinemaDNG reader not found. Build it with `python3 scripts/build_cinemadng.py`. "
        f"Tried: {tried}"
    )
    raise RuntimeError(_load_error)


def library_path() -> Path | None:
    try:
        lib = load_library()
    except RuntimeError:
        return None
    path = getattr(lib, "_library_path", None)
    return path if isinstance(path, Path) else None


def last_error() -> str:
    if _lib is None:
        return _load_error
    raw = _lib.cinemadng_last_error()
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _meta_from_struct(meta: _Meta) -> FrameMeta:
    return FrameMeta(
        width=int(meta.width),
        height=int(meta.height),
        bits=int(meta.bits),
        compression=int(meta.compression),
        cfa=(int(meta.cfa[0]), int(meta.cfa[1]), int(meta.cfa[2]), int(meta.cfa[3])),
        has_forward=bool(meta.has_forward),
        has_color_matrix=bool(meta.has_color_matrix),
        illuminant=int(meta.illuminant),
        orientation=int(meta.orientation),
        has_neutral=bool(meta.has_neutral),
        black=(
            float(meta.black[0]),
            float(meta.black[1]),
            float(meta.black[2]),
            float(meta.black[3]),
        ),
        white=float(meta.white),
        neutral=(float(meta.neutral[0]), float(meta.neutral[1]), float(meta.neutral[2])),
        color_matrix=tuple(float(meta.color_matrix[i]) for i in range(9)),
        forward_matrix=tuple(float(meta.forward_matrix[i]) for i in range(9)),
        baseline_exposure=float(meta.baseline_exposure),
        frame_rate=float(meta.frame_rate),
        make=_text(bytes(meta.make)),
        model=_text(bytes(meta.model)),
        unique_model=_text(bytes(meta.unique_model)),
    )


def probe_meta(path: str) -> FrameMeta:
    lib = load_library()
    meta = _Meta()
    rc = lib.cinemadng_probe(path.encode("utf-8"), ctypes.byref(meta))
    if rc != 0:
        raise RuntimeError(last_error() or f"Could not read CinemaDNG header: {path}")
    return _meta_from_struct(meta)


def decode_mosaic(path: str) -> tuple[np.ndarray, FrameMeta]:
    """Return the cropped, black-normalized Bayer mosaic and its header."""
    lib = load_library()
    meta = _Meta()
    ptr = lib.cinemadng_decode(path.encode("utf-8"), ctypes.byref(meta))
    if not ptr:
        raise RuntimeError(last_error() or f"Could not decode CinemaDNG frame: {path}")
    try:
        height = int(meta.height)
        width = int(meta.width)
        array = np.ctypeslib.as_array(ptr, shape=(height, width)).astype(np.float32, copy=True)
    finally:
        lib.cinemadng_free(ptr)
    return array, _meta_from_struct(meta)
