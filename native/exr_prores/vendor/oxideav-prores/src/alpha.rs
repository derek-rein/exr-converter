//! ProRes alpha channel entropy codec (SMPTE RDD 36 §5.3.3 + §7.1.2).
//!
//! When `alpha_channel_type != 0`, each compressed slice carries a
//! `scanned_alpha()` blob immediately after the Cb/Cr coefficient data.
//! Alpha is **not** DCT-coded — it is stored as raster-scanned 16-bit
//! values (8-bit promoted to 16-bit on the wire), then run-length encoded
//! with differential coding.
//!
//! ### Wire format (§5.3.3)
//!
//! ```text
//! scanned_alpha(alphaValues, numValues) {
//!   mask = 0xFF if alpha_channel_type==1 else 0xFFFF
//!   n = 0
//!   previousAlpha = -1
//!   do {
//!     alpha_difference                       // vlc, Table 13 or 14
//!     alpha = previousAlpha + alpha_difference
//!     if (isModuloAlphaDifference())
//!       alpha = alpha & mask
//!     previousAlpha = alpha
//!     run                                    // vlc, Table 12
//!     for (m = 0; m < run; m++)
//!       alphaValues[n++] = alpha
//!   } while (n < numValues)
//!   while (!byteAligned()) zero_bit          // f(1)
//! }
//! ```
//!
//! ### Run-length code (Table 12)
//!
//! | run length  | codeword |
//! |-------------|----------|
//! | 1           | `1`                  |
//! | 2..=16      | `0bbbb` (5 bits, where `bbbb = run-1`) |
//! | 17..=2048   | `00000` + 11 bits `(run-1)` little-endian-numerically |
//!
//! ### Alpha difference codes
//!
//! Table 13 (`alpha_channel_type == 1`, 8-bit alpha):
//!   `±1..=±8` → `0bbbS` (5 bits: `bbb = |diff| - 1`, `S = sign`,
//!   0 = positive). Other → `1` + 8-bit FLC of `(diff mod 256)`.
//!
//! Table 14 (`alpha_channel_type == 2`, 16-bit alpha):
//!   `±1..=±64` → `0bbbbbbS` (8 bits: `bbbbbb = |diff| - 1`,
//!   `S = sign`). Other → `1` + 16-bit FLC of `(diff mod 65536)`.
//!
//! When the escape bit is '1', `isModuloAlphaDifference()` returns true
//! so the decoder reads the FLC as the new alpha modulo `mask`.

use oxideav_core::{Error, Result};

use crate::bitstream::{BitReader, BitWriter};

/// Alpha encoding mode set by the `alpha_channel_type` syntax element.
///
/// `Eight` ⇒ Table 13 difference code, mask = 0xFF.
/// `Sixteen` ⇒ Table 14 difference code, mask = 0xFFFF.
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum AlphaChannelType {
    Eight,
    Sixteen,
}

impl AlphaChannelType {
    pub fn from_code(c: u8) -> Result<Option<Self>> {
        match c {
            0 => Ok(None),
            1 => Ok(Some(AlphaChannelType::Eight)),
            2 => Ok(Some(AlphaChannelType::Sixteen)),
            _ => Err(Error::unsupported(format!(
                "prores: alpha_channel_type {c} not supported"
            ))),
        }
    }

    pub fn code(self) -> u8 {
        match self {
            AlphaChannelType::Eight => 1,
            AlphaChannelType::Sixteen => 2,
        }
    }

    pub fn mask(self) -> u32 {
        match self {
            AlphaChannelType::Eight => 0xFF,
            AlphaChannelType::Sixteen => 0xFFFF,
        }
    }
}

const MAX_RUN: u32 = 2048;

/// Decode `num_values` raster-scanned alpha samples from `data`. Output
/// values are 16-bit unsigned (8-bit alpha is sign-extended into u16
/// range — for `AlphaChannelType::Eight` every value is in `0..=255`).
///
/// `data` must be the exact slice payload (no trailing slack); the
/// terminating `byteAligned()` zero-bit pad is implicit.
pub fn decode_scanned_alpha(
    data: &[u8],
    num_values: usize,
    act: AlphaChannelType,
) -> Result<Vec<u16>> {
    let mask = act.mask();
    let mut br = BitReader::new(data);
    let mut out = vec![0u16; num_values];
    let mut n: usize = 0;
    let mut previous_alpha: i32 = -1;

    while n < num_values {
        let (diff, is_modulo) = read_alpha_difference(&mut br, act)?;
        let mut alpha = previous_alpha.wrapping_add(diff);
        if is_modulo {
            alpha = (alpha as u32 & mask) as i32;
        }
        previous_alpha = alpha;

        let run = read_run(&mut br)? as usize;
        if run == 0 {
            return Err(Error::invalid("prores alpha: zero run"));
        }
        if n + run > num_values {
            return Err(Error::invalid(
                "prores alpha: run overruns alphaValues array",
            ));
        }
        let alpha_u = (alpha as u32 & mask) as u16;
        for _ in 0..run {
            out[n] = alpha_u;
            n += 1;
        }
    }
    Ok(out)
}

