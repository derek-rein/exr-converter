//! ProRes slice encode / decode (SMPTE RDD 36 §5.3 + §7.1 + §7.2).
//!
//! A slice covers `slice_size_in_mb` macroblocks (1, 2, 4, or 8). For
//! 4:2:2 each MB has 4 luma blocks + 2 Cb + 2 Cr (8 blocks total). For
//! 4:4:4 each MB has 4 luma + 4 Cb + 4 Cr (12 blocks total).
//!
//! Coding (per component, three times — luma, Cb, Cr):
//! 1. Apply the forward block scan to each block, yielding 64 scanned
//!    coefficients per block (`QFS`).
//! 2. Apply the slice scan to interleave coefficients across blocks: the
//!    output coefficient at index `n * sliceSizeInMb * nB + m * nB + b`
//!    is `QFS[m,b][n]` — i.e., DC of every block first, then AC freq-1
//!    of every block, etc.
//! 3. Apply the entropy coder ([`crate::entropy`]) to the scanned array.
//! 4. Pad to next byte boundary.

use oxideav_core::{Error, Result};

use crate::entropy::{decode_scanned_coefficients, encode_scanned_coefficients};
use crate::frame::ChromaFormat;
use crate::quant::{BLOCK_SCAN_INTERLACED, BLOCK_SCAN_PROGRESSIVE};

/// Maximum macroblocks per slice (1, 2, 4, or 8).
pub const MAX_MBS_PER_SLICE: usize = 8;

/// Number of luma blocks per macroblock — always 4 for ProRes.
pub const LUMA_BLOCKS_PER_MB: usize = 4;

/// Position of each 4:4:4 chroma 8x8 block inside a 16x16 macroblock, as
/// `(bx, by)` in 8-pixel units, in bitstream order.
///
/// EXR Converter patch: column order (TL, BL, TR, BR) to match FFmpeg's
/// `proresdec` / `prores_ks`, which also decode Apple-encoded 4444 files
/// correctly. Upstream oxideav-prores 0.1.1 used the
/// luma raster order (TL, TR, BL, BR) for both the encoder and the decoder,
/// so its own round trip passed while every standard decoder swapped the
/// top-right and bottom-left chroma blocks.
pub const CHROMA_444_OFFSETS: [(usize, usize); 4] = [(0, 0), (0, 1), (1, 0), (1, 1)];

/// Number of chroma blocks per macroblock per component for the given
/// chroma format. 2 for 4:2:2, 4 for 4:4:4.
pub fn chroma_blocks_per_mb(chroma: ChromaFormat) -> usize {
    match chroma {
        ChromaFormat::Y422 => 2,
        ChromaFormat::Y444 => 4,
    }
}

/// Total blocks per MB across all three color components.
pub fn blocks_per_mb(chroma: ChromaFormat) -> usize {
    LUMA_BLOCKS_PER_MB + 2 * chroma_blocks_per_mb(chroma)
}

/// Output of [`decode_slice`]: blocks in **natural (row-major) order**,
/// pre-dequant, organized as `[MB0_Y0..Y3, MB0_Cb0..CbN, MB0_Cr0..CrN, MB1_...]`
/// where N = `chroma_blocks_per_mb(chroma)`.
pub struct DecodedSlice {
    pub mb_count: u8,
    pub quant_index: u8,
    pub blocks: Vec<[i32; 64]>,
}

