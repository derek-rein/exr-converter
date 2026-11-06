"""Apple ProRes RAW (``aprn`` / ``aprh``) decode through FFmpeg.

FFmpeg 9 decodes ProRes RAW to a 16-bit linear RGGB mosaic (it applies the
clip's linearization curve). FFmpeg 8 left the samples 12-bit and in the
camera's encoding, so this needs PyAV 19 or newer.

The developer here does the rest, in float32 so no precision is lost after
the decoder:

1. Black and white level from the frame header: ``(v - black) / (white - black)``.
   Nothing is clipped, so noise under black and highlight headroom survive.
2. As-shot white balance gains on the red and blue photosites.
3. Bilinear demosaic (the same interpolation CinemaDNG uses).
4. The header's camera RGB to XYZ D65 matrix and gain, then a Bradford
   adaptation to D60 and XYZ to ACES AP0. Frames come out ACES2065-1.
5. The header's recommended crop.

ProRes RAW is a built-in source: it goes through the source-plugin dispatch
(see :mod:`.source_plugins`) so every caller handles it like the other RAW
formats, but it never loads from ``src.plugins``.
"""

from __future__ import annotations

import logging
import math
import os
import struct
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .errors import ConversionCancelled

log = logging.getLogger(__name__)

PRORES_RAW_SUFFIXES = frozenset({".mov", ".mp4", ".m4v"})
FOURCCS = {b"aprn": "ProRes RAW", b"aprh": "ProRes RAW HQ"}
OUTPUT_COLORSPACE = "ACES2065-1"
SRC_COLORSPACE_CANDIDATES: tuple[str, ...] = (
    "ACES2065-1",
    "ACES - ACES2065-1",
    "lin_ap0",
    "lin_ap0_scene",
    "aces2065_1",
)
EXR_ATTR_PREFIX = "exrconverter:proresraw:"
MIN_FFMPEG_MAJOR = 9
VENDORS = {"atm0": "Atomos", "peac": "Panasonic"}

# Largest moov atom the sniff reads. An hour of 24p is well under 2 MB.
_MOOV_LIMIT = 64 * 1024 * 1024
_D65_XY = (0.3127, 0.3290)
# A matrix whose white point lands farther than this from D65 (in xy) is
# suspicious: the frame would not be neutral on a white-balanced grey.
_WHITE_XY_TOLERANCE = 0.02


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _read_moov(path: str) -> bytes | None:
    """The ``moov`` atom of a QuickTime / MP4 file, found by walking top-level atoms."""
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            pos = 0
            while pos + 8 <= size:
                fh.seek(pos)
                head = fh.read(16)
                if len(head) < 8:
                    return None
                atom_size, kind = struct.unpack(">I4s", head[:8])
                header = 8
                if atom_size == 1:
                    if len(head) < 16:
                        return None
                    atom_size = struct.unpack(">Q", head[8:16])[0]
                    header = 16
                elif atom_size == 0:
                    atom_size = size - pos
                if atom_size < header:
                    return None
                if kind == b"moov":
                    if atom_size > _MOOV_LIMIT:
                        return None
                    fh.seek(pos)
                    return fh.read(atom_size)
                pos += atom_size
    except OSError:
        return None
    return None


def _fourcc_in_moov(moov: bytes) -> bytes | None:
    """First sample-description fourcc that is ProRes RAW, else None."""
    start = 0
    while True:
        i = moov.find(b"stsd", start)
        if i < 0:
            return None
        # size(4) 'stsd' | version+flags(4) | count(4) | entry size(4) | entry type(4)
        kind = moov[i + 16 : i + 20]
        if kind in FOURCCS:
            return kind
        start = i + 4


_sniff_lock = threading.Lock()
_sniff_cache: dict[str, tuple[tuple[int, int], bytes | None]] = {}


