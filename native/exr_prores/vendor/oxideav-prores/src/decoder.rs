//! ProRes decoder following SMPTE RDD 36 §5 + §7.
//!
//! Glues the frame/picture/slice parsers and the entropy coder to the
//! IDCT and pixel emitter, producing a `VideoFrame` of `Yuv422P` (4:2:2)
//! or `Yuv444P` (4:4:4) depending on `frame_header.chroma_format`.
//!
//! ### Bit-depth selection
//!
//! RDD 36 §5 frame_header / picture_header carry NO bit-depth syntax
//! element — the spec leaves `b` (the per-sample bit depth in §7.5.1) up
//! to the decoder. The CodecParameters that built this decoder pin the
//! output format:
//!
//! * `PixelFormat::Yuv422P` / `Yuv444P` (or none) → 8-bit planes
//!   (single-byte samples in `[0, 255]`).
//! * `PixelFormat::Yuv422P10Le` / `Yuv444P10Le` → 10-bit planes
//!   packed as little-endian u16 pairs in `[0, 1023]`.
//! * `PixelFormat::Yuv422P12Le` / `Yuv444P12Le` → 12-bit planes
//!   packed as little-endian u16 pairs in `[0, 4095]`.
//! * `PixelFormat::Yuv422P16Le` / `Yuv444P16Le` → 16-bit planes
//!   packed as little-endian u16 pairs in `[0, 65535]` (§7.5.1 with
//!   `b = 16`; all 16 bits of every word are significant).
//!
//! The `chroma_format` of the requested pixel format must agree with the
//! frame header's `chroma_format`; mismatches return `Error::invalid`.
//!
//! ### Alpha plane (RDD 36 §7.1.2 + §7.5.2)
//!
//! When the frame header's `alpha_channel_type != 0`, every slice carries
//! a per-pixel raster-scanned alpha array decoded via [`crate::alpha`] and
//! converted to the output bit depth per §7.5.2. The resulting alpha
//! plane is appended as the **fourth** entry of `VideoFrame::planes`, in
//! the same per-sample format as the chroma planes (8-bit byte stride for
//! 8-bit output, 16-bit LE for 10/12-bit).
//!
//! Two request shapes expose it:
//!
//! * **Alpha-typed surface** — `PixelFormat::Yuva422P` / `Yuva444P`
//!   (8-bit) and the deep `Yuva4(2|4)4P{10,12,16}Le` formats request a
//!   four-plane frame whose layout is *guaranteed* by the format:
//!   decoded alpha rides plane 3 converted to the surface depth per
//!   §7.5.2, and a stream that carries no coded alpha gets a
//!   synthesised fully-opaque plane so the declared plane count always
//!   holds. On the `*P16Le` surfaces a 16-bit coded alpha channel
//!   (`alpha_channel_type == 2`) passes through **exactly** — the
//!   §7.5.2 conversion at `b = 16` is the identity — so the full
//!   RDD 36 alpha fidelity is expressible in the type system.
//! * **As-coded surface** — the plane-count convention: `Yuv4(2|4)4P*`
//!   requests append the 4th plane only when the stream codes alpha;
//!   callers detect it by checking `frame.planes.len() == 4`. The 4th
//!   plane is converted to the same §7.5.2 surface depth as the colour
//!   planes, so its significant depth never differs from what the
//!   requested `PixelFormat` already declares (which is why no
//!   per-plane significant-bits side-channel is attached to decoded
//!   frames — it would restate the pixel format).

use oxideav_core::frame::VideoPlane;
use oxideav_core::Decoder;
use oxideav_core::{
    CodecId, CodecParameters, Error, Frame, Packet, PixelFormat, Result, VideoFrame,
};

use crate::alpha::{decode_scanned_alpha, AlphaChannelType};
use crate::dct::{idct8x8, idct8x8_dc_only, is_dc_only};
use crate::frame::{
    compute_slice_sizes, parse_frame, parse_picture_header, parse_slice_header, ChromaFormat,
    FrameHeader,
};
use crate::quant::qscale;
use crate::slice::{
    blocks_per_mb, chroma_blocks_per_mb, decode_slice_components, CHROMA_444_OFFSETS,
    LUMA_BLOCKS_PER_MB,
};

const MB_SIDE_PX: usize = 16;

/// Internal cap on a single decoded frame's plane allocation. Bounds
/// against header-declared dimensions: a malicious header with
/// 1M×1M pixels must NOT trigger a multi-TB allocation. 32k×32k YUV444
/// (3 GB) is far more than any sane container; we cap at that.
const MAX_DECODED_PIXELS: usize = 32_768 * 32_768;

/// Per-component output bit depth.
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum BitDepth {
    /// 8-bit planar output (`Yuv422P` / `Yuv444P`). One byte per sample.
    Eight,
    /// 10-bit planar output (`Yuv422P10Le` / `Yuv444P10Le`). Two bytes
    /// per sample, low-byte-first; valid range `0..=1023`.
    Ten,
    /// 12-bit planar output (`Yuv422P12Le` / `Yuv444P12Le`). Two bytes
    /// per sample, low-byte-first; valid range `0..=4095`.
    Twelve,
    /// 16-bit planar output (`Yuv422P16Le` / `Yuv444P16Le`). Two bytes
    /// per sample, low-byte-first; valid range `0..=65535` — all 16
    /// bits of every word are significant (§7.5.1 with `b = 16`).
    Sixteen,
}

impl BitDepth {
    pub fn bytes_per_sample(self) -> usize {
        match self {
            Self::Eight => 1,
            Self::Ten | Self::Twelve | Self::Sixteen => 2,
        }
    }

    pub fn max_value(self) -> u32 {
        match self {
            Self::Eight => 255,
            Self::Ten => 1023,
            Self::Twelve => 4095,
            Self::Sixteen => 65535,
        }
    }

    /// Bit depth as the integer `b` from RDD 36 §7.5.1.
    pub fn bits(self) -> u32 {
        match self {
            Self::Eight => 8,
            Self::Ten => 10,
            Self::Twelve => 12,
            Self::Sixteen => 16,
        }
    }
}

/// Selects the clamping bounds `(nmin, nmax)` of RDD 36 §7.5.1 for the
/// reconstructed-value → pixel-sample conversion
/// `s = clamp(round(2^b * (v + 256) / 512))`.
///
/// §7.5.1 offers two choices for the clamp limits:
///
/// - **Full** — `nmin = 0`, `nmax = 2^b − 1`; "produce pixel component
///   samples that utilize all available quantization levels". This is
///   the default and matches the planar ranges named in [`BitDepth`]
///   (`0..=255` / `0..=1023` / `0..=4095`).
/// - **Video** — `nmin`/`nmax` set to "the smallest and largest
///   permissible video quantization levels for b-bit samples if
///   avoidance of ITU-R BT.601/BT.709 synchronization/timing reference
///   quantization levels is desired". Those reserved timing-reference
///   levels are the extreme codes `0` and `2^b − 1` (the SAV/EAV
///   excursions of the BT.601/BT.709 digital interface), so the
///   permissible video levels span `1 ..= 2^b − 2`.
///
/// Both options round identically; they differ only in whether the two
/// extreme codes may appear in the output. The choice is purely an
/// output-formatting decision and does not affect entropy decoding,
/// inverse quantisation, or the IDCT.
#[derive(Copy, Clone, Debug, Eq, PartialEq, Default)]
pub enum OutputRange {
    /// Clamp to `0 ..= 2^b − 1` (all quantization levels). Default.
    #[default]
    Full,
    /// Clamp to `1 ..= 2^b − 2`, avoiding the BT.601/BT.709
    /// synchronization/timing reference quantization levels.
    Video,
}

