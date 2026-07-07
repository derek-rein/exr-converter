"""End-to-end conversion tests (source tree and release bundles).

Synthetic fixtures are generated at runtime so CI always has something to
convert. Optional real media listed in ``tests/fixtures/manifest.json`` is
picked up automatically once you add the files.
"""

from __future__ import annotations

import pytest

from tests.support.integration import (
    assert_exr_sequence,
    assert_video_output,
    load_manifest,
    resolve_exr_input,
    run_cli,
    run_cli_with_spaces,
    write_synthetic_exr_sequence,
    write_synthetic_video,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def synthetic_video(tmp_path):
    path = tmp_path / "input.mov"
    write_synthetic_video(path, width=64, height=36, frames=3)
    return path


@pytest.fixture
def synthetic_exr_dir(tmp_path):
    write_synthetic_exr_sequence(tmp_path / "exr_in")
    return tmp_path / "exr_in"


class TestSyntheticConversions:
    def test_video_to_exr(self, synthetic_video, tmp_path):
        out = tmp_path / "exr_out"
        result = run_cli(
            "video2exr",
            "-i",
            str(synthetic_video),
            "-o",
            str(out),
            "--padding",
            "4",
            "--start-frame",
            "1001",
            "--exr-compression",
            "zip",
        )
        assert result.returncode == 0, result.stderr or result.stdout
        assert_exr_sequence(
            out,
            stem=synthetic_video.stem,
            expected_frames=3,
            min_width=64,
            min_height=36,
        )

    def test_exr_to_video(self, synthetic_exr_dir, tmp_path):
        out = tmp_path / "review.mov"
        result = run_cli(
            "exr2video",
            "-i",
            resolve_exr_input(synthetic_exr_dir),
            "-o",
            str(out),
            "--fps",
            "24",
            "--codec",
            "h264",
        )
        assert result.returncode == 0, result.stderr or result.stdout
        assert_video_output(out, min_frames=3, min_width=64, min_height=36)

    def test_round_trip_video_exr_video(self, synthetic_video, tmp_path):
        exr_dir = tmp_path / "exr_mid"
        video_out = tmp_path / "roundtrip.mov"

        v2e = run_cli(
            "video2exr",
            "-i",
            str(synthetic_video),
            "-o",
            str(exr_dir),
            "--exr-compression",
            "zip",
        )
        assert v2e.returncode == 0, v2e.stderr or v2e.stdout

        e2v = run_cli(
            "exr2video",
            "-i",
            resolve_exr_input(exr_dir),
            "-o",
            str(video_out),
            "--fps",
            "24",
            "--codec",
            "h264",
        )
        assert e2v.returncode == 0, e2v.stderr or e2v.stdout
        assert_video_output(video_out, min_frames=3, min_width=64, min_height=36)


_MANIFEST_VIDEOS, _MANIFEST_EXR = load_manifest()


def test_manifest_videos_to_exr(tmp_path):
    if not _MANIFEST_VIDEOS:
        pytest.skip("Add entries under tests/fixtures/manifest.json to test real video clips")

    for fixture in _MANIFEST_VIDEOS:
        out = tmp_path / fixture.id / "exr_out"
        result = run_cli_with_spaces(
            "video2exr",
            "-i",
            str(fixture.path),
            "-o",
            str(out),
            "--exr-compression",
            "zip",
            src=fixture.src,
            dst=fixture.dst,
        )
        assert result.returncode == 0, f"{fixture.id}: {result.stderr or result.stdout}"

        expected = fixture.expected_frames or 1
        assert_exr_sequence(
            out,
            stem=fixture.path.stem,
            expected_frames=expected,
            min_width=fixture.min_width,
            min_height=fixture.min_height,
        )


def test_manifest_exr_sequences_to_video(tmp_path):
    if not _MANIFEST_EXR:
        pytest.skip("Add entries under tests/fixtures/manifest.json to test real EXR sequences")

    for fixture in _MANIFEST_EXR:
        out = tmp_path / f"{fixture.id}.mov"
        result = run_cli_with_spaces(
            "exr2video",
            "-i",
            resolve_exr_input(fixture.path),
            "-o",
            str(out),
            "--fps",
            "24",
            "--codec",
            "h264",
            src=fixture.src,
            dst=fixture.dst,
        )
        assert result.returncode == 0, f"{fixture.id}: {result.stderr or result.stdout}"

        min_frames = fixture.frame_count or 1
        assert_video_output(out, min_frames=min_frames)