def prores_raw_fourcc(path: str | os.PathLike[str]) -> bytes | None:
    """``b"aprn"`` / ``b"aprh"`` when *path* holds ProRes RAW video, else None.

    Reads only the atom headers and the ``moov`` atom. Cached per file size
    and modification time. Never opens cloud placeholders.
    """
    p = os.fspath(path)
    if Path(p).name.startswith("._") or Path(p).suffix.lower() not in PRORES_RAW_SUFFIXES:
        return None
    try:
        st = os.stat(p)
    except OSError:
        return None
    key = (int(st.st_size), int(st.st_mtime_ns))
    with _sniff_lock:
        hit = _sniff_cache.get(p)
    if hit is not None and hit[0] == key:
        return hit[1]
    from .local_fs import can_probe_media

    if not can_probe_media(p):
        return None
    moov = _read_moov(p)
    found = _fourcc_in_moov(moov) if moov else None
    with _sniff_lock:
        if len(_sniff_cache) > 4096:
            _sniff_cache.clear()
        _sniff_cache[p] = (key, found)
    return found


def is_prores_raw_path(path: str | os.PathLike[str]) -> bool:
    return prores_raw_fourcc(path) is not None


def ffmpeg_major() -> int:
    try:
        import av

        text = str(av.ffmpeg_version_info).lstrip("nN")
        return int(text.split(".", 1)[0])
    except Exception:
        return 0


def is_available() -> bool:
    try:
        import av

        return "prores_raw" in av.codecs_available and ffmpeg_major() >= MIN_FFMPEG_MAJOR
    except Exception:
        return False


def unavailable_reason() -> str:
    try:
        import av

        version = str(av.ffmpeg_version_info)
        if "prores_raw" not in av.codecs_available:
            return f"ProRes RAW needs FFmpeg's prores_raw decoder (this FFmpeg is {version})."
    except Exception:
        return "ProRes RAW needs PyAV (FFmpeg) and could not import it."
    return (
        f"ProRes RAW needs FFmpeg {MIN_FFMPEG_MAJOR} or newer for 16-bit linear decode "
        f"(this FFmpeg is {version})."
    )


# ---------------------------------------------------------------------------
# Frame header
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameHeader:
    """Fields of the ``prrf`` frame header (layout from FFmpeg's decoder)."""

    version: int
    vendor: str
    width: int
    height: int
    crop: tuple[int, int, int, int]  # left, right, top, bottom
    bayer_pattern: int
    black: float  # 16-bit decoder output units
    white: float
    wb_red: float
    wb_blue: float
    matrix: tuple[float, ...]  # camera RGB to XYZ D65, row-major 3x3
    gain: float
    cct: int
    curve: tuple[int, ...] | None

    @property
    def vendor_name(self) -> str:
        return VENDORS.get(self.vendor, self.vendor)

    @property
    def cropped_size(self) -> tuple[int, int]:
        left, right, top, bottom = self.crop
        return max(1, self.width - left - right), max(1, self.height - top - bottom)


def parse_frame_header(packet: bytes) -> FrameHeader:
    """Parse the header at the start of one ProRes RAW frame (packet)."""
    if len(packet) < 80 or packet[4:8] != b"prrf":
        raise ValueError("not a ProRes RAW frame")
    header_len = struct.unpack_from(">H", packet, 8)[0]
    if header_len < 62 or len(packet) < 8 + header_len:
        raise ValueError("truncated ProRes RAW frame header")
    hdr = packet[10 : 8 + header_len]

    def need(n: int) -> None:
        if len(hdr) < n:
            raise ValueError("truncated ProRes RAW frame header")

    need(70)
    version = hdr[1]
    vendor = hdr[2:6].decode("latin-1").rstrip("\0")
    width, height = struct.unpack_from(">HH", hdr, 6)
    crop = (hdr[10], hdr[11], hdr[12], hdr[13])
    bayer = struct.unpack_from(">H", hdr, 14)[0] & 0x3
    sensel_range = struct.unpack_from(">H", hdr, 16)[0]
    wb_red, wb_blue = struct.unpack_from(">ff", hdr, 18)
    matrix = struct.unpack_from(">9f", hdr, 26)
    gain = struct.unpack_from(">f", hdr, 62)[0]
    cct = struct.unpack_from(">H", hdr, 66)[0]
    flags = struct.unpack_from(">H", hdr, 68)[0]
    offset = 70
    if flags & 1:
        offset += 64
    curve = None
    if (flags >> 4) & 1:
        need(offset + 16)
        curve = struct.unpack_from(">8H", hdr, offset)
    # FFmpeg: black is fixed at 0x100, white is senselValueRange + 0x100.
    black = 256.0
    white = float(sensel_range + 256)
    return FrameHeader(
        version=version,
        vendor=vendor,
        width=width,
        height=height,
        crop=crop,
        bayer_pattern=bayer,
        black=black,
        white=white,
        wb_red=float(wb_red),
        wb_blue=float(wb_blue),
        matrix=tuple(float(x) for x in matrix),
        gain=float(gain),
        cct=int(cct),
        curve=curve,
    )


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


