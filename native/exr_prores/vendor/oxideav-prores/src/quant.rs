//! Quantisation matrices and scan tables for SMPTE RDD 36 ProRes.
//!
//! Per RDD 36 §7.3, when `load_luma_quantization_matrix` /
//! `load_chroma_quantization_matrix` are both 0 the default 8x8
//! all-4s matrix is used for both components — this flat default
//! applies to `apcn` (Standard) and `apch` (HQ) streams that carry no
//! custom matrix. Streams that want non-flat matrices include them in
//! the frame header explicitly.
//!
//! Two perceptual presets are provided. [`QuantMatrices::perceptual`]
//! is the JPEG K.1/K.2 matrix normalised to DC=2 and used directly —
//! one matrix for every profile. [`QuantMatrices::perceptual_for_profile`]
//! blends that same JPEG-derived matrix with the flat all-4s default
//! using a profile-aware blend factor derived from each profile's
//! [`crate::frame::Profile::default_quant_index`] — lower-quality
//! profiles (Proxy / LT) get heavier high-frequency rolloff for tighter
//! packets at matched perceptual quality; higher-quality profiles
//! (HQ / 4444 / 4444 XQ) preserve more HF detail by pulling the matrix
//! back toward flat. The blend factor is `default_quant_index / 8`, so
//! Proxy (qi=8) gets the full perceptual matrix, 4444 XQ (qi=1) gets
//! 1/8 perceptual + 7/8 flat. Every blended weight is clamped to the
//! RDD 36 §7.3 valid range `2..=63`.
//!
//! `QSCALE_TABLE` (Table 15) maps `quantization_index` (1..=224) to the
//! quantisation scale factor (1..=512). Values 1..=128 are linear, then
//! `128 + 4 * (i - 128)` from index 129 up to 224.
//!
//! `BLOCK_SCAN_PROGRESSIVE` is the 8x8 forward block scan pattern for
//! progressive pictures (Figure 4); `BLOCK_SCAN_INTERLACED` is the
//! field-picture variant (Figure 5). Both store `scan[v][u]` packed in
//! row-major order, giving the scanned-coefficient index in 0..64 for
//! the natural-order position `(u, v)`.

/// Default per-block weight used by ProRes when no custom matrix is
/// loaded. Spec value: all-4s.
pub const DEFAULT_QMAT: [u8; 64] = [4u8; 64];

/// Reference perceptual luma quantisation matrix derived from
/// ISO/IEC 10918-1 Annex K Table K.1 (the standard JPEG luma
/// quantisation table), normalised so that the DC weight is 2 — i.e.
/// `clamp(round(K1[v][u] * 2 / 16), 2, 63)` — which gives the
/// encoder twice the DC/low-frequency precision of the spec's flat
/// all-4s default at the same `quantization_index` while letting
/// high-frequency weights climb (rolling off perceptually
/// less-significant detail).
///
/// The shape — small weights at low spatial frequency and large
/// weights toward the bottom-right of the 8x8 grid — exploits the
/// human visual system's reduced sensitivity to high-frequency detail
/// (CSF rolloff). Natural images have a near-1/f² power spectrum, so
/// most signal energy lives at low spatial frequency; preserving it
/// twice as accurately while quantising the high end more coarsely
/// is a strict R-D win on natural-image-like content (smaller
/// bitstream at matched PSNR, see the `perceptual_quant` integration
/// test).
///
/// Indexed in natural (row-major) order: `weight = PERCEPTUAL_LUMA_QMAT[v * 8 + u]`.
pub const PERCEPTUAL_LUMA_QMAT: [u8; 64] = [
    2, 2, 2, 2, 3, 5, 6, 8, // v=0
    2, 2, 2, 2, 3, 7, 8, 7, // v=1
    2, 2, 2, 3, 5, 7, 9, 7, // v=2
    2, 2, 3, 4, 6, 11, 10, 8, // v=3
    2, 3, 5, 7, 9, 14, 13, 10, // v=4
    3, 4, 7, 8, 10, 13, 14, 12, // v=5
    6, 8, 10, 11, 13, 15, 15, 13, // v=6
    9, 12, 12, 12, 14, 13, 13, 12, // v=7
];

/// Reference perceptual chroma quantisation matrix derived from
/// ISO/IEC 10918-1 Annex K Table K.2 (the standard JPEG chroma
/// quantisation table), normalised so that the DC weight is 2 — i.e.
/// `clamp(round(K2[v][u] * 2 / 16), 2, 63)`.
///
/// Chroma resolution sensitivity falls off faster than luma, so the
/// high-frequency weights saturate at a lower ceiling than the luma
/// matrix. Indexed in natural (row-major) order.
pub const PERCEPTUAL_CHROMA_QMAT: [u8; 64] = [
    2, 2, 3, 6, 12, 12, 12, 12, // v=0
    2, 3, 3, 8, 12, 12, 12, 12, // v=1
    3, 3, 7, 12, 12, 12, 12, 12, // v=2
    6, 8, 12, 12, 12, 12, 12, 12, // v=3
    12, 12, 12, 12, 12, 12, 12, 12, // v=4
    12, 12, 12, 12, 12, 12, 12, 12, // v=5
    12, 12, 12, 12, 12, 12, 12, 12, // v=6
    12, 12, 12, 12, 12, 12, 12, 12, // v=7
];

