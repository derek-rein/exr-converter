"""TrustMark text-mode payloads.

Text mode packs printable ASCII into 7-bit characters, then BCH_5 protects
61 of those bits. Eight characters is the longest string that fits without
cutting a character in half (8 * 7 = 56, which is inside the 61-bit field).
The official TrustMark decoder reads this layout back as text.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import re
import socket
from dataclasses import dataclass

import numpy as np

from .bch import PACKET_BITS, decode_packet, encode_data_bits

MAX_TEXT_CHARS = 8
STRENGTH_MIN = 0.1
STRENGTH_MAX = 2.0
DEFAULT_STRENGTH = 1.0


def normalize_watermark_text(text: str) -> str:
    """Strip *text* and require 1 to 8 printable ASCII characters."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Invisible watermark payload is empty.")
    if len(cleaned) > MAX_TEXT_CHARS:
        raise ValueError(
            f"Invisible watermark payload is limited to {MAX_TEXT_CHARS} ASCII characters."
        )
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in cleaned):
        raise ValueError("Invisible watermark payload must be printable ASCII.")
    return cleaned


# Tokens a payload template may use. Values are cut to printable ASCII.
PAYLOAD_TOKENS: tuple[tuple[str, str], ...] = (
    ("{user}", "Login name"),
    ("{host}", "Computer name (before the first dot)"),
    ("{date}", "Render date as YYMMDD"),
    ("{time}", "Render time as HHMM"),
    ("{src}", "Source name (sequence or file stem)"),
)
_TOKEN_RE = re.compile(r"\{(user|host|date|time|src)\}")


@dataclass(frozen=True)
class ExpandedPayload:
    """A template after token expansion. ``truncated`` means it was over 8 characters."""

    text: str
    truncated: bool


def _ascii(value: str) -> str:
    return "".join(ch for ch in value if 32 <= ord(ch) <= 126)


def _token_values(source_name: str, now: _dt.datetime) -> dict[str, str]:
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    try:
        host = socket.gethostname().split(".", 1)[0]
    except Exception:
        host = ""
    return {
        "user": user,
        "host": host,
        "date": now.strftime("%y%m%d"),
        "time": now.strftime("%H%M"),
        "src": source_name,
    }


def expand_payload_template(
    template: str,
    *,
    source_name: str = "",
    now: _dt.datetime | None = None,
) -> ExpandedPayload:
    """Fill ``{user}``, ``{host}``, ``{date}``, ``{time}``, ``{src}`` in *template*.

    The result keeps printable ASCII only and is cut to 8 characters, the
    TrustMark text-mode limit. An empty result means no watermark.
    """
    values = _token_values(source_name, now or _dt.datetime.now())
    filled = _TOKEN_RE.sub(lambda m: values.get(m.group(1), ""), template or "")
    cleaned = _ascii(filled).strip()
    if len(cleaned) <= MAX_TEXT_CHARS:
        return ExpandedPayload(cleaned, False)
    return ExpandedPayload(cleaned[:MAX_TEXT_CHARS].strip(), True)


def normalize_strength(strength: float) -> float:
    """Return *strength* when it is inside the TrustMark slider range."""
    value = float(strength)
    if value < STRENGTH_MIN or value > STRENGTH_MAX:
        raise ValueError(
            f"Watermark strength must be between {STRENGTH_MIN:.1f} and {STRENGTH_MAX:.1f}."
        )
    return value


def encode_text(text: str) -> str:
    """Return the 100-character ``0``/``1`` packet for *text*."""
    cleaned = normalize_watermark_text(text)
    bits = "".join(format(ord(ch), "07b") for ch in cleaned)
    return encode_data_bits(bits)


def encode_text_tensor(text: str) -> np.ndarray:
    """Float32 secret tensor shaped ``(1, 100)`` for ``encoder_Q.onnx``."""
    packet = encode_text(text)
    values = [1.0 if bit == "1" else 0.0 for bit in packet]
    return np.asarray([values], dtype=np.float32)


def _text_from_data_bits(data_bits: str) -> str:
    chars: list[str] = []
    for i in range(0, len(data_bits) - 6, 7):
        chars.append(chr(int(data_bits[i : i + 7], 2)))
    return "".join(chars).rstrip("\x00").strip()


def decode_text_packet(packet: str) -> tuple[str, bool, int]:
    """Decode a 100-bit packet. Returns ``(text, ok, bit_flips)``."""
    recovered = decode_packet(packet)
    if recovered is None:
        return "", False, -1
    data_bits, flips = recovered
    return _text_from_data_bits(data_bits), True, flips


def logits_to_packet(logits: np.ndarray) -> str:
    """Threshold decoder logits the way TrustMark does (``>= 0`` is bit 1)."""
    flat = np.asarray(logits, dtype=np.float32).reshape(-1)
    if flat.shape != (PACKET_BITS,):
        raise ValueError(f"Expected {PACKET_BITS} watermark logits, got {flat.shape[0]}.")
    return "".join("1" if value >= 0.0 else "0" for value in flat)


def majority_packet(packets: list[str]) -> str:
    """Bit-wise majority vote. Ties go to 1."""
    if not packets:
        raise ValueError("No watermark packets to vote on.")
    rows = [[1 if bit == "1" else 0 for bit in packet] for packet in packets]
    votes = np.mean(np.asarray(rows, dtype=np.float32), axis=0)
    return "".join("1" if bit >= 0.5 else "0" for bit in votes)