def _finite_positive(value: float, fallback: float) -> float:
    return value if math.isfinite(value) and value > 0 else fallback


def camera_matrix(header: FrameHeader) -> tuple[np.ndarray, list[str]]:
    """Camera RGB to XYZ D65 (gain applied) and any warnings about it."""
    from .cinemadng.color import _rgb_to_xyz

    warnings: list[str] = []
    m = np.asarray(header.matrix, dtype=np.float64).reshape(3, 3)
    usable = bool(np.all(np.isfinite(m))) and abs(float(np.linalg.det(m))) > 1e-9
    if not usable:
        warnings.append(
            "ProRes RAW header has no usable colour matrix; treating camera RGB as Rec.709."
        )
        m = _rgb_to_xyz(((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), _D65_XY)
    else:
        white = m @ np.ones(3)
        total = float(white.sum())
        if total > 0:
            x, y = float(white[0]) / total, float(white[1]) / total
            off = math.hypot(x - _D65_XY[0], y - _D65_XY[1])
            if off > _WHITE_XY_TOLERANCE:
                warnings.append(
                    f"ProRes RAW colour matrix maps neutral to xy ({x:.4f}, {y:.4f}), "
                    f"{off:.3f} from D65; colours may be off."
                )
    gain = header.gain
    if not (math.isfinite(gain) and gain > 0):
        warnings.append("ProRes RAW header gain is not usable; using 1.0.")
        gain = 1.0
    return m * gain, warnings


def output_matrix(header: FrameHeader) -> np.ndarray:
    """White-balanced camera RGB to ACES2065-1."""
    from .cinemadng.color import _AP0_FROM_XYZ_D60, _D60_XY, _cat

    cam_to_xyz, _warnings = camera_matrix(header)
    return (_AP0_FROM_XYZ_D60 @ _cat(_D65_XY, _D60_XY) @ cam_to_xyz).astype(np.float32)


def header_warnings(header: FrameHeader) -> list[str]:
    out: list[str] = []
    if header.bayer_pattern != 0:
        out.append(f"ProRes RAW Bayer pattern {header.bayer_pattern} is not RGGB.")
    if header.white <= header.black:
        out.append("ProRes RAW white level is not above black; using the full 16-bit range.")
    for name, value in (("red", header.wb_red), ("blue", header.wb_blue)):
        if not (math.isfinite(value) and value > 0):
            out.append(f"ProRes RAW {name} white balance gain is not usable; using 1.0.")
    out.extend(camera_matrix(header)[1])
    return out


def _levels(header: FrameHeader) -> tuple[float, float]:
    black, white = header.black, header.white
    if white <= black:
        black, white = 0.0, 65535.0
    return black, white


def demosaic_rggb_planes(raw: np.ndarray) -> np.ndarray:
    """Bilinear demosaic of an even-sized RGGB mosaic, as float32 planes (3, H, W).

    Same interpolation as :func:`.cinemadng.color.demosaic_bilinear` (mean
    of the same-colour neighbours in the 3x3 window), written per Bayer
    phase so a 4K frame takes well under a second. Edges mirror.
    """
    raw = np.ascontiguousarray(raw, dtype=np.float32)
    h, w = raw.shape
    if h % 2 or w % 2:
        raise ValueError("RGGB mosaic must have even width and height")
    p = np.pad(raw, 2, mode="reflect")  # a 2-pixel mirror keeps the Bayer phase
    hh, hw = h // 2, w // 2

    def s(py: int, px: int, dy: int, dx: int) -> np.ndarray:
        y, x = 2 + py + dy, 2 + px + dx
        return p[y : y + 2 * hh : 2, x : x + 2 * hw : 2]

    half = np.float32(0.5)
    quarter = np.float32(0.25)

    def cross(py: int, px: int) -> np.ndarray:
        return (s(py, px, -1, 0) + s(py, px, 1, 0) + s(py, px, 0, -1) + s(py, px, 0, 1)) * quarter

    def diag(py: int, px: int) -> np.ndarray:
        return (s(py, px, -1, -1) + s(py, px, -1, 1) + s(py, px, 1, -1) + s(py, px, 1, 1)) * quarter

    def horiz(py: int, px: int) -> np.ndarray:
        return (s(py, px, 0, -1) + s(py, px, 0, 1)) * half

    def vert(py: int, px: int) -> np.ndarray:
        return (s(py, px, -1, 0) + s(py, px, 1, 0)) * half

    out = np.empty((3, h, w), dtype=np.float32)
    r, g, b = out
    # Red photosites (0, 0)
    r[0::2, 0::2] = raw[0::2, 0::2]
    g[0::2, 0::2] = cross(0, 0)
    b[0::2, 0::2] = diag(0, 0)
    # Green on red rows (0, 1)
    r[0::2, 1::2] = horiz(0, 1)
    g[0::2, 1::2] = raw[0::2, 1::2]
    b[0::2, 1::2] = vert(0, 1)
    # Green on blue rows (1, 0)
    r[1::2, 0::2] = vert(1, 0)
    g[1::2, 0::2] = raw[1::2, 0::2]
    b[1::2, 0::2] = horiz(1, 0)
    # Blue photosites (1, 1)
    r[1::2, 1::2] = diag(1, 1)
    g[1::2, 1::2] = cross(1, 1)
    b[1::2, 1::2] = raw[1::2, 1::2]
    return out


def demosaic_rggb(raw: np.ndarray) -> np.ndarray:
    """:func:`demosaic_rggb_planes` as float32 HxWx3."""
    return np.ascontiguousarray(np.moveaxis(demosaic_rggb_planes(raw), 0, -1))


def develop(mosaic: np.ndarray, header: FrameHeader, *, half: bool = False) -> np.ndarray:
    """Float32 ACES2065-1 RGB (HxWx3) from one decoded 16-bit RGGB mosaic.

    ``half`` bins each 2x2 RGGB quad into one pixel instead of demosaicing,
    for previews and half-size (or smaller) converts.
    """
    black, white = _levels(header)
    scale = np.float32(1.0 / (white - black))
    raw = (mosaic.astype(np.float32) - np.float32(black)) * scale
    wb_r = np.float32(_finite_positive(header.wb_red, 1.0))
    wb_b = np.float32(_finite_positive(header.wb_blue, 1.0))
    left, right, top, bottom = header.crop
    h, w = raw.shape
    if half:
        hh, hw = h // 2, w // 2
        q = raw[: hh * 2, : hw * 2]
        planes = np.empty((3, hh, hw), dtype=np.float32)
        planes[0] = q[0::2, 0::2] * wb_r
        planes[1] = (q[0::2, 1::2] + q[1::2, 0::2]) * np.float32(0.5)
        planes[2] = q[1::2, 1::2] * wb_b
        y0, y1 = top // 2, max(top // 2 + 1, (h - bottom) // 2)
        x0, x1 = left // 2, max(left // 2 + 1, (w - right) // 2)
    else:
        raw[0::2, 0::2] *= wb_r
        raw[1::2, 1::2] *= wb_b
        planes = demosaic_rggb_planes(raw)
        y0, y1 = top, max(top + 1, h - bottom)
        x0, x1 = left, max(left + 1, w - right)
    r, g, b = (plane[y0:y1, x0:x1] for plane in planes)
    m = output_matrix(header)
    out = np.empty((r.shape[0], r.shape[1], 3), dtype=np.float32)
    tmp = np.empty(r.shape, dtype=np.float32)
    for c in range(3):
        dst = out[..., c]
        np.multiply(r, m[c, 0], out=tmp)
        tmp += g * m[c, 1]
        tmp += b * m[c, 2]
        dst[...] = tmp
    return out


_AP0_TO_REC709: np.ndarray | None = None


def aces_to_display(rgb: np.ndarray) -> np.ndarray:
    """Rough display RGB (0-1) for thumbnails: ACES to Rec.709, clip, 1/2.2."""
    global _AP0_TO_REC709
    if _AP0_TO_REC709 is None:
        from .cinemadng.color import _AP0_PRIMARIES, _D60_XY, _cat, _rgb_to_xyz, _xyz_to_rgb

        rec709 = ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06))
        _AP0_TO_REC709 = (
            _xyz_to_rgb(rec709, _D65_XY)
            @ _cat(_D60_XY, _D65_XY)
            @ _rgb_to_xyz(_AP0_PRIMARIES, _D60_XY)
        ).astype(np.float32)
    disp = rgb.reshape(-1, 3) @ _AP0_TO_REC709.T
    disp = np.clip(disp, 0.0, 1.0) ** (1.0 / 2.2)
    return disp.reshape(rgb.shape).astype(np.float32)