impl OutputRange {
    /// The `(nmin, nmax)` clamp bounds of RDD 36 §7.5.1 for bit depth
    /// `bd`.
    pub fn bounds(self, bd: BitDepth) -> (u32, u32) {
        let max = bd.max_value();
        match self {
            Self::Full => (0, max),
            // 2^b − 2 is `max - 1`; `max` is always ≥ 255 so this never
            // underflows.
            Self::Video => (1, max - 1),
        }
    }
}

/// Fully resolved output surface for one decode: bit depth, chroma
/// format, and whether the caller's requested pixel format *guarantees*
/// a 4th alpha plane (`Yuva422P` / `Yuva444P`).
type OutputSurface = (BitDepth, ChromaFormat, bool);

/// Resolve a caller-requested `PixelFormat` to the decoder's output
/// surface. The third element is `true` for the alpha-typed formats
/// (`Yuva422P` / `Yuva444P`), whose plane layout promises alpha at plane
/// index 3 regardless of what the stream carries — see
/// [`decode_packet_with_format`].
fn resolve_pixel_format(pf: PixelFormat) -> Result<OutputSurface> {
    Ok(match pf {
        PixelFormat::Yuv422P => (BitDepth::Eight, ChromaFormat::Y422, false),
        PixelFormat::Yuv444P => (BitDepth::Eight, ChromaFormat::Y444, false),
        PixelFormat::Yuv422P10Le => (BitDepth::Ten, ChromaFormat::Y422, false),
        PixelFormat::Yuv444P10Le => (BitDepth::Ten, ChromaFormat::Y444, false),
        PixelFormat::Yuv422P12Le => (BitDepth::Twelve, ChromaFormat::Y422, false),
        PixelFormat::Yuv444P12Le => (BitDepth::Twelve, ChromaFormat::Y444, false),
        // 16-bit surfaces (§7.5.1 with b = 16): every sample word is
        // fully significant. A stream's 16-bit coded alpha reaches the
        // as-coded 4th plane exactly (the §7.5.2 conversion at b = 16
        // is the identity).
        PixelFormat::Yuv422P16Le => (BitDepth::Sixteen, ChromaFormat::Y422, false),
        PixelFormat::Yuv444P16Le => (BitDepth::Sixteen, ChromaFormat::Y444, false),
        // Alpha-typed 8-bit surfaces: full-resolution 8-bit alpha as
        // plane 3. RDD 36 alpha up to 16-bit demotes per §7.5.2, matching
        // how the Y/Cb/Cr planes demote per §7.5.1 at 8-bit output.
        PixelFormat::Yuva422P => (BitDepth::Eight, ChromaFormat::Y422, true),
        PixelFormat::Yuva444P => (BitDepth::Eight, ChromaFormat::Y444, true),
        // Alpha-typed deep surfaces: 16-bit-word planes with the low
        // 10/12 bits significant (or all 16 for the *P16Le pair), and
        // full-resolution alpha at the same depth as plane 3, converted
        // per §7.5.2.
        PixelFormat::Yuva422P10Le => (BitDepth::Ten, ChromaFormat::Y422, true),
        PixelFormat::Yuva444P10Le => (BitDepth::Ten, ChromaFormat::Y444, true),
        PixelFormat::Yuva422P12Le => (BitDepth::Twelve, ChromaFormat::Y422, true),
        PixelFormat::Yuva444P12Le => (BitDepth::Twelve, ChromaFormat::Y444, true),
        PixelFormat::Yuva422P16Le => (BitDepth::Sixteen, ChromaFormat::Y422, true),
        PixelFormat::Yuva444P16Le => (BitDepth::Sixteen, ChromaFormat::Y444, true),
        other => {
            return Err(Error::unsupported(format!(
                "prores decoder: requested pixel_format {other:?} not supported \
                 (expected Yuv4(2|4)4P / Yuva4(2|4)4P, plain or with a 10Le/12Le/16Le \
                 depth suffix)"
            )));
        }
    })
}

/// Resolve the requested output bit depth + chroma format from a caller's
/// `CodecParameters::pixel_format`. Returns `Ok(None)` if no pixel
/// format was set — caller defaults to 8-bit at decode time, derives
/// chroma from the frame header.
fn pick_output_format(params: &CodecParameters) -> Result<Option<OutputSurface>> {
    params.pixel_format.map(resolve_pixel_format).transpose()
}

pub fn make_decoder(params: &CodecParameters) -> Result<Box<dyn Decoder>> {
    let (requested, alpha_typed) = match pick_output_format(params)? {
        Some((bd, cf, alpha)) => (Some((bd, cf)), alpha),
        None => (None, false),
    };
    Ok(Box::new(ProResDecoder {
        codec_id: params.codec_id.clone(),
        requested,
        alpha_typed,
        range: OutputRange::Full,
        pending: None,
        eof: false,
    }))
}

/// Concrete ProRes decoder produced by [`make_decoder`]. Exposed so a
/// caller using the direct (non-registry) path can opt into the RDD 36
/// §7.5.1 video-range clamp via [`ProResDecoder::set_output_range`]
/// before driving the [`Decoder`] trait.
pub struct ProResDecoder {
    codec_id: CodecId,
    /// Caller-requested output (bit-depth, chroma) pair. `None` means
    /// "infer from the frame header, default to 8-bit".
    requested: Option<(BitDepth, ChromaFormat)>,
    /// `true` when the caller requested an alpha-typed pixel format
    /// (`Yuva422P` / `Yuva444P`): every emitted frame carries exactly 4
    /// planes, with a synthesised fully-opaque plane when the stream
    /// codes no alpha.
    alpha_typed: bool,
    /// RDD 36 §7.5.1 clamp range applied during sample generation.
    range: OutputRange,
    pending: Option<Packet>,
    eof: bool,
}

impl ProResDecoder {
    /// Select the RDD 36 §7.5.1 output clamp `range` for subsequent
    /// frames. Defaults to [`OutputRange::Full`].
    pub fn set_output_range(&mut self, range: OutputRange) {
        self.range = range;
    }

    /// The currently configured §7.5.1 clamp range.
    pub fn output_range(&self) -> OutputRange {
        self.range
    }
}

impl Decoder for ProResDecoder {
    fn codec_id(&self) -> &CodecId {
        &self.codec_id
    }

    fn send_packet(&mut self, packet: &Packet) -> Result<()> {
        if self.pending.is_some() {
            return Err(Error::other(
                "prores decoder: receive_frame must be called before sending another packet",
            ));
        }
        self.pending = Some(packet.clone());
        Ok(())
    }

    fn receive_frame(&mut self) -> Result<Frame> {
        let Some(pkt) = self.pending.take() else {
            return if self.eof {
                Err(Error::Eof)
            } else {
                Err(Error::NeedMore)
            };
        };
        let vf = decode_packet_inner(
            &pkt.data,
            pkt.pts,
            self.requested,
            self.range,
            self.alpha_typed,
        )?;
        Ok(Frame::Video(vf))
    }

    fn flush(&mut self) -> Result<()> {
        self.eof = true;
        Ok(())
    }
}

