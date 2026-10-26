//! SMPTE RDD 36 entropy coding for ProRes scanned coefficients.
//!
//! Implements the bit-exact run/level/sign coder from SMPTE RDD 36:2022
//! section 7.1.1, including:
//!
//! * Golomb-Rice / exponential-Golomb combination codes
//!   (`RICE_EXP_COMBO_CODE(lastRiceQ, kRice, kExp)`).
//! * Pure exponential-Golomb codes (`EXP_GOLOMB_CODE(k)`), which equal
//!   `RICE_EXP_COMBO_CODE(0, k, k+1)`.
//! * Signed integer-to-symbol mapping `S(n)` (Table 8): even symbols
//!   encode non-negative values, odd ones encode negatives.
//! * DC coding: first DC = `EXP_GOLOMB_CODE(5)` of `S(first_dc)`; each
//!   subsequent DC differential is coded with the codebook adapted from
//!   `|previousDCDiff|` (Table 9), then sign-flipped if the previous
//!   diff was negative.
//! * AC coding: alternating `run` (codebook by Table 10 from
//!   `previousRun`) and `abs_level_minus_1` (codebook by Table 11 from
//!   `previousLevelSymbol`), each level followed by a 1-bit sign.
//!
//! The coder operates on the **slice-scanned** coefficient array
//! (frequency-index-major across all blocks of a component) — see
//! `slice.rs` for the scan, and `block_scan.rs` for the per-block
//! progressive zig-zag pattern.

use oxideav_core::{Error, Result};

use crate::bitstream::{BitReader, BitWriter};

// ---------------------------------------------------------------------
// Golomb-Rice / exp-Golomb combination codes (RDD 36 §7.1.1.1).
// ---------------------------------------------------------------------

/// Decode one symbol from a Golomb-Rice / exp-Golomb combination code
/// with parameters `last_rice_q`, `k_rice`, `k_exp`. Maps to the spec's
/// `RICE_EXP_COMBO_CODE(last_rice_q, k_rice, k_exp)`.
///
/// To decode: count leading zeros to obtain `q`. If `q <= last_rice_q`
/// the codeword is an order-`k_rice` Golomb-Rice codeword (q + 1 + k_rice
/// bits, value = q * 2^k_rice + tail). Otherwise the first
/// `last_rice_q + 1` bits are zero (already consumed in the q count
/// implicitly — see decode logic below), and the rest is an order-`k_exp`
/// exp-Golomb codeword whose decoded value is added to
/// `(last_rice_q + 1) * 2^k_rice`.
pub fn read_combo(
    br: &mut BitReader<'_>,
    last_rice_q: u32,
    k_rice: u32,
    k_exp: u32,
) -> Result<u32> {
    // Count leading zeros, but cap to avoid runaway on garbage data.
    // `q` represents the unary prefix length up to a certain max.
    // For the Rice branch, q ranges over 0..=last_rice_q (then a 1 bit).
    // For the Exp branch, after the first (last_rice_q + 1) zeros we
    // start an exp-Golomb code. Its prefix length (from that point) is
    // q' = floor(log2(n + 2^k_exp)) - k_exp; for k_exp >= 0 and 32-bit n
    // this is at most ~32. Total leading zeros are bounded.
    const MAX_PREFIX: u32 = 64;
    let mut q = 0u32;
    while br.read_bit()? == 0 {
        q += 1;
        if q > MAX_PREFIX {
            return Err(Error::invalid("prores entropy: unary prefix too long"));
        }
    }
    if q <= last_rice_q {
        // Rice branch: q * 2^kRice + tail.
        let tail = if k_rice == 0 {
            0
        } else {
            br.read_bits(k_rice)?
        };
        // Compose in `u64` so a corrupt `(q, k_rice)` pair cannot overflow
        // the shift/add; a value beyond `u32::MAX` is not a legal symbol.
        let value = (((q as u64) << k_rice) + tail as u64)
            .try_into()
            .map_err(|_| Error::invalid("prores entropy: combo rice value out of range"))?;
        Ok(value)
    } else {
        // Exp-Golomb branch. Already consumed `q` zeros + one 1 bit.
        // We need to redo the exp-Golomb decode from scratch starting
        // *after* the first (last_rice_q + 1) zeros — but the simpler
        // formulation: the original codeword from the start has prefix
        // length q (zeros) and a separator 1, which corresponds to an
        // exp-Golomb code of order k_exp with the prefix length being
        // (q - (last_rice_q + 1)). For order-k_exp exp-Golomb,
        // length = q' + 1 + (q' + k_exp), where q' = code level.
        let q_exp = q - (last_rice_q + 1);
        let suffix_bits = q_exp + k_exp;
        // A spec-legal symbol fits in 32 bits, so its order-`k_exp`
        // exp-Golomb suffix is at most 32 bits wide. A wider suffix can
        // only arise from a corrupt unary prefix; reject it rather than
        // calling `read_bits` outside its `1..=32` contract (and rather
        // than shifting `1u64 << q_exp` off the end of a `u64`).
        if suffix_bits > 32 {
            return Err(Error::invalid(
                "prores entropy: combo exp-golomb suffix too wide",
            ));
        }
        let suffix = if suffix_bits == 0 {
            0
        } else {
            br.read_bits(suffix_bits)?
        };
        // Order-k_exp exp-Golomb decoded value is `(2^q_exp << k_exp) - 2^k_exp + suffix`.
        let exp_val = ((1u64 << q_exp) << k_exp) - (1u64 << k_exp) + suffix as u64;
        // Compose the final symbol in `u64` and reject anything that would
        // not fit `u32` — a corrupt codeword can drive this past the
        // 32-bit symbol range, which the old `as u32` truncation papered
        // over (and the surrounding `+` overflowed in a debug build).
        let value = (((last_rice_q as u64 + 1) << k_rice) + exp_val)
            .try_into()
            .map_err(|_| Error::invalid("prores entropy: combo exp value out of range"))?;
        Ok(value)
    }
}