# ---------------------------------------------------------------------------
# Clip
# ---------------------------------------------------------------------------


def _frame_mosaic(frame) -> np.ndarray:
    name = frame.format.name
    if not name.startswith("bayer_rggb16"):
        raise RuntimeError(f"ProRes RAW decoded to {name}, expected bayer_rggb16le")
    plane = frame.planes[0]
    dtype = np.dtype("<u2") if name.endswith("le") else np.dtype(">u2")
    stride = int(plane.line_size) // 2
    arr = np.frombuffer(plane, dtype=dtype).reshape(int(frame.height), stride)
    return arr[:, : int(frame.width)]


@dataclass(frozen=True)
class ClipInfo:
    width: int  # after the recommended crop
    height: int
    sensor_width: int
    sensor_height: int
    fps: float
    frame_count: int
    codec_label: str
    header: FrameHeader
    timecode: str


class ProResRawClip:
    """One ProRes RAW clip: probe, header and random-access developed frames."""

    def __init__(self, path: str) -> None:
        import av

        if not is_available():
            raise RuntimeError(unavailable_reason())
        self.path = path
        self._lock = threading.Lock()
        self._container = av.open(path)
        try:
            streams = self._container.streams.video
            if not streams:
                raise RuntimeError(f"No video stream in {path}")
            self._stream = streams[0]
            ctx = self._stream.codec_context
            if ctx.name != "prores_raw":
                raise RuntimeError(f"{Path(path).name} is not ProRes RAW ({ctx.name})")
            # Crop would shift the Bayer phase (and FFmpeg 9 rejects odd crops
            # on Bayer frames); develop() crops after demosaic instead.
            ctx.options = {"apply_cropping": "0"}
            try:
                ctx.thread_type = "SLICE"
                ctx.thread_count = 0
            except Exception:
                pass
            from .video import stream_fps

            self._fps = stream_fps(self._stream) or 24.0
            self._time_base = self._stream.time_base
            self._start = int(self._stream.start_time or 0)
            self._demux: Iterator | None = None
            self._next_index = 1
            first = self._read_packet(1)
            if first is None:
                raise RuntimeError(f"No ProRes RAW frames in {path}")
            header = parse_frame_header(bytes(first))
            self._first_header = header
            frames = int(self._stream.frames or 0)
            if frames <= 0 and self._stream.duration and self._time_base:
                frames = max(1, round(float(self._stream.duration * self._time_base) * self._fps))
            meta = dict(getattr(self._stream, "metadata", {}) or {})
            meta_c = dict(getattr(self._container, "metadata", {}) or {})
            tc = str(meta.get("timecode") or meta_c.get("timecode") or "")
            fourcc = prores_raw_fourcc(path) or b"aprn"
            w, h = header.cropped_size
            self.info = ClipInfo(
                width=w,
                height=h,
                sensor_width=header.width,
                sensor_height=header.height,
                fps=self._fps,
                frame_count=max(1, frames),
                codec_label=FOURCCS.get(fourcc, "ProRes RAW"),
                header=header,
                timecode=tc,
            )
        except Exception:
            self._container.close()
            raise

    def close(self) -> None:
        self._demux = None
        try:
            self._container.close()
        except Exception:
            pass

    def __enter__(self) -> ProResRawClip:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def header(self) -> FrameHeader:
        return self._first_header

    def warnings(self) -> list[str]:
        return header_warnings(self._first_header)

    def _index_of(self, pts: int | None) -> int | None:
        if pts is None or not self._time_base:
            return None
        sec = float((int(pts) - self._start) * self._time_base)
        return int(round(sec * self._fps)) + 1

    def _seek(self, index: int) -> None:
        self._demux = None
        if index <= 1 or not self._time_base:
            self._container.seek(0)
        else:
            sec = (index - 1) / self._fps
            pts = self._start + int(round(sec / float(self._time_base)))
            self._container.seek(pts, stream=self._stream, backward=True)
        self._next_index = 0  # unknown until a packet's pts says

    def _read_packet(self, index: int):
        """Packet of 1-based frame *index*, or None past the end."""
        if self._demux is None or index != self._next_index:
            self._seek(index)
        if self._demux is None:
            self._demux = iter(self._container.demux(self._stream))
        for packet in self._demux:
            if packet.size == 0:
                continue
            got = self._index_of(packet.pts)
            if got is None:
                got = self._next_index or index
            self._next_index = got + 1
            if got < index:
                continue
            if got > index:
                # The seek landed past it (no exact pts); fall back to a scan.
                self._demux = None
                return self._scan_from_start(index)
            return packet
        self._demux = None
        return None

    def _scan_from_start(self, index: int):
        self._container.seek(0)
        self._demux = iter(self._container.demux(self._stream))
        n = 0
        for packet in self._demux:
            if packet.size == 0:
                continue
            n += 1
            if n == index:
                self._next_index = index + 1
                return packet
        self._demux = None
        return None

    def _decode(self, packet) -> tuple[np.ndarray, FrameHeader]:
        header = parse_frame_header(bytes(packet))
        ctx = self._stream.codec_context
        frames = ctx.decode(packet)
        if not frames:
            # Frame threading can hold the frame back; drain, then reset so
            # the next packet decodes (every ProRes RAW frame is a keyframe).
            frames = ctx.decode(None)
            ctx.flush_buffers()
        if not frames:
            raise RuntimeError(f"ProRes RAW frame did not decode in {self.path}")
        return _frame_mosaic(frames[-1]), header

    def read_mosaic(self, index: int) -> tuple[np.ndarray, FrameHeader]:
        """16-bit RGGB mosaic and header of 1-based frame *index*."""
        with self._lock:
            packet = self._read_packet(max(1, int(index)))
            if packet is None:
                raise IndexError(f"ProRes RAW frame {index} is past the end of {self.path}")
            mosaic, header = self._decode(packet)
            return np.array(mosaic, copy=True), header

    def decode_frame(self, index: int, *, half: bool = False) -> tuple[np.ndarray, FrameHeader]:
        mosaic, header = self.read_mosaic(index)
        return develop(mosaic, header, half=half), header

    def iter_mosaics(
        self, wanted: set[int] | None = None
    ) -> Iterator[tuple[int, np.ndarray, FrameHeader]]:
        """Sequential decode from the first frame; skips frames not in *wanted*."""
        with self._lock:
            self._container.seek(0)
            self._demux = None
            last = max(wanted) if wanted else 0
            index = 0
            for packet in self._container.demux(self._stream):
                if packet.size == 0:
                    continue
                index += 1
                if wanted is not None and index not in wanted:
                    if last and index > last:
                        break
                    continue
                mosaic, header = self._decode(packet)
                yield index, mosaic, header
                if last and index >= last:
                    break

    def exr_attributes(self, header: FrameHeader) -> dict[str, str]:
        attrs = {
            f"{EXR_ATTR_PREFIX}codec": self.info.codec_label,
            f"{EXR_ATTR_PREFIX}colorspace": OUTPUT_COLORSPACE,
            f"{EXR_ATTR_PREFIX}vendor": header.vendor_name,
            f"{EXR_ATTR_PREFIX}wb_cct": str(header.cct),
            f"{EXR_ATTR_PREFIX}wb_red": f"{header.wb_red:.6g}",
            f"{EXR_ATTR_PREFIX}wb_blue": f"{header.wb_blue:.6g}",
            f"{EXR_ATTR_PREFIX}gain": f"{header.gain:.6g}",
            f"{EXR_ATTR_PREFIX}black_white": f"{header.black:g}/{header.white:g}",
        }
        if self.info.timecode:
            attrs[f"{EXR_ATTR_PREFIX}start_timecode"] = self.info.timecode
        return attrs


