"""Frame-rate parsing: decimals and N/D rationals (CLI, settings, encoders)."""

from __future__ import annotations

import argparse
from fractions import Fraction

import pytest

from src.cli import build_parser
from src.core.convert import _fps_num_den, _fps_to_rate
from src.core.fps import format_fps, fps_arg, fps_to_float, parse_fps


class TestParseFps:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("24", 24.0),
            ("23.976", 23.976),
            (" 25 ", 25.0),
            ("29.97fps", 29.97),
            (24, 24.0),
            (59.94, 59.94),
        ],
    )
    def test_decimals(self, text, expected):
        value = parse_fps(text)
        assert isinstance(value, float)
        assert value == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("24000/1001", Fraction(24000, 1001)),
            ("30000 / 1001", Fraction(30000, 1001)),
            ("60000:1001", Fraction(60000, 1001)),
            ("48/2", Fraction(24, 1)),
            ("23.976/1", Fraction("23.976")),
        ],
    )
    def test_rationals(self, text, expected):
        value = parse_fps(text)
        assert isinstance(value, Fraction)
        assert value == expected

    @pytest.mark.parametrize("bad", ["", "abc", "24000/0", "0", "-24", "nan", "inf", "1/2/3", True])
    def test_rejects(self, bad):
        with pytest.raises(ValueError):
            parse_fps(bad)

    def test_argparse_type(self):
        assert fps_arg("24000/1001") == Fraction(24000, 1001)
        with pytest.raises(argparse.ArgumentTypeError, match="24000/1001"):
            fps_arg("fast")

    def test_fps_to_float_lenient(self):
        assert fps_to_float("24000/1001") == pytest.approx(23.976, abs=1e-3)
        assert fps_to_float("garbage", 25.0) == 25.0
        assert fps_to_float(None, 24.0) == 24.0

    def test_format(self):
        assert format_fps(Fraction(24000, 1001)) == "24000/1001"
        assert format_fps(Fraction(24, 1)) == "24"
        assert format_fps(24.0) == "24"
        assert format_fps(23.976) == "23.976"


class TestEncoderRate:
    def test_rational_is_exact(self):
        assert _fps_to_rate(Fraction(24000, 1001)) == Fraction(24000, 1001)
        assert _fps_num_den(Fraction(30000, 1001)) == (30000, 1001)
        assert _fps_to_rate(Fraction(48, 2)) == 24

    def test_decimal_still_snaps(self):
        assert _fps_to_rate(23.976) == Fraction(24000, 1001)
        assert _fps_num_den(29.97) == (30000, 1001)
        assert _fps_to_rate(25.0) == 25


class TestCliFps:
    def test_exr2video_accepts_rational(self):
        args = build_parser().parse_args(["exr2video", "-i", "x", "--fps", "24000/1001"])
        assert args.fps == Fraction(24000, 1001)

    def test_exr2video_accepts_decimal(self):
        args = build_parser().parse_args(["exr2video", "-i", "x", "--fps", "23.976"])
        assert args.fps == pytest.approx(23.976)

    def test_exr2video_rejects_garbage(self, capsys):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["exr2video", "-i", "x", "--fps", "24/0"])
        assert "--fps" in capsys.readouterr().err
