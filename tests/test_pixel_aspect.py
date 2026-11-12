"""Pixel aspect: EXR float header, broadcast vs lens, video tags, slate geometry."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import OpenImageIO as oiio
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter

from src.core.exr_io import write_exr
from src.core.pixel_aspect import (
    SQUARE,
    PixelAspect,
    apply_override,
    apply_stream_sample_aspect,
    classify_sample_aspect,
    display_size,
    is_broadcast_sample_aspect,
    merge_for_write,
    optical_from_default_scale,
    optical_from_mapping,
    parse_pixel_aspect_choice,
    tagged_sample_aspect,
)
from src.core.transcode import TranscodeSettings, run_transcode
from src.gui.player.preview_view import ImagePreviewView
from src.render.storage_paint import paint_storage_image


def _attr_map(path: Path) -> dict[str, object]:
    inp = oiio.ImageInput.open(str(path))
    assert inp is not None
    try:
        spec = inp.spec()
        out: dict[str, object] = {
            "pixelAspectRatio": float(spec.get_float_attribute("pixelAspectRatio"))
        }
        for attr in spec.extra_attribs:
            out[attr.name] = attr
        return out
    finally:
        inp.close()


def _type_name(attr: object) -> str:
    return str(getattr(attr, "type", "")).lower()


def test_string_pixel_aspect_survives_as_float(tmp_path: Path) -> None:
    path = tmp_path / "squeeze.exr"
    rgb = np.zeros((8, 16, 3), dtype=np.float32)
    write_exr(
        str(path),
        rgb,
        extra_attrs={"pixelAspectRatio": "2.0", "exrconverter:r3d:camera_model": "stage"},
    )
    attrs = _attr_map(path)
    assert attrs["pixelAspectRatio"] == pytest.approx(2.0)
    par_attr = next(
        attr
        for name, attr in attrs.items()
        if str(name).lower() == "pixelaspectratio" and hasattr(attr, "value")
    )
    assert float(par_attr.value) == pytest.approx(2.0)
    assert "string" not in _type_name(par_attr)
    squeeze = attrs["exrconverter:opticalSqueeze"]
    assert float(squeeze.value) == pytest.approx(2.0)
    assert "string" not in _type_name(squeeze)
    camera = attrs["exrconverter:r3d:camera_model"]
    assert str(camera.value) == "stage"


def test_other_extra_attributes_stay_strings(tmp_path: Path) -> None:
    path = tmp_path / "notes.exr"
    write_exr(
        str(path),
        np.zeros((4, 4, 3), dtype=np.float32),
        extra_attrs={"exrconverter:note": "2.0", "exrconverter:reel": "A001"},
    )
    attrs = _attr_map(path)
    assert attrs["pixelAspectRatio"] == pytest.approx(1.0)
    assert str(attrs["exrconverter:note"].value) == "2.0"
    assert "string" in _type_name(attrs["exrconverter:note"])


def test_override_square_beats_string_attribute(tmp_path: Path) -> None:
    path = tmp_path / "forced.exr"
    write_exr(
        str(path),
        np.zeros((4, 4, 3), dtype=np.float32),
        extra_attrs={"pixelAspectRatio": "2.0"},
        aspect=PixelAspect(1.0, "override"),
    )
    attrs = _attr_map(path)
    assert attrs["pixelAspectRatio"] == pytest.approx(1.0)


def test_broadcast_sar_is_not_a_lens() -> None:
    pal = classify_sample_aspect(float(Fraction(16, 11)), 720, 576)
    assert pal.kind == "broadcast"
    assert pal.exr_factor == pytest.approx(1.0)
    assert pal.sample_factor == pytest.approx(float(Fraction(16, 11)))
    assert is_broadcast_sample_aspect(float(Fraction(40, 33)), 720, 480)
    assert "anamorphic" not in pal.label()

    hdv = classify_sample_aspect(4 / 3, 1440, 1080)
    assert hdv.kind == "broadcast"

    alexa = classify_sample_aspect(2.0, 2048, 1152)
    assert alexa.kind == "lens"
    assert alexa.exr_factor == pytest.approx(2.0)
    assert alexa.label() == "2x anamorphic"

    one_three = classify_sample_aspect(1.3, 4448, 3096)
    assert one_three.kind == "lens"
    assert one_three.label() == "1.3x anamorphic"


def test_optical_metadata_beats_container_sar() -> None:
    meta = {"lens_squeeze_factor": "1.3", "anamorphic": "off"}
    # first non-square key wins; "off" is square and is skipped when a real squeeze exists
    assert optical_from_mapping(meta) == pytest.approx(1.3)
    assert optical_from_mapping({"anamorphic": "2x"}) == pytest.approx(2.0)
    assert optical_from_mapping({"anamorphic": "off"}) == pytest.approx(1.0)
    assert optical_from_mapping({"camera": "2.0"}) is None
    assert optical_from_default_scale(2.0, 1.0) == pytest.approx(2.0)


def test_merge_promotes_text_attribute() -> None:
    merged = merge_for_write(SQUARE, {"pixelAspectRatio": "2.0", "reel": "A"})
    assert merged.kind == "lens"
    assert merged.exr_factor == pytest.approx(2.0)
    forced = merge_for_write(PixelAspect(1.0, "override"), {"pixelAspectRatio": "2.0"})
    assert forced.exr_factor == pytest.approx(1.0)


def test_parse_and_resize_tag() -> None:
    assert parse_pixel_aspect_choice("auto") is None
    assert parse_pixel_aspect_choice("2") == pytest.approx(2.0)
    assert parse_pixel_aspect_choice("4/3") == pytest.approx(4 / 3)
    with pytest.raises(ValueError):
        parse_pixel_aspect_choice("wide")
    aspect = apply_override(SQUARE, 2.0)
    assert tagged_sample_aspect(aspect, 100, 50, 50, 25) == pytest.approx(2.0)
    detected = PixelAspect(2.0, "lens")
    # Uniform scale keeps the sample aspect. A non-uniform resize keeps the display aspect.
    assert tagged_sample_aspect(detected, 200, 100, 100, 50) == pytest.approx(2.0)
    assert tagged_sample_aspect(detected, 200, 100, 100, 100) == pytest.approx(4.0)


def _read_sar(path: Path) -> float:
    with av.open(str(path)) as container:
        return float(container.streams.video[0].sample_aspect_ratio)


def _write_h264(path: Path, sar: Fraction) -> None:
    container = av.open(str(path), mode="w")
    stream = container.add_stream("libx264", rate=24)
    stream.width = 64
    stream.height = 32
    stream.pix_fmt = "yuv420p"
    stream.options = {"preset": "ultrafast", "crf": "32"}
    apply_stream_sample_aspect(stream, float(sar))
    image = np.zeros((32, 64, 3), dtype=np.uint8)
    image[..., 1] = 40
    for _ in range(3):
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()


def test_encoder_writes_sample_aspect(tmp_path: Path) -> None:
    path = tmp_path / "tagged.mov"
    _write_h264(path, Fraction(2, 1))
    assert _read_sar(path) == pytest.approx(2.0)


def test_transcode_keeps_incoming_sample_aspect(tmp_path: Path) -> None:
    src = tmp_path / "source.mov"
    _write_h264(src, Fraction(2, 1))
    out = tmp_path / "out.mov"
    run_transcode(
        src,
        out,
        TranscodeSettings(codec_key="h264", container="mov", audio="none", crf=32),
    )
    assert _read_sar(out) == pytest.approx(2.0, rel=0.02)


def test_preview_and_slate_use_the_factor(qapp, tmp_path: Path) -> None:
    view = ImagePreviewView()
    view.set_frame_size(100, 50, 1.0, "")
    square_w = view._frame_rect.width()
    view.set_frame_size(100, 50, 2.0, "2x anamorphic")
    assert view._frame_rect.width() == pytest.approx(square_w * 2, rel=0.02)
    assert view._frame_rect.height() == 1080 or view._frame_rect.height() > 0
    assert "2x anamorphic" in view.format_text()
    assert "100" in view.format_text()

    def paint(painter: QPainter, width: int, height: int) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.drawEllipse((width - 80) // 2, (height - 80) // 2, 80, 80)

    image = paint_storage_image(100, 100, 2.0, QColor(0, 0, 0, 0), paint)
    assert (image.width(), image.height()) == (100, 100)
    assert display_size(100, 100, 2.0) == (200, 100)
    # The circle was 80 display pixels wide. After a 2x squeeze its storage
    # width is about half its height.
    bounds = _opaque_bounds(image)
    assert bounds is not None
    bw, bh = bounds
    assert bh == pytest.approx(80, abs=4)
    assert bw == pytest.approx(40, abs=4)
    assert bw < bh * 0.7


def _opaque_bounds(image) -> tuple[int, int] | None:
    image = image.convertToFormat(image.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    ptr = image.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4))
    mask = arr[..., 3] > 128
    if not mask.any():
        return None
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    return int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1)
