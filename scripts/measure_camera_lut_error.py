#!/usr/bin/env python3
"""Report max and mean error for the bundled camera log formulas.

Analytic rows always run: each added curve is compared to the published
equation (or, for Blackmagic Gen 4, to the fitted LogCamera parameters) on a
dense code grid. Vendor rows run when ``VENDOR_LUT_DIR`` or ``/tmp/vendor-luts``
contains the official files. Those files are not part of this repo.

A Rec.709 cube is a viewing transform. Matching it would mean baking the
vendor's tone map into the scene-linear input transform, so those rows are
reported and are not expected to be near zero.

    uv run python scripts/measure_camera_lut_error.py
    VENDOR_LUT_DIR=/tmp/vendor-luts uv run python scripts/measure_camera_lut_error.py
"""

from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import PyOpenColorIO as OCIO

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "resources" / "ocio" / "aces-studio-v4.ocio"

# Float32 OCIO vs the published math. A wrong constant is orders larger.
_FORMULA_TOL = 5e-5
_NLOG_KNOT_TOL = 2e-5
_NLOG_MID_TOL = 2e-4


@dataclass(frozen=True)
class ErrorRow:
    """One comparison. ``tolerance`` is None when the row is report-only."""

    name: str
    kind: str
    samples: int
    max_abs: float
    mean_abs: float
    tolerance: float | None
    note: str


def nlog_to_linear(code: float) -> float:
    """Nikon N-Log specification v1.0.0. ``code`` is full-range 0–1."""
    ten_bit = code * 1023.0
    if ten_bit < 452.0:
        return (ten_bit / 650.0) ** 3 - 0.0075
    return math.exp((ten_bit - 619.0) / 150.0)


def flog_to_linear(code: float) -> float:
    """Fujifilm F-Log data sheet Ver.1.2 / IDT CTL v1.00 constants."""
    if code > 0.100537775223865:
        return (10.0 ** ((code - 0.790453) / 0.344676) - 0.009468) / 0.555556
    return (code - 0.092864) / 8.735631


def flog2_to_linear(code: float) -> float:
    """Fujifilm F-Log2 / F-Log2 C log segment (CLF v1.10, no explicit toe slope)."""
    return (10.0 ** ((code - 0.384316) / 0.245281) - 0.064829) / 5.555556


def flog2_datasheet_to_linear(code: float) -> float:
    """F-Log2 data sheet Ver.1.1, including the published linear toe."""
    cut2 = 8.799461 * 0.000889 + 0.092864
    if code < cut2:
        return (code - 0.092864) / 8.799461
    return flog2_to_linear(code)


def llog_to_linear(code: float) -> float:
    """Leica L-Log reference manual."""
    # Log piece meets the linear piece at reflectance 0.006 → code 0.138.
    if code < 8.0 * 0.006 + 0.09:
        return (code - 0.09) / 8.0
    return (10.0 ** ((code - 0.6) / 0.27) - 0.0115) / 1.3


def slog_to_linear(code: float, *, slog2: bool) -> float:
    """Sony S-Log / S-Log2 white paper, legal range stored in a full-range word."""
    slope = 0.432699 * (876.0 / 1023.0)
    offset = (0.616596 + 0.03) * (876.0 / 1023.0) + 64.0 / 1023.0
    exposure = 10.0 ** ((code - offset) / slope) - 0.037584
    sensor = exposure / (155.0 / 219.0) if slog2 else exposure
    return sensor * 0.9


def canon_log_to_linear(code: float) -> float:
    """Canon Log (2012 / later legal-range constants are the same curve)."""
    sensor = (10.0 ** ((code - 0.12512248) / 0.45310179) - 1.0) / 10.1596
    return sensor * 0.9


def canon_log2_to_linear(code: float) -> float:
    """Canon Log 2 v1.2 highlight formula (18% is above the toe)."""
    sensor = (10.0 ** ((code - 0.092864125) / 0.24136077) - 1.0) / 87.09937546
    return sensor * 0.9