/// Encode one symbol into a Golomb-Rice / exp-Golomb combination code.
///
/// Inverse of [`read_combo`].
pub fn write_combo(bw: &mut BitWriter, n: u32, last_rice_q: u32, k_rice: u32, k_exp: u32) {
    let switch_value = (last_rice_q + 1) << k_rice;
    if n < switch_value {
        // Rice branch: q = n / 2^kRice, r = n mod 2^kRice.
        let q = n >> k_rice;
        let r = n & ((1u32 << k_rice) - 1);
        for _ in 0..q {
            bw.write_bit(0);
        }
        bw.write_bit(1);
        if k_rice > 0 {
            bw.write_bits(r, k_rice);
        }
    } else {
        // Exp-Golomb branch: emit (last_rice_q + 1) zeros prefix, then
        // an order-k_exp exp-Golomb codeword for `n - switch_value`.
        let n2 = n - switch_value;
        // Order-k_exp exp-Golomb code-level q_exp = floor(log2(n2 + 2^k_exp)) - k_exp.
        let x = (n2 as u64) + (1u64 << k_exp);
        let q_exp = (63 - x.leading_zeros()) - k_exp;
        // Total leading zeros = (last_rice_q + 1) + q_exp; then a 1 bit; then suffix.
        for _ in 0..(last_rice_q + 1 + q_exp) {
            bw.write_bit(0);
        }
        bw.write_bit(1);
        let suffix_bits = q_exp + k_exp;
        if suffix_bits > 0 {
            // suffix = n2 + 2^k_exp - 2^(q_exp + k_exp) (last suffix_bits bits)
            let suffix_val = (x - (1u64 << (q_exp + k_exp))) as u32;
            bw.write_bits(suffix_val, suffix_bits);
        }
    }
}