/// Decode a packet at the default output bit depth (8-bit). Equivalent
/// to `decode_packet_with_depth(data, pts, None)`.
pub fn decode_packet(data: &[u8], pts: Option<i64>) -> Result<VideoFrame> {
    decode_packet_with_depth(data, pts, None)
}

/// Decode a packet, emitting the requested `(BitDepth, ChromaFormat)` if
/// supplied. When `requested` is `None`, the chroma format is taken from
/// the frame header and the output is 8-bit.
///
/// Equivalent to [`decode_packet_with_options`] with
/// [`OutputRange::Full`].
pub fn decode_packet_with_depth(
    data: &[u8],
    pts: Option<i64>,
    requested: Option<(BitDepth, ChromaFormat)>,
) -> Result<VideoFrame> {
    decode_packet_with_options(data, pts, requested, OutputRange::Full)
}

/// Decode a packet with explicit control over both the output
/// `(BitDepth, ChromaFormat)` and the RDD 36 §7.5.1 clamp `range`.
///
/// `range` chooses whether reconstructed color component samples may use
/// all available quantization levels ([`OutputRange::Full`]) or are
/// confined to the permissible video levels that avoid the
/// BT.601/BT.709 synchronization/timing reference codes
/// ([`OutputRange::Video`]). The alpha plane is unaffected — §7.5.2
/// always maps decoded alpha across the full opacity range.
pub fn decode_packet_with_options(
    data: &[u8],
    pts: Option<i64>,
    requested: Option<(BitDepth, ChromaFormat)>,
    range: OutputRange,
) -> Result<VideoFrame> {
    decode_packet_inner(data, pts, requested, range, false)
}

/// Decode a packet to the surface a `PixelFormat` describes — the
/// free-function twin of the registry path's
/// `CodecParameters::pixel_format` handling.
///
/// Accepts the eight `Yuv4(2|4)4P{,10Le,12Le,16Le}` requests of
/// [`decode_packet_with_depth`] plus the alpha-typed formats
/// `Yuva4(2|4)4P{,10Le,12Le,16Le}`. For the alpha-typed formats the
/// returned frame *always* has 4 planes:
///
/// * a stream that codes alpha (RDD 36 §6.1.1 Table 7,
///   `alpha_channel_type` 1 or 2) delivers it as plane 3 at the
///   surface depth, converted per §7.5.2 — consistent with the §7.5.1
///   conversion the Y/Cb/Cr planes undergo at that depth. On the
///   `*P16Le` surfaces 16-bit coded alpha passes through exactly (the
///   §7.5.2 conversion at `b = 16` is the identity);
/// * a stream with no coded alpha gets a synthesised fully-opaque
///   (all-`2^b − 1`) plane, so the declared plane layout is a hard
///   contract.
///
/// `None` behaves like [`decode_packet`] (8-bit, chroma from the frame
/// header, alpha plane only when coded). The chroma format of a `Some`
/// request must match the frame header's, as elsewhere.
pub fn decode_packet_with_format(
    data: &[u8],
    pts: Option<i64>,
    pixel_format: Option<PixelFormat>,
    range: OutputRange,
) -> Result<VideoFrame> {
    let (requested, alpha_typed) = match pixel_format.map(resolve_pixel_format).transpose()? {
        Some((bd, cf, alpha)) => (Some((bd, cf)), alpha),
        None => (None, false),
    };
    decode_packet_inner(data, pts, requested, range, alpha_typed)
}

/// Shared decode core. `alpha_typed` requests the 4-plane surface
/// guarantee of the `Yuva*` pixel formats (see
/// [`decode_packet_with_format`]); the coded-stream handling is
/// otherwise identical.
fn decode_packet_inner(
    data: &[u8],
    pts: Option<i64>,
    requested: Option<(BitDepth, ChromaFormat)>,
    range: OutputRange,
    alpha_typed: bool,
) -> Result<VideoFrame> {
    let (fh, after_frame) = parse_frame(data)?;

    let width = fh.width as usize;
    let height = fh.height as usize;
    if width == 0 || height == 0 {
        return Err(Error::invalid("prores: zero-sized frame"));
    }
    // Bound allocations against the declared dimensions.
    let pixels = width.saturating_mul(height);
    if pixels > MAX_DECODED_PIXELS {
        return Err(Error::invalid(format!(
            "prores: declared frame size ({width}x{height}) exceeds internal cap"
        )));
    }
    let alpha_kind = AlphaChannelType::from_code(fh.alpha_channel_type)?;
    let has_alpha = alpha_kind.is_some();
    let interlaced = fh.interlace_mode != 0;

    let chroma = fh.chroma_format;
    let bit_depth = if let Some((bd, requested_chroma)) = requested {
        if requested_chroma != chroma {
            return Err(Error::invalid(format!(
                "prores: requested pixel_format chroma {:?} does not match \
                 frame chroma {:?}",
                requested_chroma, chroma,
            )));
        }
        bd
    } else {
        BitDepth::Eight
    };
    let bps = bit_depth.bytes_per_sample();
    let mbs_x = width.div_ceil(MB_SIDE_PX);

    // Allocate full-frame padded planes. For interlaced the two field
    // pictures decode into rows {0, 2, 4, …} (top) and {1, 3, 5, …}
    // (bottom) of these buffers per RDD 36 §7.5.3.
    let padded_w = mbs_x * MB_SIDE_PX;
    let padded_c_w = match chroma {
        ChromaFormat::Y422 => padded_w / 2,
        ChromaFormat::Y444 => padded_w,
    };
    // Padded frame height: round up to a multiple of MB_SIDE_PX, then
    // (for interlaced) ensure each field gets a whole MB row count by
    // rounding the per-field MB-row count up.
    let padded_frame_h = if interlaced {
        let top_h = height.div_ceil(2);
        let bot_h = height / 2;
        let top_mb_rows = top_h.div_ceil(MB_SIDE_PX);
        let bot_mb_rows = bot_h.div_ceil(MB_SIDE_PX);
        // Interleaved padded height = max field padded * 2.
        top_mb_rows.max(bot_mb_rows) * MB_SIDE_PX * 2
    } else {
        height.div_ceil(MB_SIDE_PX) * MB_SIDE_PX
    };
    let y_alloc = padded_w.saturating_mul(padded_frame_h);
    let c_alloc = padded_c_w.saturating_mul(padded_frame_h);
    if y_alloc > MAX_DECODED_PIXELS || c_alloc > MAX_DECODED_PIXELS {
        return Err(Error::invalid("prores: padded plane size exceeds cap"));
    }
    let y_byte_stride = padded_w * bps;
    let c_byte_stride = padded_c_w * bps;
    let a_byte_stride = padded_w * bps;
    let mut y_plane = vec![0u8; y_byte_stride * padded_frame_h];
    let mut cb_plane = vec![0u8; c_byte_stride * padded_frame_h];
    let mut cr_plane = vec![0u8; c_byte_stride * padded_frame_h];
    let mut a_plane: Vec<u8> = if has_alpha {
        vec![0u8; a_byte_stride * padded_frame_h]
    } else {
        Vec::new()
    };

    let mut cursor = after_frame;
    if interlaced {
        // Per §6.2 picture_vertical_size:
        //   topFieldVerticalSize = (vertical_size + 1) / 2
        //   bottomFieldVerticalSize = vertical_size / 2
        // interlace_mode==1 → first picture is top field
        // interlace_mode==2 → first picture is bottom field
        let top_h = height.div_ceil(2);
        let bot_h = height / 2;
        let (first_h, first_field_offset, second_h, second_field_offset) = match fh.interlace_mode {
            1 => (top_h, 0usize, bot_h, 1usize),
            2 => (bot_h, 1usize, top_h, 0usize),
            other => {
                return Err(Error::invalid(format!(
                    "prores: invalid interlace_mode {other}"
                )));
            }
        };
        cursor = decode_picture_into_planes(
            cursor,
            &fh,
            mbs_x,
            first_h,
            chroma,
            bit_depth,
            range,
            alpha_kind,
            true,
            FieldStride::new(2, first_field_offset),
            &mut y_plane,
            y_byte_stride,
            &mut cb_plane,
            c_byte_stride,
            &mut cr_plane,
            &mut a_plane,
            a_byte_stride,
        )?;
        cursor = decode_picture_into_planes(
            cursor,
            &fh,
            mbs_x,
            second_h,
            chroma,
            bit_depth,
            range,
            alpha_kind,
            true,
            FieldStride::new(2, second_field_offset),
            &mut y_plane,
            y_byte_stride,
            &mut cb_plane,
            c_byte_stride,
            &mut cr_plane,
            &mut a_plane,
            a_byte_stride,
        )?;
    } else {
        cursor = decode_picture_into_planes(
            cursor,
            &fh,
            mbs_x,
            height,
            chroma,
            bit_depth,
            range,
            alpha_kind,
            false,
            FieldStride::progressive(),
            &mut y_plane,
            y_byte_stride,
            &mut cb_plane,
            c_byte_stride,
            &mut cr_plane,
            &mut a_plane,
            a_byte_stride,
        )?;
    }
    let _ = cursor; // remaining bytes are stuffing per §5.1 / §6.1.2

    // Crop padded buffers to declared picture size.
    let c_w = match chroma {
        ChromaFormat::Y422 => width.div_ceil(2),
        ChromaFormat::Y444 => width,
    };
    let y_cropped = crop_plane(&y_plane, y_byte_stride, width, height, bps);
    let cb_cropped = crop_plane(&cb_plane, c_byte_stride, c_w, height, bps);
    let cr_cropped = crop_plane(&cr_plane, c_byte_stride, c_w, height, bps);
    let mut planes = vec![
        VideoPlane {
            stride: width * bps,
            data: y_cropped,
        },
        VideoPlane {
            stride: c_w * bps,
            data: cb_cropped,
        },
        VideoPlane {
            stride: c_w * bps,
            data: cr_cropped,
        },
    ];
    if has_alpha {
        let a_cropped = crop_plane(&a_plane, a_byte_stride, width, height, bps);
        planes.push(VideoPlane {
            stride: width * bps,
            data: a_cropped,
        });
    } else if alpha_typed {
        // The caller's pixel format (`Yuva422P` / `Yuva444P`) promises a
        // 4-plane layout, but the stream codes no alpha
        // (alpha_channel_type == 0, §6.1.1 Table 7): synthesise a fully
        // opaque plane — §7.5.2 maps full opacity to the all-ones code
        // at the output depth.
        planes.push(opaque_alpha_plane(width, height, bit_depth));
    }
    Ok(VideoFrame { pts, planes })
}

