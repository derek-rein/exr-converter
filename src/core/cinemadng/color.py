"""Scene-linear colour for CinemaDNG mosaics.

DJI Inspire 3 / Zenmuse X9 frames are tagged Linear P3-D60 after the +1.4
stop exposure DJI recommends. Other CinemaDNG sequences are tagged
ACES2065-1. D-Log and D-Gamut stay in the bundled OCIO config; this module
does not build those curves.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LINEAR_P3_D60 = "Linear P3-D60"
ACES2065_1 = "ACES2065-1"
DJI_EXPOSURE_STOPS = 1.4

# SMPTE RP 431-2 P3 primaries, xy.
_P3_PRIMARIES = ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060))
# ACES AP0 primaries, xy.
_AP0_PRIMARIES = ((0.7347, 0.2653), (0.0, 1.0), (0.0001, -0.0770))
_D60_XY = (0.32168, 0.33767)
_D65_XY = (0.3127, 0.3290)
_D50_XY = (0.34567, 0.35850)

# Bradford cone matrix. The inverse is the numerical inverse so a D65
# check matches the published Linear P3-D65 matrix in the bundled config.
_BRADFORD = np.array(
    [
        [0.8951, 0.2664, -0.1614],
        [-0.7502, 1.7135, 0.0367],
        [0.0389, -0.0685, 1.0296],
    ],
    dtype=np.float64,
)
_BRADFORD_INV = np.linalg.inv(_BRADFORD)

# DNG CalibrationIlluminant / EXIF LightSource to CIE xy.
_ILLUMINANT_XY: dict[int, tuple[float, float]] = {
    1: (0.33242, 0.34743),  # Daylight, treated as D55
    2: (0.37208, 0.37529),  # Fluorescent, CIE F2
    3: (0.44757, 0.40745),  # Tungsten, CIE A
    4: (0.33242, 0.34743),  # Flash, treated as D55
    9: (0.33242, 0.34743),  # Fine weather
    10: (0.31270, 0.32900),  # Cloudy weather
    11: (0.29902, 0.31485),  # Shade
    12: (0.37208, 0.37529),
    13: (0.37208, 0.37529),
    14: (0.37208, 0.37529),
    15: (0.37208, 0.37529),
    17: (0.44757, 0.40745),  # Standard light A
    18: (0.34842, 0.35161),  # Standard light B
    19: (0.31006, 0.31616),  # Standard light C
    20: (0.33242, 0.34743),  # D55
    21: (0.31270, 0.32900),  # D65
    22: (0.29902, 0.31485),  # D75
    23: (0.34567, 0.35850),  # D50
    24: (0.44757, 0.40745),  # ISO studio tungsten
}


@dataclass
class FrameMeta:
    """Header fields the native reader returns for one CinemaDNG frame."""

    width: int
    height: int
    bits: int
    compression: int
    cfa: tuple[int, int, int, int]
    has_forward: bool
    has_color_matrix: bool
    illuminant: int
    orientation: int
    has_neutral: bool
    black: tuple[float, float, float, float]
    white: float
    neutral: tuple[float, float, float]
    color_matrix: tuple[float, ...]
    forward_matrix: tuple[float, ...]
    baseline_exposure: float
    frame_rate: float
    make: str
    model: str
    unique_model: str


def _xy_to_xyz(xy: tuple[float, float]) -> np.ndarray:
    x, y = xy
    if abs(y) < 1e-12:
        return np.array([0.0, 1.0, 0.0], dtype=np.float64)
    return np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=np.float64)


def _rgb_to_xyz(
    primaries: tuple[tuple[float, float], ...], white_xy: tuple[float, float]
) -> np.ndarray:
    columns = [_xy_to_xyz(xy) for xy in primaries]
    basis = np.column_stack(columns)
    scale = np.linalg.solve(basis, _xy_to_xyz(white_xy))
    return basis * scale


def _cat(src_xy: tuple[float, float], dst_xy: tuple[float, float]) -> np.ndarray:
    src = _BRADFORD @ _xy_to_xyz(src_xy)
    dst = _BRADFORD @ _xy_to_xyz(dst_xy)
    scale = dst / src
    return _BRADFORD_INV @ np.diag(scale) @ _BRADFORD


def _xyz_to_rgb(
    primaries: tuple[tuple[float, float], ...], white_xy: tuple[float, float]
) -> np.ndarray:
    return np.linalg.inv(_rgb_to_xyz(primaries, white_xy))


def p3_d60_to_ap0() -> np.ndarray:
    """Linear P3-D60 to ACES2065-1. (1, 1, 1) maps to (1, 1, 1)."""
    xyz_from_p3 = _rgb_to_xyz(_P3_PRIMARIES, _D60_XY)
    ap0_from_xyz = _xyz_to_rgb(_AP0_PRIMARIES, _D60_XY)
    return ap0_from_xyz @ xyz_from_p3


def ap0_to_p3_d65() -> np.ndarray:
    """ACES2065-1 to linear P3-D65, matching the bundled config's direction."""
    xyz_from_ap0 = _rgb_to_xyz(_AP0_PRIMARIES, _D60_XY)
    cat = _cat(_D60_XY, _D65_XY)
    p3_from_xyz = _xyz_to_rgb(_P3_PRIMARIES, _D65_XY)
    return p3_from_xyz @ cat @ xyz_from_ap0