/// Pure exp-Golomb of order `k`. Equivalent to
/// `RICE_EXP_COMBO_CODE(0, k, k+1)` only for the special pairing the spec
/// uses for `EXP_GOLOMB_CODE(k)`. The simplest direct encoding/decoding
/// is convenient where the spec invokes `EXP_GOLOMB_CODE(k)` directly.
pub fn read_exp_golomb(br: &mut BitReader<'_>, k: u32) -> Result<u32> {
    const MAX_PREFIX: u32 = 32;
    let mut q = 0u32;
    while br.read_bit()? == 0 {
        q += 1;
        if q > MAX_PREFIX {
            return Err(Error::invalid("prores entropy: exp-golomb prefix too long"));
        }
    }
    let bits = q + k;
    // A spec-legal value fits in 32 bits, so the order-`k` exp-Golomb
    // suffix is at most 32 bits wide. A wider suffix can only come from a
    // corrupt unary prefix; reject it rather than calling `read_bits`
    // outside its `1..=32` contract (and rather than shifting `1u64 << q`
    // off the end of a `u64`).
    if bits > 32 {
        return Err(Error::invalid("prores entropy: exp-golomb suffix too wide"));
    }
    let suffix = if bits == 0 { 0 } else { br.read_bits(bits)? };
    // n + 2^k = 2^(q+k), zero-extended by q bits.
    // value = (2^q << k) - 2^k + suffix.
    let val = ((1u64 << q) << k) - (1u64 << k) + suffix as u64;
    // Reject (rather than silently truncate via `as u32`) a corrupt
    // codeword whose value exceeds the 32-bit symbol range.
    val.try_into()
        .map_err(|_| Error::invalid("prores entropy: exp-golomb value out of range"))
}

pub fn write_exp_golomb(bw: &mut BitWriter, n: u32, k: u32) {
    let x = (n as u64) + (1u64 << k);
    let q = (63 - x.leading_zeros()) - k;
    for _ in 0..q {
        bw.write_bit(0);
    }
    bw.write_bit(1);
    let bits = q + k;
    if bits > 0 {
        let suffix = (x - (1u64 << (q + k))) as u32;
        bw.write_bits(suffix, bits);
    }
}

// ---------------------------------------------------------------------
// Codebook adaptation tables (RDD 36 §7.1.1.3 / §7.1.1.4).
// ---------------------------------------------------------------------

/// `(last_rice_q, k_rice, k_exp)` triple for one of the supported
/// codebooks. `last_rice_q == 0 && k_rice == 0` indicates a pure
/// exp-Golomb code of order `k_exp - 1`... not used here directly.
/// We carry whichever values the table specifies and let
/// `read_combo` / `write_combo` (or the dedicated exp-Golomb path) pick
/// the right path.
#[derive(Copy, Clone, Debug)]
pub enum Codebook {
    /// `EXP_GOLOMB_CODE(k)`.
    ExpGolomb(u32),
    /// `RICE_EXP_COMBO_CODE(last_rice_q, k_rice, k_exp)`.
    RiceExp {
        last_rice_q: u32,
        k_rice: u32,
        k_exp: u32,
    },
}

impl Codebook {
    pub fn read(self, br: &mut BitReader<'_>) -> Result<u32> {
        match self {
            Codebook::ExpGolomb(k) => read_exp_golomb(br, k),
            Codebook::RiceExp {
                last_rice_q,
                k_rice,
                k_exp,
            } => read_combo(br, last_rice_q, k_rice, k_exp),
        }
    }

    pub fn write(self, bw: &mut BitWriter, n: u32) {
        match self {
            Codebook::ExpGolomb(k) => write_exp_golomb(bw, n, k),
            Codebook::RiceExp {
                last_rice_q,
                k_rice,
                k_exp,
            } => write_combo(bw, n, last_rice_q, k_rice, k_exp),
        }
    }
}

/// Codebook for a `dc_coeff_difference` element given the absolute value
/// of the previous diff (Table 9).
pub fn dc_diff_codebook(prev_abs: u32) -> Codebook {
    match prev_abs {
        0 => Codebook::ExpGolomb(0),
        1 => Codebook::ExpGolomb(1),
        2 => Codebook::RiceExp {
            last_rice_q: 1,
            k_rice: 2,
            k_exp: 3,
        },
        _ => Codebook::ExpGolomb(3),
    }
}