/// Build a fully opaque full-resolution alpha plane at the output
/// depth: every sample is the depth's maximum (§7.5.2 maps full opacity
/// to `2^b − 1`).
fn opaque_alpha_plane(width: usize, height: usize, bit_depth: BitDepth) -> VideoPlane {
    let bps = bit_depth.bytes_per_sample();
    let max = bit_depth.max_value();
    let mut data = vec![0u8; width * height * bps];
    if bps == 1 {
        data.fill(max as u8);
    } else {
        let le = (max as u16).to_le_bytes();
        for s in data.chunks_exact_mut(2) {
            s.copy_from_slice(&le);
        }
    }
    VideoPlane {
        stride: width * bps,
        data,
    }
}

/// Field-row mapping. For progressive: `step=1, offset=0` (rows are
/// contiguous). For interlaced: `step=2` and `offset` is 0 (top field)
/// or 1 (bottom field). When pasting block sample row `r`, the actual
/// frame row is `step * r + offset`.
#[derive(Copy, Clone, Debug)]
struct FieldStride {
    step: usize,
    offset: usize,
}

impl FieldStride {
    fn new(step: usize, offset: usize) -> Self {
        Self { step, offset }
    }
    fn progressive() -> Self {
        Self { step: 1, offset: 0 }
    }
    fn map(self, picture_row: usize) -> usize {
        self.step * picture_row + self.offset
    }
}