// ---------------------------------------------------------------------
// Per-profile signature quantisation matrices (RDD 36 §6.1.1 / §7.3).
//
// The six ProRes profiles each carry a characteristic quantisation
// weight matrix in the frame header. Unlike the JPEG-derived
// [`PERCEPTUAL_LUMA_QMAT`] preset above (a general R-D shaping matrix),
// these are the exact per-profile weights carried by the reference
// ProRes streams in the in-tree corpus (`docs/video/prores/fixtures/`).
// They let the encoder reproduce each profile's native quantisation
// signature — the proxy profile's aggressive high-frequency 63-clamp,
// the standard/LT low-frequency-preserving shapes, and the near-flat
// high-quality tables — rather than the flat all-4s default.
//
// RDD 36 stores the matrix in natural (row-major) order: entry
// `[v * 8 + u]` weights the DCT coefficient at spatial frequency
// `(u, v)`, so the DC weight is `[0]` (top-left) and the highest
// spatial frequency is `[63]` (bottom-right). Every constant below is
// therefore directly usable as a [`QuantMatrices`] field and is pinned
// byte-for-byte against the corresponding corpus fixture's carried
// matrix by `tests/quant_matrix_signature.rs` (decoded with this
// crate's own parser — no external decoder consulted).

/// 422 Proxy (`apco`) luma signature matrix. High-frequency weights
/// saturate to the §6.1.1 maximum of 63 along the anti-diagonal — the
/// aggressive quantisation that gives the proxy profile its small
/// packets. Natural (row-major) order.
pub const SIGNATURE_PROXY_LUMA_QMAT: [u8; 64] = [
    4, 7, 9, 11, 13, 14, 15, 63, //
    7, 7, 11, 12, 14, 15, 63, 63, //
    9, 11, 13, 14, 15, 63, 63, 63, //
    11, 11, 13, 14, 63, 63, 63, 63, //
    11, 13, 14, 63, 63, 63, 63, 63, //
    13, 14, 63, 63, 63, 63, 63, 63, //
    13, 63, 63, 63, 63, 63, 63, 63, //
    63, 63, 63, 63, 63, 63, 63, 63, //
];

/// 422 Proxy (`apco`) chroma signature matrix. Proxy is the only
/// profile whose chroma matrix differs from its luma matrix — the
/// chroma 63-clamp starts one anti-diagonal earlier, so the profile
/// carries both tables (`load_luma = load_chroma = 1`). Natural order.
pub const SIGNATURE_PROXY_CHROMA_QMAT: [u8; 64] = [
    4, 7, 9, 11, 13, 14, 63, 63, //
    7, 7, 11, 12, 14, 63, 63, 63, //
    9, 11, 13, 14, 63, 63, 63, 63, //
    11, 11, 13, 14, 63, 63, 63, 63, //
    11, 13, 14, 63, 63, 63, 63, 63, //
    13, 14, 63, 63, 63, 63, 63, 63, //
    13, 63, 63, 63, 63, 63, 63, 63, //
    63, 63, 63, 63, 63, 63, 63, 63, //
];

/// 422 LT (`apcs`) signature matrix, used for both luma and chroma. A
/// JPEG-shaped table relaxed at the high end relative to Standard.
/// Natural order.
pub const SIGNATURE_LT_QMAT: [u8; 64] = [
    4, 5, 6, 7, 9, 11, 13, 15, //
    5, 5, 7, 8, 11, 13, 15, 17, //
    6, 7, 9, 11, 13, 15, 15, 17, //
    7, 7, 9, 11, 13, 15, 17, 19, //
    7, 9, 11, 13, 14, 16, 19, 23, //
    9, 11, 13, 14, 16, 19, 23, 29, //
    9, 11, 13, 15, 17, 21, 28, 35, //
    11, 13, 16, 17, 21, 28, 35, 41, //
];

/// 422 Standard (`apcn`) signature matrix, used for both luma and
/// chroma. Tighter low-frequency quantisation than LT. Natural order.
pub const SIGNATURE_STANDARD_QMAT: [u8; 64] = [
    4, 4, 5, 5, 6, 7, 7, 9, //
    4, 4, 5, 6, 7, 7, 9, 9, //
    5, 5, 6, 7, 7, 9, 9, 10, //
    5, 5, 6, 7, 7, 9, 9, 10, //
    5, 6, 7, 7, 8, 9, 10, 12, //
    6, 7, 7, 8, 9, 10, 12, 15, //
    6, 7, 7, 9, 10, 11, 14, 17, //
    7, 7, 9, 10, 11, 14, 17, 21, //
];