P3_D60_TO_AP0 = p3_d60_to_ap0()
_AP0_FROM_XYZ_D60 = _xyz_to_rgb(_AP0_PRIMARIES, _D60_XY)
_P3_FROM_XYZ_D60 = _xyz_to_rgb(_P3_PRIMARIES, _D60_XY)
_D50_TO_D60 = _cat(_D50_XY, _D60_XY)


def is_dji(meta: FrameMeta) -> bool:
    """True when Make, Model, or UniqueCameraModel names DJI or Zenmuse."""
    blob = " ".join((meta.make, meta.model, meta.unique_model)).lower()
    return "dji" in blob or "zenmuse" in blob


def output_colorspace(meta: FrameMeta) -> str:
    """OCIO name for pixels :func:`develop` returns."""
    if is_dji(meta):
        return LINEAR_P3_D60
    return ACES2065_1


def exposure_scale(meta: FrameMeta) -> float:
    """``2 ** (BaselineExposure + 1.4)`` for DJI, else ``2 ** BaselineExposure``.

    The +1.4 stop is the exposure DJI documents on top of BaselineExposure.
    If a clip's BaselineExposure is already about 1.4, the frame will look
    another 1.4 stops hot.
    """
    stops = float(meta.baseline_exposure)
    if is_dji(meta):
        stops += DJI_EXPOSURE_STOPS
    return float(2.0**stops)


def _as_matrix(values: tuple[float, ...]) -> np.ndarray:
    return np.asarray(values, dtype=np.float64).reshape(3, 3)


def _camera_to_xyz_d50(meta: FrameMeta) -> np.ndarray | None:
    """White-balanced camera RGB to XYZ D50, or None when no matrix is usable."""
    if meta.has_forward:
        return _as_matrix(meta.forward_matrix)
    if not meta.has_color_matrix:
        return None
    color_matrix = _as_matrix(meta.color_matrix)
    try:
        camera_to_illuminant = np.linalg.inv(color_matrix)
    except np.linalg.LinAlgError:
        return None
    src_xy = _ILLUMINANT_XY.get(int(meta.illuminant), _D50_XY)
    return _cat(src_xy, _D50_XY) @ camera_to_illuminant


def _apply_white_balance(
    mosaic: np.ndarray,
    cfa: tuple[int, int, int, int],
    neutral: tuple[float, float, float],
    has_neutral: bool,
) -> np.ndarray:
    balanced = np.array(mosaic, dtype=np.float32, copy=True)
    if not has_neutral:
        return balanced
    gains = []
    for value in neutral:
        if value > 1e-8:
            gains.append(1.0 / float(value))
        else:
            gains.append(1.0)
    for row in range(2):
        for col in range(2):
            channel = int(cfa[row * 2 + col])
            if 0 <= channel <= 2:
                balanced[row::2, col::2] *= np.float32(gains[channel])
    return balanced