/// Encode raster-scanned alpha samples into a byte buffer (padded to the
/// next byte boundary with zero bits).
///
/// Encoding emits a "non-modulo" difference (escape bit = 0 / Table
/// codeword) whenever the literal `current - previous` fits in the small
/// codeword range and falls back to the escape FLC otherwise. This is one
/// of the alternatives the spec explicitly permits (§7.1.2).
pub fn encode_scanned_alpha(values: &[u16], act: AlphaChannelType) -> Result<Vec<u8>> {
    let mask = act.mask();
    let mut bw = BitWriter::new();
    let mut n: usize = 0;
    let mut previous_alpha: i32 = -1;
    while n < values.len() {
        let alpha = (values[n] as u32 & mask) as i32;
        let mut run: u32 = 1;
        while n + (run as usize) < values.len() && values[n + (run as usize)] == values[n] {
            if run == MAX_RUN {
                break;
            }
            run += 1;
        }
        let raw_diff: i32 = alpha - previous_alpha;
        write_alpha_difference(&mut bw, raw_diff, alpha, mask, act);
        write_run(&mut bw, run);
        previous_alpha = alpha;
        n += run as usize;
    }
    bw.align_byte();
    Ok(bw.finish())
}

/// Read one alpha_difference syntax element. Returns `(diff, is_modulo)`.
///
/// In the escape (modulo) path the FLC is read as an unsigned addend; the
/// caller sums it with `previousAlpha` and masks with `0xFF` / `0xFFFF`
/// so the spec's two's-complement-mod-mask reconstruction yields the
/// correct alpha regardless of how the producer chose to encode the
/// difference (signed, unsigned, or as the literal alpha value — all
/// three are spec-permitted alternatives because the modulo cancels the
/// difference).
fn read_alpha_difference(br: &mut BitReader<'_>, act: AlphaChannelType) -> Result<(i32, bool)> {
    let escape = br.read_bit()?;
    if escape == 1 {
        let bits = match act {
            AlphaChannelType::Eight => 8,
            AlphaChannelType::Sixteen => 16,
        };
        let raw = br.read_bits(bits)?;
        Ok((raw as i32, true))
    } else {
        // Small-magnitude path. Tables 13 / 14.
        let mag_bits = match act {
            AlphaChannelType::Eight => 3,   // ±1..=±8
            AlphaChannelType::Sixteen => 6, // ±1..=±64
        };
        let mag = br.read_bits(mag_bits)? + 1;
        let sign = br.read_bit()?;
        let val = if sign == 0 { mag as i32 } else { -(mag as i32) };
        Ok((val, false))
    }
}

/// Write an alpha_difference. If the literal `raw_diff` fits the small
/// table (Tables 13/14, ±1..=±8 for 8-bit alpha or ±1..=±64 for 16-bit),
/// use it; otherwise use the escape path with the unsigned FLC of
/// `(alpha - previousAlpha) mod (mask+1)`. The decoder reconstructs the
/// same alpha either way because of the spec's modulo wrap.
fn write_alpha_difference(
    bw: &mut BitWriter,
    raw_diff: i32,
    _alpha: i32,
    mask: u32,
    act: AlphaChannelType,
) {
    let small_max = match act {
        AlphaChannelType::Eight => 8,
        AlphaChannelType::Sixteen => 64,
    };
    let mag_bits = match act {
        AlphaChannelType::Eight => 3,
        AlphaChannelType::Sixteen => 6,
    };
    let abs = raw_diff.unsigned_abs();
    if abs >= 1 && abs <= small_max {
        bw.write_bit(0);
        bw.write_bits(abs - 1, mag_bits);
        bw.write_bit(if raw_diff < 0 { 1 } else { 0 });
    } else {
        bw.write_bit(1);
        let bits = match act {
            AlphaChannelType::Eight => 8,
            AlphaChannelType::Sixteen => 16,
        };
        // Encode raw = (alpha - previous_alpha) mod (mask+1). This
        // sums-to-alpha when added to previous_alpha and masked.
        let raw_u: u32 = (raw_diff as u32) & mask;
        bw.write_bits(raw_u, bits);
    }
}

/// Read one run-length syntax element per Table 12.
fn read_run(br: &mut BitReader<'_>) -> Result<u32> {
    let b0 = br.read_bit()?;
    if b0 == 1 {
        return Ok(1);
    }
    // Next 4 bits: if any of them is non-zero, this is the 5-bit short
    // form (b0 + 4 bits = 5 total) with value `(b1234 + 1)` for runs
    // 2..=16.
    let nibble = br.read_bits(4)?;
    if nibble != 0 {
        return Ok(nibble + 1);
    }
    // Otherwise the prefix is `00000` and an 11-bit FLC follows for runs
    // 17..=2048; value = FLC + 1.
    let flc = br.read_bits(11)?;
    let run = flc + 1;
    if !(17..=2048).contains(&run) {
        return Err(Error::invalid("prores alpha: run length out of range"));
    }
    Ok(run)
}