def canon_log3_to_linear(code: float) -> float:
    """Canon Log 3 v1.2 highlight formula (18% is above the toe)."""
    sensor = (10.0 ** ((code - 0.12240537) / 0.36726845) - 1.0) / 14.98325
    return sensor * 0.9


def kinelog3_to_linear(code: float) -> float:
    """Kinefinity KineLOG3 technical specification."""
    if code >= 0.0:
        return (10.0 ** ((code - 0.092864) / (0.296 * 0.907136)) - 1.0) / 66.64
    return code * 0.017178 - 0.008239


def log_base_to_linear(code: float, base: float) -> float:
    """GoPro ``(base**code - 1) / (base - 1)``."""
    return (base**code - 1.0) / (base - 1.0)


def bmd_to_linear(
    code: float,
    *,
    slope: float,
    offset: float,
    lin_offset: float,
    lin_break: float,
    linear_slope: float,
) -> float:
    """LogCamera decode (natural log) matching the fitted Gen 4 parameters."""
    log_break = slope * math.log(lin_break + lin_offset) + offset
    if code >= log_break:
        return math.exp((code - offset) / slope) - lin_offset
    return (code - log_break) / linear_slope + lin_break


def _codes(count: int, start: float = 0.0, stop: float = 1.0) -> np.ndarray:
    return np.linspace(start, stop, count)


def _apply(processor: OCIO.CPUProcessor, rgb: np.ndarray) -> np.ndarray:
    buf = np.ascontiguousarray(rgb, dtype=np.float32)
    processor.apply(OCIO.PackedImageDesc(buf, buf.shape[0], 1, 3))
    return buf


def _curve_cpu(config: OCIO.Config, name: str) -> OCIO.CPUProcessor:
    named = config.getNamedTransform(name)
    if named is None:
        raise KeyError(name)
    group = OCIO.GroupTransform()
    group.appendTransform(named.getTransform(OCIO.TRANSFORM_DIR_FORWARD))
    return config.getProcessor(group).getDefaultCPUProcessor()


def _space_cpu(config: OCIO.Config, src: str, dst: str) -> OCIO.CPUProcessor:
    return config.getProcessor(src, dst).getDefaultCPUProcessor()


def _grey(processor: OCIO.CPUProcessor, codes: np.ndarray) -> np.ndarray:
    rgb = np.repeat(codes.astype(np.float64)[:, None], 3, axis=1)
    out = _apply(processor, rgb)
    return out[:, 0].astype(np.float64)


def _stats(err: np.ndarray) -> tuple[float, float]:
    absolute = np.abs(np.asarray(err, dtype=np.float64))
    if absolute.size == 0:
        return 0.0, 0.0
    return float(absolute.max()), float(absolute.mean())


def _row(
    name: str,
    kind: str,
    err: np.ndarray,
    *,
    tolerance: float | None,
    note: str,
) -> ErrorRow:
    max_abs, mean_abs = _stats(err)
    return ErrorRow(name, kind, int(np.size(err)), max_abs, mean_abs, tolerance, note)


def _vector_decode(fn, codes: np.ndarray) -> np.ndarray:
    return np.array([fn(float(code)) for code in codes], dtype=np.float64)