/// Encode the per-component coefficient bitstreams for one slice and
/// return `(encoded_y, encoded_cb, encoded_cr)`. Each output vector is
/// the entropy-coded payload (NOT including the slice header).
pub fn encode_slice_components(
    mb_count: usize,
    chroma: ChromaFormat,
    interlaced: bool,
    blocks: &[[i32; 64]],
) -> Result<(Vec<u8>, Vec<u8>, Vec<u8>)> {
    if mb_count == 0 || mb_count > MAX_MBS_PER_SLICE {
        return Err(Error::invalid("prores: slice mb_count out of range"));
    }
    let cb_per_mb = chroma_blocks_per_mb(chroma);
    let per_mb = LUMA_BLOCKS_PER_MB + 2 * cb_per_mb;
    if blocks.len() != mb_count * per_mb {
        return Err(Error::invalid("prores: slice block-count mismatch"));
    }
    let scan = if interlaced {
        &BLOCK_SCAN_INTERLACED
    } else {
        &BLOCK_SCAN_PROGRESSIVE
    };

    // Build the three slice-scanned coefficient arrays.
    let y_coeffs = build_slice_scan(blocks, mb_count, per_mb, 0, LUMA_BLOCKS_PER_MB, scan);
    let cb_coeffs = build_slice_scan(
        blocks,
        mb_count,
        per_mb,
        LUMA_BLOCKS_PER_MB,
        cb_per_mb,
        scan,
    );
    let cr_coeffs = build_slice_scan(
        blocks,
        mb_count,
        per_mb,
        LUMA_BLOCKS_PER_MB + cb_per_mb,
        cb_per_mb,
        scan,
    );

    let y_bits = encode_scanned_coefficients(&y_coeffs, mb_count * LUMA_BLOCKS_PER_MB)?;
    let cb_bits = encode_scanned_coefficients(&cb_coeffs, mb_count * cb_per_mb)?;
    let cr_bits = encode_scanned_coefficients(&cr_coeffs, mb_count * cb_per_mb)?;
    Ok((y_bits, cb_bits, cr_bits))
}

/// Decode the three per-component coefficient bitstreams back to
/// natural-order blocks. `blocks_out` is laid out the same way as the
/// `blocks` argument to [`encode_slice_components`].
pub fn decode_slice_components(
    y_data: &[u8],
    cb_data: &[u8],
    cr_data: &[u8],
    mb_count: usize,
    chroma: ChromaFormat,
    interlaced: bool,
) -> Result<Vec<[i32; 64]>> {
    let cb_per_mb = chroma_blocks_per_mb(chroma);
    let per_mb = LUMA_BLOCKS_PER_MB + 2 * cb_per_mb;
    let scan = if interlaced {
        &BLOCK_SCAN_INTERLACED
    } else {
        &BLOCK_SCAN_PROGRESSIVE
    };

    let y_blocks = mb_count * LUMA_BLOCKS_PER_MB;
    let c_blocks = mb_count * cb_per_mb;
    let y_coeffs = decode_scanned_coefficients(y_data, y_blocks)?;
    let cb_coeffs = decode_scanned_coefficients(cb_data, c_blocks)?;
    let cr_coeffs = decode_scanned_coefficients(cr_data, c_blocks)?;

    let mut out = vec![[0i32; 64]; mb_count * per_mb];
    inverse_slice_scan(
        &y_coeffs,
        &mut out,
        mb_count,
        per_mb,
        0,
        LUMA_BLOCKS_PER_MB,
        scan,
    );
    inverse_slice_scan(
        &cb_coeffs,
        &mut out,
        mb_count,
        per_mb,
        LUMA_BLOCKS_PER_MB,
        cb_per_mb,
        scan,
    );
    inverse_slice_scan(
        &cr_coeffs,
        &mut out,
        mb_count,
        per_mb,
        LUMA_BLOCKS_PER_MB + cb_per_mb,
        cb_per_mb,
        scan,
    );
    Ok(out)
}

/// Apply the forward slice scan to one component of a slice. Returns
/// `mb_count * blocks_per_mb_component * 64` coefficients in
/// frequency-major order.
fn build_slice_scan(
    blocks: &[[i32; 64]],
    mb_count: usize,
    per_mb: usize,
    component_offset: usize,
    blocks_per_mb_component: usize,
    block_scan: &[u8; 64],
) -> Vec<i32> {
    let total_blocks = mb_count * blocks_per_mb_component;
    let mut out = vec![0i32; total_blocks * 64];
    // For each frequency-index n in [0, 64): for each MB (m), for each
    // block-within-MB (b): store block's natural-order coefficient
    // located at `block_scan` inverse position == ?
    //
    // Given block_scan[v*8+u] = scanned-index k for natural position
    // (u, v), the natural-order entry that ends up at scanned index n is
    // the (u, v) such that block_scan[v*8+u] == n. Use the inverse:
    // build a freq→natural lookup.
    let mut inv = [0u8; 64];
    for (nat_idx, &k) in block_scan.iter().enumerate() {
        inv[k as usize] = nat_idx as u8;
    }
    for n in 0..64 {
        let nat_pos = inv[n] as usize;
        for m in 0..mb_count {
            for b in 0..blocks_per_mb_component {
                let block_idx = m * per_mb + component_offset + b;
                let blk = &blocks[block_idx];
                let dst_idx = n * total_blocks + m * blocks_per_mb_component + b;
                out[dst_idx] = blk[nat_pos];
            }
        }
    }
    out
}