# ---------------------------------------------------------------------------
# Frame sources
# ---------------------------------------------------------------------------


class ProResRawPreviewDecoder:
    """Player decoder: half-size developed frames (quad binning)."""

    def __init__(self, path: str, *, fps: float = 0.0) -> None:
        self.path = path
        self._clip = ProResRawClip(path)
        self.fps = float(fps) if fps and fps > 0 else self._clip.info.fps
        self._last: tuple[int, np.ndarray] | None = None
        i = self._clip.info
        # Same rounding the player uses for preview_scale() sizes.
        self._size = (max(1, int(round(i.width * 0.5))), max(1, int(round(i.height * 0.5))))

    @property
    def decode_size(self) -> tuple[int, int]:
        return self._size

    def close(self) -> None:
        self._clip.close()

    def get_frame(self, idx_1based: int) -> np.ndarray | None:
        idx = max(1, int(idx_1based))
        if self._last and self._last[0] == idx:
            return self._last[1]
        try:
            rgb, _header = self._clip.decode_frame(idx, half=True)
        except Exception:
            log.debug("ProRes RAW preview decode failed frame=%s", idx, exc_info=True)
            return None
        w, h = self._size
        if (rgb.shape[1], rgb.shape[0]) != (w, h):
            from .frame_source import resize_rgb_f32

            rgb = resize_rgb_f32(rgb, w, h)
        self._last = (idx, rgb)
        return rgb


