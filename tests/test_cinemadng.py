"""CinemaDNG reader: generated Bayer frames, DJI P3-D60, and sequence wiring."""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from src.core.cinemadng.color import (
    ACES2065_1,
    DJI_EXPOSURE_STOPS,
    LINEAR_P3_D60,
    FrameMeta,
    ap0_to_p3_d65,
    develop,
    p3_d60_to_ap0,
)
from src.core.cinemadng.native import decode_mosaic, library_path, probe_meta
from src.core.constants import IMAGE_SEQUENCE_EXTS, is_scene_referred_image_ext
from src.core.exr_io import read_image
from src.core.frame_source import open_ingest_source
from src.core.sequence import find_exr_sequence, probe_pixel_colorspace
from src.core.video import guess_video_colorspace_candidates, probe_video

_ROOT = Path(__file__).resolve().parents[1]
_PUBLISHED_AP0_TO_P3_D65 = np.array(
    [
        [2.02490528596679, -0.689069761034766, -0.335835524932019],
        [-0.183597032256178, 1.28950620775902, -0.105909175502841],
        [0.00905856112234766, -0.0592796840575522, 1.0502211229352],
    ],
    dtype=np.float64,
)


@pytest.fixture(scope="module")
def cinemadng_library() -> Path:
    subprocess.check_call(
        [sys.executable, str(_ROOT / "scripts" / "build_cinemadng.py")], cwd=_ROOT
    )
    path = library_path()
    assert path is not None and path.is_file()
    return path


def test_p3_d65_method_matches_bundled_matrix() -> None:
    ours = ap0_to_p3_d65()
    assert np.max(np.abs(ours - _PUBLISHED_AP0_TO_P3_D65)) < 1e-12


def test_p3_d60_to_ap0_preserves_white() -> None:
    matrix = p3_d60_to_ap0()
    assert matrix @ np.ones(3) == pytest.approx(np.ones(3), abs=1e-12)
    assert matrix.sum(axis=1) == pytest.approx(np.ones(3), abs=1e-12)


def _d50_white() -> np.ndarray:
    x, y = 0.34567, 0.35850
    return np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=np.float64)


def _forward_matrix_to_d50_white() -> np.ndarray:
    white = _d50_white()
    matrix = np.zeros((3, 3), dtype=np.float64)
    for row in range(3):
        matrix[row, :] = white[row] / 3.0
    return matrix