/// Inverse of [`build_slice_scan`]: scatter slice-scanned coefficients
/// into the natural-order block array.
#[allow(clippy::too_many_arguments)]
fn inverse_slice_scan(
    coeffs: &[i32],
    blocks_out: &mut [[i32; 64]],
    mb_count: usize,
    per_mb: usize,
    component_offset: usize,
    blocks_per_mb_component: usize,
    block_scan: &[u8; 64],
) {
    let total_blocks = mb_count * blocks_per_mb_component;
    let mut inv = [0u8; 64];
    for (nat_idx, &k) in block_scan.iter().enumerate() {
        inv[k as usize] = nat_idx as u8;
    }
    for n in 0..64 {
        let nat_pos = inv[n] as usize;
        for m in 0..mb_count {
            for b in 0..blocks_per_mb_component {
                let block_idx = m * per_mb + component_offset + b;
                let src_idx = n * total_blocks + m * blocks_per_mb_component + b;
                blocks_out[block_idx][nat_pos] = coeffs[src_idx];
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn synth_blocks(mb_count: usize, chroma: ChromaFormat) -> Vec<[i32; 64]> {
        let per_mb = blocks_per_mb(chroma);
        let mut blocks = Vec::with_capacity(mb_count * per_mb);
        for m in 0..mb_count {
            for b in 0..per_mb {
                let mut blk = [0i32; 64];
                blk[0] = ((m * 13 + b * 7) as i32 % 41) - 20; // DC
                                                              // Sparse AC coefficients
                for k in 1..8 {
                    blk[k] = (((m + b + k) as i32) % 5) - 2;
                }
                blocks.push(blk);
            }
        }
        blocks
    }

    #[test]
    fn slice_components_roundtrip_422() {
        let mb_count = 4;
        let chroma = ChromaFormat::Y422;
        let blocks = synth_blocks(mb_count, chroma);
        let (y, cb, cr) = encode_slice_components(mb_count, chroma, false, &blocks).unwrap();
        let decoded = decode_slice_components(&y, &cb, &cr, mb_count, chroma, false).unwrap();
        assert_eq!(decoded.len(), blocks.len());
        for (i, (a, b)) in blocks.iter().zip(decoded.iter()).enumerate() {
            assert_eq!(a, b, "block {i} differs");
        }
    }

    #[test]
    fn slice_components_roundtrip_444() {
        let mb_count = 2;
        let chroma = ChromaFormat::Y444;
        let blocks = synth_blocks(mb_count, chroma);
        let (y, cb, cr) = encode_slice_components(mb_count, chroma, false, &blocks).unwrap();
        let decoded = decode_slice_components(&y, &cb, &cr, mb_count, chroma, false).unwrap();
        for (i, (a, b)) in blocks.iter().zip(decoded.iter()).enumerate() {
            assert_eq!(a, b, "block {i} differs");
        }
    }

    #[test]
    fn slice_components_roundtrip_8mb() {
        let mb_count = 8;
        let chroma = ChromaFormat::Y422;
        let blocks = synth_blocks(mb_count, chroma);
        let (y, cb, cr) = encode_slice_components(mb_count, chroma, false, &blocks).unwrap();
        let decoded = decode_slice_components(&y, &cb, &cr, mb_count, chroma, false).unwrap();
        assert_eq!(decoded, blocks);
    }

    #[test]
    fn chroma_444_offsets_are_column_order() {
        // TL, BL, TR, BR as (bx, by): the FFmpeg / prores_ks 4:4:4 chroma order.
        assert_eq!(CHROMA_444_OFFSETS, [(0, 0), (0, 1), (1, 0), (1, 1)]);
    }
}