/// High-quality signature matrix shared by 422 HQ (`apch`), 4444
/// (`ap4h`), and 4444 XQ (`ap4x`), used for both luma and chroma. It is
/// near-flat (mostly 4) — the three profiles diverge through their
/// slice-level `quantization_index` envelope rather than the matrix.
/// Natural order.
pub const SIGNATURE_HQ_QMAT: [u8; 64] = [
    4, 4, 4, 4, 4, 4, 4, 4, //
    4, 4, 4, 4, 4, 4, 4, 4, //
    4, 4, 4, 4, 4, 4, 4, 4, //
    4, 4, 4, 4, 4, 4, 4, 5, //
    4, 4, 4, 4, 4, 4, 5, 5, //
    4, 4, 4, 4, 4, 5, 5, 6, //
    4, 4, 4, 4, 5, 5, 6, 7, //
    4, 4, 4, 4, 5, 6, 7, 7, //
];

/// Per-component pair of 8x8 quantisation weight matrices for the
/// ProRes encoder.
///
/// Indexed in natural (row-major) order — entry `[v * 8 + u]` is the
/// weight `W[v][u]` from RDD 36 §7.3. Values must be in `2..=63`.
/// `Default::default()` returns the spec's flat all-4s matrices for
/// both components (equivalent to the legacy encoder behaviour with
/// `load_luma_quantization_matrix = load_chroma_quantization_matrix = 0`).
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
pub struct QuantMatrices {
    pub luma: [u8; 64],
    pub chroma: [u8; 64],
}

impl QuantMatrices {
    /// All-4s flat matrices — matches what the encoder emits when no
    /// custom matrices are loaded.
    pub const fn flat() -> Self {
        Self {
            luma: DEFAULT_QMAT,
            chroma: DEFAULT_QMAT,
        }
    }

    /// JPEG-derived perceptual matrices from [`PERCEPTUAL_LUMA_QMAT`]
    /// and [`PERCEPTUAL_CHROMA_QMAT`].
    pub const fn perceptual() -> Self {
        Self {
            luma: PERCEPTUAL_LUMA_QMAT,
            chroma: PERCEPTUAL_CHROMA_QMAT,
        }
    }

    /// Profile-aware perceptual preset: blend the JPEG-derived
    /// perceptual matrix with the flat all-4s default in proportion to
    /// the supplied profile's [`crate::frame::Profile::default_quant_index`].
    ///
    /// `blend = default_quant_index / 8` (a rational in `1/8..=8/8`),
    /// and every output weight is
    /// `clamp(round((1 - blend) * 4 + blend * W_perceptual[k]), 2, 63)`.
    ///
    /// Concretely the six profiles map to:
    ///
    /// | Profile     | qi | blend | HF rolloff       |
    /// |-------------|----|-------|------------------|
    /// | Proxy       |  8 |  8/8  | full (identical to [`Self::perceptual`]) |
    /// | LT          |  6 |  6/8  | heavy            |
    /// | Standard    |  4 |  4/8  | moderate         |
    /// | HQ          |  2 |  2/8  | light            |
    /// | 4444        |  2 |  2/8  | light            |
    /// | 4444 XQ     |  1 |  1/8  | minimal (matrix close to flat)   |
    ///
    /// Lower-quality profiles get heavier high-frequency rolloff for
    /// tighter packets at matched perceptual quality; higher-quality
    /// profiles preserve more HF detail. Per RDD 36 §7.3 the weights
    /// stay in `2..=63` (the clamp covers the corner where rounding
    /// would land on `1` or `0`). The chroma matrix derives from
    /// [`PERCEPTUAL_CHROMA_QMAT`] using the same blend; flat-anchored
    /// chroma weights also rise toward the saturated end as `blend`
    /// approaches 1.
    pub fn perceptual_for_profile(profile: crate::frame::Profile) -> Self {
        // Blend numerator is the profile's default_quant_index. Range
        // covers 1 (4444 XQ) up to 8 (Proxy) per RDD 36 Profile table —
        // div 8 fits cleanly into integer math (anti-bias rounding via
        // +4 / 8 below).
        let bn = profile.default_quant_index() as u32; // 1..=8
        let mut luma = [0u8; 64];
        let mut chroma = [0u8; 64];
        for k in 0..64 {
            // blended = ((8 - bn) * 4 + bn * W) / 8, with +4 for round-to-nearest.
            let lw = ((8 - bn) * 4 + bn * PERCEPTUAL_LUMA_QMAT[k] as u32 + 4) / 8;
            let cw = ((8 - bn) * 4 + bn * PERCEPTUAL_CHROMA_QMAT[k] as u32 + 4) / 8;
            luma[k] = lw.clamp(2, 63) as u8;
            chroma[k] = cw.clamp(2, 63) as u8;
        }
        Self { luma, chroma }
    }