/// Decode one `picture()` (header + slice table + slice payloads) into
/// the supplied padded plane buffers. `picture_height` is the picture's
/// luma-sample height (= field height for interlaced, full height
/// otherwise). `field` controls how picture-row indices map onto
/// destination plane rows; for progressive pictures it is the identity.
///
/// Returns a slice positioned just past the consumed picture bytes —
/// the caller advances its cursor with the returned slice.
#[allow(clippy::too_many_arguments)]
fn decode_picture_into_planes<'a>(
    data: &'a [u8],
    fh: &FrameHeader,
    mbs_x: usize,
    picture_height: usize,
    chroma: ChromaFormat,
    bit_depth: BitDepth,
    range: OutputRange,
    alpha_kind: Option<AlphaChannelType>,
    interlaced: bool,
    field: FieldStride,
    y_plane: &mut [u8],
    y_byte_stride: usize,
    cb_plane: &mut [u8],
    c_byte_stride: usize,
    cr_plane: &mut [u8],
    a_plane: &mut [u8],
    a_byte_stride: usize,
) -> Result<&'a [u8]> {
    let has_alpha = alpha_kind.is_some();
    let mbs_y = picture_height.div_ceil(MB_SIDE_PX);
    let (ph, after_pic) = parse_picture_header(data)?;
    let slice_sizes_template = compute_slice_sizes(mbs_x, ph.log2_desired_slice_size_in_mb);
    let slices_per_row = slice_sizes_template.len();
    let expected_slice_count = slices_per_row * mbs_y;
    let slice_table_bytes = expected_slice_count
        .checked_mul(2)
        .ok_or_else(|| Error::invalid("prores: slice count overflow"))?;
    if after_pic.len() < slice_table_bytes {
        return Err(Error::invalid("prores: slice-size table truncated"));
    }
    let mut slice_sizes = Vec::with_capacity(expected_slice_count);
    for i in 0..expected_slice_count {
        let off = i * 2;
        slice_sizes.push(u16::from_be_bytes(after_pic[off..off + 2].try_into().unwrap()) as usize);
    }
    let mut cursor = &after_pic[slice_table_bytes..];
    // The bytes occupied by the defined picture syntax this decoder
    // consumes: the picture header, the slice table, and every slice
    // payload. RDD 36 §6.2.1 makes `picture_size` (which "includes the
    // picture header") the authoritative total — and §6.4 permits a
    // *version variant* to append informative bytes after the defined
    // syntax, inflating `picture_size` beyond this consumed total.
    let consumed_picture_bytes: usize =
        ph.picture_header_size as usize + slice_table_bytes + slice_sizes.iter().sum::<usize>();
    let declared_picture_bytes = ph.picture_size as usize;
    // The defined syntax can never claim more space than the picture
    // declares; if it does the slice table / sizes are corrupt.
    if consumed_picture_bytes > declared_picture_bytes {
        return Err(Error::invalid(
            "prores: header + slice table + payloads exceed declared picture_size",
        ));
    }
    // RDD 36 §6.2.1 / §6.4: "decoders shall use the specified size —
    // rather than inference from the syntax itself — to determine the
    // start of the immediately following syntax structure." Advance past
    // the *declared* `picture_size` (which equals the consumed total for
    // a base bitstream, or exceeds it by the trailing version-variant
    // bytes a forward-compatible stream may carry) so that, for an
    // interlaced frame, the second field's `picture()` starts at the
    // correct offset even when version-variant data sits after the first
    // field's slices.
    if data.len() < declared_picture_bytes {
        return Err(Error::invalid("prores: picture overruns buffer"));
    }

    const LUMA_OFFSETS: [(usize, usize); 4] = [(0, 0), (1, 0), (0, 1), (1, 1)];
    // EXR Converter patch: 4:4:4 chroma 8x8 blocks are coded in column
    // order (top-left, bottom-left, top-right, bottom-right), the order
    // FFmpeg's prores decoder and encoder use. Upstream 0.1.1 reused the
    // luma (raster) order here, which swaps the top-right and bottom-left
    // chroma blocks of every macroblock for standard decoders. See
    // `slice::CHROMA_444_OFFSETS`.
    let chroma_offsets: &[(usize, usize)] = match chroma {
        ChromaFormat::Y422 => &[(0, 0), (0, 1)],
        ChromaFormat::Y444 => &CHROMA_444_OFFSETS,
    };
    let cb_per_mb = chroma_blocks_per_mb(chroma);
    let per_mb = blocks_per_mb(chroma);

    let mut slice_idx = 0usize;
    for my in 0..mbs_y {
        let mut mx = 0usize;
        for &slice_size_in_mb_template in &slice_sizes_template {
            let mbs_this_slice = slice_size_in_mb_template.min(mbs_x - mx);
            if mbs_this_slice == 0 {
                break;
            }
            let coded_size = slice_sizes[slice_idx];
            if cursor.len() < coded_size {
                return Err(Error::invalid("prores: slice payload truncated"));
            }
            let slice_data = &cursor[..coded_size];
            cursor = &cursor[coded_size..];
            slice_idx += 1;

            let (sh, after_sh) = parse_slice_header(slice_data, has_alpha)?;
            let coded_y = sh.coded_size_of_y_data as usize;
            let coded_cb = sh.coded_size_of_cb_data as usize;
            let cr_data_size = if let Some(sz) = sh.coded_size_of_cr_data {
                sz as usize
            } else {
                slice_data
                    .len()
                    .checked_sub(sh.slice_header_size as usize + coded_y + coded_cb)
                    .ok_or_else(|| Error::invalid("prores: cr_data size underflow"))?
            };
            if after_sh.len() < coded_y + coded_cb + cr_data_size {
                return Err(Error::invalid("prores: slice components truncated"));
            }
            let y_data = &after_sh[..coded_y];
            let cb_data = &after_sh[coded_y..coded_y + coded_cb];
            let cr_data = &after_sh[coded_y + coded_cb..coded_y + coded_cb + cr_data_size];
            let alpha_data: &[u8] = if has_alpha {
                &after_sh[coded_y + coded_cb + cr_data_size..]
            } else {
                &[]
            };

            let blocks = decode_slice_components(
                y_data,
                cb_data,
                cr_data,
                mbs_this_slice,
                chroma,
                interlaced,
            )?;
            if blocks.len() != mbs_this_slice * per_mb {
                return Err(Error::invalid(
                    "prores: decoded block count mismatch in slice",
                ));
            }

            for mb_within in 0..mbs_this_slice {
                let mb_x = mx + mb_within;
                let base = mb_within * per_mb;
                for (i, (bx, by)) in LUMA_OFFSETS.iter().enumerate() {
                    let mut blk_f =
                        dequant_to_f32(&blocks[base + i], &fh.luma_qmat, sh.quantization_index);
                    // RDD 36 §7.4 IDCT specialised by AC content: a block
                    // whose AC coefficients dequantise to zero (smooth
                    // areas, common at higher quantisation indices) yields
                    // a constant `block[0] / 8` plane after the IDCT, so
                    // we can skip the 64 × 16 multiply-add general loop.
                    // See `dct::idct8x8_dc_only` for the derivation.
                    if is_dc_only(&blk_f) {
                        idct8x8_dc_only(&mut blk_f);
                    } else {
                        idct8x8(&mut blk_f);
                    }
                    paste_block(
                        y_plane,
                        y_byte_stride,
                        mb_x * MB_SIDE_PX + bx * 8,
                        my * MB_SIDE_PX + by * 8,
                        &blk_f,
                        bit_depth,
                        range,
                        field,
                    );
                }
                for (i, (bx, by)) in chroma_offsets.iter().enumerate() {
                    let mut blk_f = dequant_to_f32(
                        &blocks[base + LUMA_BLOCKS_PER_MB + i],
                        &fh.chroma_qmat,
                        sh.quantization_index,
                    );
                    if is_dc_only(&blk_f) {
                        idct8x8_dc_only(&mut blk_f);
                    } else {
                        idct8x8(&mut blk_f);
                    }
                    let (x0, y0) = match chroma {
                        ChromaFormat::Y422 => (mb_x * 8, my * MB_SIDE_PX + by * 8),
                        ChromaFormat::Y444 => {
                            (mb_x * MB_SIDE_PX + bx * 8, my * MB_SIDE_PX + by * 8)
                        }
                    };
                    paste_block(
                        cb_plane,
                        c_byte_stride,
                        x0,
                        y0,
                        &blk_f,
                        bit_depth,
                        range,
                        field,
                    );
                }
                for (i, (bx, by)) in chroma_offsets.iter().enumerate() {
                    let mut blk_f = dequant_to_f32(
                        &blocks[base + LUMA_BLOCKS_PER_MB + cb_per_mb + i],
                        &fh.chroma_qmat,
                        sh.quantization_index,
                    );
                    if is_dc_only(&blk_f) {
                        idct8x8_dc_only(&mut blk_f);
                    } else {
                        idct8x8(&mut blk_f);
                    }
                    let (x0, y0) = match chroma {
                        ChromaFormat::Y422 => (mb_x * 8, my * MB_SIDE_PX + by * 8),
                        ChromaFormat::Y444 => {
                            (mb_x * MB_SIDE_PX + bx * 8, my * MB_SIDE_PX + by * 8)
                        }
                    };
                    paste_block(
                        cr_plane,
                        c_byte_stride,
                        x0,
                        y0,
                        &blk_f,
                        bit_depth,
                        range,
                        field,
                    );
                }
            }
            if let Some(act) = alpha_kind {
                // The per-slice scanned-alpha array carries the FULL
                // macroblock-row height (16 sample rows) even when the
                // picture's last MB row is only partially visible; the
                // padded plane allocates the extra rows and the final crop
                // trims them.
                //
                // NOTE ON RDD 36 §7.5.3: the published wording states the
                // alpha array "does not include alpha values for the excess
                // row(s) of pixels at the bottom of slices with i =
                // height_in_mb − 1 when 16 * height_in_mb >
                // picture_vertical_size", which reads as "size the bottom
                // MB row's array to the visible row count". Empirically
                // that is NOT what real ProRes 4444 streams carry: the
                // in-tree reference fixture `4444-with-alpha` (1920×1080,
                // height_in_mb = 68, picture_vertical_size = 1080 → bottom
                // MB row visible height = 8) encodes a bottom-row alpha
                // array of the full 16 rows; decoding with the visible-row
                // count (8) raises "run overruns alphaValues array" because
                // the run/level stream keeps producing values past the
                // truncated `numValues`. So the empirically-correct
                // `numValues` is `16 * slice_size_in_mb` columns × 16 rows
                // for every slice, and the *excess rows* are discarded on
                // paste (clamped via `usable_rows` below), exactly like the
                // excess *columns* the spec already says are present. The
                // §7.5.3 distinction therefore applies to what a decoder
                // WRITES to the frame buffer, not to the array's coded
                // length. (DOCS-GAP candidate: §7.5.3 wording vs. reference
                // bitstream.)
                let slice_vertical_size = MB_SIDE_PX;
                let cols = MB_SIDE_PX * mbs_this_slice;
                let num_alpha_values = cols * slice_vertical_size;
                let alpha_values = decode_scanned_alpha(alpha_data, num_alpha_values, act)?;
                // Clamp visible rows to the padded plane bounds —
                // `paste_alpha` would otherwise overrun for the last
                // MB row of a picture whose height isn't a multiple of
                // MB_SIDE_PX (mapped through field).
                let plane_rows = a_plane.len() / a_byte_stride;
                let dst_y = my * MB_SIDE_PX;
                let usable_rows = plane_rows
                    .saturating_sub(field.map(dst_y))
                    .div_ceil(field.step.max(1))
                    .min(MB_SIDE_PX);
                paste_alpha(
                    a_plane,
                    a_byte_stride,
                    mx * MB_SIDE_PX,
                    dst_y,
                    cols,
                    usable_rows,
                    &alpha_values,
                    act,
                    bit_depth,
                    field,
                );
            }
            mx += mbs_this_slice;
        }
    }
    Ok(&data[declared_picture_bytes..])
}

