"""BCH error correction for a TrustMark Q payload.

TrustMark protects a 100-bit watermark with a binary BCH code so a decoder
can repair a few flipped bits. Variant Q's default schema (BCH_5) uses:

* GF(2^7) with primitive polynomial 137 (x^7 + x^3 + 1)
* designed correction radius t = 5 (35 parity bits)
* 61 data bits, then 4 schema bits (``0001``) that are not part of the code

This is an independent implementation of that code, checked against the
published TrustMark test packet. It is not a copy of the Linux kernel BCH
library.
"""

from __future__ import annotations

_M = 7
_N = (1 << _M) - 1
_PRIMITIVE = 137
_T = 5
_DATA_BITS = 61
_VERSION_BITS = "0001"

_EXP = [0] * (2 * _N)
_LOG = [0] * (_N + 1)


def _build_tables() -> list[int]:
    """Return generator coefficients (index is degree) and fill GF tables."""
    x = 1
    for i in range(_N):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & (1 << _M):
            x ^= _PRIMITIVE
    for i in range(_N, 2 * _N):
        _EXP[i] = _EXP[i - _N]

    roots = [0] * (_N + 1)
    for i in range(_T):
        r = 2 * i + 1
        for _step in range(_M):
            roots[r] = 1
            r = (2 * r) % _N

    gen = [1]
    for i in range(_N):
        if not roots[i]:
            continue
        root = _EXP[i]
        nxt = [0] * (len(gen) + 1)
        nxt[len(gen)] = 1
        for j in range(len(gen) - 1, 0, -1):
            nxt[j] = _mul(gen[j], root) ^ gen[j - 1]
        nxt[0] = _mul(gen[0], root)
        gen = nxt
    return gen


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[(_LOG[a] + _LOG[b]) % _N]


def _inv(a: int) -> int:
    return _EXP[(_N - _LOG[a]) % _N]


_GEN = _build_tables()
ECC_BITS = len(_GEN) - 1
PACKET_BITS = _DATA_BITS + ECC_BITS + len(_VERSION_BITS)


def _poly_mod(message: int) -> int:
    """Remainder of ``message * x^ECC_BITS`` divided by the generator."""
    gen = 0
    for coeff in reversed(_GEN):
        gen = (gen << 1) | (1 if coeff else 0)
    rem = message << ECC_BITS
    top = rem.bit_length() - 1
    while top >= ECC_BITS:
        rem ^= gen << (top - ECC_BITS)
        top = rem.bit_length() - 1
    return rem


def encode_data_bits(data_bits: str) -> str:
    """Pad *data_bits* to 61 bits and return the 100-bit BCH_5 packet."""
    if any(bit not in "01" for bit in data_bits):
        raise ValueError("Watermark data bits must be 0 or 1.")
    if len(data_bits) > _DATA_BITS:
        raise ValueError(f"Watermark data is longer than {_DATA_BITS} bits.")
    packet_d = data_bits.ljust(_DATA_BITS, "0")
    pad = (8 - (_DATA_BITS % 8)) % 8
    padded = packet_d + ("0" * pad)
    ecc = format(_poly_mod(int(padded, 2)), f"0{ECC_BITS}b")
    packet = packet_d + ecc + _VERSION_BITS
    if len(packet) != PACKET_BITS:
        raise RuntimeError("BCH packet was not 100 bits.")
    return packet


def _syndromes(code: str) -> list[int]:
    syn: list[int] = []
    for j in range(1, 2 * _T + 1):
        point = _EXP[j % _N]
        acc = 0
        for ch in code:
            acc = _mul(acc, point) ^ (1 if ch == "1" else 0)
        syn.append(acc)
    return syn


def _berlekamp_massey(syn: list[int]) -> list[int]:
    locator = [1]
    prev = [1]
    length = 0
    shift = 1
    prev_disc = 1
    for n in range(len(syn)):
        disc = syn[n]
        for i in range(1, min(length, len(locator) - 1) + 1):
            disc ^= _mul(locator[i], syn[n - i])
        if disc == 0:
            shift += 1
            continue
        saved = locator[:]
        coef = _mul(disc, _inv(prev_disc))
        need = len(prev) + shift
        if len(locator) < need:
            locator.extend([0] * (need - len(locator)))
        for i, coeff in enumerate(prev):
            locator[i + shift] ^= _mul(coef, coeff)
        if 2 * length <= n:
            length = n + 1 - length
            prev = saved
            prev_disc = disc
            shift = 1
        else:
            shift += 1
    return locator


def decode_packet(packet: str) -> tuple[str, int] | None:
    """Return ``(61 data bits, corrected bit flips)`` or ``None``.

    The 4 schema bits are ignored. Recovery always uses the BCH_5 code this
    app writes, which still succeeds when compression flips the schema bits.
    """
    if len(packet) != PACKET_BITS or any(bit not in "01" for bit in packet):
        return None
    data = packet[:_DATA_BITS]
    ecc = packet[_DATA_BITS : _DATA_BITS + ECC_BITS]
    pad = (8 - (_DATA_BITS % 8)) % 8
    code = data + ("0" * pad) + ecc
    syn = _syndromes(code)
    if not any(syn):
        return data, 0

    locator = _berlekamp_massey(syn)
    deg = max(i for i, coeff in enumerate(locator) if coeff)
    nbits = len(code)
    found: list[int] = []
    for degree in range(nbits):
        point = _EXP[(_N - degree) % _N]
        acc = 0
        power = 1
        for coeff in locator:
            if coeff:
                acc ^= _mul(coeff, power)
            power = _mul(power, point)
        if acc == 0:
            found.append(degree)
    if len(found) != deg:
        return None

    chars = list(code)
    for degree in found:
        idx = nbits - 1 - degree
        chars[idx] = "0" if chars[idx] == "1" else "1"
    fixed = "".join(chars)
    if format(_poly_mod(int(fixed[:64], 2)), f"0{ECC_BITS}b") != fixed[64:]:
        return None
    return fixed[:_DATA_BITS], len(found)