def measure_formulas(config: OCIO.Config) -> list[ErrorRow]:
    """Compare OCIO curves to published (or fitted) equations. No vendor files."""
    rows: list[ErrorRow] = []
    grid = _codes(4097)

    def curve_vs(
        name: str,
        fn,
        *,
        tolerance: float,
        note: str,
        codes: np.ndarray = grid,
        kind: str = "published",
        space: str | None = None,
    ) -> None:
        processor = (
            _space_cpu(config, space, "ACES2065-1")
            if space is not None
            else _curve_cpu(config, name)
        )
        got = _grey(processor, codes)
        expect = _vector_decode(fn, codes)
        rows.append(_row(name, kind, got - expect, tolerance=tolerance, note=note))

    knots = np.arange(4096, dtype=np.float64) / 4095.0
    curve_vs(
        "N-Log - Curve",
        nlog_to_linear,
        tolerance=_NLOG_KNOT_TOL,
        note="spi1d sample points vs Nikon spec v1.0.0",
        codes=knots,
    )
    mids = (np.arange(4095, dtype=np.float64) + 0.5) / 4095.0
    curve_vs(
        "N-Log - Curve",
        nlog_to_linear,
        tolerance=_NLOG_MID_TOL,
        note="linear interpolation between spi1d samples",
        codes=mids,
    )
    curve_vs(
        "F-Log - Curve",
        flog_to_linear,
        tolerance=_FORMULA_TOL,
        note="Fujifilm IDT CTL v1.00 curve",
    )
    flog2_break = 0.245281 * math.log10(5.555556 * 0.000889 + 0.064829) + 0.384316
    flog2_log = grid[grid >= flog2_break]
    curve_vs(
        "F-Log2 - Curve",
        flog2_to_linear,
        tolerance=3e-4,
        note=(
            f"CLF v1.10 log segment above code {flog2_break:.4f}; "
            "max is float32 at code 1 (linear ~58)"
        ),
        codes=flog2_log,
    )
    curve_vs(
        "F-Log2C - Curve",
        flog2_to_linear,
        tolerance=3e-4,
        note="same log segment as F-Log2; F-Gamut C is the matrix",
        codes=flog2_log,
    )
    toe = _codes(2049, 0.0, 8.799461 * 0.000889 + 0.092864)
    got_toe = _grey(_curve_cpu(config, "F-Log2 - Curve"), toe)
    rows.append(
        _row(
            "F-Log2 - Curve",
            "published",
            got_toe - _vector_decode(flog2_datasheet_to_linear, toe),
            tolerance=5e-4,
            note="data-sheet linear slope vs CLF continuous toe (code 0 only)",
        )
    )
    llog_cut = 8.0 * 0.006 + 0.09
    curve_vs(
        "L-Log - Curve",
        llog_to_linear,
        tolerance=1e-4,
        note="Leica log piece (reflectance ≥ 0.006). Max is float32 near code 1",
        codes=grid[grid >= llog_cut],
    )
    curve_vs(
        "L-Log - Curve",
        llog_to_linear,
        tolerance=2e-4,
        note=(
            "linear toe. The manual's two pieces miss by ~0.0009 code; "
            "OCIO joins them, absolute error ~1.1e-4"
        ),
        codes=grid[grid < llog_cut],
    )
    curve_vs(
        "S-Log - Curve",
        lambda code: slog_to_linear(code, slog2=False),
        tolerance=_FORMULA_TOL,
        note="Sony S-Log white paper through code 0 (log argument stays positive)",
    )
    curve_vs(
        "S-Log2 - Curve",
        lambda code: slog_to_linear(code, slog2=True),
        tolerance=_FORMULA_TOL,
        note="Sony S-Log2 technical paper",
    )
    curve_vs(
        "Canon Log - Curve",
        canon_log_to_linear,
        tolerance=_FORMULA_TOL,
        note="Canon Log through code 0, including below the 128/1023 black level",
    )
    highlight = _codes(2049, 0.2, 1.0)
    canon_builtin = (
        ("CanonLog2 Rec.2020", canon_log2_to_linear, "OCIO CANON_CLOG2 vs v1.2 highlights"),
        ("CanonLog3 Rec.2020", canon_log3_to_linear, "OCIO CANON_CLOG3 vs v1.2 highlights"),
    )
    for space, fn, note in canon_builtin:
        # Highlight segment only. The builtin toe is not this one-line formula.
        curve_vs(space, fn, tolerance=_FORMULA_TOL, note=note, codes=highlight, space=space)
    curve_vs(
        "KineLOG3 - Curve",
        kinelog3_to_linear,
        tolerance=2e-4,
        note="Kinefinity equation, codes 0–1. Max is float32 near linear ~30",
        codes=grid,
    )
    curve_vs(
        "Protune - Curve",
        lambda code: log_base_to_linear(code, 113.0),
        tolerance=_FORMULA_TOL,
        note="GoPro Protune, log base 113, no exposure gain",
    )
    curve_vs(
        "GP-Log - Curve",
        lambda code: log_base_to_linear(code, 400.0),
        tolerance=_FORMULA_TOL,
        note="GoPro GP-Log, log base 400, no exposure gain",
    )
    curve_vs(
        "GP-Log2 - Curve",
        lambda code: log_base_to_linear(code, 600.0),
        tolerance=_FORMULA_TOL,
        note="GoPro GP-Log2 decode before the +1.8 stop in the matrix",
    )
    gain = 2.0**1.8
    gplog2 = _grey(_space_cpu(config, "GP-Log2 Rec.2020", "ACES2065-1"), grid)
    raw = _vector_decode(lambda code: log_base_to_linear(code, 600.0), grid) * gain
    rows.append(
        _row(
            "GP-Log2 Rec.2020",
            "published",
            gplog2 - raw,
            tolerance=_FORMULA_TOL,
            note="neutral grey: decode × 2^1.8, Rec.2020 row sums are 1",
        )
    )

    bmd = (
        (
            "BMD Broadcast Film Gen4 - Curve",
            0.21566456116952773,
            0.7133134738229736,
            0.03630411093543444,
            0.00500072683168086,
            5.2212906000378565,
        ),
        (
            "BMD Pocket 4K Film Gen4 - Curve",
            0.1703663112023471,
            0.6454296550413368,
            0.03444835397444396,
            0.004958295208669562,
            4.323288448370592,
        ),
        (
            "BMD Pocket 6K Film Gen4 - Curve",
            0.15545874964938466,
            0.6272665887366995,
            0.027941380463157067,
            0.004963316175308281,
            4.724515510884684,
        ),
    )
    for name, slope, offset, lin_offset, lin_break, linear_slope in bmd:

        def _decode(
            code: float,
            *,
            slope: float = slope,
            offset: float = offset,
            lin_offset: float = lin_offset,
            lin_break: float = lin_break,
            linear_slope: float = linear_slope,
        ) -> float:
            return bmd_to_linear(
                code,
                slope=slope,
                offset=offset,
                lin_offset=lin_offset,
                lin_break=lin_break,
                linear_slope=linear_slope,
            )

        curve_vs(
            name,
            _decode,
            tolerance=_FORMULA_TOL,
            kind="fitted",
            note="OCIO vs the fitted LogCamera parameters (spi1d residual is a separate row)",
        )
    return rows