fn dequant_to_f32(blk: &[i32; 64], qmat: &[u8; 64], quantization_index: u8) -> [f32; 64] {
    // F[v][u] = (QF[v][u] * W[v][u] * qScale) / 8
    let qs = qscale(quantization_index) as f32;
    let mut out = [0.0f32; 64];
    for k in 0..64 {
        out[k] = (blk[k] as f32 * qmat[k] as f32 * qs) / 8.0;
    }
    out
}

/// Write an 8x8 block of IDCT output `blk` into `plane` at sample
/// position `(x0, y0)`. `byte_stride` is the row stride of `plane` in
/// **bytes** (i.e. samples_per_row * bytes_per_sample).
///
/// Per RDD 36 §7.5.1, the IDCT output `v` is centred in the half-open
/// range `[-256, 256)` (9-bit signed integers). The pixel sample is
/// `s = clamp(round(2^b * (v + 256) / 512))`. For 8-bit that simplifies
/// to `s = clamp(round((v + 256) / 2))`; for 10-bit `s = (v+256) * 2`;
/// for 12-bit `s = (v+256) * 8`.
///
/// `range` selects the §7.5.1 clamp bounds `(nmin, nmax)`: `Full`
/// (`0 ..= 2^b − 1`) or `Video` (`1 ..= 2^b − 2`, avoiding the
/// BT.601/BT.709 sync/timing reference levels).
/// Convert a single reconstructed color component value `v` (the centred
/// IDCT output, nominally in `[-256, 256)` per §7.5.1) to a pixel
/// component sample of the requested output bit depth and clamp `range`,
/// per RDD 36 §7.5.1:
///
/// `s = clamp(round(2^b * (v + 256) / 512))`,
///
/// where `clamp(n)` restricts `n` to `(nmin, nmax)`. For `b = 8` the
/// `2^b / 512` factor is `0.5`, for `b = 10` it is `2.0`, for `b = 12`
/// it is `8.0`, and for `b = 16` it is `128.0`. The clamp bounds are
/// `(0, 2^b − 1)` for
/// [`OutputRange::Full`] and `(1, 2^b − 2)` for [`OutputRange::Video`].
///
/// Pulled out of [`paste_block`] as a pure function so the §7.5.1
/// arithmetic — the per-depth scale, the round-to-nearest, and both
/// clamp arms — can be locked directly by unit tests, mirroring
/// [`alpha_to_sample`] for §7.5.2.
fn color_to_sample(v: f32, bit_depth: BitDepth, range: OutputRange) -> u32 {
    let scale = match bit_depth {
        BitDepth::Eight => 0.5,
        BitDepth::Ten => 2.0,
        BitDepth::Twelve => 8.0,
        BitDepth::Sixteen => 128.0,
    };
    let (nmin, nmax) = range.bounds(bit_depth);
    let s = (v + 256.0) * scale;
    if s <= nmin as f32 {
        nmin
    } else if s >= nmax as f32 {
        nmax
    } else {
        s.round() as u32
    }
}

#[allow(clippy::too_many_arguments)]
fn paste_block(
    plane: &mut [u8],
    byte_stride: usize,
    x0: usize,
    y0: usize,
    blk: &[f32; 64],
    bit_depth: BitDepth,
    range: OutputRange,
    field: FieldStride,
) {
    match bit_depth {
        BitDepth::Eight => {
            for j in 0..8 {
                let row = field.map(y0 + j);
                for i in 0..8 {
                    let px = color_to_sample(blk[j * 8 + i], bit_depth, range) as u8;
                    plane[row * byte_stride + x0 + i] = px;
                }
            }
        }
        BitDepth::Ten | BitDepth::Twelve | BitDepth::Sixteen => {
            for j in 0..8 {
                let row = field.map(y0 + j);
                for i in 0..8 {
                    let px = color_to_sample(blk[j * 8 + i], bit_depth, range) as u16;
                    let off = row * byte_stride + (x0 + i) * 2;
                    plane[off] = (px & 0xFF) as u8;
                    plane[off + 1] = (px >> 8) as u8;
                }
            }
        }
    }
}

/// Convert a decoded alpha value (8-bit in `0..=255` or 16-bit in
/// `0..=65535` per `act`) to a pixel sample of the requested output bit
/// depth, per RDD 36 §7.5.2:
///
/// `alphaSample = round((2^b - 1) * alpha / mask)`
fn alpha_to_sample(alpha: u16, act: AlphaChannelType, out_depth: BitDepth) -> u16 {
    let max_out = out_depth.max_value();
    let mask = act.mask();
    // round((max_out * alpha) / mask) using integer math.
    let num = max_out as u64 * alpha as u64;
    let denom = mask as u64;
    ((num + denom / 2) / denom) as u16
}