def demosaic_bilinear(mosaic: np.ndarray, cfa: tuple[int, int, int, int]) -> np.ndarray:
    """Bilinear Bayer demosaic, one channel at a time.

    Missing samples are the average of the same-channel neighbors in the 3x3
    window. A flat field stays flat, which is what the colour tests check.
    """
    raw = np.ascontiguousarray(mosaic, dtype=np.float32)
    height, width = raw.shape
    out = np.zeros((height, width, 3), dtype=np.float32)
    for channel in range(3):
        samples = np.zeros((height, width), dtype=np.float32)
        mask = np.zeros((height, width), dtype=np.float32)
        for row in range(2):
            for col in range(2):
                if int(cfa[row * 2 + col]) != channel:
                    continue
                samples[row::2, col::2] = raw[row::2, col::2]
                mask[row::2, col::2] = 1.0
        padded_s = np.pad(samples, 1, mode="edge")
        padded_m = np.pad(mask, 1, mode="constant")
        acc = (
            padded_s[:-2, :-2]
            + padded_s[:-2, 1:-1]
            + padded_s[:-2, 2:]
            + padded_s[1:-1, :-2]
            + padded_s[1:-1, 2:]
            + padded_s[2:, :-2]
            + padded_s[2:, 1:-1]
            + padded_s[2:, 2:]
        )
        weight = (
            padded_m[:-2, :-2]
            + padded_m[:-2, 1:-1]
            + padded_m[:-2, 2:]
            + padded_m[1:-1, :-2]
            + padded_m[1:-1, 2:]
            + padded_m[2:, :-2]
            + padded_m[2:, 1:-1]
            + padded_m[2:, 2:]
        )
        missing = mask < 0.5
        usable = missing & (weight > 0)
        samples[usable] = acc[usable] / weight[usable]
        out[:, :, channel] = samples
    return out


def apply_orientation(rgb: np.ndarray, orientation: int) -> np.ndarray:
    """TIFF orientations 1, 3, 6, and 8. Other values are left unchanged."""
    if orientation == 3:
        return np.ascontiguousarray(np.rot90(rgb, 2))
    if orientation == 6:
        return np.ascontiguousarray(np.rot90(rgb, -1))
    if orientation == 8:
        return np.ascontiguousarray(np.rot90(rgb, 1))
    return np.ascontiguousarray(rgb)


def develop(mosaic: np.ndarray, meta: FrameMeta) -> tuple[np.ndarray, str]:
    """Return float32 RGB and the OCIO name those pixels are in.

    Missing colour matrices keep white-balanced camera RGB and still apply
    the exposure. DJI is then Linear P3-D60. Anything else is ACES2065-1.
    """
    cfa = meta.cfa
    balanced = _apply_white_balance(mosaic, cfa, meta.neutral, meta.has_neutral)
    rgb = demosaic_bilinear(balanced, cfa).astype(np.float64, copy=False)
    camera_to_d50 = _camera_to_xyz_d50(meta)
    if camera_to_d50 is not None:
        flat = rgb.reshape(-1, 3)
        xyz_d50 = flat @ camera_to_d50.T
        xyz_d60 = xyz_d50 @ _D50_TO_D60.T
        if is_dji(meta):
            coded = xyz_d60 @ _P3_FROM_XYZ_D60.T
        else:
            coded = xyz_d60 @ _AP0_FROM_XYZ_D60.T
        rgb = coded.reshape(rgb.shape)
    rgb = rgb * exposure_scale(meta)
    oriented = apply_orientation(rgb.astype(np.float32, copy=False), int(meta.orientation))
    return oriented, output_colorspace(meta)


def oriented_size(meta: FrameMeta) -> tuple[int, int]:
    """Width and height after TIFF orientation 6 or 8."""
    if int(meta.orientation) in (6, 8):
        return int(meta.height), int(meta.width)
    return int(meta.width), int(meta.height)