def _file_cpu(path: Path) -> OCIO.CPUProcessor:
    file_transform = OCIO.FileTransform()
    file_transform.setSrc(str(path))
    file_transform.setInterpolation(OCIO.INTERP_LINEAR)
    return OCIO.Config.CreateRaw().getProcessor(file_transform).getDefaultCPUProcessor()


def _lattice(size: int) -> np.ndarray:
    """RGB lattice, red fastest, matching Resolve / Adobe ``.cube`` order."""
    codes = np.linspace(0.0, 1.0, size)
    blue, green, red = np.meshgrid(codes, codes, codes, indexing="ij")
    return np.stack([red, green, blue], axis=-1).reshape(-1, 3)


def load_cube(path: Path) -> tuple[int, np.ndarray]:
    size = 0
    values: list[tuple[float, float, float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.upper().startswith("TITLE"):
            continue
        upper = stripped.upper()
        if upper.startswith("LUT_3D_SIZE"):
            size = int(stripped.split()[-1])
            continue
        if upper.startswith(("LUT_", "DOMAIN_")):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        try:
            values.append((float(parts[0]), float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    array = np.asarray(values, dtype=np.float64)
    if size < 2 or array.shape != (size**3, 3):
        raise ValueError(f"{path} is not a {size}^3 cube ({array.shape})")
    return size, array


def _rgb_grid(count: int = 17) -> np.ndarray:
    return _lattice(count)


def _compare_cpu(left: OCIO.CPUProcessor, right: OCIO.CPUProcessor, rgb: np.ndarray) -> np.ndarray:
    return _apply(left, rgb).astype(np.float64) - _apply(right, rgb).astype(np.float64)


def _parse_clf_matrix(path: Path) -> np.ndarray:
    root = ET.parse(path).getroot()
    arrays = [node for node in root.iter() if node.tag.endswith("Array")]
    if not arrays or arrays[0].text is None:
        raise ValueError(f"{path} has no matrix")
    values = [float(part) for part in arrays[0].text.split()]
    return np.asarray(values, dtype=np.float64).reshape(3, 3)


def measure_vendor(config: OCIO.Config, root: Path) -> list[ErrorRow]:
    """Compare formulas to official files under ``root``. Missing files are skipped."""
    rows: list[ErrorRow] = []
    rgb = _rgb_grid(17)
    grey_codes = _codes(4097)

    clf_pairs = (
        ("F-Log2 F-Gamut", root / "fuji" / "FUJIFILM_IDT_F-Log2_Ver.1.10.clf"),
        ("F-Log2C F-Gamut C", root / "fuji" / "FUJIFILM_IDT_F-Log2C_Ver.1.10.clf"),
    )
    for space, path in clf_pairs:
        if not path.is_file():
            continue
        err = _compare_cpu(_space_cpu(config, space, "ACES2065-1"), _file_cpu(path), rgb)
        matrix = _parse_clf_matrix(path)
        rows.append(
            _row(
                space,
                "vendor-idt",
                err,
                tolerance=1e-6,
                note=f"every 17³ sample vs {path.name}; matrix row0 {matrix[0, 0]:.6f}",
            )
        )

    ctl = root / "fuji" / "FUJIFILM_IDT_F-Log_Ver.1.00.ctl"
    if ctl.is_file():
        got = _apply(_space_cpu(config, "F-Log F-Gamut", "ACES2065-1"), rgb).astype(np.float64)
        linear = np.vectorize(flog_to_linear)(rgb)
        matrix = np.array(
            [
                [0.70225442114640, 0.18334266960590, 0.11440290924770],
                [-0.07108200261915, 1.05471859084908, 0.01636341177007],
                [-0.12854056609733, 0.07603368101636, 1.05250688508097],
            ]
        )
        expect = linear @ matrix.T
        rows.append(
            _row(
                "F-Log F-Gamut",
                "vendor-idt",
                got - expect,
                tolerance=5e-5,
                note="17³ vs Fujifilm IDT CTL v1.00 curve and matrix",
            )
        )

    bmd_dir = root / "bmd" / "extracted" / "Blackmagic Design - Transfer Function LUTs - 2022-04-23"
    bmd_files = (
        ("BMD Broadcast Film Gen4 - Curve", "Broadcast Film Gen 4 - Transfer Function.spi1d", 2e-5),
        ("BMD Pocket 4K Film Gen4 - Curve", "Pocket 4K Film Gen 4 - Transfer Function.spi1d", 2e-4),
        ("BMD Pocket 6K Film Gen4 - Curve", "Pocket 6K Film Gen 4 - Transfer Function.spi1d", 2e-4),
        ("BMD Film Gen 5 (stock)", "Film Gen 5 - Transfer Function.spi1d", None),
    )
    for curve, filename, tolerance in bmd_files:
        path = bmd_dir / filename
        if not path.is_file():
            continue
        if curve.startswith("BMD Film"):
            # Stock Gen 5, reported so the Gen 4 fit method can be compared.
            processor = _space_cpu(config, "BMDFilm WideGamut Gen5", "ACES2065-1")
            kind = "viewing-lut"
            note = "stock Gen 5 vs Resolve-exported spi1d (method check, space unchanged)"
        else:
            processor = _curve_cpu(config, curve)
            kind = "fitted"
            note = "fitted curve vs Resolve-exported spi1d (Vegdahl 2022-04-23), every sample"
        err = _grey(processor, grey_codes) - _grey(_file_cpu(path), grey_codes)
        ref = _grey(_file_cpu(path), grey_codes)
        rel = np.abs(err) / np.maximum(np.abs(ref), 1e-3)
        rows.append(
            _row(
                curve,
                kind,
                err,
                tolerance=tolerance,
                note=f"{note}; max rel above 1e-3 = {float(rel.max()):.3e}",
            )
        )

    nikon_cubes = (
        root / "nikon" / "v2" / "3DLUT" / "N-Log_BT2020_to_REC709_BT1886_size_33.cube",
        root / "nikon" / "v1" / "3DLUT" / "Z_6_N-Log-Full_to_REC709-Full_33_V01-00.cube",
        root / "nikon" / "v1" / "3DLUT" / "Z_9_N-Log-Full_to_REC709-Full_33_V02-00.cube",
    )
    display = None
    for path in nikon_cubes:
        if not path.is_file():
            continue
        if display is None:
            display = _space_cpu(config, "N-Log N-Gamut", "Gamma 2.4 Encoded Rec.709")
        size, lut = load_cube(path)
        lattice = _lattice(size).astype(np.float32)
        predicted = _apply(display, lattice).astype(np.float64)
        err = predicted - lut
        rows.append(
            _row(
                "N-Log N-Gamut",
                "viewing-lut",
                err,
                tolerance=None,
                note=(
                    f"full {size}³ vs {path.name}: IDT + BT.1886, no Nikon tonemap. "
                    "Large error is highlight rolloff, not the N-Log equation"
                ),
            )
        )
        scene = _grey(_curve_cpu(config, "N-Log - Curve"), np.linspace(0.0, 1.0, size))
        axis = np.arange(size)
        grey_lut = lut.reshape(size, size, size, 3)[axis, axis, axis]
        grey_pred = predicted.reshape(size, size, size, 3)[axis, axis, axis]
        mask = (scene > 0.0) & (scene < 1.2)
        rows.append(
            _row(
                "N-Log N-Gamut",
                "viewing-lut",
                (grey_pred - grey_lut)[mask],
                tolerance=None,
                note=f"grey axis of {path.name} where scene linear is in (0, 1.2)",
            )
        )

    tech = (
        root
        / "fuji"
        / "xh2s"
        / "x-h2s-3d-lut-v100"
        / "F-Log"
        / "XH2S_FLog_FGamut_to_FLog_BT.709_33grid_V.1.00.cube"
    )
    if tech.is_file():
        size, lut = load_cube(tech)
        lattice = _lattice(size)
        grey_index = np.arange(size) * (1 + size + size * size)
        rows.append(
            _row(
                "F-Log F-Gamut",
                "viewing-lut",
                lut[grey_index] - lattice[grey_index],
                tolerance=1e-3,
                note="X-H2S F-Log→F-Log BT.709 cube, grey axis vs identity (neutrals kept)",
            )
        )
        to_lin = _space_cpu(config, "F-Log F-Gamut", "Linear Rec.709 (sRGB)")
        linear = _apply(to_lin, lattice).astype(np.float64)
        toe = linear < 0.00089
        reencoded = np.empty_like(linear)
        reencoded[toe] = 8.735631 * linear[toe] + 0.092864
        above = ~toe
        reencoded[above] = 0.344676 * np.log10(0.555556 * linear[above] + 0.009468) + 0.790453
        rows.append(
            _row(
                "F-Log F-Gamut",
                "viewing-lut",
                reencoded - lut,
                tolerance=None,
                note=(
                    "full 33³ vs F-Log→F-Log BT.709 after the IDT matrix. "
                    "Not a 3×3; this cube is not the ACES IDT"
                ),
            )
        )
        in_gamut = np.all(linear >= 0.0, axis=1) & np.all((lut >= 0.0) & (lut <= 1.0), axis=1)
        rows.append(
            _row(
                "F-Log F-Gamut",
                "viewing-lut",
                (reencoded - lut)[in_gamut],
                tolerance=None,
                note="same cube, samples whose Rec.709 linear is non-negative",
            )
        )

    eterna = (
        root
        / "fuji"
        / "xh2s"
        / "x-h2s-3d-lut-v100"
        / "F-Log"
        / "XH2S_FLog_FGamut_to_ETERNA_BT.709_33grid_V.1.00.cube"
    )
    if eterna.is_file():
        size, lut = load_cube(eterna)
        grey_index = np.arange(size) * (1 + size + size * size)
        codes = np.linspace(0.0, 1.0, size)
        grey_in = np.repeat(codes[:, None], 3, axis=1)
        rows.append(
            _row(
                "F-Log F-Gamut",
                "viewing-lut",
                lut[grey_index] - grey_in,
                tolerance=None,
                note="ETERNA film-sim cube grey vs identity (a look, not an IDT)",
            )
        )

    dji_a = root / "dji" / "mavic3-dlogm.cube"
    dji_b = root / "dji" / "dlogm-docs.cube"
    if dji_a.is_file() and dji_b.is_file():
        size_a, lut_a = load_cube(dji_a)
        size_b, lut_b = load_cube(dji_b)
        if size_a == size_b:
            rows.append(
                _row(
                    "D-Log M (not shipped)",
                    "viewing-lut",
                    lut_a - lut_b,
                    tolerance=None,
                    note=(
                        "two official D-Log M Rec.709 cubes disagree; both clip code 1 to display 1"
                    ),
                )
            )
            last = lut_a.reshape(size_a, size_a, size_a, 3)[-1, -1, -1]
            rows.append(
                ErrorRow(
                    "D-Log M (not shipped)",
                    "viewing-lut",
                    1,
                    float(np.max(np.abs(last - 1.0))),
                    float(np.mean(np.abs(last - 1.0))),
                    None,
                    (
                        f"Mavic 3 cube at code 1 is {last.tolist()} "
                        "(display-referred, no scene headroom)"
                    ),
                )
            )
    return rows


def format_report(rows: list[ErrorRow]) -> str:
    lines = [
        f"{'name':<36} {'kind':<12} {'n':>7} {'max':>12} {'mean':>12} {'tol':>10}  note",
        "-" * 118,
    ]
    for row in rows:
        tol = "—" if row.tolerance is None else f"{row.tolerance:.0e}"
        lines.append(
            f"{row.name:<36} {row.kind:<12} {row.samples:7d} "
            f"{row.max_abs:12.3e} {row.mean_abs:12.3e} {tol:>10}  {row.note}"
        )
    return "\n".join(lines)


def vendor_root() -> Path | None:
    env = os.environ.get("VENDOR_LUT_DIR")
    if env:
        path = Path(env)
        return path if path.is_dir() else None
    default = Path("/tmp/vendor-luts")
    return default if default.is_dir() else None


def main() -> None:
    config = OCIO.Config.CreateFromFile(str(_CONFIG))
    rows = measure_formulas(config)
    root = vendor_root()
    print(format_report(rows))
    if root is None:
        print("\nVendor LUTs not found. Set VENDOR_LUT_DIR to compare official files.")
        return
    print(f"\nVendor files: {root}")
    print(format_report(measure_vendor(config, root)))


if __name__ == "__main__":
    main()