/// Paste a slice's worth of decoded alpha values into the padded alpha
/// plane. `cols` is the alpha array width (in samples); `rows` is the
/// number of rows to actually write to the plane (may be < the decoded
/// macroblock-row height of 16 when the picture's last MB row is
/// partially visible — see decode dispatch).
#[allow(clippy::too_many_arguments)]
fn paste_alpha(
    plane: &mut [u8],
    byte_stride: usize,
    x0: usize,
    y0: usize,
    cols: usize,
    rows: usize,
    values: &[u16],
    act: AlphaChannelType,
    out_depth: BitDepth,
    field: FieldStride,
) {
    debug_assert!(values.len() >= cols * rows);
    let bps = out_depth.bytes_per_sample();
    for r in 0..rows {
        let row = field.map(y0 + r);
        for c in 0..cols {
            let s = alpha_to_sample(values[r * cols + c], act, out_depth);
            let off = row * byte_stride + (x0 + c) * bps;
            match out_depth {
                BitDepth::Eight => {
                    plane[off] = s as u8;
                }
                BitDepth::Ten | BitDepth::Twelve | BitDepth::Sixteen => {
                    plane[off] = (s & 0xFF) as u8;
                    plane[off + 1] = (s >> 8) as u8;
                }
            }
        }
    }
}

/// Crop a padded plane to a tight (`dst_w` samples × `dst_h` rows)
/// output buffer. `bps` is the per-sample byte count (1 for 8-bit,
/// 2 for 10-bit packed LE).
fn crop_plane(
    src: &[u8],
    src_byte_stride: usize,
    dst_w: usize,
    dst_h: usize,
    bps: usize,
) -> Vec<u8> {
    let dst_byte_stride = dst_w * bps;
    let mut out = vec![0u8; dst_byte_stride * dst_h];
    for y in 0..dst_h {
        out[y * dst_byte_stride..y * dst_byte_stride + dst_byte_stride]
            .copy_from_slice(&src[y * src_byte_stride..y * src_byte_stride + dst_byte_stride]);
    }
    out
}

#[cfg(test)]
mod alpha_sample_tests {
    //! White-box coverage for the RDD 36 §7.5.2 decoded-alpha →
    //! pixel-alpha-sample conversion `alpha_to_sample`. The end-to-end
    //! decode path only ever reaches this code with an alpha-bearing
    //! 4444 stream, and the only prior coverage of the bit-depth
    //! promotion/demotion arm lived in the `ffmpeg_interop` integration
    //! tests — which skip entirely when the external validator binary is
    //! absent (the usual CI case). These tests lock the §7.5.2 formula
    //! `alphaSample = round((2^b − 1) * alpha ÷ mask)` directly, with no
    //! external dependency.
    use super::{alpha_to_sample, AlphaChannelType, BitDepth};

    /// §7.5.2: "If the pixel component sample bit depth matches that of
    /// the decoded alpha values, the pixel alpha component samples shall
    /// be the decoded values themselves." 8-bit alpha (alpha_channel_type
    /// == 1) into an 8-bit sample must be the exact identity across the
    /// full `0..=255` domain.
    #[test]
    fn eight_bit_alpha_to_eight_bit_is_identity() {
        for a in 0u16..=255 {
            assert_eq!(
                alpha_to_sample(a, AlphaChannelType::Eight, BitDepth::Eight),
                a,
                "8-bit alpha {a} must map to itself at 8-bit output"
            );
        }
    }

    /// §7.5.2 endpoints: the smallest and largest decoded alpha values
    /// signify opacities of exactly 0.0 and 1.0 and must map to the
    /// smallest (0) and largest (`2^b − 1`) pixel sample at every output
    /// depth, for both alpha_channel_type values.
    #[test]
    fn endpoints_map_to_full_opacity_range() {
        for act in [AlphaChannelType::Eight, AlphaChannelType::Sixteen] {
            let max_in = act.mask() as u16;
            for depth in [
                BitDepth::Eight,
                BitDepth::Ten,
                BitDepth::Twelve,
                BitDepth::Sixteen,
            ] {
                assert_eq!(
                    alpha_to_sample(0, act, depth),
                    0,
                    "alpha 0 (opacity 0.0) must map to sample 0 ({act:?} -> {depth:?})"
                );
                assert_eq!(
                    u32::from(alpha_to_sample(max_in, act, depth)),
                    depth.max_value(),
                    "alpha {max_in} (opacity 1.0) must map to sample 2^b-1 ({act:?} -> {depth:?})"
                );
            }
        }
    }

    /// §7.5.2 promotion: 8-bit decoded alpha into a 10/12-bit pixel
    /// sample uses `round((2^b − 1) * alpha ÷ 255)`. Spot-check a handful
    /// of values against the formula evaluated independently here with
    /// round-half-away-from-zero (== round-half-up for non-negatives,
    /// the §3 `round(x) = floor(x + 1/2)` convention).
    #[test]
    fn eight_bit_alpha_promotes_to_higher_depth() {
        fn expect(alpha: u32, max_out: u32, mask: u32) -> u16 {
            ((max_out * alpha * 2 + mask) / (mask * 2)) as u16
        }
        for &alpha in &[0u32, 1, 64, 127, 128, 200, 254, 255] {
            // 8-bit -> 10-bit (max 1023, mask 255)
            assert_eq!(
                alpha_to_sample(alpha as u16, AlphaChannelType::Eight, BitDepth::Ten),
                expect(alpha, 1023, 255),
                "8->10 promotion mismatch at alpha {alpha}"
            );
            // 8-bit -> 12-bit (max 4095, mask 255)
            assert_eq!(
                alpha_to_sample(alpha as u16, AlphaChannelType::Eight, BitDepth::Twelve),
                expect(alpha, 4095, 255),
                "8->12 promotion mismatch at alpha {alpha}"
            );
            // 8-bit -> 16-bit (max 65535, mask 255). 65535 = 255 * 257,
            // so the §7.5.2 promotion is the exact integer 257 * alpha.
            assert_eq!(
                alpha_to_sample(alpha as u16, AlphaChannelType::Eight, BitDepth::Sixteen),
                (alpha * 257) as u16,
                "8->16 promotion mismatch at alpha {alpha}"
            );
        }
    }

    /// §7.5.2 at matched 16-bit depths: 16-bit decoded alpha
    /// (alpha_channel_type == 2) into a 16-bit pixel sample is the exact
    /// identity — "If the pixel component sample bit depth matches that
    /// of the decoded alpha values, the pixel alpha component samples
    /// shall be the decoded values themselves." This is the full-alpha-
    /// fidelity guarantee of the `*P16Le` surfaces.
    #[test]
    fn sixteen_bit_alpha_to_sixteen_bit_is_identity() {
        for a in (0u32..=65535).step_by(97).chain([65535]) {
            assert_eq!(
                alpha_to_sample(a as u16, AlphaChannelType::Sixteen, BitDepth::Sixteen),
                a as u16,
                "16-bit alpha {a} must map to itself at 16-bit output"
            );
        }
    }

    /// §7.5.2 demotion: 16-bit decoded alpha (alpha_channel_type == 2)
    /// into 8/10/12-bit samples uses `round((2^b − 1) * alpha ÷ 65535)`.
    /// The output is monotonic non-decreasing in the input and never
    /// exceeds `2^b − 1`.
    #[test]
    fn sixteen_bit_alpha_demotes_monotonically() {
        fn expect(alpha: u32, max_out: u32, mask: u32) -> u16 {
            ((max_out as u64 * alpha as u64 * 2 + mask as u64) / (mask as u64 * 2)) as u16
        }
        for depth in [BitDepth::Eight, BitDepth::Ten, BitDepth::Twelve] {
            let max_out = depth.max_value();
            let mut prev = 0u16;
            for alpha in (0u32..=65535).step_by(257) {
                let got = alpha_to_sample(alpha as u16, AlphaChannelType::Sixteen, depth);
                assert_eq!(
                    got,
                    expect(alpha, max_out, 65535),
                    "16->{}b demotion mismatch at alpha {alpha}",
                    depth.bits()
                );
                assert!(
                    u32::from(got) <= max_out,
                    "demoted sample {got} exceeds 2^b-1 = {max_out}"
                );
                assert!(got >= prev, "demotion must be monotonic non-decreasing");
                prev = got;
            }
        }
    }

