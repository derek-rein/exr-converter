"""TrustMark BCH_5 text payloads, without downloading a model."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.core.trustmark import bch, models, payload
from src.core.trustmark.bch import decode_packet, encode_data_bits
from src.core.trustmark.models import ENCODER_FILENAME, ensure_model
from src.core.trustmark.payload import (
    decode_text_packet,
    encode_text,
    majority_packet,
    normalize_watermark_text,
)
from src.core.trustmark.resample import content_region

# Published TrustMark Q / BCH_5 packet (rust trustmark unit test).
_PACKET = (
    "1011011110011000111111000000011111011111011100000110110110111"
    "00011001010110111101001101100001000"
    "0001"
)


def test_published_packet_roundtrip() -> None:
    data = _PACKET[:61]
    assert len(_PACKET) == 100
    assert encode_data_bits(data) == _PACKET
    assert decode_packet(_PACKET) == (data, 0)
    flipped = ("0" if _PACKET[0] == "1" else "1") + _PACKET[1:]
    assert decode_packet(flipped) == (data, 1)


def test_five_bitflips_correct_and_six_do_not() -> None:
    packet = encode_text("mysecret")
    chars = list(packet)
    for index in (1, 10, 20, 40, 70):
        chars[index] = "0" if chars[index] == "1" else "1"
    text, ok, flips = decode_text_packet("".join(chars))
    assert (text, ok, flips) == ("mysecret", True, 5)
    chars[5] = "0" if chars[5] == "1" else "1"
    text, ok, _flips = decode_text_packet("".join(chars))
    assert ok is False
    assert text == ""


@pytest.mark.parametrize("text", ["A", "hi", "mysecret", "ABCD1234"])
def test_text_mode_fits_eight_ascii_characters(text: str) -> None:
    assert decode_text_packet(encode_text(text))[:2] == (text, True)


def test_payload_limits() -> None:
    with pytest.raises(ValueError, match="8"):
        normalize_watermark_text("123456789")
    with pytest.raises(ValueError, match="ASCII"):
        normalize_watermark_text("héllo")
    with pytest.raises(ValueError, match="empty"):
        normalize_watermark_text("   ")
    with pytest.raises(ValueError, match="0.1"):
        payload.normalize_strength(0.0)


def test_majority_vote_repairs_a_split_decision() -> None:
    packet = encode_text("clipID")
    flipped = ("0" if packet[0] == "1" else "1") + packet[1:]
    assert majority_packet([packet, packet, flipped]) == packet


def test_content_region_is_full_frame_until_aspect_exceeds_two() -> None:
    assert content_region(1080, 1920) == (0, 0, 1080, 1920)
    assert content_region(200, 1000) == (0, 400, 200, 200)
    assert content_region(1000, 200) == (400, 0, 200, 200)


def test_sha256_mismatch_deletes_the_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXR_TRUSTMARK_CACHE", str(tmp_path))

    class _Body:
        def __init__(self) -> None:
            self._sent = False

        def read(self, _n: int = -1) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return b"not-a-model"

        def __enter__(self) -> _Body:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(
        "src.core.trustmark.models.urllib.request.urlopen",
        lambda *_a, **_k: _Body(),
    )
    with pytest.raises(RuntimeError, match="SHA256"):
        ensure_model(ENCODER_FILENAME)
    assert not (tmp_path / ENCODER_FILENAME).exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_cache_hit_does_not_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXR_TRUSTMARK_CACHE", str(tmp_path))
    blob = b"cached-encoder"
    monkeypatch.setitem(models._SHA256, ENCODER_FILENAME, hashlib.sha256(blob).hexdigest())
    (tmp_path / ENCODER_FILENAME).write_bytes(blob)
    calls: list[str] = []
    monkeypatch.setattr(
        "src.core.trustmark.models.urllib.request.urlopen",
        lambda *_a, **_k: calls.append("download"),
    )
    assert ensure_model(ENCODER_FILENAME).read_bytes() == blob
    assert calls == []


def test_decoder_model_is_not_in_the_repo_or_the_release_bundle() -> None:
    root = Path(__file__).resolve().parents[1]
    assert list(root.rglob("decoder_Q.onnx")) == []
    assert list(root.rglob("encoder_Q.onnx")) == []
    release = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "decoder_Q.onnx" not in release
    assert "--include-package=onnxruntime" in release
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    assert "--include-package=onnxruntime" in makefile
    assert bch.ECC_BITS == 35
