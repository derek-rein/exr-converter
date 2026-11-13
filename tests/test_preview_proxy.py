"""Browser preview sizing, native scale steps, and the preview cache."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import OpenImageIO as oiio

from src.core.braw.constants import (
    DECODE_EIGHTH,
    DECODE_FULL,
    DECODE_HALF,
    DECODE_QUARTER,
)
from src.core.cinemadng.color import FrameMeta, develop, develop_half
from src.core.exr_io import read_image, read_image_preview
from src.core.frame_source import VideoPreviewDecoder
from src.core.preview_proxy import (
    BRAW_PREVIEW_MODES,
    R3D_PREVIEW_MODES,
    PreviewCache,
    braw_preview_mode,
    bucket_edge,
    pick_covering_level,
    pick_named_level,
    preview_cache_key,
    proxy_storage_target,
    r3d_preview_mode,
)
from src.core.r3d.constants import (
    DECODE_EIGHTH_GOOD,
    DECODE_FULL_PREMIUM,
    DECODE_HALF_GOOD,
    DECODE_HALF_PREMIUM,
    DECODE_QUARTER_GOOD,
    DECODE_SIXTEENTH_GOOD,
)

R3D_LEVELS = (
    (1.0, "full"),
    (0.5, "half"),
    (0.25, "quarter"),
    (0.125, "eighth"),
    (0.0625, "sixteenth"),
)
BRAW_LEVELS = (
    (1.0, "full"),
    (0.5, "half"),
    (0.25, "quarter"),
    (0.125, "eighth"),
)


def test_mode_tables_match_bridge_abi() -> None:
    assert R3D_PREVIEW_MODES["full"] == DECODE_FULL_PREMIUM
    assert R3D_PREVIEW_MODES["half"] == DECODE_HALF_GOOD
    assert R3D_PREVIEW_MODES["half"] != DECODE_HALF_PREMIUM
    assert R3D_PREVIEW_MODES["quarter"] == DECODE_QUARTER_GOOD
    assert R3D_PREVIEW_MODES["eighth"] == DECODE_EIGHTH_GOOD
    assert R3D_PREVIEW_MODES["sixteenth"] == DECODE_SIXTEENTH_GOOD
    assert BRAW_PREVIEW_MODES["full"] == DECODE_FULL
    assert BRAW_PREVIEW_MODES["half"] == DECODE_HALF
    assert BRAW_PREVIEW_MODES["quarter"] == DECODE_QUARTER
    assert BRAW_PREVIEW_MODES["eighth"] == DECODE_EIGHTH


def test_proxy_target_covers_widget_and_buckets_together() -> None:
    wide = proxy_storage_target(3840, 2160, 800, 450, 1.0, 1.0)
    narrow = proxy_storage_target(3840, 2160, 700, 394, 1.0, 1.0)
    assert wide is not None and narrow is not None
    assert wide.bucket == narrow.bucket == 1024
    assert wide.width == 1024
    assert wide.height == 576
    assert wide.width >= 800 and wide.height >= 450
    smaller = proxy_storage_target(3840, 2160, 400, 225, 1.0, 1.0)
    assert smaller is not None
    assert smaller.bucket == 512
    assert bucket_edge(513) == 1024
    assert bucket_edge(512) == 512


def test_proxy_target_accounts_for_pixel_ratio_and_anamorphic() -> None:
    retina = proxy_storage_target(3840, 2160, 800, 450, 2.0, 1.0)
    assert retina is not None
    assert retina.bucket == 2048
    assert retina.width >= 1600
    assert retina.height >= 900
    squeezed = proxy_storage_target(2048, 1080, 800, 400, 1.0, 2.0)
    assert squeezed is not None
    # Stored pixels are half as wide as the on-screen picture (PAR 2).
    assert squeezed.width == 512
    assert squeezed.height == 270
    assert squeezed.width * 2 >= 800
    assert squeezed.height >= 211


def test_r3d_and_braw_pick_smallest_covering_level() -> None:
    assert pick_named_level(3840, 2160, 960, 540, R3D_LEVELS, ceiling=0.5) == "quarter"
    assert pick_named_level(3840, 2160, 1000, 540, R3D_LEVELS, ceiling=0.5) == "half"
    assert pick_named_level(3840, 2160, 200, 100, R3D_LEVELS, ceiling=0.5) == "sixteenth"
    # Larger than half would be a full debayer, which the player does not do today.
    assert pick_named_level(3840, 2160, 3000, 1600, R3D_LEVELS, ceiling=0.5) == "half"
    assert pick_named_level(3840, 2160, 3000, 1600, R3D_LEVELS, ceiling=None) == "full"
    assert r3d_preview_mode(3840, 2160, 960, 540) == DECODE_QUARTER_GOOD
    assert r3d_preview_mode(3840, 2160, 200, 100) == DECODE_SIXTEENTH_GOOD

    assert pick_named_level(3840, 2160, 900, 500, BRAW_LEVELS, ceiling=0.5) == "quarter"
    assert pick_named_level(3840, 2160, 1000, 540, BRAW_LEVELS, ceiling=0.5) == "half"
    assert pick_named_level(3840, 2160, 200, 100, BRAW_LEVELS, ceiling=0.5) == "eighth"
    assert pick_named_level(3840, 2160, 3000, 1600, BRAW_LEVELS, ceiling=0.5) == "half"
    assert braw_preview_mode(3840, 2160, 900, 500) == DECODE_QUARTER
    assert braw_preview_mode(3840, 2160, 3000, 1600) == DECODE_HALF


def test_exr_mip_level_choice_including_ripmap() -> None:
    pyramid = [(4096, 2160), (2048, 1080), (1024, 540), (512, 270), (256, 135)]
    assert pick_covering_level(pyramid, 800, 400) == 2
    assert pick_covering_level(pyramid, 100, 50) == 4
    assert pick_covering_level(pyramid, 3000, 2000) == 0
    ripmap = [(4096, 2160), (2048, 2160), (4096, 1080), (2048, 1080)]
    assert pick_covering_level(ripmap, 1800, 900) == 3
    assert pick_covering_level(ripmap, 1800, 1200) == 1


def test_preview_cache_misses_on_bucket_frame_and_mtime() -> None:
    cache = PreviewCache(max_entries=2)
    frame = np.zeros((2, 2, 3), dtype=np.float32)
    first = preview_cache_key("/tmp/plate.exr", 1, 512, mtime_ns=10)
    cache.put(first, frame)
    assert cache.get(first) is not None
    assert cache.get(preview_cache_key("/tmp/plate.exr", 1, 1024, mtime_ns=10)) is None
    assert cache.get(preview_cache_key("/tmp/plate.exr", 2, 512, mtime_ns=10)) is None
    assert cache.get(preview_cache_key("/tmp/plate.exr", 1, 512, mtime_ns=11)) is None
    cache.put(preview_cache_key("/tmp/plate.exr", 3, 512, mtime_ns=10), frame)
    cache.put(preview_cache_key("/tmp/plate.exr", 4, 512, mtime_ns=10), frame)
    assert cache.get(first) is None
    assert len(cache) == 2


def _write_constant_exr(path: Path, width: int, height: int) -> None:
    spec = oiio.ImageSpec(width, height, 3, oiio.FLOAT)
    buf = oiio.ImageBuf(spec)
    oiio.ImageBufAlgo.fill(buf, (0.25, 0.5, 0.75))
    assert buf.write(str(path))


def _write_mip_exr(path: Path, width: int, height: int) -> None:
    spec = oiio.ImageSpec(width, height, 3, oiio.FLOAT)
    buf = oiio.ImageBuf(spec)
    oiio.ImageBufAlgo.fill(buf, (0.25, 0.5, 0.75))
    assert oiio.ImageBufAlgo.make_texture(oiio.MakeTextureMode.MakeTxTexture, buf, str(path))


def test_plain_exr_preview_matches_full_read(tmp_path: Path) -> None:
    path = tmp_path / "flat.exr"
    _write_constant_exr(path, 128, 72)
    full = read_image(str(path))
    _draft, preview, reduced = read_image_preview(str(path), 40, 20)
    assert _draft is None
    assert reduced is False
    assert preview.shape == full.shape
    assert np.allclose(preview, full)


def test_mip_exr_preview_uses_a_smaller_level(tmp_path: Path) -> None:
    path = tmp_path / "mip.exr"
    _write_mip_exr(path, 512, 256)
    _draft, preview, reduced = read_image_preview(str(path), 100, 50, with_draft=True)
    assert reduced is True
    assert preview.shape[1] >= 100
    assert preview.shape[0] >= 50
    assert preview.shape[1] < 512
    assert np.allclose(preview[..., 0], 0.25, atol=0.05)
    assert np.allclose(preview[..., 1], 0.5, atol=0.05)
    assert np.allclose(preview[..., 2], 0.75, atol=0.05)
    # The chosen level is already small, so there is no extra draft pass.
    assert _draft is None


def test_large_mip_shows_a_smaller_draft_before_the_sharp_level(tmp_path: Path) -> None:
    path = tmp_path / "big.exr"
    _write_mip_exr(path, 3840, 2160)
    seen: list[tuple[int, int]] = []

    def on_draft(rgb: np.ndarray) -> None:
        seen.append((int(rgb.shape[1]), int(rgb.shape[0])))

    draft, preview, reduced = read_image_preview(
        str(path),
        1500,
        800,
        with_draft=True,
        on_draft=on_draft,
    )
    assert reduced is True
    assert preview.shape[1] >= 1500
    assert draft is not None
    assert draft.shape[1] * draft.shape[0] < preview.shape[1] * preview.shape[0]
    assert seen == [(int(draft.shape[1]), int(draft.shape[0]))]


def _write_mpeg4(path: Path, width: int, height: int) -> None:
    import av

    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=24)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[..., 0] = 180
    frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
    frame = frame.reformat(format="yuv420p")
    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def test_video_preview_scales_below_full_raster(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    _write_mpeg4(path, 64, 48)
    full = VideoPreviewDecoder(str(path))
    proxy = VideoPreviewDecoder(str(path), target_size=(20, 16))
    try:
        full_rgb = full.get_frame(1)
        proxy_rgb = proxy.get_frame(1)
    finally:
        full.close()
        proxy.close()
    assert full_rgb is not None and proxy_rgb is not None
    assert full_rgb.shape[1] == 64
    assert proxy_rgb.shape[1] < full_rgb.shape[1]
    assert proxy_rgb.shape[0] < full_rgb.shape[0]
    assert proxy_rgb.shape[1] >= 16
    assert proxy_rgb.shape[0] >= 12


def _flat_meta(width: int, height: int) -> FrameMeta:
    matrix = np.zeros((3, 3), dtype=np.float64)
    white = np.array([0.34567 / 0.35850, 1.0, (1.0 - 0.34567 - 0.35850) / 0.35850])
    for row in range(3):
        matrix[row, :] = white[row] / 3.0
    return FrameMeta(
        width=width,
        height=height,
        bits=16,
        compression=1,
        cfa=(0, 1, 1, 2),
        has_forward=True,
        has_color_matrix=False,
        illuminant=23,
        orientation=1,
        has_neutral=False,
        black=(0.0, 0.0, 0.0, 0.0),
        white=1.0,
        neutral=(1.0, 1.0, 1.0),
        color_matrix=tuple(matrix.reshape(-1)),
        forward_matrix=tuple(matrix.reshape(-1)),
        baseline_exposure=0.0,
        frame_rate=24.0,
        make="Acme",
        model="Cam",
        unique_model="Cam",
    )


def test_half_debayer_matches_flat_field_and_is_half_size() -> None:
    mosaic = np.full((32, 32), 0.4, dtype=np.float32)
    meta = _flat_meta(32, 32)
    full, space_full = develop(mosaic, meta)
    half, space_half = develop_half(mosaic, meta)
    assert space_half == space_full
    assert half.shape == (16, 16, 3)
    assert np.allclose(half[8, 8], full[16, 16], atol=1e-5)