    /// The native per-profile signature quantisation-matrix pair (RDD 36
    /// §6.1.1 / §7.3) — the exact weights the reference ProRes streams
    /// carry for each profile (see the `SIGNATURE_*_QMAT` constants).
    ///
    /// Unlike [`Self::perceptual_for_profile`] (a general R-D shaping
    /// preset), this reproduces the profile's *native* quantisation
    /// signature, so an encoder can emit streams whose carried matrices
    /// match the reference corpus byte-for-byte:
    ///
    /// | Profile  | luma            | chroma          | wire flags |
    /// |----------|-----------------|-----------------|------------|
    /// | Proxy    | proxy luma      | proxy chroma    | `(1, 1)`   |
    /// | LT       | LT              | = luma          | `(1, 0)`   |
    /// | Standard | standard        | = luma          | `(1, 0)`   |
    /// | HQ       | HQ              | = luma          | `(1, 0)`   |
    /// | 4444     | HQ              | = luma          | `(1, 0)`   |
    /// | 4444 XQ  | HQ              | = luma          | `(1, 0)`   |
    ///
    /// Proxy is the only profile whose chroma matrix differs from its
    /// luma matrix, so it is the only one that carries two tables; the
    /// other five reuse the luma matrix for chroma via the §6.1.1
    /// fallback, which [`Self::wire_flags`] emits as `(1, 0)` (a single
    /// 64-byte table, 84-byte frame header).
    pub fn signature_for_profile(profile: crate::frame::Profile) -> Self {
        use crate::frame::Profile;
        match profile {
            Profile::Proxy => Self {
                luma: SIGNATURE_PROXY_LUMA_QMAT,
                chroma: SIGNATURE_PROXY_CHROMA_QMAT,
            },
            Profile::Lt => Self {
                luma: SIGNATURE_LT_QMAT,
                chroma: SIGNATURE_LT_QMAT,
            },
            Profile::Standard => Self {
                luma: SIGNATURE_STANDARD_QMAT,
                chroma: SIGNATURE_STANDARD_QMAT,
            },
            Profile::Hq | Profile::Prores4444 | Profile::Prores4444Xq => Self {
                luma: SIGNATURE_HQ_QMAT,
                chroma: SIGNATURE_HQ_QMAT,
            },
        }
    }

    /// Recover the `(luma, chroma)` matrix pair a parsed frame header
    /// carries, applying the RDD 36 §6.1.1 chroma-copies-luma fallback.
    ///
    /// The parsed [`crate::frame::FrameHeader`] already exposes the
    /// reconstructed `luma_qmat` / `chroma_qmat` (with the fallback
    /// applied when `load_chroma_quantization_matrix == 0`), so this is a
    /// direct convenience for transcode callers: decode a source frame,
    /// then feed `QuantMatrices::from_header(&fh)` straight into an
    /// [`crate::encoder::EncoderConfig`] to forward the source's exact
    /// quantisation matrices into the re-encode. Round-tripping a header
    /// through this and [`Self::wire_flags`] reproduces the source's
    /// carriage form.
    pub fn from_header(header: &crate::frame::FrameHeader) -> Self {
        Self {
            luma: header.luma_qmat,
            chroma: header.chroma_qmat,
        }
    }

    /// True when both matrices equal the spec's all-4s default.
    pub fn is_default(&self) -> bool {
        self.luma == DEFAULT_QMAT && self.chroma == DEFAULT_QMAT
    }

    /// Minimal RDD 36 §6.1.1 quantization-matrix carriage flags for this
    /// matrix pair: `(load_luma_quantization_matrix,
    /// load_chroma_quantization_matrix)`.
    ///
    /// The two wire flags admit four distinct derivations of the matrices a
    /// decoder reconstructs (see [`crate::frame::QuantizationMatrixSource`]),
    /// and this picks the smallest header that reproduces the pair exactly:
    ///
    /// | luma vs default | chroma vs luma | flags | header cost | reconstruction |
    /// |-----------------|----------------|-------|-------------|----------------|
    /// | equal           | equal          | `(0,0)` | +0 B  | luma = default, chroma = default (§6.1.1 fallback) |
    /// | equal           | differs        | `(0,1)` | +64 B | luma = default, chroma = carried custom |
    /// | differs         | equal          | `(1,0)` | +64 B | luma = carried custom, chroma = luma (§6.1.1 fallback) |
    /// | differs         | differs        | `(1,1)` | +128 B | both carried custom |
    ///
    /// `load_luma` is set iff the luma matrix differs from the §7.2 all-4s
    /// default, since a `load_luma == 0` frame always dequantises luma with
    /// that default. `load_chroma` is set iff the chroma matrix differs from
    /// the *effective* luma matrix, because RDD 36 §6.1.1 specifies that a
    /// `load_chroma == 0` frame reuses the luma matrix for chroma ("the
    /// specified custom luma quantization matrix if
    /// `load_luma_quantization_matrix` is 1 or the default matrix
    /// otherwise"). The effective luma matrix equals `self.luma` in both
    /// `load_luma` cases, so the chroma test reduces to `chroma != luma`.
    ///
    /// A decoder reconstructs exactly `(self.luma, self.chroma)` for every
    /// pair, including the `(0,1)` "default luma, custom chroma" form that
    /// carries a single 64-byte table.
    pub fn wire_flags(&self) -> (bool, bool) {
        let load_luma = self.luma != DEFAULT_QMAT;
        let load_chroma = self.chroma != self.luma;
        (load_luma, load_chroma)
    }