/// Codebook for a `run` element given the previous run (Table 10).
pub fn run_codebook(prev_run: u32) -> Codebook {
    match prev_run {
        0 | 1 => Codebook::RiceExp {
            last_rice_q: 2,
            k_rice: 0,
            k_exp: 1,
        },
        2 | 3 => Codebook::RiceExp {
            last_rice_q: 1,
            k_rice: 0,
            k_exp: 1,
        },
        4 => Codebook::ExpGolomb(0),
        5..=8 => Codebook::RiceExp {
            last_rice_q: 1,
            k_rice: 1,
            k_exp: 2,
        },
        9..=14 => Codebook::ExpGolomb(1),
        _ => Codebook::ExpGolomb(2), // 15 and above
    }
}

/// Codebook for an `abs_level_minus_1` element given the previous level
/// symbol (Table 11). `prev_level_symbol == 0` corresponds to a previous
/// level magnitude of 1, since `abs_level = abs_level_minus_1 + 1`.
pub fn level_codebook(prev_level_symbol: u32) -> Codebook {
    match prev_level_symbol {
        0 => Codebook::RiceExp {
            last_rice_q: 2,
            k_rice: 0,
            k_exp: 2,
        },
        1 => Codebook::RiceExp {
            last_rice_q: 1,
            k_rice: 0,
            k_exp: 1,
        },
        2 => Codebook::RiceExp {
            last_rice_q: 2,
            k_rice: 0,
            k_exp: 1,
        },
        3 => Codebook::ExpGolomb(0),
        4..=7 => Codebook::ExpGolomb(1),
        _ => Codebook::ExpGolomb(2), // 8 and above
    }
}

// ---------------------------------------------------------------------
// DC + AC coding (slice-scanned coefficient stream, one component).
// ---------------------------------------------------------------------

/// Decode `num_blocks * 64` quantised DCT coefficients in slice-scan order
/// from the entropy-coded bitstream `data` (which spans `data_len` bytes
/// — not necessarily byte-aligned at the end; trailing bits up to a byte
/// boundary are ignored as required by the spec).
///
/// The output `coeffs` is laid out as the spec's slice-scanned array:
/// for frequency-index n in `0..64`, the next `num_blocks` entries are
/// the n-th coefficient of each block in the slice, in macroblock order
/// (and within each macroblock, block-within-MB order).
pub fn decode_scanned_coefficients(data: &[u8], num_blocks: usize) -> Result<Vec<i32>> {
    let total = num_blocks
        .checked_mul(64)
        .ok_or_else(|| Error::invalid("prores entropy: num_blocks overflow"))?;
    let mut coeffs = vec![0i32; total];

    // A slice carrying zero blocks has no DC/AC syntax at all per §7.1.1:
    // the first-DC read below would otherwise index an empty `coeffs`.
    // Return the empty scan array without consuming any bitstream bits.
    if num_blocks == 0 {
        return Ok(coeffs);
    }

    let mut br = BitReader::new(data);

    // ---- DC coefficients (one per block) ----
    // First DC: order-5 exp-Golomb of S(first_dc).
    let s = read_exp_golomb(&mut br, 5)?;
    let first_dc = inv_signed_mapping(s);
    coeffs[0] = first_dc;
    let mut previous_dc_coeff = first_dc;
    let mut previous_dc_diff: i32 = 3; // Initial "previous diff" magnitude is 3 (per §7.1.1.3).
    for n in 1..num_blocks {
        let cb = dc_diff_codebook(previous_dc_diff.unsigned_abs());
        let s = cb.read(&mut br)?;
        let mut diff = inv_signed_mapping(s);
        if previous_dc_diff < 0 {
            // `wrapping_neg` guards the `-i32::MIN` corner a corrupt
            // symbol can hand us; legal DC diffs are tiny so the result
            // is identical for any valid stream.
            diff = diff.wrapping_neg();
        }
        // Wrapping accumulation: legal DC values stay well within `i32`,
        // but an adversarial difference chain could otherwise drive the
        // running sum past `i32::MAX` and panic in a debug build.
        let dc = previous_dc_coeff.wrapping_add(diff);
        coeffs[n] = dc;
        previous_dc_coeff = dc;
        previous_dc_diff = diff;
    }

    // ---- AC coefficients (run + level + sign over remaining positions) ----
    let mut n = num_blocks; // first AC index in the scanned array
    let mut previous_run: u32 = 4; // initial codebook = EXP_GOLOMB_CODE(0)
                                   // Per §7.1.1.4, initial "previous level" magnitude is 2, so
                                   // `previousLevelSymbol = |2| - 1 = 1`, which selects
                                   // `RICE_EXP_COMBO_CODE(1, 0, 1)` for the first abs_level_minus_1.
    let mut previous_level_symbol: u32 = 1;

    while n < total && !br.end_of_data() {
        // run
        let run_cb = run_codebook(previous_run);
        let run = run_cb.read(&mut br)?;
        previous_run = run;
        // skip `run` zeros
        let advance = run as usize;
        if n + advance >= total {
            // Trailing run with no level — should be the implicit final
            // run "by reaching the end of the array". Spec says only
            // runs terminated by levels are encoded; if we ran off the
            // end with the prior level present, that means the stream
            // is malformed.
            return Err(Error::invalid("prores entropy: AC run overruns array"));
        }
        n += advance;
        // abs_level_minus_1
        let lvl_cb = level_codebook(previous_level_symbol);
        let abs_minus_1 = lvl_cb.read(&mut br)?;
        previous_level_symbol = abs_minus_1;
        // Wrapping arithmetic guards the corners a corrupt `abs_minus_1`
        // symbol can reach (`i32::MAX + 1`, `i32::MIN * -1`); legal AC
        // levels are small so valid streams are unaffected.
        let abs_level = (abs_minus_1 as i32).wrapping_add(1);
        // sign bit
        let sign = br.read_bit()?;
        let level = abs_level.wrapping_mul(1 - 2 * sign as i32);
        coeffs[n] = level;
        n += 1;
    }
    // Remaining positions (if any) are zero — already initialised that way.

    Ok(coeffs)
}