class ProResRawIngestSource:
    """Convert-path source: full demosaic, or quad binning at half size or less."""

    def __init__(self, path: str, *, scale: float = 1.0) -> None:
        from .frame_source import MediaInfo, scaled_dims

        self._path = path
        self._scale = float(scale)
        self._half = self._scale <= 0.5 + 1e-6
        self._clip = ProResRawClip(path)
        i = self._clip.info
        self._info = MediaInfo(
            path=path,
            width=i.width,
            height=i.height,
            fps=i.fps,
            frame_count=i.frame_count,
            kind="prores_raw",
            colorspace_hint=OUTPUT_COLORSPACE,
        )
        self._out_w, self._out_h = scaled_dims(i.width, i.height, self._scale)

    @property
    def info(self):
        return self._info

    @property
    def output_size(self) -> tuple[int, int]:
        return self._out_w, self._out_h

    def close(self) -> None:
        self._clip.close()

    def __enter__(self) -> ProResRawIngestSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def log_header(self, log_fn: Callable[[str], None] | None) -> None:
        if not log_fn:
            return
        i = self._clip.info
        h = i.header
        import av

        log_fn(
            f"{i.codec_label}: {i.sensor_width}x{i.sensor_height} sensor, "
            f"{i.width}x{i.height} after crop, recorder {h.vendor_name or '?'}, "
            f"FFmpeg {av.ffmpeg_version_info}"
        )
        log_fn(
            f"ProRes RAW develop: 16-bit linear, black {h.black:g} white {h.white:g}, "
            f"WB {h.cct}K (R x{h.wb_red:.4f}, B x{h.wb_blue:.4f}), gain {h.gain:.4f}, "
            f"{'2x2 binned' if self._half else 'bilinear demosaic'}, output {OUTPUT_COLORSPACE}"
        )
        for warning in self._clip.warnings():
            log_fn(f"WARNING: {warning}")

    def iter_frames(
        self,
        frame_set: set[int] | None = None,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterator[tuple[int, np.ndarray, dict[str, str]]]:
        from .frame_source import resize_rgb_f32

        ow, oh = self._out_w, self._out_h
        produced = 0
        for idx, mosaic, header in self._clip.iter_mosaics(frame_set):
            if cancel_check and cancel_check():
                raise ConversionCancelled()
            rgb = develop(mosaic, header, half=self._half)
            if (rgb.shape[1], rgb.shape[0]) != (ow, oh):
                rgb = resize_rgb_f32(rgb, ow, oh)
            produced += 1
            yield idx, rgb, self._clip.exr_attributes(header)
        if produced == 0:
            raise RuntimeError(f"No ProRes RAW frames decoded from {Path(self._path).name}")


# ---------------------------------------------------------------------------
# Source plugin
# ---------------------------------------------------------------------------


class ProResRawSource:
    """Built-in source for the source-plugin dispatch."""

    name = "ProRes RAW"
    suffixes = PRORES_RAW_SUFFIXES

    def claims(self, path: str) -> bool:
        return is_prores_raw_path(path)

    def is_available(self) -> bool:
        return is_available()

    def unavailable_reason(self) -> str:
        return unavailable_reason()

    def codec_label(self, path: str) -> str:
        return FOURCCS.get(prores_raw_fourcc(path) or b"aprn", "ProRes RAW")

    def probe(self, path: str) -> tuple[int, int, float, int]:
        with ProResRawClip(path) as clip:
            i = clip.info
            return i.width, i.height, i.fps, i.frame_count

    def probe_metadata(self, path: str) -> dict[str, str]:
        result = {"Format": "QuickTime / MOV", "Video codec": self.codec_label(path)}
        if not is_available():
            result["error"] = unavailable_reason()
            return result
        with ProResRawClip(path) as clip:
            i = clip.info
            h = i.header
            fps = f"{i.fps:.3f}".rstrip("0").rstrip(".")
            result.update(
                {
                    "Video resolution": f"{i.width}×{i.height}",
                    "Sensor resolution": f"{i.sensor_width}×{i.sensor_height}",
                    "Video fps": fps,
                    "Video frames": str(i.frame_count),
                    "Recorder": h.vendor_name,
                    "Bit depth": "16-bit linear (FFmpeg decode), developed to float",
                    "White balance": f"{h.cct}K (R x{h.wb_red:.4f}, B x{h.wb_blue:.4f})",
                    "Gain": f"{h.gain:.4f}",
                    "Black / white level": f"{h.black:g} / {h.white:g}",
                    "Recommended crop (L R T B)": " ".join(str(c) for c in h.crop),
                    "Decoded color space": OUTPUT_COLORSPACE,
                }
            )
            if i.timecode:
                result["Start timecode"] = i.timecode
            try:
                for k, v in clip._container.metadata.items():
                    result[f"meta:{k}"] = str(v)
            except Exception:
                pass
            warnings = clip.warnings()
            if warnings:
                result["Warnings"] = " ".join(warnings)
        return result

    def colorspace_candidates(self, path: str) -> list[str]:
        return list(SRC_COLORSPACE_CANDIDATES)

    def preview_scale(self) -> float:
        return 0.5

    def open_preview(self, path: str, *, fps: float = 0.0) -> ProResRawPreviewDecoder:
        return ProResRawPreviewDecoder(path, fps=fps)

    def open_ingest(self, path: str, *, scale: float = 1.0) -> ProResRawIngestSource:
        return ProResRawIngestSource(path, scale=scale)

    def thumbnail_rgb(self, path: str) -> np.ndarray | None:
        with ProResRawClip(path) as clip:
            rgb, _header = clip.decode_frame(1, half=True)
        return aces_to_display(rgb)

    def about_lines(self) -> list[str]:
        return []

    def notices(self) -> list[tuple[str, str]]:
        return []


_builtin = ProResRawSource()


def get_source_plugin() -> ProResRawSource:
    return _builtin