    /// True when every weight is in `2..=63`.
    pub fn weights_valid(&self) -> bool {
        self.luma.iter().all(|w| (2..=63).contains(w))
            && self.chroma.iter().all(|w| (2..=63).contains(w))
    }
}

impl Default for QuantMatrices {
    fn default() -> Self {
        Self::flat()
    }
}

/// SMPTE RDD 36 progressive block scan pattern (Figure 4).
///
/// `scan[v][u]` (with the table laid out row-major as v*8+u) yields the
/// scanned-coefficient index in 0..64 for the natural-order position
/// `(u, v)`. To go from a scanned index back to natural order, use
/// [`PROGRESSIVE_INV_SCAN`].
pub const BLOCK_SCAN_PROGRESSIVE: [u8; 64] = [
    0, 1, 4, 5, 16, 17, 21, 22, // v=0
    2, 3, 6, 7, 18, 20, 23, 28, // v=1
    8, 9, 12, 13, 19, 24, 27, 29, // v=2
    10, 11, 14, 15, 25, 26, 30, 31, // v=3
    32, 33, 37, 38, 45, 46, 53, 54, // v=4
    34, 36, 39, 44, 47, 52, 55, 60, // v=5
    35, 40, 43, 48, 51, 56, 59, 61, // v=6
    41, 42, 49, 50, 57, 58, 62, 63, // v=7
];

/// SMPTE RDD 36 interlaced block scan pattern (Figure 5). Used when
/// `interlace_mode != 0` (field pictures).
pub const BLOCK_SCAN_INTERLACED: [u8; 64] = [
    0, 2, 8, 10, 32, 34, 35, 41, // v=0
    1, 3, 9, 11, 33, 36, 40, 42, // v=1
    4, 6, 12, 14, 37, 39, 43, 49, // v=2
    5, 7, 13, 15, 38, 44, 48, 50, // v=3
    16, 18, 19, 25, 45, 47, 51, 57, // v=4
    17, 20, 24, 26, 46, 52, 56, 58, // v=5
    21, 23, 27, 30, 53, 55, 59, 62, // v=6
    22, 28, 29, 31, 54, 60, 61, 63, // v=7
];

/// Inverse of [`BLOCK_SCAN_PROGRESSIVE`]: at index k (the scanned
/// position), the natural-order (row-major v*8+u) position.
pub const PROGRESSIVE_INV_SCAN: [u8; 64] = invert_scan(&BLOCK_SCAN_PROGRESSIVE);

/// Inverse of [`BLOCK_SCAN_INTERLACED`].
pub const INTERLACED_INV_SCAN: [u8; 64] = invert_scan(&BLOCK_SCAN_INTERLACED);

const fn invert_scan(scan: &[u8; 64]) -> [u8; 64] {
    let mut out = [0u8; 64];
    let mut i = 0;
    while i < 64 {
        out[scan[i] as usize] = i as u8;
        i += 1;
    }
    out
}