/// Encode `num_blocks * 64` slice-scanned coefficients into a byte
/// buffer, padded out to the next byte boundary with zero bits.
///
/// `coeffs` must be laid out in slice-scan order (DC block 0, DC block 1,
/// ..., DC block N-1, AC freq 1 block 0, AC freq 1 block 1, ...).
pub fn encode_scanned_coefficients(coeffs: &[i32], num_blocks: usize) -> Result<Vec<u8>> {
    let total = num_blocks * 64;
    if coeffs.len() != total {
        return Err(Error::invalid(
            "prores entropy: coefficient buffer size mismatch",
        ));
    }
    let mut bw = BitWriter::new();

    // Symmetric with `decode_scanned_coefficients`: a zero-block slice
    // emits no DC/AC syntax. Return an empty payload before the first-DC
    // read below would index an empty `coeffs`.
    if num_blocks == 0 {
        return Ok(bw.finish());
    }

    // ---- DC ----
    let first_dc = coeffs[0];
    write_exp_golomb(&mut bw, signed_mapping(first_dc), 5);
    let mut previous_dc_coeff = first_dc;
    let mut previous_dc_diff: i32 = 3;
    for n in 1..num_blocks {
        let dc = coeffs[n];
        let diff = dc - previous_dc_coeff;
        let cb = dc_diff_codebook(previous_dc_diff.unsigned_abs());
        // The decoder applies a sign flip when previous_dc_diff < 0; do
        // the inverse here so the round-trip stays exact.
        let stored = if previous_dc_diff < 0 { -diff } else { diff };
        cb.write(&mut bw, signed_mapping(stored));
        previous_dc_coeff = dc;
        previous_dc_diff = diff;
    }

    // ---- AC: scan from index `num_blocks` to `total`, emitting (run, level, sign) ----
    let mut previous_run: u32 = 4;
    // Initial previous level magnitude is 2 → previousLevelSymbol = 1 (§7.1.1.4).
    let mut previous_level_symbol: u32 = 1;
    let mut run_acc: u32 = 0;
    for n in num_blocks..total {
        let v = coeffs[n];
        if v == 0 {
            run_acc += 1;
            continue;
        }
        // emit run
        let run_cb = run_codebook(previous_run);
        run_cb.write(&mut bw, run_acc);
        previous_run = run_acc;
        // emit level
        let abs_level = v.unsigned_abs();
        let abs_minus_1 = abs_level - 1;
        let lvl_cb = level_codebook(previous_level_symbol);
        lvl_cb.write(&mut bw, abs_minus_1);
        previous_level_symbol = abs_minus_1;
        // sign: 0 = positive, 1 = negative
        bw.write_bit(if v < 0 { 1 } else { 0 });
        run_acc = 0;
    }
    // Trailing zeros run is implicit (no level emitted). Per spec we
    // terminate by `endOfData()` — we just stop and pad the byte.

    // Pad with zero bits to next byte boundary (spec's `zero_bit` loop).
    bw.align_byte();
    Ok(bw.finish())
}