def _meta(**overrides: object) -> FrameMeta:
    matrix = _forward_matrix_to_d50_white().reshape(-1)
    base = FrameMeta(
        width=4,
        height=4,
        bits=16,
        compression=1,
        cfa=(0, 1, 1, 2),
        has_forward=True,
        has_color_matrix=False,
        illuminant=23,
        orientation=1,
        has_neutral=True,
        black=(0.0, 0.0, 0.0, 0.0),
        white=10000.0,
        neutral=(1.0, 1.0, 1.0),
        color_matrix=tuple(0.0 for _ in range(9)),
        forward_matrix=tuple(float(v) for v in matrix),
        baseline_exposure=0.0,
        frame_rate=24.0,
        make="DJI",
        model="Zenmuse X9",
        unique_model="Zenmuse X9",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_develop_dji_neutral_includes_1_4_stops() -> None:
    mosaic = np.full((8, 8), 0.18, dtype=np.float32)
    rgb, space = develop(mosaic, _meta())
    expected = 0.18 * (2.0**DJI_EXPOSURE_STOPS)
    assert space == LINEAR_P3_D60
    assert rgb[4, 4] == pytest.approx([expected, expected, expected], abs=1e-5)


def test_develop_other_camera_is_aces_neutral() -> None:
    mosaic = np.full((8, 8), 0.18, dtype=np.float32)
    rgb, space = develop(mosaic, _meta(make="Acme", model="Cam", unique_model="Cam"))
    assert space == ACES2065_1
    assert rgb[4, 4] == pytest.approx([0.18, 0.18, 0.18], abs=1e-5)


def test_dji_without_matrices_stays_in_p3() -> None:
    mosaic = np.full((4, 4), 0.18, dtype=np.float32)
    meta = _meta(has_forward=False, forward_matrix=tuple(0.0 for _ in range(9)))
    rgb, space = develop(mosaic, meta)
    expected = 0.18 * (2.0**DJI_EXPOSURE_STOPS)
    assert space == LINEAR_P3_D60
    assert rgb[1, 1] == pytest.approx([expected, expected, expected], abs=1e-5)


def _pack(fmt: str, value: int, little: bool) -> bytes:
    endian = "<" if little else ">"
    return struct.pack(endian + fmt, value)


def _rational(value: float, little: bool, *, signed: bool = False) -> bytes:
    den = 10_000_000
    num = int(round(value * den))
    endian = "<" if little else ">"
    code = "i" if signed else "I"
    return struct.pack(endian + code + "I", num, den)


class _Tiff:
    def __init__(self, little: bool = True) -> None:
        self.little = little
        self.entries: list[tuple[int, int, int, bytes]] = []

    def add(self, tag: int, typ: int, count: int, raw: bytes) -> None:
        self.entries.append((tag, typ, count, raw))

    def add_short(self, tag: int, values: list[int]) -> None:
        raw = b"".join(_pack("H", v, self.little) for v in values)
        self.add(tag, 3, len(values), raw)

    def add_long(self, tag: int, values: list[int]) -> None:
        raw = b"".join(_pack("I", v, self.little) for v in values)
        self.add(tag, 4, len(values), raw)

    def add_rational(self, tag: int, values: list[float], *, signed: bool = False) -> None:
        raw = b"".join(_rational(v, self.little, signed=signed) for v in values)
        self.add(tag, 10 if signed else 5, len(values), raw)

    def add_ascii(self, tag: int, text: str) -> None:
        raw = text.encode("ascii") + b"\0"
        self.add(tag, 2, len(raw), raw)

    def add_byte(self, tag: int, raw: bytes) -> None:
        self.add(tag, 1, len(raw), raw)

    def ifd_bytes(self, data_at: int, next_ifd: int = 0) -> tuple[bytes, bytes]:
        entries = sorted(self.entries, key=lambda item: item[0])
        ifd_size = 2 + 12 * len(entries) + 4
        blobs = bytearray()
        body = bytearray()
        body += _pack("H", len(entries), self.little)
        for tag, typ, count, raw in entries:
            body += _pack("H", tag, self.little)
            body += _pack("H", typ, self.little)
            body += _pack("I", count, self.little)
            if len(raw) <= 4:
                body += raw + b"\0" * (4 - len(raw))
            else:
                offset = data_at + ifd_size + len(blobs)
                body += _pack("I", offset, self.little)
                blobs += raw
        body += _pack("I", next_ifd, self.little)
        return bytes(body), bytes(blobs)


def _pack_msb(samples: np.ndarray, bits: int) -> bytes:
    acc = 0
    filled = 0
    out = bytearray()
    for sample in samples.reshape(-1):
        acc = (acc << bits) | int(sample)
        filled += bits
        while filled >= 8:
            filled -= 8
            out.append((acc >> filled) & 0xFF)
            acc &= (1 << filled) - 1 if filled else 0
    if filled:
        out.append((acc << (8 - filled)) & 0xFF)
    return bytes(out)


def _pixels(width: int, height: int, value: int) -> np.ndarray:
    return np.full((height, width), value, dtype=np.uint16)


def _write_uncompressed(
    path: Path,
    samples: np.ndarray,
    *,
    little: bool = True,
    make: str = "DJI",
    model: str = "Zenmuse X9",
    unique: str = "Zenmuse X9",
    white: int = 10000,
    forward: np.ndarray | None = None,
    color_matrix: np.ndarray | None = None,
    illuminant: int = 23,
    neutral: tuple[float, float, float] = (1.0, 1.0, 1.0),
    baseline: float = 0.0,
    fps: float = 24.0,
    orientation: int = 1,
    active: tuple[int, int, int, int] | None = None,
    bits: int = 16,
    compression_bytes: bytes | None = None,
    subfile: bool = False,
) -> None:
    height, width = samples.shape
    if compression_bytes is not None:
        pixel_bytes = compression_bytes
    elif bits in (10, 12, 14):
        pixel_bytes = _pack_msb(samples, bits)
    elif bits == 16:
        pixel = bytearray()
        for sample in samples.reshape(-1):
            pixel += _pack("H", int(sample), little)
        pixel_bytes = bytes(pixel)
    else:
        raise AssertionError("bits")
    tiff = _Tiff(little)
    tiff.add_short(256, [width])
    tiff.add_short(257, [height])
    tiff.add_short(258, [bits])
    tiff.add_short(259, [1 if compression_bytes is None else 7])
    tiff.add_short(262, [32803])
    tiff.add_short(277, [1])
    tiff.add_short(274, [orientation])
    tiff.add_long(278, [height])
    tiff.add_short(33421, [2, 2])
    tiff.add_byte(33422, bytes((0, 1, 1, 2)))
    tiff.add_short(50714, [0])
    tiff.add_long(50717, [white])
    tiff.add_rational(50728, list(neutral))
    tiff.add_rational(50730, [baseline], signed=True)
    tiff.add_rational(51044, [fps])
    tiff.add_ascii(271, make)
    tiff.add_ascii(272, model)
    tiff.add_ascii(50708, unique)
    tiff.add_short(50778, [illuminant])
    if forward is not None:
        tiff.add_rational(50964, [float(v) for v in forward.reshape(-1)])
    if color_matrix is not None:
        tiff.add_rational(50721, [float(v) for v in color_matrix.reshape(-1)])
    if active is not None:
        tiff.add_long(50829, list(active))
    header = bytearray(b"II" if little else b"MM")
    header += _pack("H", 42, little)
    # pixel, then ifd, then blobs. Strip offset filled after we know it.
    # Placeholder: pixel starts at 8.
    pixel_at = 8
    ifd_at = pixel_at + len(pixel_bytes)
    ifd, blobs = tiff.ifd_bytes(ifd_at)
    # Strip offset and count must be inside the IFD. Add them before packing
    # by rebuilding with the known pixel offset.
    tiff.add_long(273, [pixel_at])
    tiff.add_long(279, [len(pixel_bytes)])
    ifd, blobs = tiff.ifd_bytes(ifd_at)
    header += _pack("I", ifd_at, little)
    raw_ifd = bytes(header) + pixel_bytes + ifd + blobs
    if not subfile:
        path.write_bytes(raw_ifd)
        return
    # Wrap the raw IFD as a SubIFD of a non-CFA preview so the reader must
    # walk tag 330, which is how many CinemaDNG files store the Bayer frame.
    preview = _Tiff(little)
    preview.add_short(256, [2])
    preview.add_short(257, [2])
    preview.add_short(258, [8])
    preview.add_short(259, [1])
    preview.add_short(262, [2])
    preview.add_short(277, [3])
    preview_pixels = b"\0" * 12
    # File: header, preview pixels, raw blob, preview IFD, raw IFD.
    # The raw blob is a TIFF of its own only if offsets inside it are absolute.
    # Rebuild the raw IFD with absolute offsets by appending it after the preview.
    preview_at = 8
    raw_at = preview_at + len(preview_pixels)
    raw_tiff = _Tiff(little)
    # Copy entries except strip offsets, then set absolute strip offset.
    for tag, typ, count, raw in tiff.entries:
        if tag in (273, 279):
            continue
        raw_tiff.entries.append((tag, typ, count, raw))
    raw_pixel_at = raw_at
    # We don't yet know the raw IFD size. Place raw pixels first, then both IFDs.
    # Simpler layout:
    # [header][preview pixels][raw pixels][preview IFD][raw IFD + blobs]
    raw_pixel_at = 8 + len(preview_pixels)
    preview_ifd_at = raw_pixel_at + len(pixel_bytes)
    # Estimate raw IFD position after preview IFD. Preview IFD size depends on
    # the SubIFD pointer, which is a LONG (inline).
    preview.add_long(330, [0])  # patched below
    preview.add_long(273, [preview_at])
    preview.add_long(279, [len(preview_pixels)])
    preview_ifd, preview_blobs = preview.ifd_bytes(preview_ifd_at)
    raw_ifd_at = preview_ifd_at + len(preview_ifd) + len(preview_blobs)
    raw_tiff.add_long(273, [raw_pixel_at])
    raw_tiff.add_long(279, [len(pixel_bytes)])
    raw_ifd_bytes, raw_blobs = raw_tiff.ifd_bytes(raw_ifd_at)
    # Patch SubIFD offset (the inline LONG for tag 330).
    preview.entries = [entry for entry in preview.entries if entry[0] != 330]
    preview.add_long(330, [raw_ifd_at])
    preview_ifd, preview_blobs = preview.ifd_bytes(preview_ifd_at)
    raw_ifd_at = preview_ifd_at + len(preview_ifd) + len(preview_blobs)
    raw_tiff.entries = [entry for entry in raw_tiff.entries if entry[0] not in (273, 279)]
    raw_tiff.add_long(273, [raw_pixel_at])
    raw_tiff.add_long(279, [len(pixel_bytes)])
    raw_ifd_bytes, raw_blobs = raw_tiff.ifd_bytes(raw_ifd_at)
    blob = bytearray(b"II" if little else b"MM")
    blob += _pack("H", 42, little)
    blob += _pack("I", preview_ifd_at, little)
    blob += preview_pixels
    blob += pixel_bytes
    blob += preview_ifd + preview_blobs
    blob += raw_ifd_bytes + raw_blobs
    path.write_bytes(bytes(blob))


def _ashift(value: int) -> int:
    return value >> 1 if value >= 0 else -((-value) >> 1)


def _predict(which: int, left: int, above: int, upper_left: int) -> int:
    if which == 1:
        return left
    if which == 2:
        return above
    if which == 3:
        return upper_left
    if which == 4:
        return left + above - upper_left
    if which == 5:
        return left + _ashift(above - upper_left)
    if which == 6:
        return above + _ashift(left - upper_left)
    if which == 7:
        return _ashift(left + above)
    return left


def _split_diff(diff: int) -> tuple[int, int]:
    if diff == 0:
        return 0, 0
    magnitude = abs(diff)
    category = magnitude.bit_length()
    if diff > 0:
        return category, diff
    return category, diff + (1 << category) - 1


class _Bits:
    def __init__(self) -> None:
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def write(self, value: int, nbits: int) -> None:
        for shift in range(nbits - 1, -1, -1):
            self.acc = (self.acc << 1) | ((value >> shift) & 1)
            self.n += 1
            if self.n == 8:
                self._emit(self.acc)
                self.acc = 0
                self.n = 0

    def _emit(self, byte: int) -> None:
        self.out.append(byte & 0xFF)
        if byte == 0xFF:
            self.out.append(0x00)

    def align_ones(self) -> None:
        if self.n:
            pad = 8 - self.n
            self.acc = (self.acc << pad) | ((1 << pad) - 1)
            self._emit(self.acc)
            self.acc = 0
            self.n = 0


def _lossless_jpeg(samples: np.ndarray, *, predictor: int = 1, restart: int = 0) -> bytes:
    height, width = samples.shape
    flat = [int(v) for v in samples.reshape(-1)]
    precision = 16
    point = 0
    modulus = 1 << (precision - point)
    init = 1 << (precision - point - 1)
    above = [0] * width
    current = [0] * width
    left = 0
    since = 0
    bits = _Bits()
    restart_id = 0
    for y in range(height):
        for x in range(width):
            if since == 0:
                base = init
            elif x == 0:
                base = above[x]
            elif y == 0:
                base = left
            else:
                base = _predict(predictor, left, above[x], above[x - 1])
            sample = flat[y * width + x]
            diff = (sample - base) % modulus
            if diff >= modulus // 2:
                diff -= modulus
            category, extra = _split_diff(diff)
            bits.write(category, 8)
            if category:
                bits.write(extra, category)
            value = base + diff
            value %= modulus
            if value < 0:
                value += modulus
            left = value
            current[x] = value
            since += 1
            if restart and since == restart:
                bits.align_ones()
                bits.out.append(0xFF)
                bits.out.append(0xD0 + (restart_id & 7))
                restart_id += 1
                since = 0
        above = current[:]
        current = [0] * width
    bits.align_ones()
    counts = [0] * 16
    counts[7] = 17
    dht = bytearray()
    dht += b"\xff\xc4"
    # length, table id, 16 counts, 17 symbols
    payload = bytes([0x00]) + bytes(counts) + bytes(range(17))
    dht += struct.pack(">H", 2 + len(payload))
    dht += payload
    sof = bytearray(b"\xff\xc3")
    sof += struct.pack(">H", 11)
    sof += bytes([precision])
    sof += struct.pack(">HH", height, width)
    sof += bytes([1, 1, 0x11, 0])
    sos = bytearray(b"\xff\xda")
    sos += struct.pack(">H", 8)
    sos += bytes([1, 1, 0, predictor & 0xFF, 0, point & 0x0F])
    dri = b""
    if restart:
        dri = b"\xff\xdd" + struct.pack(">H", 4) + struct.pack(">H", restart)
    return bytes(b"\xff\xd8" + dht + sof + dri + sos + bytes(bits.out) + b"\xff\xd9")


def _dji_file(path: Path, samples: np.ndarray, **kwargs: object) -> None:
    forward = kwargs.pop("forward", _forward_matrix_to_d50_white())
    _write_uncompressed(path, samples, forward=forward, **kwargs)  # type: ignore[arg-type]


def test_library_is_small(cinemadng_library: Path) -> None:
    size = cinemadng_library.stat().st_size
    assert size < 200_000, size


def test_decode_dji_neutral_grey(cinemadng_library: Path, tmp_path: Path) -> None:
    path = tmp_path / "dji.0001.dng"
    _dji_file(path, _pixels(8, 8, 1800))
    rgb = read_image(str(path))
    expected = 0.18 * (2.0**DJI_EXPOSURE_STOPS)
    assert rgb.shape == (8, 8, 3)
    assert rgb[4, 4] == pytest.approx([expected, expected, expected], abs=2e-4)
    assert probe_pixel_colorspace(str(path)) == LINEAR_P3_D60
    meta = probe_meta(str(path))
    assert meta.make == "DJI"
    assert meta.frame_rate == pytest.approx(24.0)


def test_decode_generic_is_aces(cinemadng_library: Path, tmp_path: Path) -> None:
    path = tmp_path / "other.0001.dng"
    _dji_file(path, _pixels(8, 8, 1800), make="Acme", model="Cam", unique="Cam")
    rgb = read_image(str(path))
    assert probe_pixel_colorspace(str(path)) == ACES2065_1
    assert rgb[4, 4] == pytest.approx([0.18, 0.18, 0.18], abs=2e-4)


def test_uncompressed_matches_lossless_jpeg(cinemadng_library: Path, tmp_path: Path) -> None:
    gradient = np.arange(64, dtype=np.uint16).reshape(8, 8) * 100 + 1000
    plain = tmp_path / "plain.dng"
    jpeg = tmp_path / "jpeg.dng"
    _dji_file(plain, gradient, make="Acme", model="Cam", unique="Cam")
    for predictor in (1, 6):
        blob = _lossless_jpeg(gradient, predictor=predictor, restart=8)
        _write_uncompressed(
            jpeg,
            gradient,
            make="Acme",
            model="Cam",
            unique="Cam",
            forward=_forward_matrix_to_d50_white(),
            compression_bytes=blob,
            bits=16,
        )
        raw_plain, _meta = decode_mosaic(str(plain))
        raw_jpeg, meta = decode_mosaic(str(jpeg))
        assert meta.compression == 7
        assert raw_jpeg == pytest.approx(raw_plain, abs=1e-6)


def test_subifd_bayer_and_orientation(cinemadng_library: Path, tmp_path: Path) -> None:
    path = tmp_path / "sub.dng"
    _dji_file(path, _pixels(6, 4, 1800), orientation=6, subfile=True)
    meta = probe_meta(str(path))
    assert meta.width == 6
    assert meta.height == 4
    assert meta.orientation == 6
    rgb = read_image(str(path))
    assert rgb.shape == (6, 4, 3)


def test_packed_12bit(cinemadng_library: Path, tmp_path: Path) -> None:
    path = tmp_path / "pack.dng"
    samples = np.array([[0x100, 0x200], [0x300, 0x3FF]], dtype=np.uint16)
    _dji_file(path, samples, bits=12, white=4095, make="Acme", model="Cam", unique="Cam")
    mosaic, meta = decode_mosaic(str(path))
    assert meta.bits == 12
    assert mosaic[0, 0] == pytest.approx(0x100 / 4095, abs=1e-6)
    assert mosaic[1, 1] == pytest.approx(0x3FF / 4095, abs=1e-6)


def test_color_matrix_matches_forward_matrix() -> None:
    white = _d50_white()
    forward = np.diag(white)
    color_matrix = np.linalg.inv(forward)
    mosaic = np.full((8, 8), 0.18, dtype=np.float32)
    via_forward, space_a = develop(mosaic, _meta(make="Acme", model="Cam", unique_model="Cam"))
    via_matrix, space_b = develop(
        mosaic,
        _meta(
            make="Acme",
            model="Cam",
            unique_model="Cam",
            has_forward=False,
            has_color_matrix=True,
            illuminant=23,
            color_matrix=tuple(float(v) for v in color_matrix.reshape(-1)),
        ),
    )
    assert space_a == space_b == ACES2065_1
    assert via_matrix[4, 4] == pytest.approx(via_forward[4, 4], abs=1e-5)


def test_big_endian_frame(cinemadng_library: Path, tmp_path: Path) -> None:
    path = tmp_path / "be.dng"
    _dji_file(path, _pixels(4, 4, 1800), little=False, make="Acme", model="Cam", unique="Cam")
    rgb = read_image(str(path))
    assert rgb[1, 1] == pytest.approx([0.18, 0.18, 0.18], abs=2e-4)


def test_two_frame_sequence_probe(cinemadng_library: Path, tmp_path: Path) -> None:
    folder = tmp_path / "shot"
    folder.mkdir()
    for frame in (1001, 1002):
        _dji_file(folder / f"shot.{frame}.dng", _pixels(4, 4, 1800))
    paths, name = find_exr_sequence(str(folder))
    assert name == "shot"
    assert len(paths) == 2
    assert ".dng" in IMAGE_SEQUENCE_EXTS
    assert is_scene_referred_image_ext(".dng")
    width, height, fps, count = probe_video(str(folder))
    assert (width, height, count) == (4, 4, 2)
    assert fps == pytest.approx(24.0)
    assert guess_video_colorspace_candidates(str(paths[0])) == [LINEAR_P3_D60]
    with open_ingest_source(folder) as src:
        frames = list(src.iter_frames())
    assert len(frames) == 2
    assert frames[0][2]["exrconverter:cinemadng:colorspace"] == LINEAR_P3_D60
    assert float(frames[0][2]["exrconverter:cinemadng:exposure_scale"]) == pytest.approx(
        2.0**DJI_EXPOSURE_STOPS, abs=1e-4
    )