/// Quantisation scale factor `qScale` as a function of
/// `quantization_index` (RDD 36 Table 15). Index 0 is unused; valid
/// indices are 1..=224.
pub fn qscale(quantization_index: u8) -> i32 {
    let i = quantization_index as i32;
    if i <= 128 {
        i
    } else {
        128 + 4 * (i - 128)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wire_flags_all_four_combinations() {
        let default = DEFAULT_QMAT;
        let mut custom_a = DEFAULT_QMAT;
        custom_a[7] = 9;
        let mut custom_b = DEFAULT_QMAT;
        custom_b[63] = 40;
        assert_ne!(custom_a, default);
        assert_ne!(custom_b, default);
        assert_ne!(custom_a, custom_b);

        // (0,0): luma == default, chroma == default.
        assert_eq!(
            QuantMatrices {
                luma: default,
                chroma: default
            }
            .wire_flags(),
            (false, false)
        );
        // (0,1): default luma, custom chroma — the form the old derivation
        // could not emit (it forced load_luma whenever either was custom).
        assert_eq!(
            QuantMatrices {
                luma: default,
                chroma: custom_b
            }
            .wire_flags(),
            (false, true)
        );
        // (1,0): custom luma, chroma copies luma (§6.1.1 fallback).
        assert_eq!(
            QuantMatrices {
                luma: custom_a,
                chroma: custom_a
            }
            .wire_flags(),
            (true, false)
        );
        // (1,1): both custom and distinct.
        assert_eq!(
            QuantMatrices {
                luma: custom_a,
                chroma: custom_b
            }
            .wire_flags(),
            (true, true)
        );
        // custom luma + default chroma: chroma differs from luma, so it
        // must be carried explicitly → (1,1), not (1,0).
        assert_eq!(
            QuantMatrices {
                luma: custom_a,
                chroma: default
            }
            .wire_flags(),
            (true, true)
        );
    }

    #[test]
    fn wire_flags_flat_and_perceptual() {
        assert_eq!(QuantMatrices::flat().wire_flags(), (false, false));
        // Every perceptual preset has weight-2 low-frequency entries, so the
        // luma matrix always differs from the all-4s default and the chroma
        // matrix differs from the luma matrix → the full (1,1) carriage.
        assert_eq!(QuantMatrices::perceptual().wire_flags(), (true, true));
        for profile in [
            crate::frame::Profile::Proxy,
            crate::frame::Profile::Lt,
            crate::frame::Profile::Standard,
            crate::frame::Profile::Hq,
            crate::frame::Profile::Prores4444,
            crate::frame::Profile::Prores4444Xq,
        ] {
            assert_eq!(
                QuantMatrices::perceptual_for_profile(profile).wire_flags(),
                (true, true),
                "profile {profile:?} should carry both custom tables",
            );
        }
    }

    #[test]
    fn progressive_scan_is_permutation() {
        let mut seen = [false; 64];
        for &v in &BLOCK_SCAN_PROGRESSIVE {
            assert!(!seen[v as usize], "duplicate {v}");
            seen[v as usize] = true;
        }
        assert!(seen.iter().all(|&b| b));
    }

    #[test]
    fn interlaced_scan_is_permutation() {
        let mut seen = [false; 64];
        for &v in &BLOCK_SCAN_INTERLACED {
            assert!(!seen[v as usize], "duplicate {v}");
            seen[v as usize] = true;
        }
        assert!(seen.iter().all(|&b| b));
    }

    #[test]
    fn inverse_progressive_scan_roundtrips() {
        for k in 0..64 {
            let nat = PROGRESSIVE_INV_SCAN[k] as usize;
            assert_eq!(BLOCK_SCAN_PROGRESSIVE[nat], k as u8);
        }
    }

    #[test]
    fn perceptual_matrices_in_valid_weight_range() {
        // RDD 36 §7.3: weights must be integers in 2..=63.
        let m = QuantMatrices::perceptual();
        assert!(m.weights_valid());
        // DC weight = 2 (twice as fine as the flat all-4 default —
        // this is what lets the perceptual matrix beat flat on PSNR
        // at matched bitrate on natural-image-like spectra).
        assert_eq!(m.luma[0], 2);
        assert_eq!(m.chroma[0], 2);
        // High-frequency luma weight is materially larger than 4.
        assert!(m.luma[63] > 4);
    }

    #[test]
    fn perceptual_matrices_match_jpeg_k1_k2_normalised_dc2() {
        // K.1 and K.2 normalised by 2/16, clamped to 2..=63, with
        // round-half-up. Spot check the most distinctive entries.
        // K.1[0][0] = 16 → round(2.0) = 2
        assert_eq!(PERCEPTUAL_LUMA_QMAT[0], 2);
        // K.1[0][1] = 11 → round(1.375) = 1 → clamp to 2
        assert_eq!(PERCEPTUAL_LUMA_QMAT[1], 2);
        // K.1[0][7] = 61 → round(7.625) = 8
        assert_eq!(PERCEPTUAL_LUMA_QMAT[7], 8);
        // K.1[7][0] = 72 → round(9.0) = 9
        assert_eq!(PERCEPTUAL_LUMA_QMAT[7 * 8], 9);
        // K.2[0][0] = 17 → round(2.125) = 2
        assert_eq!(PERCEPTUAL_CHROMA_QMAT[0], 2);
        // K.2[0][3] = 47 → round(5.875) = 6
        assert_eq!(PERCEPTUAL_CHROMA_QMAT[3], 6);
        // K.2[3][3] = 99 → round(12.375) = 12
        assert_eq!(PERCEPTUAL_CHROMA_QMAT[3 * 8 + 3], 12);
    }

    #[test]
    fn quant_matrices_default_is_flat() {
        assert_eq!(QuantMatrices::default(), QuantMatrices::flat());
        assert!(QuantMatrices::default().is_default());
        assert!(!QuantMatrices::perceptual().is_default());
    }

    #[test]
    fn quant_matrices_weights_valid_rejects_out_of_range() {
        let mut bad = QuantMatrices::flat();
        bad.luma[0] = 1; // below min
        assert!(!bad.weights_valid());
        bad.luma[0] = 64; // above max
        assert!(!bad.weights_valid());
        bad.luma[0] = 4;
        bad.chroma[5] = 0;
        assert!(!bad.weights_valid());
    }

    #[test]
    fn perceptual_for_profile_proxy_equals_perceptual() {
        // Proxy has the highest default qi (8) so the blend factor is
        // 8/8 = 1.0 — the result must coincide with the plain
        // perceptual preset (the JPEG-derived matrix unmodified).
        let p = QuantMatrices::perceptual_for_profile(crate::frame::Profile::Proxy);
        assert_eq!(p, QuantMatrices::perceptual());
    }

    #[test]
    fn perceptual_for_profile_weights_in_valid_range_for_all_profiles() {
        use crate::frame::Profile;
        for &profile in &[
            Profile::Proxy,
            Profile::Lt,
            Profile::Standard,
            Profile::Hq,
            Profile::Prores4444,
            Profile::Prores4444Xq,
        ] {
            let m = QuantMatrices::perceptual_for_profile(profile);
            assert!(
                m.weights_valid(),
                "weights out of 2..=63 for profile {profile:?}: luma {:?} chroma {:?}",
                &m.luma[..],
                &m.chroma[..],
            );
            // The DC weight is the maximum-precision corner — at every
            // blend it should stay ≤ 4 (flat) since both endpoints are
            // ≤ 4 there (flat = 4, perceptual = 2).
            assert!(m.luma[0] <= 4, "DC luma should not exceed 4");
            assert!(m.chroma[0] <= 4, "DC chroma should not exceed 4");
        }
    }

    #[test]
    fn perceptual_for_profile_hf_weight_monotonic_in_quality_tier() {
        // The highest-quality profile (XQ, qi=1) blends mostly toward
        // the flat default (4); the lowest-quality (Proxy, qi=8) blends
        // fully to the JPEG matrix (HF weight = 12 on the deep luma
        // corner). So HF luma weight at index 63 must strictly increase
        // from XQ → 4444/HQ → Standard → LT → Proxy.
        use crate::frame::Profile;
        let xq = QuantMatrices::perceptual_for_profile(Profile::Prores4444Xq).luma[63];
        let hq = QuantMatrices::perceptual_for_profile(Profile::Hq).luma[63];
        let std = QuantMatrices::perceptual_for_profile(Profile::Standard).luma[63];
        let lt = QuantMatrices::perceptual_for_profile(Profile::Lt).luma[63];
        let proxy = QuantMatrices::perceptual_for_profile(Profile::Proxy).luma[63];
        assert!(xq <= hq, "XQ {xq} > HQ {hq} (HF should grow toward Proxy)");
        assert!(hq <= std, "HQ {hq} > Standard {std}");
        assert!(std <= lt, "Standard {std} > LT {lt}");
        assert!(lt <= proxy, "LT {lt} > Proxy {proxy}");
        // Sanity: Proxy hits the full perceptual HF weight (PERCEPTUAL_LUMA_QMAT[63] = 12).
        assert_eq!(proxy, PERCEPTUAL_LUMA_QMAT[63]);
    }

    #[test]
    fn perceptual_for_profile_xq_pulls_toward_flat() {
        // 4444 XQ (qi=1) blend = 1/8 — the resulting matrix should be
        // visibly closer to the flat default than to the full perceptual
        // matrix. Quantify "closer": mean absolute difference from flat
        // < mean absolute difference from perceptual.
        let xq = QuantMatrices::perceptual_for_profile(crate::frame::Profile::Prores4444Xq);
        let flat = QuantMatrices::flat();
        let perc = QuantMatrices::perceptual();
        let dist_flat: u32 = xq
            .luma
            .iter()
            .zip(flat.luma.iter())
            .map(|(a, b)| a.abs_diff(*b) as u32)
            .sum();
        let dist_perc: u32 = xq
            .luma
            .iter()
            .zip(perc.luma.iter())
            .map(|(a, b)| a.abs_diff(*b) as u32)
            .sum();
        assert!(
            dist_flat < dist_perc,
            "XQ matrix distance to flat ({dist_flat}) must be < distance to perceptual ({dist_perc})",
        );
    }

    #[test]
    fn perceptual_for_profile_not_default_for_any_profile() {
        // Even the most flat-leaning profile (4444 XQ, blend = 1/8)
        // must differ from the all-4s default at least in the corners
        // where PERCEPTUAL_*_QMAT carries weights ≥ 12. Otherwise the
        // encoder would emit load_*_qmat = 0 and the matrix selection
        // would be a silent no-op.
        use crate::frame::Profile;
        for &profile in &[
            Profile::Proxy,
            Profile::Lt,
            Profile::Standard,
            Profile::Hq,
            Profile::Prores4444,
            Profile::Prores4444Xq,
        ] {
            let m = QuantMatrices::perceptual_for_profile(profile);
            assert!(
                !m.is_default(),
                "profile {profile:?} blended matrix collapsed to flat default — \
                 encoder would silently emit load_*_qmat = 0",
            );
        }
    }

    #[test]
    fn signature_matrices_all_in_valid_range() {
        // RDD 36 §6.1.1: every entry in 2..=63.
        for m in [
            &SIGNATURE_PROXY_LUMA_QMAT,
            &SIGNATURE_PROXY_CHROMA_QMAT,
            &SIGNATURE_LT_QMAT,
            &SIGNATURE_STANDARD_QMAT,
            &SIGNATURE_HQ_QMAT,
        ] {
            assert!(
                m.iter().all(|&w| (2..=63).contains(&w)),
                "weight out of range"
            );
            assert_eq!(m[0], 4, "DC weight is 4");
        }
    }

    #[test]
    fn signature_matrices_are_low_to_high_frequency_monotone() {
        // Natural (row-major) order: weights are non-decreasing left to
        // right and top to bottom (DC at [0], highest frequency at [63]).
        // This is what distinguishes a natural-order matrix from a
        // zigzag-serialised one and is the structural fact the decoder
        // relies on when it applies qmat[k] to the natural-order block.
        for m in [
            &SIGNATURE_PROXY_LUMA_QMAT,
            &SIGNATURE_LT_QMAT,
            &SIGNATURE_STANDARD_QMAT,
            &SIGNATURE_HQ_QMAT,
        ] {
            for v in 0..8 {
                for u in 0..7 {
                    assert!(m[v * 8 + u] <= m[v * 8 + u + 1], "row {v} not monotone");
                }
            }
            for u in 0..8 {
                for v in 0..7 {
                    assert!(m[v * 8 + u] <= m[(v + 1) * 8 + u], "col {u} not monotone");
                }
            }
        }
    }

    #[test]
    fn signature_for_profile_wire_flags() {
        use crate::frame::Profile;
        // Proxy is the only profile with distinct chroma → carries both
        // tables (1, 1). The rest reuse luma for chroma → (1, 0).
        assert_eq!(
            QuantMatrices::signature_for_profile(Profile::Proxy).wire_flags(),
            (true, true)
        );
        for p in [
            Profile::Lt,
            Profile::Standard,
            Profile::Hq,
            Profile::Prores4444,
            Profile::Prores4444Xq,
        ] {
            let qm = QuantMatrices::signature_for_profile(p);
            assert_eq!(qm.luma, qm.chroma, "{p:?} chroma should equal luma");
            assert_eq!(qm.wire_flags(), (true, false), "{p:?} flags");
            assert!(!qm.is_default(), "{p:?} differs from all-4s default");
        }
    }

    #[test]
    fn signature_hq_family_shares_one_matrix() {
        use crate::frame::Profile;
        let hq = QuantMatrices::signature_for_profile(Profile::Hq);
        assert_eq!(
            hq,
            QuantMatrices::signature_for_profile(Profile::Prores4444)
        );
        assert_eq!(
            hq,
            QuantMatrices::signature_for_profile(Profile::Prores4444Xq)
        );
    }

    #[test]
    fn from_header_recovers_matrix_pair() {
        use crate::frame::{parse_frame_header, write_frame_with_meta, ChromaFormat, FrameMeta};
        // Encode a frame header carrying the proxy signature (both
        // tables), parse it back, and confirm from_header round-trips the
        // pair including the distinct chroma matrix.
        let qm = QuantMatrices::signature_for_profile(crate::frame::Profile::Proxy);
        let (load_luma, load_chroma) = qm.wire_flags();
        let mut buf = Vec::new();
        write_frame_with_meta(
            &mut buf,
            0,
            64,
            64,
            ChromaFormat::Y422,
            0,
            &qm.luma,
            &qm.chroma,
            load_luma,
            load_chroma,
            0,
            FrameMeta::default(),
        );
        let (fh, _) = parse_frame_header(&buf[8..]).unwrap();
        let recovered = QuantMatrices::from_header(&fh);
        assert_eq!(recovered, qm);
        assert_ne!(recovered.luma, recovered.chroma, "proxy chroma differs");
    }

    #[test]
    fn qscale_table15_samples() {
        // Table 15 spot checks.
        assert_eq!(qscale(1), 1);
        assert_eq!(qscale(2), 2);
        assert_eq!(qscale(126), 126);
        assert_eq!(qscale(127), 127);
        assert_eq!(qscale(128), 128);
        assert_eq!(qscale(129), 132);
        assert_eq!(qscale(130), 136);
        assert_eq!(qscale(223), 508);
        assert_eq!(qscale(224), 512);
    }
}