/// Signed integer-to-symbol mapping S(n) (RDD 36 §7.1.1.2 Table 8).
///
/// `n >= 0  -> S(n) = 2 * n`
/// `n <  0  -> S(n) = 2 * |n| - 1`
pub fn signed_mapping(n: i32) -> u32 {
    if n >= 0 {
        (2 * n) as u32
    } else {
        (2 * (-n) - 1) as u32
    }
}

/// Inverse of [`signed_mapping`].
pub fn inv_signed_mapping(s: u32) -> i32 {
    if (s & 1) == 0 {
        (s >> 1) as i32
    } else {
        // `wrapping_add(1)` guards the `s == u32::MAX` corner (which a
        // corrupt symbol can reach) from panicking in a debug build; for
        // every legal symbol `s + 1` does not overflow.
        -((s.wrapping_add(1) >> 1) as i32)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_block_slice_decodes_to_empty_without_reading_bits() {
        // A slice carrying zero blocks has no DC/AC syntax (§7.1.1). The
        // decoder must return an empty scan array without indexing the
        // empty `coeffs` Vec or consuming any payload bits — regardless of
        // what (if anything) follows. Exercise both an empty payload and a
        // non-empty one to confirm no bits are read.
        for payload in [&[][..], &[0x88][..], &[0xff, 0x00, 0x13][..]] {
            let out = decode_scanned_coefficients(payload, 0).expect("zero-block decode");
            assert!(out.is_empty(), "zero-block scan array must be empty");
        }
    }

    #[test]
    fn zero_block_slice_encodes_to_empty_payload() {
        // Symmetric encoder behaviour: a zero-block slice emits no syntax.
        let out = encode_scanned_coefficients(&[], 0).expect("zero-block encode");
        assert!(out.is_empty(), "zero-block payload must be empty");
        // And the round-trip stays consistent.
        let back = decode_scanned_coefficients(&out, 0).expect("zero-block decode");
        assert!(back.is_empty());
    }

    #[test]
    fn signed_mapping_roundtrip() {
        for n in -1000..=1000 {
            let s = signed_mapping(n);
            assert_eq!(inv_signed_mapping(s), n, "roundtrip n={n}");
        }
    }

    #[test]
    fn signed_mapping_table8() {
        // From Table 8 of RDD 36:
        // 0->0, -1->1, +1->2, -2->3, +2->4, -3->5, +3->6
        assert_eq!(signed_mapping(0), 0);
        assert_eq!(signed_mapping(-1), 1);
        assert_eq!(signed_mapping(1), 2);
        assert_eq!(signed_mapping(-2), 3);
        assert_eq!(signed_mapping(2), 4);
        assert_eq!(signed_mapping(-3), 5);
        assert_eq!(signed_mapping(3), 6);
    }

    #[test]
    fn exp_golomb_roundtrip() {
        for k in [0u32, 1, 2, 3, 5] {
            for v in [0u32, 1, 2, 3, 7, 15, 16, 100, 1000, 65_535] {
                let mut bw = BitWriter::new();
                write_exp_golomb(&mut bw, v, k);
                let buf = bw.finish();
                let mut br = BitReader::new(&buf);
                let got = read_exp_golomb(&mut br, k).expect("decode");
                assert_eq!(got, v, "k={k} v={v}");
            }
        }
    }

    #[test]
    fn combo_roundtrip() {
        let params = [
            (1u32, 2u32, 3u32),
            (2, 0, 1),
            (1, 0, 1),
            (1, 1, 2),
            (2, 0, 2),
        ];
        for (lq, kr, ke) in params {
            for v in 0u32..200 {
                let mut bw = BitWriter::new();
                write_combo(&mut bw, v, lq, kr, ke);
                let buf = bw.finish();
                let mut br = BitReader::new(&buf);
                let got = read_combo(&mut br, lq, kr, ke).expect("decode");
                assert_eq!(got, v, "(lq={lq},kr={kr},ke={ke}) v={v}");
            }
        }
    }

    #[test]
    fn combo_eq_exp_golomb_when_lq_zero() {
        // Per spec: RICE_EXP_COMBO_CODE(0, k, k+1) ≡ EXP_GOLOMB_CODE(k).
        for k in [0u32, 1, 2, 3, 5] {
            for v in [0u32, 1, 5, 100, 1000] {
                let mut a = BitWriter::new();
                write_exp_golomb(&mut a, v, k);
                let mut b = BitWriter::new();
                write_combo(&mut b, v, 0, k, k + 1);
                assert_eq!(a.finish(), b.finish(), "k={k} v={v}");
            }
        }
    }

    #[test]
    fn rdd36_scanned_coeffs_roundtrip() {
        // 1 macroblock of 4 luma blocks (4:2:2 luma component) → 4 blocks * 64 = 256 coeffs.
        let num_blocks = 4;
        let mut coeffs = vec![0i32; num_blocks * 64];
        // DC values
        coeffs[0] = 100;
        coeffs[1] = 102;
        coeffs[2] = 99;
        coeffs[3] = 101;
        // Sparse AC values at varied positions
        coeffs[num_blocks] = 5; // first AC of block 0
        coeffs[num_blocks * 3 + 1] = -3; // freq-3, block 1
        coeffs[num_blocks * 10 + 2] = 1;
        coeffs[num_blocks * 20 + 3] = -1;

        let buf = encode_scanned_coefficients(&coeffs, num_blocks).unwrap();
        let decoded = decode_scanned_coefficients(&buf, num_blocks).unwrap();
        assert_eq!(decoded.len(), coeffs.len());
        for (i, (a, b)) in coeffs.iter().zip(decoded.iter()).enumerate() {
            assert_eq!(a, b, "coeff {i}");
        }
    }

    #[test]
    fn rdd36_dc_only_roundtrip() {
        let num_blocks = 8;
        let mut coeffs = vec![0i32; num_blocks * 64];
        let dcs = [10, -10, 20, 0, -50, 50, 100, -100];
        for (i, dc) in dcs.iter().enumerate() {
            coeffs[i] = *dc;
        }
        let buf = encode_scanned_coefficients(&coeffs, num_blocks).unwrap();
        let decoded = decode_scanned_coefficients(&buf, num_blocks).unwrap();
        for i in 0..num_blocks {
            assert_eq!(decoded[i], coeffs[i], "DC {i}");
        }
        for i in num_blocks..coeffs.len() {
            assert_eq!(decoded[i], 0, "AC {i} should stay zero");
        }
    }

    #[test]
    fn rdd36_random_pattern_roundtrip() {
        // Pseudo-random pattern with realistic ProRes-like sparsity.
        let num_blocks = 16;
        let mut coeffs = vec![0i32; num_blocks * 64];
        // DCs
        let mut dc = 200i32;
        for n in 0..num_blocks {
            dc += if n % 2 == 0 { -5 } else { 7 };
            coeffs[n] = dc;
        }
        // Some AC sprinkled near low frequencies
        for f in 1..16 {
            for b in 0..num_blocks {
                let idx = f * num_blocks + b;
                let v = ((b as i32 + f as i32 * 3) % 7) - 3;
                if v != 0 && (b + f) % 3 == 0 {
                    coeffs[idx] = v;
                }
            }
        }
        let buf = encode_scanned_coefficients(&coeffs, num_blocks).unwrap();
        let decoded = decode_scanned_coefficients(&buf, num_blocks).unwrap();
        assert_eq!(decoded, coeffs);
    }
}
