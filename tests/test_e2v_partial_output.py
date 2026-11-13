"""EXR → video writes a hidden partial file and renames it only on success."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.convert import run_exr_to_video
from src.core.errors import ConversionCancelled
from src.core.ocio_utils import resolve_ocio_for_cli
from src.core.oxideav_prores import is_available as oxideav_available
from tests.support.integration import write_synthetic_exr_sequence

pytestmark = pytest.mark.integration

CODECS = {
    "libav": {"video_codec": "libx264", "pix_fmt_out": "yuv420p", "codec_key": "h264"},
    "oxideav": {
        "video_codec": "oxideav_prores",
        "pix_fmt_out": "yuv444p12le",
        "codec_key": "prores_ox_4444",
    },
}


def _run(exr_dir: Path, out: Path, codec: str, **kw) -> None:
    if codec == "oxideav" and not oxideav_available():
        pytest.skip("oxideav-prores extension not built")
    run_exr_to_video(
        str(exr_dir),
        out,
        resolve_ocio_for_cli(None),
        "ACEScg",
        "Rec.1886 Rec.709 - Display",
        fps=24,
        workers=1,
        **CODECS[codec],
        **kw,
    )


@pytest.mark.parametrize("codec", list(CODECS))
def test_cancel_leaves_no_output(tmp_path: Path, codec: str):
    exr_dir = tmp_path / "exr"
    write_synthetic_exr_sequence(exr_dir, frames=(1, 2, 3, 4), width=64, height=48)
    out = tmp_path / "out.mov"
    done: list[int] = []
    with pytest.raises(ConversionCancelled):
        _run(
            exr_dir,
            out,
            codec,
            progress=lambda n, _t: done.append(n),
            cancel_check=lambda: bool(done),
        )
    assert not out.exists(), "a cancelled encode left a truncated video"
    assert not list(tmp_path.glob(".*partial*"))


@pytest.mark.parametrize("codec", list(CODECS))
def test_success_renames_partial_into_place(tmp_path: Path, codec: str):
    exr_dir = tmp_path / "exr"
    write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=64, height=48)
    out = tmp_path / "out.mov"
    _run(exr_dir, out, codec)
    assert out.is_file() and out.stat().st_size > 0
    assert not list(tmp_path.glob(".*partial*"))
