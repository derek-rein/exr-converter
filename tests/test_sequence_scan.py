from __future__ import annotations

from pathlib import Path

import pytest

from src.core.sequence_scan import parse_frame_name, scan_around


def _touch(folder: Path, *names: str) -> None:
    for name in names:
        (folder / name).write_bytes(b"")


@pytest.mark.parametrize(
    ("name", "head", "digits", "tail", "frame"),
    [
        ("plate.1001.exr", "plate.", "1001", ".exr", 1001),
        ("plate_0007.dpx", "plate_", "0007", ".dpx", 7),
        ("shot_v02.1001.exr", "shot_v02.", "1001", ".exr", 1001),
        ("plate.-0003.exr", "plate.", "0003", ".exr", -3),
    ],
)
def test_parse_frame_name(name, head, digits, tail, frame) -> None:
    parsed = parse_frame_name(name)
    assert parsed is not None
    assert (parsed.head, parsed.digits, parsed.tail, parsed.frame) == (head, digits, tail, frame)


@pytest.mark.parametrize("name", ["plate.exr", "plate1001.exr", "1001.exr", "plate.1001", "a.b"])
def test_names_without_a_frame_number_are_not_sequences(name: str) -> None:
    assert parse_frame_name(name) is None


def test_finds_the_clicked_sequence_among_others(tmp_path: Path) -> None:
    _touch(tmp_path, *(f"plate.{i:04d}.exr" for i in range(1001, 1006)))
    _touch(tmp_path, *(f"other.{i:04d}.exr" for i in range(1, 4)))
    _touch(tmp_path, "plate.1001.dpx", "notes.txt")
    scan = scan_around(tmp_path / "plate.1003.exr")
    assert scan is not None
    assert scan.frames == (1001, 1002, 1003, 1004, 1005)
    assert (scan.first, scan.last, scan.clicked, scan.pad) == (1001, 1005, 1003, 4)
    assert scan.missing == () and scan.name == "plate"
    assert Path(scan.path(1004)) == tmp_path / "plate.1004.exr"


def test_gaps_are_reported_not_hidden(tmp_path: Path) -> None:
    _touch(tmp_path, "s.0001.exr", "s.0002.exr", "s.0005.exr", "s.0006.exr")
    scan = scan_around(tmp_path / "s.0005.exr")
    assert scan is not None
    assert scan.frames == (1, 2, 5, 6) and scan.missing == (3, 4)
    assert sorted(scan.file_sequence().frameSet()) == [1, 2, 5, 6]


def test_different_padding_is_a_different_sequence(tmp_path: Path) -> None:
    _touch(tmp_path, "a.1001.exr", "a.1002.exr", "a.01001.exr")
    scan = scan_around(tmp_path / "a.1001.exr")
    assert scan is not None and scan.frames == (1001, 1002)


def test_padded_sequence_clicked_on_a_frame_without_leading_zero(tmp_path: Path) -> None:
    _touch(tmp_path, "a.0999.exr", "a.1000.exr", "a.1001.exr")
    scan = scan_around(tmp_path / "a.1001.exr")
    assert scan is not None and scan.frames == (999, 1000, 1001) and scan.pad == 4


def test_unpadded_numbers_of_different_widths_are_one_sequence(tmp_path: Path) -> None:
    _touch(tmp_path, "a.8.exr", "a.9.exr", "a.10.exr", "a.11.exr")
    scan = scan_around(tmp_path / "a.9.exr")
    assert scan is not None and scan.frames == (8, 9, 10, 11) and scan.pad == 1
    assert Path(scan.path(10)) == tmp_path / "a.10.exr"


def test_negative_frames(tmp_path: Path) -> None:
    _touch(tmp_path, "a.-0002.exr", "a.-0001.exr", "a.0000.exr", "a.0001.exr")
    scan = scan_around(tmp_path / "a.0000.exr")
    assert scan is not None and scan.frames == (-2, -1, 0, 1)
    assert Path(scan.path(-2)) == tmp_path / "a.-0002.exr"


def test_underscore_style(tmp_path: Path) -> None:
    _touch(tmp_path, "a_0001.dpx", "a_0002.dpx")
    scan = scan_around(tmp_path / "a_0002.dpx")
    assert scan is not None and scan.frames == (1, 2) and scan.name == "a"


def test_returns_none_when_it_cannot_help(tmp_path: Path) -> None:
    _touch(tmp_path, "still.exr", "a.0001.txt")
    assert scan_around(tmp_path / "still.exr") is None  # no frame number
    assert scan_around(tmp_path / "a.0001.txt") is None  # not an image format
    assert scan_around(tmp_path / "missing.0001.exr") is None  # not on disk
    assert scan_around(tmp_path / "nodir" / "a.0001.exr") is None


def test_matches_the_general_scan(tmp_path: Path) -> None:
    from src.core.sequence import find_exr_sequence_info

    _touch(tmp_path, *(f"plate.{i:04d}.exr" for i in (1, 2, 3, 5)))
    _touch(tmp_path, *(f"other.{i:04d}.exr" for i in (1, 2)))
    paths, name, frames, pad, seq = find_exr_sequence_info(str(tmp_path / "plate.0002.exr"))
    scan = scan_around(tmp_path / "plate.0002.exr")
    assert scan is not None
    assert list(scan.frames) == frames and scan.pad == pad and scan.name == name
    assert [Path(p) for p in scan.paths()] == [Path(p) for p in paths]