/// Write a run length per Table 12.
fn write_run(bw: &mut BitWriter, run: u32) {
    debug_assert!((1..=MAX_RUN).contains(&run));
    if run == 1 {
        bw.write_bit(1);
        return;
    }
    if run <= 16 {
        bw.write_bit(0);
        bw.write_bits(run - 1, 4);
        return;
    }
    // 17..=2048
    bw.write_bits(0, 5); // 00000
    bw.write_bits(run - 1, 11);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn roundtrip(values: &[u16], act: AlphaChannelType) {
        let buf = encode_scanned_alpha(values, act).expect("encode");
        let decoded = decode_scanned_alpha(&buf, values.len(), act).expect("decode");
        assert_eq!(decoded, values, "alpha roundtrip mismatch");
    }

    #[test]
    fn run_codeword_length_one() {
        // A run-length of 1 is the single bit '1' followed by 7 zero pads.
        let mut bw = BitWriter::new();
        write_run(&mut bw, 1);
        bw.align_byte();
        let buf = bw.finish();
        assert_eq!(buf, vec![0b1000_0000]);
    }

    #[test]
    fn run_codeword_short() {
        // run = 5 -> 0 + 4-bit (5-1=4) → "0 0100" = 6 bits
        let mut bw = BitWriter::new();
        write_run(&mut bw, 5);
        bw.align_byte();
        let buf = bw.finish();
        // 0 0100 (then 3 zero pad) = 0b00100_000 -> hmm 6 bits + 2 pad
        // 0(b0=0), 0(b1), 1(b2), 0(b3), 0(b4): 0b00100_000 = 0x20
        assert_eq!(buf, vec![0b0010_0000]);
    }

    #[test]
    fn run_roundtrip_all_lengths() {
        for r in 1u32..=MAX_RUN {
            let mut bw = BitWriter::new();
            write_run(&mut bw, r);
            bw.align_byte();
            let buf = bw.finish();
            let mut br = BitReader::new(&buf);
            let got = read_run(&mut br).expect("decode run");
            assert_eq!(got, r, "run {r}");
        }
    }

    #[test]
    fn alpha_8bit_constant_value() {
        // Single run of 16 samples all equal to 0xFF (typical "fully
        // opaque" alpha).
        let values = vec![0xFFu16; 16];
        roundtrip(&values, AlphaChannelType::Eight);
    }

    #[test]
    fn alpha_16bit_constant_value() {
        let values = vec![0xFFFFu16; 32];
        roundtrip(&values, AlphaChannelType::Sixteen);
    }

    #[test]
    fn alpha_8bit_first_run_diff_minus_1() {
        // First diff: alpha - (-1). For alpha=0 the diff is +1 → fits Table 13.
        let values = vec![0u16, 0u16, 0u16];
        roundtrip(&values, AlphaChannelType::Eight);
    }

    #[test]
    fn alpha_8bit_alternating_two_values() {
        let mut v = Vec::new();
        for i in 0..32 {
            v.push(if i & 1 == 0 { 0x10 } else { 0x20 });
        }
        roundtrip(&v, AlphaChannelType::Eight);
    }

    #[test]
    fn alpha_8bit_escape_path_large_diff() {
        // Diff > 8 forces escape.
        let values = vec![0u16, 100u16, 100u16];
        roundtrip(&values, AlphaChannelType::Eight);
    }

    #[test]
    fn alpha_16bit_escape_path_large_diff() {
        let values = vec![0u16, 5000u16, 5000u16, 5000u16];
        roundtrip(&values, AlphaChannelType::Sixteen);
    }

    #[test]
    fn alpha_8bit_random_pattern() {
        // Pseudo-random opaque-ish alpha pattern with some variation.
        let mut v = Vec::new();
        for i in 0..256 {
            v.push((255u16 - (i as u16 % 32)) & 0xFF);
        }
        roundtrip(&v, AlphaChannelType::Eight);
    }

    #[test]
    fn alpha_16bit_long_runs() {
        // Run-lengths > 16 force the escape run codeword.
        let mut v = vec![0x1234u16; 100];
        v.extend(vec![0xABCDu16; 50]);
        roundtrip(&v, AlphaChannelType::Sixteen);
    }

    #[test]
    fn alpha_channel_type_codes() {
        assert_eq!(AlphaChannelType::from_code(0).unwrap(), None);
        assert_eq!(
            AlphaChannelType::from_code(1).unwrap(),
            Some(AlphaChannelType::Eight)
        );
        assert_eq!(
            AlphaChannelType::from_code(2).unwrap(),
            Some(AlphaChannelType::Sixteen)
        );
        assert!(AlphaChannelType::from_code(3).is_err());
        assert_eq!(AlphaChannelType::Eight.mask(), 0xFF);
        assert_eq!(AlphaChannelType::Sixteen.mask(), 0xFFFF);
    }
}