    /// §7.5.2 round-half-up boundary: pick an `(alpha, depth)` whose exact
    /// quotient lands on a half-integer and confirm `alpha_to_sample`
    /// rounds it up (the §3 `floor(x + 1/2)` convention), not toward zero.
    #[test]
    fn rounds_half_up_at_a_known_midpoint() {
        // 8-bit alpha 1 into a hypothetical b where (2^b-1)/255 = 0.5
        // exact is not realisable, so construct a concrete half-integer:
        // 16-bit alpha into 8-bit: (255 * alpha) / 65535. Choose alpha so
        // 255*alpha / 65535 = k + 0.5 exactly. 65535 = 255*257, so
        // 255*alpha/65535 = alpha/257; alpha = 257*k + 128.5 is not
        // integral, but alpha = 128 gives 128/257 = 0.498… (-> 0) and the
        // exact half-integer arises for 8-bit alpha into a depth where
        // max_out is odd. Use 8-bit alpha 1 into 10-bit: 1023/255 =
        // 4.0117… (no half). Instead verify the +mask/2 bias directly:
        // for mask 255, alpha 1, max_out chosen so product*2/(2*mask)
        // straddles .5 — use the general invariant against a brute float.
        for act in [AlphaChannelType::Eight, AlphaChannelType::Sixteen] {
            let mask = act.mask();
            for depth in [
                BitDepth::Eight,
                BitDepth::Ten,
                BitDepth::Twelve,
                BitDepth::Sixteen,
            ] {
                let max_out = depth.max_value();
                for alpha in [1u32, 7, 19, 99, 100, mask / 2, mask / 2 + 1] {
                    let alpha = alpha.min(mask);
                    let exact = (max_out as f64 * alpha as f64) / mask as f64;
                    let want = exact.round() as u16; // ties -> away from zero == up here
                    assert_eq!(
                        alpha_to_sample(alpha as u16, act, depth),
                        want,
                        "round mismatch: {act:?} alpha {alpha} -> {depth:?} (exact {exact})"
                    );
                }
            }
        }
    }
}

#[cfg(test)]
mod color_sample_tests {
    //! White-box coverage for the RDD 36 §7.5.1 reconstructed-color to
    //! pixel-sample conversion `color_to_sample`, i.e.
    //! `s = clamp(round(2^b * (v + 256) / 512))`. The end-to-end
    //! `output_range` integration test drives this through a full
    //! encode-then-decode round-trip; these cases lock the pure
    //! arithmetic (per-depth scale, round-to-nearest, and both clamp
    //! arms) directly with no codec round-trip, mirroring
    //! `alpha_sample_tests` for §7.5.2.
    use super::{color_to_sample, BitDepth, OutputRange};

    /// The §7.5.1 mid-range value `v = 0` maps to the centre of the
    /// output range: `2^b * 256 / 512 = 2^(b-1)`. For 8/10/12-bit that
    /// is 128 / 512 / 2048 respectively, in either clamp range.
    #[test]
    fn midpoint_v_zero_maps_to_half_scale() {
        for range in [OutputRange::Full, OutputRange::Video] {
            assert_eq!(color_to_sample(0.0, BitDepth::Eight, range), 128);
            assert_eq!(color_to_sample(0.0, BitDepth::Ten, range), 512);
            assert_eq!(color_to_sample(0.0, BitDepth::Twelve, range), 2048);
            assert_eq!(color_to_sample(0.0, BitDepth::Sixteen, range), 32768);
        }
    }

    /// The bottom of the centred IDCT range (`v = -256`) maps to the
    /// clamp floor: 0 for `Full`, 1 for `Video`. A large positive `v`
    /// saturates to the clamp ceiling: `2^b − 1` for `Full`,
    /// `2^b − 2` for `Video`.
    #[test]
    fn clamp_floor_and_ceiling_track_the_range() {
        // Floor: v = -256 → s = 0 before clamp.
        assert_eq!(
            color_to_sample(-256.0, BitDepth::Eight, OutputRange::Full),
            0
        );
        assert_eq!(
            color_to_sample(-256.0, BitDepth::Eight, OutputRange::Video),
            1
        );
        assert_eq!(color_to_sample(-256.0, BitDepth::Ten, OutputRange::Full), 0);
        assert_eq!(
            color_to_sample(-256.0, BitDepth::Twelve, OutputRange::Video),
            1
        );
        assert_eq!(
            color_to_sample(-256.0, BitDepth::Sixteen, OutputRange::Full),
            0
        );
        assert_eq!(
            color_to_sample(-256.0, BitDepth::Sixteen, OutputRange::Video),
            1
        );

        // Ceiling: a large positive v saturates to the top of the range.
        assert_eq!(
            color_to_sample(1000.0, BitDepth::Eight, OutputRange::Full),
            255
        );
        assert_eq!(
            color_to_sample(1000.0, BitDepth::Eight, OutputRange::Video),
            254
        );
        assert_eq!(
            color_to_sample(1000.0, BitDepth::Ten, OutputRange::Full),
            1023
        );
        assert_eq!(
            color_to_sample(1000.0, BitDepth::Ten, OutputRange::Video),
            1022
        );
        assert_eq!(
            color_to_sample(1000.0, BitDepth::Twelve, OutputRange::Full),
            4095
        );
        assert_eq!(
            color_to_sample(1000.0, BitDepth::Twelve, OutputRange::Video),
            4094
        );
        assert_eq!(
            color_to_sample(1000.0, BitDepth::Sixteen, OutputRange::Full),
            65535
        );
        assert_eq!(
            color_to_sample(1000.0, BitDepth::Sixteen, OutputRange::Video),
            65534
        );
    }

    /// Round-to-nearest: at 8-bit the scale is 0.5, so `v = 1` →
    /// `(1 + 256) * 0.5 = 128.5` → rounds to 129 (ties away from zero),
    /// and `v = -1` → `127.5` → 128.
    #[test]
    fn round_to_nearest_at_eight_bit() {
        assert_eq!(
            color_to_sample(1.0, BitDepth::Eight, OutputRange::Full),
            129
        );
        assert_eq!(
            color_to_sample(-1.0, BitDepth::Eight, OutputRange::Full),
            128
        );
        // v = 2 → 129.0 exactly, no tie.
        assert_eq!(
            color_to_sample(2.0, BitDepth::Eight, OutputRange::Full),
            129
        );
    }

    /// The `Video` clamp differs from `Full` only at the two extreme
    /// codes; every interior value is identical between the two ranges.
    #[test]
    fn video_equals_full_away_from_the_extremes() {
        for &v in &[-200.0f32, -64.0, -1.0, 0.0, 1.0, 64.0, 200.0] {
            for depth in [
                BitDepth::Eight,
                BitDepth::Ten,
                BitDepth::Twelve,
                BitDepth::Sixteen,
            ] {
                assert_eq!(
                    color_to_sample(v, depth, OutputRange::Full),
                    color_to_sample(v, depth, OutputRange::Video),
                    "interior v={v} must match across ranges at {depth:?}"
                );
            }
        }
    }
}
