"""Bundled ACES Studio config: extra camera logs load and decode mid-grey."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import PyOpenColorIO as OCIO
import pytest

from src.core.ocio_utils import get_bundled_aces_studio_path

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "resources" / "ocio" / "aces-studio-v4.ocio"
_NLOG_LUT = _ROOT / "resources" / "ocio" / "luts" / "nlog_to_lin.spi1d"

# Colorspace names that already shipped with the ASWF studio file.
_STOCK = (
    "ACES2065-1",
    "ACEScg",
    "Apple Log",
    "ARRI LogC3 (EI800)",
    "CanonLog2 CinemaGamut D55",
    "CanonLog3 CinemaGamut D55",
    "D-Log D-Gamut",
    "Linear Rec.2020",
    "Log3G10 REDWideGamutRGB",
    "S-Log3 S-Gamut3",
    "V-Log V-Gamut",
)

_ADDED = (
    "N-Log N-Gamut",
    "F-Log F-Gamut",
    "F-Log2 F-Gamut",
    "L-Log Rec.2020",
    "L-Log Rec.709",
    "S-Log S-Gamut",
    "S-Log2 S-Gamut",
    "CanonLog CinemaGamut D55",
    "CanonLog Rec.2020",
    "CanonLog Rec.709",
    "CanonLog2 Rec.2020",
    "CanonLog2 Rec.709",
    "CanonLog3 Rec.2020",
    "CanonLog3 Rec.709",
    "F-Log2C F-Gamut C",
    "BMD Broadcast Film WideGamut Gen4",
    "BMD Pocket 4K Film Gen4",
    "BMD Pocket 6K Film Gen4",
    "KineLOG3 Wide Gamut",
    "Protune Rec.709",
    "GP-Log Rec.709",
    "GP-Log2 Rec.2020",
    "O-Log Rec.2020",
    "Apple Log 2",
)


def _nlog_code(reflectance: float) -> float:
    """Nikon N-Log spec v1.0.0, full-range code / 1023. 0.18 is stop 0."""
    if reflectance < 0.328:
        return 650.0 * (reflectance + 0.0075) ** (1.0 / 3.0) / 1023.0
    return (150.0 * math.log(reflectance) + 619.0) / 1023.0


def _flog_code(reflectance: float, *, flog2: bool) -> float:
    """Fujifilm F-Log / F-Log2 data-sheet encoding (reflectance → full-range code)."""
    if flog2:
        cut, a, b, c, d, e, f = (
            0.000889,
            5.555556,
            0.064829,
            0.245281,
            0.384316,
            8.799461,
            0.092864,
        )
    else:
        cut, a, b, c, d, e, f = (
            0.00089,
            0.555556,
            0.009468,
            0.344676,
            0.790453,
            8.735631,
            0.092864,
        )
    if reflectance < cut:
        return e * reflectance + f
    return c * math.log10(a * reflectance + b) + d


def _llog_code(reflectance: float) -> float:
    """Leica L-Log reference-manual encoding."""
    if reflectance < 0.006:
        return 8.0 * reflectance + 0.09
    return 0.27 * math.log10(1.3 * reflectance + 0.0115) + 0.6


def _sony_code(reflectance: float, *, slog2: bool) -> float:
    """Sony S-Log / S-Log2, legal-range code stored in a full-range 0–1 word."""
    sensor = reflectance / 0.9
    exposure = (155.0 / 219.0) * sensor if slog2 else sensor
    ire = 0.432699 * math.log10(exposure + 0.037584) + 0.646596
    return ire * (876.0 / 1023.0) + 64.0 / 1023.0


def _canon_log_code(reflectance: float) -> float:
    """Canon Log (2012 curve / later legal-range constants are the same)."""
    sensor = reflectance / 0.9
    return 0.45310179 * math.log10(10.1596 * sensor + 1.0) + 0.12512248


def _canon_log2_code(reflectance: float) -> float:
    """Canon Log 2 v1.2, matching OCIO ``CURVE - CANON_CLOG2_to_LINEAR``."""
    sensor = reflectance / 0.9
    return 0.24136077 * math.log10(sensor * 87.09937546 + 1.0) + 0.092864125


def _kinelog3_code(reflectance: float) -> float:
    """Kinefinity KineLOG3 technical specification."""
    return math.log10(66.64 * reflectance + 1.0) * 0.296 * 0.907136 + 0.092864


def _log_base_code(reflectance: float, base: float) -> float:
    """GoPro-style ``log(reflectance * (base - 1) + 1) / log(base)``."""
    return math.log(reflectance * (base - 1.0) + 1.0) / math.log(base)


def _olog_code(reflectance: float) -> float:
    """OPPO O-Log white paper section 3.1. ``reflectance`` 0.18 is 18%."""
    return 0.139 * math.log(reflectance + 0.019) + 0.614


def _apple_log_code(reflectance: float) -> float:
    """Apple Log encoding (18% is above the parabolic toe). Apple Log 2 uses it too."""
    return 0.08550479 * math.log2(reflectance + 0.00964052) + 0.69336945


def _canon_log3_code(reflectance: float) -> float:
    """Canon Log 3 v1.2 highlight segment (18% is above the linear toe)."""
    sensor = reflectance / 0.9
    return 0.36726845 * math.log10(sensor * 14.98325 + 1.0) + 0.12240537


def _apply(config: OCIO.Config, src: str, rgb: list[float]) -> np.ndarray:
    proc = config.getProcessor(src, "ACES2065-1").getDefaultCPUProcessor()
    buf = np.array([rgb], dtype=np.float32)
    proc.apply(OCIO.PackedImageDesc(buf, 1, 1, 3))
    return buf[0]


@pytest.fixture(scope="module")
def bundled() -> OCIO.Config:
    if not _CONFIG.is_file():
        pytest.skip("bundled OCIO config missing")
    try:
        return OCIO.Config.CreateFromFile(str(_CONFIG))
    except Exception as exc:
        pytest.fail(f"bundled OCIO config failed to load: {exc}")


def test_bundled_path_resolves() -> None:
    found = get_bundled_aces_studio_path()
    assert found is not None
    assert found.resolve() == _CONFIG.resolve()


def test_stock_colorspaces_unchanged(bundled: OCIO.Config) -> None:
    names = set(bundled.getColorSpaceNames())
    missing = [name for name in _STOCK if name not in names]
    assert missing == []
    assert "N-Log" not in names


def test_added_colorspaces_convert(bundled: OCIO.Config) -> None:
    names = set(bundled.getColorSpaceNames())
    for name in _ADDED:
        assert name in names
        out = _apply(bundled, name, [0.2, 0.45, 0.7])
        assert np.all(np.isfinite(out))


def test_nlog_lut_matches_spec() -> None:
    """The committed spi1d is the Nikon equation, not a redistributed cube."""
    text = _NLOG_LUT.read_text(encoding="utf-8").splitlines()
    assert text[0] == "Version 1"
    assert text[2] == "Length 4096"
    samples = [float(line) for line in text[5:-1]]
    assert len(samples) == 4096
    assert samples[0] == pytest.approx(-0.0075, abs=1e-9)
    # 18% grey code sits on the cube-root segment.
    code = _nlog_code(0.18)
    index = code * (len(samples) - 1)
    lo = int(math.floor(index))
    hi = min(lo + 1, len(samples) - 1)
    weight = index - lo
    decoded = samples[lo] * (1.0 - weight) + samples[hi] * weight
    assert decoded == pytest.approx(0.18, abs=1e-6)


@pytest.mark.parametrize(
    ("space", "code"),
    [
        ("N-Log N-Gamut", _nlog_code(0.18)),
        ("F-Log F-Gamut", _flog_code(0.18, flog2=False)),
        ("F-Log2 F-Gamut", _flog_code(0.18, flog2=True)),
        ("L-Log Rec.2020", _llog_code(0.18)),
        ("L-Log Rec.709", _llog_code(0.18)),
        ("S-Log S-Gamut", _sony_code(0.18, slog2=False)),
        ("S-Log2 S-Gamut", _sony_code(0.18, slog2=True)),
        ("CanonLog CinemaGamut D55", _canon_log_code(0.18)),
        ("CanonLog Rec.2020", _canon_log_code(0.18)),
        ("CanonLog Rec.709", _canon_log_code(0.18)),
        ("CanonLog2 Rec.2020", _canon_log2_code(0.18)),
        ("CanonLog2 Rec.709", _canon_log2_code(0.18)),
        ("CanonLog3 Rec.2020", _canon_log3_code(0.18)),
        ("CanonLog3 Rec.709", _canon_log3_code(0.18)),
        ("F-Log2C F-Gamut C", _flog_code(0.18, flog2=True)),
        ("KineLOG3 Wide Gamut", _kinelog3_code(0.18)),
        ("Protune Rec.709", _log_base_code(0.18, 113.0)),
        ("GP-Log Rec.709", _log_base_code(0.18, 400.0)),
        ("GP-Log2 Rec.2020", _log_base_code(0.18 / (2.0**1.8), 600.0)),
        ("O-Log Rec.2020", _olog_code(0.18)),
        ("Apple Log 2", _apple_log_code(0.18)),
    ],
)
def test_mid_grey_lands_near_018(bundled: OCIO.Config, space: str, code: float) -> None:
    out = _apply(bundled, space, [code, code, code])
    assert out == pytest.approx([0.18, 0.18, 0.18], abs=1e-4)


@pytest.mark.parametrize(
    ("space", "code"),
    [
        ("F-Log F-Gamut", 470 / 1023),
        ("F-Log2 F-Gamut", 400 / 1023),
        ("S-Log S-Gamut", 394 / 1023),
        ("S-Log2 S-Gamut", 347 / 1023),
        ("CanonLog Rec.709", 351 / 1023),
        ("N-Log N-Gamut", 372 / 1023),
        ("O-Log Rec.2020", 399 / 1023),
    ],
)
def test_published_10bit_mid_grey_codes(bundled: OCIO.Config, space: str, code: float) -> None:
    """Maker tables round 18% grey to an integer 10-bit code."""
    out = _apply(bundled, space, [code, code, code])
    assert out == pytest.approx([0.18, 0.18, 0.18], abs=1e-3)


def test_apple_log2_matches_ocio_unit_sample(bundled: OCIO.Config) -> None:
    """OCIO PR 2343 sample: code (0.5, 0.4, 0.3) through Apple Log 2."""
    out = _apply(bundled, "Apple Log 2", [0.5, 0.4, 0.3])
    assert out == pytest.approx([0.160302015, 0.091223177, 0.026405713], abs=1e-5)


def test_apple_log2_neutral_matches_apple_log(bundled: OCIO.Config) -> None:
    """Same curve and a white-preserving matrix, so grey matches stock Apple Log."""
    code = _apple_log_code(0.18)
    log2 = _apply(bundled, "Apple Log 2", [code, code, code])
    log1 = _apply(bundled, "Apple Log", [code, code, code])
    assert log2 == pytest.approx(log1, abs=1e-6)


def test_stock_slog3_matches_ocio_builtin(bundled: OCIO.Config) -> None:
    try:
        builtin = OCIO.Config.CreateFromBuiltinConfig("studio-config-v4.0.0_aces-v2.0_ocio-v2.5")
    except Exception as exc:
        pytest.skip(f"studio builtin unavailable: {exc}")
    code = 420 / 1023
    ours = _apply(bundled, "S-Log3 S-Gamut3", [code, code, code])
    proc = builtin.getProcessor("S-Log3 S-Gamut3", "ACES2065-1").getDefaultCPUProcessor()
    buf = np.array([[code, code, code]], dtype=np.float32)
    proc.apply(OCIO.PackedImageDesc(buf, 1, 1, 3))
    assert ours == pytest.approx(buf[0], abs=0.0)


def test_nlog_alias(bundled: OCIO.Config) -> None:
    cs = bundled.getColorSpace("nlog_ngamut")
    assert cs is not None
    assert cs.getName() == "N-Log N-Gamut"
    assert cs.getFamily() == "Input/Nikon"


def _load_measure():
    path = _ROOT / "scripts" / "measure_camera_lut_error.py"
    spec = importlib.util.spec_from_file_location("measure_camera_lut_error", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure_camera_lut_error"] = module
    spec.loader.exec_module(module)
    return module


_measure = _load_measure()


def test_formula_error_within_tolerance(bundled: OCIO.Config) -> None:
    """Max and mean error of every added curve against its published equation."""
    rows = _measure.measure_formulas(bundled)
    report = _measure.format_report(rows)
    over = [row for row in rows if row.tolerance is not None and row.max_abs > row.tolerance]
    assert over == [], report
    names = {row.name for row in rows}
    for curve in (
        "N-Log - Curve",
        "F-Log - Curve",
        "F-Log2 - Curve",
        "F-Log2C - Curve",
        "L-Log - Curve",
        "S-Log - Curve",
        "S-Log2 - Curve",
        "Canon Log - Curve",
        "KineLOG3 - Curve",
        "Protune - Curve",
        "GP-Log - Curve",
        "GP-Log2 - Curve",
        "O-Log - Curve",
        "Apple Log 2",
        "BMD Broadcast Film Gen4 - Curve",
        "BMD Pocket 4K Film Gen4 - Curve",
        "BMD Pocket 6K Film Gen4 - Curve",
    ):
        assert curve in names


@pytest.mark.skipif(
    _measure.vendor_root() is None,
    reason="official camera LUTs are downloaded outside the repo",
)
def test_vendor_lut_error_within_tolerance(bundled: OCIO.Config) -> None:
    """Lattice / 1D comparison when VENDOR_LUT_DIR or /tmp/vendor-luts is present."""
    root = _measure.vendor_root()
    assert root is not None
    rows = _measure.measure_vendor(bundled, root)
    report = _measure.format_report(rows)
    over = [row for row in rows if row.tolerance is not None and row.max_abs > row.tolerance]
    assert over == [], report
    # Viewing-LUT rows are reported and are not required to be near zero.
    assert any(row.tolerance is None for row in rows)
