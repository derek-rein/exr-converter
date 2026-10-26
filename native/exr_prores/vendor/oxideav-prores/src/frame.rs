//! ProRes frame, picture, and slice headers (SMPTE RDD 36 §5).
//!
//! Wire layout (big-endian throughout):
//!
//! ```text
//! frame() {
//!   frame_size:        u32  // total bytes including frame_size itself
//!   frame_identifier:  4*u8 // 'icpf' (0x69637066)
//!   frame_header():
//!     frame_header_size:    u16
//!     reserved:              u8
//!     bitstream_version:     u8  // 0 (4:2:2 only) or 1 (4:2:2 / 4:4:4 + alpha)
//!     encoder_identifier:  4*u8
//!     horizontal_size:      u16
//!     vertical_size:        u16
//!     chroma_format:       u2 + reserved u2  // 2=4:2:2, 3=4:4:4
//!     interlace_mode:      u2 + reserved u2
//!     aspect_ratio_info:   u4
//!     frame_rate_code:     u4
//!     color_primaries:      u8
//!     transfer_charac:      u8
//!     matrix_coefficients:  u8
//!     reserved:            u4
//!     alpha_channel_type:  u4
//!     reserved:           u14
//!     load_luma_qmat:      u1
//!     load_chroma_qmat:    u1
//!     [luma_qmat 64*u8]   if load_luma_qmat
//!     [chroma_qmat 64*u8] if load_chroma_qmat
//!   picture():
//!     picture_header():
//!       picture_header_size: u5  // in bytes (= 8 normally)
//!       reserved:           u3
//!       picture_size:      u32  // bytes incl. picture header
//!       deprecated_n_slc:  u16
//!       reserved:           u2
//!       log2_desired_slice_size_in_mb: u2
//!       reserved:           u4
//!     slice_table():
//!       coded_size_of_slice[]: u16 each
//!     slice() ...
//! }
//! ```
//!
//! The `picture_header_size` field is in **bytes** (per §5.2.1), and the
//! 5 bits used to encode it makes the maximum picture_header 31 bytes
//! long. The standard layout above totals 8 bytes — the value emitted
//! is therefore 8.

use oxideav_core::{Error, Result};

/// 'icpf' magic, big-endian. Spec value: 0x69637066.
pub const FRAME_IDENTIFIER: &[u8; 4] = b"icpf";

/// In-stream frame identifier of an Apple **ProRes RAW** sample: `aprh`
/// (at the same byte offset as `icpf` in a standard ProRes frame, i.e.
/// immediately after the 4-byte `frame_size`). ProRes RAW is a separate
/// Apple format that wraps single-plane Bayer/CFA sensor data — it is
/// NOT covered by SMPTE RDD 36 (which scopes itself to the six
/// YUV/RGB profiles), uses an incompatible sample structure, and is
/// documented only in Apple's proprietary ProRes RAW white paper. A
/// conforming decoder must surface a clear `Unsupported` error rather
/// than dispatch a ProRes RAW sample to the RDD 36 frame parser, whose
/// bitstream layout is different. See
/// `docs/video/prores/fixtures/proresraw-not-supported/notes.md`.
pub const PRORES_RAW_FRAME_IDENTIFIER: &[u8; 4] = b"aprh";

/// 'oxav' encoder identifier — the four-character code we emit when
/// producing ProRes frames.
pub const ENCODER_IDENTIFIER: &[u8; 4] = b"oxav";

pub const CHROMA_FMT_422_CODE: u8 = 2;
pub const CHROMA_FMT_444_CODE: u8 = 3;

/// Chroma sampling format for a ProRes picture.
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
pub enum ChromaFormat {
    /// 4:2:2 — 2 Cb + 2 Cr blocks per macroblock.
    Y422,
    /// 4:4:4 — 4 Cb + 4 Cr blocks per macroblock.
    Y444,
}

impl ChromaFormat {
    pub fn from_code(c: u8) -> Result<Self> {
        match c {
            CHROMA_FMT_422_CODE => Ok(Self::Y422),
            CHROMA_FMT_444_CODE => Ok(Self::Y444),
            other => Err(Error::unsupported(format!(
                "prores: chroma_format {other} not supported"
            ))),
        }
    }

    pub fn code(self) -> u8 {
        match self {
            Self::Y422 => CHROMA_FMT_422_CODE,
            Self::Y444 => CHROMA_FMT_444_CODE,
        }
    }
}

/// Profile inferred from the container FourCC. Not a bitstream syntax
/// element — RDD 36 frames carry only `chroma_format`, not a profile
/// code. We keep it for the encoder API and to set sensible defaults.
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum Profile {
    Proxy,
    Lt,
    Standard,
    Hq,
    Prores4444,
    Prores4444Xq,
}

impl Profile {
    pub fn fourcc(self) -> &'static [u8; 4] {
        match self {
            Profile::Proxy => b"apco",
            Profile::Lt => b"apcs",
            Profile::Standard => b"apcn",
            Profile::Hq => b"apch",
            Profile::Prores4444 => b"ap4h",
            Profile::Prores4444Xq => b"ap4x",
        }
    }

    pub fn chroma_format(self) -> ChromaFormat {
        match self {
            Profile::Proxy | Profile::Lt | Profile::Standard | Profile::Hq => ChromaFormat::Y422,
            Profile::Prores4444 | Profile::Prores4444Xq => ChromaFormat::Y444,
        }
    }

    /// Default `quantization_index` used by the encoder when the caller
    /// does not specify one. Lower index → higher quality + larger packets.
    pub fn default_quant_index(self) -> u8 {
        match self {
            Profile::Proxy => 8,
            Profile::Lt => 6,
            Profile::Standard => 4,
            Profile::Hq => 2,
            Profile::Prores4444 => 2,
            Profile::Prores4444Xq => 1,
        }
    }
}

/// Named values of the RDD 36 §6.1.1 / Table 2 `interlace_mode` field.
///
/// `interlace_mode` is a 2-bit field at the low nibble of byte 12 of the
/// frame header (alongside `chroma_format`). Only three codes are
/// defined; code `3` is reserved and `parse_frame_header` refuses it.
///
/// The wire-level scan-order semantics come straight from Table 2:
/// - `0` → progressive frame; a single picture() follows the header.
/// - `1` → interlaced, top field first; two pictures follow, the first
///   carries the top field (offset 0 in the source), the second carries
///   the bottom field (offset 1 / +1 stride).
/// - `2` → interlaced, bottom field first; two pictures follow, the
///   first carries the bottom field, the second carries the top field.
///
/// Field ordering also gates the `mb_height` calculation in §7: per
/// `picture()` the macroblock height is `(picture_pixel_height + 15) >> 4`,
/// where `picture_pixel_height` = `frame_height >> 1` for an interlaced
/// frame and `= frame_height` for a progressive one.
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum InterlaceMode {
    /// Code 0 — progressive frame; one picture() per frame.
    Progressive = 0,
    /// Code 1 — interlaced, top field first; the frame carries two
    /// pictures, the first being the top field.
    TopFieldFirst = 1,
    /// Code 2 — interlaced, bottom field first; the frame carries two
    /// pictures, the first being the bottom field.
    BottomFieldFirst = 2,
}

impl InterlaceMode {
    /// The on-the-wire u8 code for this variant.
    pub fn code(self) -> u8 {
        self as u8
    }

    /// `true` for the two interlaced variants (`TopFieldFirst` /
    /// `BottomFieldFirst`). The `picture_count()` accessor on
    /// [`FrameHeader`] uses the same predicate to decide between 1
    /// and 2 pictures per frame.
    pub fn is_interlaced(self) -> bool {
        !matches!(self, Self::Progressive)
    }
}

/// Map an RDD 36 §6.1.1 / Table 2 `interlace_mode` u2 code to the named
/// scan order it identifies. Returns `None` for the reserved code `3`
/// — note that `parse_frame_header` refuses code `3` outright per the
/// table's "reserved" entry, so a downstream consumer of a successfully
/// parsed header will only see `Some(_)` from this helper. Codes above
/// the u2 field width (`4..=255`) are also `None`; callers that pass a
/// raw byte must first mask the 2 bits out of the byte-12 packing
/// (`(b >> 2) & 0x3`) — `parse_frame_header` already does so.
pub fn interlace_mode_from_code(code: u8) -> Option<InterlaceMode> {
    match code {
        0 => Some(InterlaceMode::Progressive),
        1 => Some(InterlaceMode::TopFieldFirst),
        2 => Some(InterlaceMode::BottomFieldFirst),
        // 3 = reserved per Table 2 (`parse_frame_header` rejects on read);
        // 4..=255 cannot appear in the u2 wire field.
        _ => None,
    }
}

/// Parsed RDD 36 frame header. Fields the rest of the decoder needs.
#[derive(Clone, Debug)]
pub struct FrameHeader {
    pub frame_size: u32,
    pub frame_header_size: u16,
    pub bitstream_version: u8,
    /// RDD 36 §6.1.1 `encoder_identifier` — the f(32) four-character
    /// code at frame-header bytes 4..8 naming the encoder vendor /
    /// product that produced the frame (Apple maintains a registry of
    /// licensee codes). The spec marks it "Decoders should ignore this
    /// element", so it carries no decode semantics; it is surfaced here
    /// verbatim for stream-inspection and transcode-provenance callers
    /// (see [`FrameHeader::encoder_identifier_str`]).
    pub encoder_identifier: [u8; 4],
    pub width: u16,
    pub height: u16,
    pub chroma_format: ChromaFormat,
    pub interlace_mode: u8,
    pub aspect_ratio_information: u8,
    pub frame_rate_code: u8,
    pub color_primaries: u8,
    pub transfer_characteristic: u8,
    pub matrix_coefficients: u8,
    pub alpha_channel_type: u8,
    /// RDD 36 §5.1.1 / §6.1.1 `load_luma_quantization_matrix` flag, as it
    /// appeared on the wire. When `true`, [`Self::luma_qmat`] is the custom
    /// matrix carried in the frame header; when `false`, it is the default
    /// matrix (all 64 weights = 4 per §7.2). Surfaced for stream-inspection
    /// and transcode-provenance callers — see
    /// [`Self::quantization_matrix_source`].
    pub load_luma_quantization_matrix: bool,
    /// RDD 36 §5.1.1 / §6.1.1 `load_chroma_quantization_matrix` flag, as it
    /// appeared on the wire. When `false`, the §6.1.1 rule applies and
    /// [`Self::chroma_qmat`] mirrors [`Self::luma_qmat`] (which is itself
    /// the custom luma matrix if `load_luma_quantization_matrix` is `true`,
    /// otherwise the default). See [`Self::quantization_matrix_source`].
    pub load_chroma_quantization_matrix: bool,
    pub luma_qmat: [u8; 64],
    pub chroma_qmat: [u8; 64],
}

/// Provenance of a [`FrameHeader`]'s effective chroma quantization weight
/// matrix, per RDD 36 §6.1.1 / §7.2. The two `load_*_quantization_matrix`
/// wire flags allow three distinct derivations of the matrix actually used
/// to inverse-quantize the chroma (Cb, Cr) components.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum QuantizationMatrixSource {
    /// `load_chroma_quantization_matrix == 1`: the chroma matrix is the
    /// custom `chroma_quantization_matrix` carried in the frame header,
    /// independent of the luma flag (§6.1.1).
    CustomChroma,
    /// `load_chroma_quantization_matrix == 0` and
    /// `load_luma_quantization_matrix == 1`: the §6.1.1 fallback applies
    /// and the chroma matrix is a copy of the custom luma matrix.
    LumaCustom,
    /// `load_chroma_quantization_matrix == 0` and
    /// `load_luma_quantization_matrix == 0`: both fall through to the §7.2
    /// default matrix (all 64 weights = 4).
    Default,
}

impl FrameHeader {
    pub fn picture_count(&self) -> u32 {
        if self.interlace_mode == 0 {
            1
        } else {
            2
        }
    }

    /// Typed accessor for the RDD 36 §6.1.1 / Table 2
    /// `interlace_mode` field. Returns the named variant when the
    /// stream's u2 code is one of the three defined values
    /// (`0` → [`InterlaceMode::Progressive`],
    ///  `1` → [`InterlaceMode::TopFieldFirst`],
    ///  `2` → [`InterlaceMode::BottomFieldFirst`]).
    ///
    /// The raw `interlace_mode` field on this struct is the u8 code
    /// as it appeared on the wire (masked to the u2 width by the
    /// parser). `parse_frame_header` already refuses code `3` per
    /// Table 2's "reserved" entry, so this accessor always returns
    /// `Some(_)` for a successfully-parsed header — the `Option`
    /// shape matches the rest of the §6.1.1 reverse-helper surface
    /// (`alpha_channel_type_from_code`, `color_primaries_from_code`,
    /// `matrix_coefficients_from_code`) and lets callers handle a
    /// constructed-by-hand header that bypassed the parser.
    ///
    /// Field count semantics line up with [`Self::picture_count`]:
    /// a `Progressive` return implies one picture() in the frame;
    /// the two interlaced variants imply two pictures, with the
    /// first carrying the named-leading field.
    pub fn interlace_kind(&self) -> Option<InterlaceMode> {
        interlace_mode_from_code(self.interlace_mode)
    }

    /// Typed accessor for the RDD 36 §6.1.1 / Table 7
    /// `alpha_channel_type` field. Returns the named variant when the
    /// stream's u4 code is one of the three defined values
    /// (`0` → [`AlphaChannelType::None`], `1` → [`AlphaChannelType::Bits8`],
    /// `2` → [`AlphaChannelType::Bits16`]), and `None` for the
    /// reserved codes `3..=15`.
    ///
    /// The raw `alpha_channel_type` field on this struct is the u8 code
    /// as it appeared on the wire (masked to the low nibble by the
    /// parser); call this accessor when a downstream stage wants the
    /// named variant — e.g. to switch on the alpha-plane storage width
    /// without reproducing Table 7 at every call site. Returning the
    /// `None` outer-Option for reserved codes preserves the
    /// wire-level distinction between "no alpha is present" (which is
    /// `Some(AlphaChannelType::None)`) and "the field carried a
    /// reserved code that does not correspond to any named alpha
    /// configuration" (which is the outer `None`).
    ///
    /// Per the spec's clause-cross-checks the field is constrained
    /// further by [`Self::bitstream_version`]: a version-0 stream must
    /// carry `alpha_channel_type == 0` (§6.4), and `parse_frame_header`
    /// rejects any version-0 stream that violates that — so a
    /// `Some(AlphaChannelType::Bits8 | Bits16)` return from this
    /// accessor implies `bitstream_version == 1`.
    pub fn alpha_kind(&self) -> Option<AlphaChannelType> {
        alpha_channel_type_from_code(self.alpha_channel_type)
    }

    /// Typed accessor for the RDD 36 §6.1.1 / Table 5
    /// `color_primaries` field. Returns the named variant when the
    /// stream's u8 code matches one of the six nonreserved values
    /// (`1` → [`ColorPrimaries::Bt709`], `5` → [`ColorPrimaries::Bt601_625`],
    /// `6` → [`ColorPrimaries::Bt601_525`], `9` → [`ColorPrimaries::Bt2020`],
    /// `11` → [`ColorPrimaries::DciP3`], `12` → [`ColorPrimaries::P3D65`])
    /// and `None` for the "unknown / unspecified" codes (`0` and `2`)
    /// plus every reserved code in `[3, 4, 7, 8, 10, 13..=255]`.
    ///
    /// The raw `color_primaries` field on this struct is the u8 code as
    /// it appeared on the wire (Table 5 is a full-byte field, so no
    /// masking is needed in `parse_frame_header`); call this accessor
    /// when a downstream colour-management stage wants the named
    /// chromaticity set rather than re-deriving Table 5 at every call
    /// site. Returning `None` for the unknown codes preserves the
    /// wire-level distinction between "the stream says BT.709" (which
    /// is `Some(ColorPrimaries::Bt709)`) and "the stream did not pin a
    /// known primary set" (which is `None`) — a downstream colour
    /// pipeline can then fall back to a project default rather than
    /// silently re-interpreting an unknown stream as BT.709.
    ///
    /// The accessor is the natural mirror of the existing
    /// [`Self::interlace_kind`] and [`Self::alpha_kind`] surfaces: it
    /// returns `Option<ColorPrimaries>` with the same outer-Option
    /// discriminant, so a consumer reading a parsed packet can call
    /// `fh.color_primaries_kind()` and [`Self::matrix_coefficients_kind`]
    /// together without breaking up the read.
    pub fn color_primaries_kind(&self) -> Option<ColorPrimaries> {
        color_primaries_from_code(self.color_primaries)
    }

    /// Typed accessor for the RDD 36 §6.1.1 / Table 6
    /// `matrix_coefficients` field. Returns the named variant when the
    /// stream's u8 code matches one of the three nonreserved values
    /// (`1` → [`MatrixCoefficients::Bt709`],
    ///  `6` → [`MatrixCoefficients::Bt601`],
    ///  `9` → [`MatrixCoefficients::Bt2020Ncl`]) and `None` for the
    /// "unknown / unspecified" codes (`0` and `2`) plus every reserved
    /// code in `[3, 4, 5, 7, 8, 10..=255]`.
    ///
    /// The raw `matrix_coefficients` field on this struct is the u8 code
    /// as it appeared on the wire (Table 6 is a full-byte field, so
    /// `parse_frame_header` reads it verbatim with the same width as
    /// `color_primaries` and `transfer_characteristic`); call this
    /// accessor when a downstream Y'CbCr → R'G'B' conversion stage
    /// wants the named matrix rather than re-deriving Table 6 at every
    /// call site. The returned variant carries the `(K_R, K_G, K_B)`
    /// luma-coefficient triple via [`MatrixCoefficients::luma_coefficients`],
    /// so the §6.1.1 derivation formulas can be evaluated directly off
    /// the accessor result without a second table lookup.
    ///
    /// Returning the outer-Option `None` for unknown codes preserves
    /// the wire-level distinction between "the stream pins BT.709"
    /// (which is `Some(MatrixCoefficients::Bt709)`) and "the stream did
    /// not pin a known matrix" (which is `None`) — a downstream
    /// pipeline can then fall back to a project default rather than
    /// silently re-interpreting an unknown stream as BT.709.
    ///
    /// The accessor mirrors [`Self::color_primaries_kind`] /
    /// [`Self::interlace_kind`] / [`Self::alpha_kind`]: same
    /// outer-Option discriminant, so a consumer reading a parsed packet
    /// can call `fh.matrix_coefficients_kind()`,
    /// `fh.color_primaries_kind()`, and `fh.alpha_kind()` in a single
    /// read without breaking up the call chain.
    pub fn matrix_coefficients_kind(&self) -> Option<MatrixCoefficients> {
        matrix_coefficients_from_code(self.matrix_coefficients)
    }

    /// Typed accessor for the RDD 36 §6.1.1 `transfer_characteristic`
    /// field. Returns the named variant when the stream's u8 code is
    /// one of the three defined nonreserved values (`1` →
    /// [`TransferCharacteristic::Bt1886`] — the BT.601 / BT.709 /
    /// BT.2020 OETF; `16` → [`TransferCharacteristic::St2084`] — the
    /// SMPTE ST 2084:2014 Inverse-EOTF, a.k.a. PQ; `18` →
    /// [`TransferCharacteristic::Hlg`] — the BT.2100-2 HLG Reference
    /// OETF) and `None` for the "unknown / unspecified" codes (`0`
    /// and `2`) plus every reserved code in the remainder of
    /// `[3..=255]`.
    ///
    /// Unlike `color_primaries` and `matrix_coefficients`, the spec
    /// does not enumerate the named codes in a Table — §6.1.1 spells
    /// out the three OETF formulas in prose ("the value 1 signifies
    /// the function specified by ITU-R BT.601/BT.709/BT.2020 …", "the
    /// value 16 signifies the Inverse-EOTF formula in Section 5.3 of
    /// SMPTE ST 2084:2014 …", "the value 18 signifies the HLG
    /// Reference OETF from Table 5 of ITU-R BT.2100-2 …"), with the
    /// closing note that the named code numbers agree with Table 3 of
    /// ITU-T H.273.
    ///
    /// The raw `transfer_characteristic` field on this struct is the
    /// u8 code as it appeared on the wire (full byte width — no mask
    /// is involved in `parse_frame_header`); call this accessor when
    /// a downstream colour-management stage wants the named OETF
    /// rather than re-deriving §6.1.1 at every call site. Returning
    /// `None` for the unknown codes preserves the wire-level
    /// distinction between "the stream pins ST 2084"
    /// (`Some(TransferCharacteristic::St2084)`) and "the stream did
    /// not pin a known transfer function" (`None`) — a downstream
    /// pipeline can then fall back to a project default rather than
    /// silently re-interpreting an unknown stream as BT.1886.
    ///
    /// The accessor is the natural mirror of
    /// [`Self::color_primaries_kind`] and
    /// [`Self::matrix_coefficients_kind`]: same outer-Option
    /// discriminant, same `code()` round-trip property, so a consumer
    /// reading a parsed packet can call
    /// `fh.transfer_characteristic_kind()`,
    /// `fh.color_primaries_kind()`, and
    /// `fh.matrix_coefficients_kind()` in a single read.
    pub fn transfer_characteristic_kind(&self) -> Option<TransferCharacteristic> {
        transfer_characteristic_from_code(self.transfer_characteristic)
    }

    /// Typed accessor for the RDD 36 §6.2 / Table 4 `frame_rate_code`
    /// field. Returns the named rate as an [`oxideav_core::Rational`]
    /// when the stream's u4 code is one of the eleven defined values
    /// (`1` → `24000/1001`, `2` → `24/1`, `3` → `25/1`, `4` →
    /// `30000/1001`, `5` → `30/1`, `6` → `50/1`, `7` → `60000/1001`,
    /// `8` → `60/1`, `9` → `100/1`, `10` → `120000/1001`, `11` →
    /// `120/1`) and `None` for the "unknown / unspecified" code `0`
    /// plus every reserved code in `12..=15`.
    ///
    /// The raw `frame_rate_code` field on this struct is the u4 code as
    /// it appeared on the wire (masked to the low nibble by the parser
    /// — `parse_frame_header` reads it from the packed
    /// `aspect_ratio_information(4) + frame_rate_code(4)` byte). Call
    /// this accessor when a downstream pipeline stage wants the named
    /// rate as a [`oxideav_core::Rational`] rather than re-deriving
    /// Table 4 at every call site. The returned fractions are the
    /// spec's exact symbolic forms (e.g. `30000/1001`, not the reduced
    /// or float-rounded value), so the result can be forwarded along an
    /// `oxideav_core` graph as a [`CodecParameters::frame_rate`]
    /// without precision loss; the encoder side already does the
    /// inverse via [`frame_rate_code_from_rational`] when filling
    /// [`FrameMeta::frame_rate_code`] from a caller-supplied rate.
    ///
    /// Returning the outer-Option `None` for unknown + reserved codes
    /// preserves the wire-level distinction between "the stream pins
    /// 29.97 fps" (`Some(Rational::new(30000, 1001))`) and "the stream
    /// did not pin a known rate" (`None`) — a downstream pipeline can
    /// then fall back to a project default rather than silently
    /// re-interpreting an unknown stream as 30 fps.
    ///
    /// The accessor is the natural mirror of
    /// [`Self::color_primaries_kind`] / [`Self::matrix_coefficients_kind`]
    /// / [`Self::transfer_characteristic_kind`]: same outer-Option
    /// discriminant, so a consumer reading a parsed packet can read
    /// `fh.frame_rate()` alongside the colour-metadata accessors
    /// without breaking up the call chain. Unlike those accessors the
    /// returned type is a [`oxideav_core::Rational`] (rather than a
    /// named enum) because §6.2 Table 4 is a list of exact rational
    /// rates with no closer-grained naming — `30000/1001` and `30/1`
    /// are wire-distinct codes (4 and 5), and the natural typed surface
    /// is the rate fraction itself.
    ///
    /// [`CodecParameters::frame_rate`]: oxideav_core::CodecParameters::frame_rate
    pub fn frame_rate(&self) -> Option<oxideav_core::Rational> {
        rational_from_frame_rate_code(self.frame_rate_code)
    }

    /// Typed accessor for the RDD 36 §6.2 / Table 3
    /// `aspect_ratio_information` field. Returns the named ratio as an
    /// [`oxideav_core::Rational`] when the stream's u4 code is one of the
    /// three defined values (`1` → `1/1` square pixels, `2` → `4/3`,
    /// `3` → `16/9`) and `None` for the "unknown / unspecified" code `0`
    /// plus every reserved code in `4..=15`.
    ///
    /// The raw `aspect_ratio_information` field on this struct is the u4
    /// code as it appeared on the wire (masked to the high nibble by the
    /// parser — `parse_frame_header` reads it from the packed
    /// `aspect_ratio_information(4) + frame_rate_code(4)` byte). Call
    /// this accessor when a downstream pipeline stage wants the named
    /// ratio as a [`oxideav_core::Rational`] rather than re-deriving
    /// Table 3 at every call site. Per Table 3 the code distinguishes a
    /// pixel-aspect signal (`1` → square pixels, i.e. PAR = 1/1) from
    /// the two display-aspect signals (`2` → 4:3 picture, `3` → 16:9
    /// picture); the returned fraction is the documented value with no
    /// further normalisation, so a code-1 stream decodes to a literal
    /// `1/1` and stays structurally distinct from a `None` (unknown)
    /// result.
    ///
    /// Returning the outer-Option `None` for unknown + reserved codes
    /// preserves the wire-level distinction between "the stream pins
    /// 16:9" (`Some(Rational::new(16, 9))`) and "the stream did not pin
    /// a known aspect" (`None`) — a downstream pipeline can then fall
    /// back to a project default rather than silently re-interpreting
    /// an unknown stream as 16:9.
    ///
    /// The accessor is the natural mirror of [`Self::frame_rate`] (its
    /// neighbour in the packed §6.2 byte): same outer-Option
    /// discriminant, same [`oxideav_core::Rational`] return type, so a
    /// consumer reading a parsed packet can read `fh.aspect_ratio()` and
    /// `fh.frame_rate()` alongside the §6.1.1 colour-metadata accessors
    /// without breaking up the call chain. Like [`Self::frame_rate`] the
    /// returned type is the rate fraction itself (rather than a named
    /// enum) because Table 3 enumerates exact rational ratios with no
    /// closer-grained naming — `1/1`, `4/3`, and `16/9` are
    /// wire-distinct codes (1, 2, 3) and the natural typed surface is
    /// the ratio itself.
    pub fn aspect_ratio(&self) -> Option<oxideav_core::Rational> {
        aspect_ratio_from_code(self.aspect_ratio_information)
    }

    /// Typed accessor folding the five descriptive RDD 36 §5.1.1 / §6.2
    /// frame-header metadata bytes — `aspect_ratio_information` (§6.2
    /// Table 3), `frame_rate_code` (§6.2 Table 4), `color_primaries`
    /// (§6.1.1 Table 5), `transfer_characteristic` (§6.1.1),
    /// `matrix_coefficients` (§6.1.1 Table 6) — back into the
    /// [`FrameMeta`] struct the encoder consumes.
    ///
    /// Each raw field stays on this struct individually (wire-level
    /// fidelity; the per-field typed accessors
    /// [`Self::aspect_ratio`] / [`Self::frame_rate`] /
    /// [`Self::color_primaries_kind`] /
    /// [`Self::transfer_characteristic_kind`] /
    /// [`Self::matrix_coefficients_kind`] lift them to named values).
    /// This accessor serves the *re-encode* direction instead: a
    /// transcode pipeline that parses an incoming packet via
    /// [`parse_frame`] can forward `fh.meta()` straight into
    /// [`crate::encoder::EncoderConfig::with_meta`] so the outgoing
    /// stream carries the same descriptive metadata, without copying
    /// the five fields by hand at every call site — the same
    /// parsed-header → encoder-config forwarding shape as
    /// [`PictureHeader::mbs_per_slice`] →
    /// [`crate::encoder::EncoderConfig::with_mbs_per_slice`].
    ///
    /// The returned value is the raw bytes verbatim (no named-value
    /// filtering): §5.1.1 documents these fields as descriptive hints a
    /// decoder passes through rather than validates, so even a
    /// reserved / unknown code (which the per-field typed accessors
    /// surface as `None`) is preserved bit-exactly across the
    /// transcode. An all-zero header yields a value for which
    /// [`FrameMeta::is_unknown`] is `true` — identical to
    /// [`FrameMeta::unknown`], the encoder's no-op default.
    pub fn meta(&self) -> FrameMeta {
        FrameMeta {
            aspect_ratio_information: self.aspect_ratio_information,
            frame_rate_code: self.frame_rate_code,
            color_primaries: self.color_primaries,
            transfer_characteristic: self.transfer_characteristic,
            matrix_coefficients: self.matrix_coefficients,
        }
    }

    /// The raw four bytes of the RDD 36 §6.1.1 `encoder_identifier`
    /// frame-header field (bytes 4..8 of `frame_header()`), as they
    /// appeared on the wire. The spec marks this element "Decoders
    /// should ignore" — it names the encoder vendor / product (Apple
    /// maintains a registry of licensee codes) and carries no decode
    /// semantics — so this is a pure pass-through for stream-inspection
    /// and transcode-provenance callers. The bytes are not validated as
    /// printable ASCII; use [`Self::encoder_identifier_str`] when a
    /// human-readable form is wanted.
    pub fn encoder_identifier(&self) -> [u8; 4] {
        self.encoder_identifier
    }

    /// The RDD 36 §6.1.1 `encoder_identifier` rendered as a string when
    /// all four bytes are printable ASCII (`0x20..=0x7E`), else `None`.
    ///
    /// `encoder_identifier` is documented as a four-character code, and
    /// in practice every encoder writes a printable FourCC (this crate
    /// writes [`ENCODER_IDENTIFIER`]). The `Option` discriminant lets a
    /// caller distinguish a conventional printable vendor tag from a
    /// non-printable / binary value some non-conforming encoder might
    /// have written, without panicking on invalid UTF-8 — mirroring the
    /// `None`-for-out-of-range shape of the other §6.1.1 typed
    /// accessors on this struct ([`Self::color_primaries_kind`] et al.).
    /// Callers wanting the raw bytes regardless take
    /// [`Self::encoder_identifier`].
    pub fn encoder_identifier_str(&self) -> Option<&str> {
        if self
            .encoder_identifier
            .iter()
            .all(|&b| (0x20..=0x7E).contains(&b))
        {
            // All four bytes are printable ASCII → valid UTF-8.
            std::str::from_utf8(&self.encoder_identifier).ok()
        } else {
            None
        }
    }

    /// Typed accessor for the §6.1.1 / §7.2 derivation of the effective
    /// **chroma** quantization weight matrix, decoded from the two
    /// `load_*_quantization_matrix` wire flags
    /// ([`Self::load_luma_quantization_matrix`] /
    /// [`Self::load_chroma_quantization_matrix`]).
    ///
    /// The wire carries no chroma matrix at all when
    /// `load_chroma_quantization_matrix == 0`; in that case §6.1.1 says the
    /// luma matrix is used for chroma as well. This accessor distinguishes
    /// the three resulting cases — [`QuantizationMatrixSource::CustomChroma`],
    /// [`QuantizationMatrixSource::LumaCustom`], and
    /// [`QuantizationMatrixSource::Default`] — so stream-inspection and
    /// transcode-provenance callers can tell whether [`Self::chroma_qmat`]
    /// originated from a header-carried chroma matrix, a copied custom luma
    /// matrix, or the §7.2 default. The luma side is the simpler binary
    /// [`Self::load_luma_quantization_matrix`] (custom vs default).
    pub fn quantization_matrix_source(&self) -> QuantizationMatrixSource {
        if self.load_chroma_quantization_matrix {
            QuantizationMatrixSource::CustomChroma
        } else if self.load_luma_quantization_matrix {
            QuantizationMatrixSource::LumaCustom
        } else {
            QuantizationMatrixSource::Default
        }
    }

    /// Derive the RDD 36 §6.2 picture geometry implied by this frame
    /// header's `width` / `height` / `interlace_mode`.
    ///
    /// The frame header carries the source-picture luma dimensions
    /// (`horizontal_size` / `vertical_size`), but the decode loop works
    /// in macroblocks against the *encoded* picture, whose width/height
    /// are rounded up to whole 16×16 macroblocks. §6.2 fixes every step
    /// of that derivation:
    ///
    /// * `width_in_mb = (horizontal_size + 15) / 16` — the same for every
    ///   picture in the frame.
    /// * For an interlaced frame the §6.2 `picture_vertical_size` split
    ///   gives `topFieldVerticalSize = (vertical_size + 1) / 2` and
    ///   `bottomFieldVerticalSize = vertical_size / 2` (so an odd height
    ///   puts the extra row in the top field); a progressive frame's sole
    ///   picture has `picture_vertical_size = vertical_size`.
    /// * `height_in_mb = (picture_vertical_size + 15) / 16` per picture.
    /// * §6.2 / §7.5.3 cropping: when `16 * width_in_mb > horizontal_size`
    ///   the rightmost `16 * width_in_mb − horizontal_size` columns of the
    ///   decoded picture are discarded, and when `16 * height_in_mb >
    ///   picture_vertical_size` the bottom `16 * height_in_mb −
    ///   picture_vertical_size` rows are discarded.
    ///
    /// This is the geometry the decode path computes internally; surfacing
    /// it as a typed value lets a stream-inspection / muxer / transcode
    /// caller size buffers, cross-check the §6.3 `deprecated_number_of_slices`
    /// field (via [`PictureGeometry::slice_count`]), or validate a
    /// container's declared frame dimensions without re-deriving the §6.2
    /// rounding and field-split rules.
    pub fn picture_geometry(&self) -> PictureGeometry {
        let horizontal_size = self.width as usize;
        let vertical_size = self.height as usize;
        let width_in_mb = horizontal_size.div_ceil(MB_SIDE_PX);

        // §6.2 picture_vertical_size: progressive → full height; the two
        // interlaced modes each carry one field, the leading field (the
        // first picture() in the frame) being the named one. The top
        // field is the taller of the two when the frame height is odd.
        let (picture_vertical_size, second_picture_vertical_size) = if self.interlace_mode == 0 {
            (vertical_size, None)
        } else {
            let top = vertical_size.div_ceil(2); // (vertical_size + 1) / 2
            let bottom = vertical_size / 2;
            match self.interlace_mode {
                // TopFieldFirst: first picture is the top field.
                1 => (top, Some(bottom)),
                // BottomFieldFirst: first picture is the bottom field.
                _ => (bottom, Some(top)),
            }
        };

        let height_in_mb = picture_vertical_size.div_ceil(MB_SIDE_PX);
        let coded_width = width_in_mb * MB_SIDE_PX;
        let coded_height = height_in_mb * MB_SIDE_PX;

        PictureGeometry {
            width_in_mb,
            height_in_mb,
            picture_vertical_size,
            second_picture_vertical_size,
            picture_count: self.picture_count(),
            right_crop: coded_width - horizontal_size,
            bottom_crop: coded_height - picture_vertical_size,
        }
    }
}

/// Luma side length of a ProRes macroblock in pixels (16×16), per
/// RDD 36 §6.2. The encoded picture is an integral number of these.
const MB_SIDE_PX: usize = 16;

/// RDD 36 §6.2 picture geometry derived from a [`FrameHeader`] — the
/// macroblock dimensions of the *encoded* picture(s) plus the
/// source-picture cropping that §6.2 / §7.5.3 mandate on decode.
///
/// All macroblock dimensions are per single picture. For an interlaced
/// frame both field pictures share [`Self::width_in_mb`]; the leading
/// field's [`Self::picture_vertical_size`] / [`Self::height_in_mb`] are
/// reported in the named fields and the trailing field's luma height in
/// [`Self::second_picture_vertical_size`]. See
/// [`FrameHeader::picture_geometry`] for the full derivation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PictureGeometry {
    /// `width_in_mb = (horizontal_size + 15) / 16` — the encoded picture
    /// width in macroblocks; identical for every picture in the frame.
    pub width_in_mb: usize,
    /// `height_in_mb = (picture_vertical_size + 15) / 16` for the leading
    /// (or sole) picture.
    pub height_in_mb: usize,
    /// The leading (or sole) picture's luma-sample height after the §6.2
    /// `picture_vertical_size` field split.
    pub picture_vertical_size: usize,
    /// The trailing field picture's luma-sample height for an interlaced
    /// frame; `None` for a progressive frame (one picture only).
    pub second_picture_vertical_size: Option<usize>,
    /// Number of pictures in the frame — `1` progressive, `2` interlaced
    /// (mirrors [`FrameHeader::picture_count`]).
    pub picture_count: u32,
    /// §6.2 / §7.5.3 right-edge crop: columns of the decoded picture to
    /// discard because `16 * width_in_mb` exceeds `horizontal_size`. `0`
    /// when the frame width is already a multiple of 16.
    pub right_crop: usize,
    /// §6.2 / §7.5.3 bottom-edge crop for the leading picture: rows to
    /// discard because `16 * height_in_mb` exceeds `picture_vertical_size`.
    /// `0` when the picture height is already a multiple of 16.
    pub bottom_crop: usize,
}

impl PictureGeometry {
    /// Number of slices the leading picture is partitioned into, given the
    /// picture header's `log2_desired_slice_size_in_mb` (§6.2 / §6.3).
    ///
    /// Bridges the frame-header-only geometry to the slice partitioning,
    /// which depends on the per-picture `log2_desired_slice_size_in_mb`
    /// carried in the picture header rather than the frame header. Equal to
    /// [`slice_count`]`(self.width_in_mb, log2, self.height_in_mb)`.
    pub fn slice_count(&self, log2_desired_slice_size_in_mb: u8) -> usize {
        slice_count(
            self.width_in_mb,
            log2_desired_slice_size_in_mb,
            self.height_in_mb,
        )
    }

    /// `number_of_slices_per_mb_row` (§6.2) — the count of entries in the
    /// `slice_size_in_mb` array, the same for every macroblock row.
    pub fn slices_per_mb_row(&self, log2_desired_slice_size_in_mb: u8) -> usize {
        compute_slice_sizes(self.width_in_mb, log2_desired_slice_size_in_mb).len()
    }
}

/// Parse the frame() syntax (frame_size + 'icpf' + frame_header()).
/// Returns the parsed header and the remaining bytes (everything after
/// the frame header, i.e. starting at the first picture()).
pub fn parse_frame(data: &[u8]) -> Result<(FrameHeader, &[u8])> {
    if data.len() < 8 {
        return Err(Error::invalid("prores: frame truncated (need 8 bytes)"));
    }
    let frame_size = u32::from_be_bytes(data[0..4].try_into().unwrap());
    if &data[4..8] != FRAME_IDENTIFIER {
        // ProRes RAW samples carry the `aprh` in-stream marker at the
        // same offset. They are a distinct, RDD-36-out-of-scope format
        // (single-plane Bayer/CFA, not YUV/RGB); surface a precise
        // Unsupported error instead of a generic magic mismatch so the
        // caller can tell "this is ProRes RAW, which we don't decode"
        // apart from "this isn't a ProRes frame at all".
        if &data[4..8] == PRORES_RAW_FRAME_IDENTIFIER {
            return Err(Error::unsupported(
                "prores: ProRes RAW sample ('aprh' marker) is not decodable — \
                 ProRes RAW is a separate Apple format (single-plane Bayer/CFA \
                 sensor data) outside the scope of SMPTE RDD 36",
            ));
        }
        return Err(Error::invalid("prores: frame magic mismatch (not 'icpf')"));
    }
    if (frame_size as usize) > data.len() {
        return Err(Error::invalid(
            "prores: frame_size exceeds available buffer",
        ));
    }
    // RDD 36 §5.1 frame(): `frame_size` is the size of the whole frame
    // unit INCLUDING the 4-byte size field, the 4-byte 'icpf' magic, the
    // frame_header() (≥20 bytes per §6.1.1), and ≥1 picture() — so it
    // must be at least 8. A malformed `frame_size < 8` would otherwise
    // panic on the `&frame_data[8..]` slice; refuse it cleanly.
    if (frame_size as usize) < 8 {
        return Err(Error::invalid(
            "prores: frame_size below the 8-byte size+magic prefix",
        ));
    }
    let frame_data = &data[..frame_size as usize];
    let after_magic = &frame_data[8..];
    let (fh, after_fh) = parse_frame_header(after_magic)?;
    Ok((fh, after_fh))
}

/// Parse just the frame_header() block (assumes `data` starts at the
/// frame_header_size field; i.e. caller has already consumed the 8
/// bytes of frame_size + 'icpf').
pub fn parse_frame_header(data: &[u8]) -> Result<(FrameHeader, &[u8])> {
    if data.len() < 20 {
        return Err(Error::invalid("prores: frame header truncated"));
    }
    let frame_header_size = u16::from_be_bytes(data[0..2].try_into().unwrap());
    if (frame_header_size as usize) < 20 || (frame_header_size as usize) > data.len() {
        return Err(Error::invalid("prores: bad frame_header_size"));
    }
    // RDD 36 §6.1.1: the reserved byte at offset 2 is "set to 0" by
    // encoders and decoders shall ignore it (in particular shall NOT
    // expect zero) — read but do not validate.
    let _reserved = data[2];
    let bitstream_version = data[3];
    // RDD 36 §6.1.1 / §6.4: "A decoder shall abort if it encounters a
    // bitstream with an unsupported bitstream_version value." The spec
    // currently describes versions 0 and 1; anything else is unsupported.
    if bitstream_version > 1 {
        return Err(Error::unsupported(format!(
            "prores: unsupported bitstream_version {bitstream_version} \
             (RDD 36 specifies versions 0 and 1)"
        )));
    }
    // RDD 36 §6.1.1 encoder_identifier: f(32) vendor/product code at
    // bytes 4..8. "Decoders should ignore this element" — we do not
    // validate it, but surface the raw bytes on the parsed header for
    // inspection / transcode-provenance callers.
    let encoder_identifier: [u8; 4] = data[4..8].try_into().unwrap();
    let width = u16::from_be_bytes(data[8..10].try_into().unwrap());
    let height = u16::from_be_bytes(data[10..12].try_into().unwrap());
    // byte 12: chroma_format (u2) + reserved (u2) + interlace_mode (u2) + reserved (u2)
    let b12 = data[12];
    let chroma_code = (b12 >> 6) & 0x3;
    let interlace_mode = (b12 >> 2) & 0x3;
    let chroma_format = ChromaFormat::from_code(chroma_code)?;
    // RDD 36 §6.1.1 Table 2: interlace_mode == 3 is reserved. The two
    // reserved bits framing the u2 field can never spill into the read
    // (mask `& 0x3` above) but the value 3 itself must be rejected as
    // a "decoder shall refuse" case per the table.
    if interlace_mode == 3 {
        return Err(Error::invalid(
            "prores: interlace_mode 3 is reserved (RDD 36 §6.1.1 Table 2)",
        ));
    }
    // byte 13: aspect_ratio_information (u4) + frame_rate_code (u4)
    let b13 = data[13];
    let aspect_ratio_information = (b13 >> 4) & 0xF;
    let frame_rate_code = b13 & 0xF;
    let color_primaries = data[14];
    let transfer_characteristic = data[15];
    let matrix_coefficients = data[16];
    // byte 17: reserved u4 + alpha_channel_type u4
    let b17 = data[17];
    let alpha_channel_type = b17 & 0xF;
    // RDD 36 §6.4 (also §6.1.1 alpha_channel_type semantics): if
    // bitstream_version == 0 then chroma_format MUST be 2 (4:2:2) and
    // alpha_channel_type MUST be 0. Version-0 streams predate the
    // 4:4:4 and alpha extensions and a conforming decoder must refuse
    // any version-0 stream that carries them.
    if bitstream_version == 0 {
        if chroma_format != ChromaFormat::Y422 {
            return Err(Error::invalid(format!(
                "prores: bitstream_version 0 requires chroma_format=2 (4:2:2), got code {chroma_code} \
                 (RDD 36 §6.4)"
            )));
        }
        if alpha_channel_type != 0 {
            return Err(Error::invalid(format!(
                "prores: bitstream_version 0 requires alpha_channel_type=0, got {alpha_channel_type} \
                 (RDD 36 §6.4)"
            )));
        }
    }
    // bytes 18..20: 14 reserved + load_luma + load_chroma (the last two bits)
    let b19 = data[19];
    let load_luma = (b19 >> 1) & 1;
    let load_chroma = b19 & 1;

    let mut luma_qmat = [4u8; 64];
    let mut chroma_qmat = [4u8; 64];
    let mut cursor = 20usize;
    if load_luma == 1 {
        if data.len() < cursor + 64 {
            return Err(Error::invalid("prores: luma_qmat truncated"));
        }
        luma_qmat.copy_from_slice(&data[cursor..cursor + 64]);
        cursor += 64;
        // RDD 36 §6.1.1 (luma_quantization_matrix): "Each entry of the
        // matrix will be in the range 2, 3, …, 63." A custom matrix
        // outside that range cannot be inverse-quantized per §7.3 (the
        // qScale * weight product would be 0 or > 32256), so a
        // conforming decoder must refuse it.
        if let Some(&bad) = luma_qmat.iter().find(|&&w| !(2..=63).contains(&w)) {
            return Err(Error::invalid(format!(
                "prores: luma_quantization_matrix entry {bad} out of range 2..=63 \
                 (RDD 36 §6.1.1)"
            )));
        }
    }
    if load_chroma == 1 {
        if data.len() < cursor + 64 {
            return Err(Error::invalid("prores: chroma_qmat truncated"));
        }
        chroma_qmat.copy_from_slice(&data[cursor..cursor + 64]);
        cursor += 64;
        // RDD 36 §6.1.1 (chroma_quantization_matrix): same 2..=63
        // range constraint.
        if let Some(&bad) = chroma_qmat.iter().find(|&&w| !(2..=63).contains(&w)) {
            return Err(Error::invalid(format!(
                "prores: chroma_quantization_matrix entry {bad} out of range 2..=63 \
                 (RDD 36 §6.1.1)"
            )));
        }
    } else if load_luma == 1 {
        // Per §6.1.1 load_chroma_quantization_matrix: "If 0, the luma
        // matrix shall be used (i.e., the specified custom luma
        // quantization matrix if load_luma_quantization_matrix is 1 or
        // the default matrix otherwise)."
        chroma_qmat = luma_qmat;
    }
    // Skip up to frame_header_size, allowing trailing reserved bytes.
    if cursor > frame_header_size as usize {
        return Err(Error::invalid(
            "prores: frame header parser overran declared size",
        ));
    }
    // We don't use the frame_size from frame() here (parse_frame_header
    // is also called directly in tests). Use 0 as a placeholder — the
    // encoder fills it in.
    Ok((
        FrameHeader {
            frame_size: 0,
            frame_header_size,
            bitstream_version,
            encoder_identifier,
            width,
            height,
            chroma_format,
            interlace_mode,
            aspect_ratio_information,
            frame_rate_code,
            color_primaries,
            transfer_characteristic,
            matrix_coefficients,
            alpha_channel_type,
            load_luma_quantization_matrix: load_luma == 1,
            load_chroma_quantization_matrix: load_chroma == 1,
            luma_qmat,
            chroma_qmat,
        },
        &data[frame_header_size as usize..],
    ))
}

/// Optional descriptive metadata fields written into the frame header.
/// All fields are documented in RDD 36 §5.1.1 / §6.2 and default to 0
/// (= "unknown / unspecified") so RDD 36 decoders treat them as a hint
/// only.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct FrameMeta {
    /// `aspect_ratio_information` per §6.2 / Table 3 (0 = unknown,
    /// 1 = square pixels, 2 = 4:3, 3 = 16:9, 4..=15 = reserved).
    pub aspect_ratio_information: u8,
    /// `frame_rate_code` per §6.2 / Table 4 — see
    /// [`frame_rate_code_from_rational`] for the mapping. 0 = unknown.
    pub frame_rate_code: u8,
    /// `color_primaries` per §6.2 (BT.601, BT.709, etc — uses the same
    /// codes as ISO/IEC 23001-8 / Rec. ITU-T H.273).
    pub color_primaries: u8,
    /// `transfer_characteristic` per §6.2.
    pub transfer_characteristic: u8,
    /// `matrix_coefficients` per §6.2.
    pub matrix_coefficients: u8,
}

impl FrameMeta {
    /// All fields zeroed = "unknown / unspecified". Identical to
    /// `Default::default()`.
    pub fn unknown() -> Self {
        Self::default()
    }

    /// True when every field is 0 ("unknown / unspecified"). Encoder
    /// uses this to detect the no-op case for short-circuit testing.
    pub fn is_unknown(self) -> bool {
        self.aspect_ratio_information == 0
            && self.frame_rate_code == 0
            && self.color_primaries == 0
            && self.transfer_characteristic == 0
            && self.matrix_coefficients == 0
    }
}

/// Map a frame rate (as a [`oxideav_core::Rational`]) to the RDD 36
/// §6.2 / Table 4 `frame_rate_code` (4-bit field). Returns 0
/// ("unknown") for any rate that is not one of the spec's named codes.
///
/// The spec's named rates (rounded to the table values):
///
/// | code | rate                |
/// |------|---------------------|
/// | 1    | 24000 / 1001 (~23.976) |
/// | 2    | 24                  |
/// | 3    | 25                  |
/// | 4    | 30000 / 1001 (~29.97)  |
/// | 5    | 30                  |
/// | 6    | 50                  |
/// | 7    | 60000 / 1001 (~59.94)  |
/// | 8    | 60                  |
/// | 9    | 100                 |
/// | 10   | 120000 / 1001 (~119.88) |
/// | 11   | 120                 |
///
/// `12..=15` are reserved per the spec — never emitted.
pub fn frame_rate_code_from_rational(r: oxideav_core::Rational) -> u8 {
    if r.num <= 0 || r.den <= 0 {
        return 0;
    }
    // Compare against each named rate by integer cross-product
    // (num * d_named == n_named * den) — exact, drift-free for the
    // canonical fractions that ship as TimeBase / Rational pairs.
    let num = r.num as i128;
    let den = r.den as i128;
    let candidates: &[(u8, i128, i128)] = &[
        (1, 24_000, 1001),
        (2, 24, 1),
        (3, 25, 1),
        (4, 30_000, 1001),
        (5, 30, 1),
        (6, 50, 1),
        (7, 60_000, 1001),
        (8, 60, 1),
        (9, 100, 1),
        (10, 120_000, 1001),
        (11, 120, 1),
    ];
    for &(code, n, d) in candidates {
        if num * d == n * den {
            return code;
        }
    }
    0
}

/// Map an RDD 36 §6.2 / Table 4 `frame_rate_code` back to the
/// [`oxideav_core::Rational`] it names. Returns `None` for the
/// unknown / reserved codes (0 and 12..=15) and for the upper 4
/// bits that are never meaningful for a u4 field — callers must
/// strip them with `& 0x0F` before passing in.
///
/// This is the inverse of [`frame_rate_code_from_rational`] for
/// every code that has a defined rate, and is the natural surface
/// for downstream pipeline code reading a decoded ProRes packet
/// (the codec carries no frame rate in any external timebase, so
/// a decoder that wants to forward `frame_rate` along an
/// `oxideav_core` graph must convert the in-stream u4 itself).
///
/// The returned fractions are the spec's exact symbolic forms
/// (e.g. `30000/1001`, not the reduced or float-rounded value),
/// so a parsed code 4 round-trips back through
/// [`frame_rate_code_from_rational`] to 4 without loss.
///
/// | code | returned rate              |
/// |------|----------------------------|
/// | 0    | `None` (unknown)           |
/// | 1    | `24000 / 1001` (~23.976)   |
/// | 2    | `24 / 1`                   |
/// | 3    | `25 / 1`                   |
/// | 4    | `30000 / 1001` (~29.97)    |
/// | 5    | `30 / 1`                   |
/// | 6    | `50 / 1`                   |
/// | 7    | `60000 / 1001` (~59.94)    |
/// | 8    | `60 / 1`                   |
/// | 9    | `100 / 1`                  |
/// | 10   | `120000 / 1001` (~119.88)  |
/// | 11   | `120 / 1`                  |
/// | 12..=15 | `None` (reserved)       |
pub fn rational_from_frame_rate_code(code: u8) -> Option<oxideav_core::Rational> {
    match code {
        1 => Some(oxideav_core::Rational::new(24_000, 1001)),
        2 => Some(oxideav_core::Rational::new(24, 1)),
        3 => Some(oxideav_core::Rational::new(25, 1)),
        4 => Some(oxideav_core::Rational::new(30_000, 1001)),
        5 => Some(oxideav_core::Rational::new(30, 1)),
        6 => Some(oxideav_core::Rational::new(50, 1)),
        7 => Some(oxideav_core::Rational::new(60_000, 1001)),
        8 => Some(oxideav_core::Rational::new(60, 1)),
        9 => Some(oxideav_core::Rational::new(100, 1)),
        10 => Some(oxideav_core::Rational::new(120_000, 1001)),
        11 => Some(oxideav_core::Rational::new(120, 1)),
        // 0 = unknown/unspecified; 12..=15 = reserved per Table 4.
        _ => None,
    }
}

/// Map an RDD 36 §6.2 / Table 3 `aspect_ratio_information` u4 code to
/// the pixel/image aspect ratio it names, returned as an
/// [`oxideav_core::Rational`].
///
/// Table 3 only defines four codes:
/// - `0` → unknown / unspecified → `None`
/// - `1` → square pixels → `Some(1/1)`
/// - `2` → 4:3 image aspect → `Some(4/3)`
/// - `3` → 16:9 image aspect → `Some(16/9)`
/// - `4..=15` → reserved → `None`
///
/// The returned fraction is the documented value with no further
/// normalisation; a code-1 stream therefore decodes to a literal
/// `1/1` and stays distinct from a `None` (unknown) result.
pub fn aspect_ratio_from_code(code: u8) -> Option<oxideav_core::Rational> {
    match code {
        1 => Some(oxideav_core::Rational::new(1, 1)),
        2 => Some(oxideav_core::Rational::new(4, 3)),
        3 => Some(oxideav_core::Rational::new(16, 9)),
        // 0 = unknown/unspecified; 4..=15 = reserved per Table 3.
        _ => None,
    }
}

/// Named values of the RDD 36 §6.1.1 / Table 5 `color_primaries` field.
///
/// Each named variant carries the same numeric code value defined by
/// the spec (whose nonreserved values agree with Table 2 of ITU-T
/// H.273, as noted in §6.1.1). The reserved / unknown codes are
/// surfaced as `None` from [`color_primaries_from_code`] rather than
/// landing on a variant, so a downstream consumer can distinguish "the
/// stream said unknown" from "the stream specified BT.709".
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum ColorPrimaries {
    /// Code 1 — ITU-R BT.709 primaries (Red 0.640/0.330, Green
    /// 0.300/0.600, Blue 0.150/0.060, white D65 0.3127/0.3290).
    Bt709 = 1,
    /// Code 5 — ITU-R BT.601 625-line primaries (Red 0.640/0.330,
    /// Green 0.290/0.600, Blue 0.150/0.060, white D65).
    Bt601_625 = 5,
    /// Code 6 — ITU-R BT.601 525-line primaries (Red 0.630/0.340,
    /// Green 0.310/0.595, Blue 0.155/0.070, white D65).
    Bt601_525 = 6,
    /// Code 9 — ITU-R BT.2020 primaries (Red 0.708/0.292, Green
    /// 0.170/0.797, Blue 0.131/0.046, white D65).
    Bt2020 = 9,
    /// Code 11 — DCI-P3 primaries with the DCI white point
    /// (0.314/0.351).
    DciP3 = 11,
    /// Code 12 — DCI-P3 primaries with the D65 white point.
    P3D65 = 12,
}

impl ColorPrimaries {
    /// The on-the-wire u8 code for this variant.
    pub fn code(self) -> u8 {
        self as u8
    }
}

/// Map an RDD 36 §6.1.1 / Table 5 `color_primaries` u8 code to the
/// chromaticity-set name it identifies. Returns `None` for the
/// "unknown / unspecified" codes (0 and 2) and every reserved code in
/// `[3, 4, 7, 8, 10]` + `[13..=255]`.
///
/// The named codes are the nonreserved values of Table 5; the spec
/// explicitly notes that the named code numbers agree with ITU-T
/// H.273 Table 2.
pub fn color_primaries_from_code(code: u8) -> Option<ColorPrimaries> {
    match code {
        1 => Some(ColorPrimaries::Bt709),
        5 => Some(ColorPrimaries::Bt601_625),
        6 => Some(ColorPrimaries::Bt601_525),
        9 => Some(ColorPrimaries::Bt2020),
        11 => Some(ColorPrimaries::DciP3),
        12 => Some(ColorPrimaries::P3D65),
        // 0 + 2 = unknown/unspecified; 3, 4, 7, 8, 10, 13..=255 = reserved.
        _ => None,
    }
}

/// Named values of the RDD 36 §6.1.1 / Table 6 `matrix_coefficients`
/// field. The nonreserved codes agree with Table 4 of ITU-T H.273
/// (noted in §6.1.1). Each variant exposes the K_R / K_G / K_B luma
/// coefficients via [`MatrixCoefficients::luma_coefficients`] using
/// the spec's exact decimal values.
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum MatrixCoefficients {
    /// Code 1 — ITU-R BT.709 (K_R = 0.2126, K_G = 0.7152, K_B = 0.0722).
    Bt709 = 1,
    /// Code 6 — ITU-R BT.601 (K_R = 0.299, K_G = 0.587, K_B = 0.114).
    Bt601 = 6,
    /// Code 9 — ITU-R BT.2020 NCL (K_R = 0.2627, K_G = 0.6780, K_B = 0.0593).
    Bt2020Ncl = 9,
}

impl MatrixCoefficients {
    /// The on-the-wire u8 code for this variant.
    pub fn code(self) -> u8 {
        self as u8
    }

    /// `(K_R, K_G, K_B)` triple as written in Table 6, exactly: BT.709
    /// = (0.2126, 0.7152, 0.0722), BT.601 = (0.299, 0.587, 0.114),
    /// BT.2020 NCL = (0.2627, 0.6780, 0.0593). The §6.1.1 derivation
    /// formulas `E'_Y = K_R · E'_R + K_G · E'_G + K_B · E'_B`,
    /// `E'_Cb = (E'_B − E'_Y) / (2 · (1 − K_B))`,
    /// `E'_Cr = (E'_R − E'_Y) / (2 · (1 − K_R))` operate on these
    /// triples; the returned f64 values come straight from Table 6,
    /// no rounding.
    pub fn luma_coefficients(self) -> (f64, f64, f64) {
        match self {
            Self::Bt709 => (0.2126, 0.7152, 0.0722),
            Self::Bt601 => (0.299, 0.587, 0.114),
            Self::Bt2020Ncl => (0.2627, 0.6780, 0.0593),
        }
    }
}

/// Map an RDD 36 §6.1.1 / Table 6 `matrix_coefficients` u8 code to
/// the named matrix it identifies. Returns `None` for the unknown
/// codes (0 and 2) and the reserved codes `3..=5`, `7..=8`,
/// `10..=255`.
pub fn matrix_coefficients_from_code(code: u8) -> Option<MatrixCoefficients> {
    match code {
        1 => Some(MatrixCoefficients::Bt709),
        6 => Some(MatrixCoefficients::Bt601),
        9 => Some(MatrixCoefficients::Bt2020Ncl),
        // 0 + 2 = unknown/unspecified; 3..=5, 7..=8, 10..=255 = reserved.
        _ => None,
    }
}

/// Named values of the RDD 36 §6.1.1 `transfer_characteristic` field.
///
/// Unlike `color_primaries` (Table 5) and `matrix_coefficients`
/// (Table 6), the spec lists the named transfer functions in prose
/// rather than a numbered Table: §6.1.1 spells out each OETF formula
/// and ends with the note that the nonreserved code numbers agree
/// with Table 3 of ITU-T H.273. Three codes are named; everything
/// else is either "unknown/unspecified" (0 and 2) or reserved.
///
/// Each variant carries the same numeric code value as written on the
/// wire, so a downstream colour-management stage that wants to select
/// an OETF can match on the typed enum rather than re-deriving the
/// code-to-function mapping at every call site.
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum TransferCharacteristic {
    /// Code 1 — the OETF specified by ITU-R BT.601 / BT.709 / BT.2020:
    /// `V = α · L^0.45 − (α − 1)` for `β ≤ L ≤ 1` and `V = 4.5 · L`
    /// for `0 ≤ L ≤ β`, with `α = 1.099_296_826_809_44…` and
    /// `β = 0.018_053_968_510_807…`, per §6.1.1. Commonly referred
    /// to in industry usage as "BT.1886" (the studio reference
    /// display EOTF whose inverse coincides with this curve at the
    /// receiving end).
    Bt1886 = 1,
    /// Code 16 — the Inverse-EOTF formula in Section 5.3 of
    /// SMPTE ST 2084:2014 (commonly referred to as "PQ"):
    /// `V = ((c1 + c2 · L^m1) / (1 + c3 · L^m1))^m2` with
    /// `m1 = 0.25 · (2610 / 4096)`, `m2 = 128 · (2523 / 4096)`,
    /// `c1 = c3 − c2 + 1 = 3424 / 4096`, `c2 = 32 · (2413 / 4096)`,
    /// and `c3 = 32 · (2392 / 4096)`, where `L` is normalised so
    /// that `L = 1` corresponds to an absolute optical intensity of
    /// 10000 cd/m².
    St2084 = 16,
    /// Code 18 — the Reference OETF in Table 5 of ITU-R BT.2100-2
    /// (the Hybrid Log-Gamma curve, "HLG"):
    /// `V = sqrt(3 · L)` for `0 ≤ L ≤ 1/12` and
    /// `V = a · ln(12 · L − b) + c` for `1/12 ≤ L ≤ 1`, with
    /// `a = 0.178_832_77`, `b = 1 − 4a` (≈ 0.284_668_92), and
    /// `c = 1/2 − a · ln(4a)` (≈ 0.559_910_73). `L` has no
    /// prescribed normalisation under HLG.
    Hlg = 18,
}

impl TransferCharacteristic {
    /// The on-the-wire u8 code for this variant.
    pub fn code(self) -> u8 {
        self as u8
    }
}

/// Map an RDD 36 §6.1.1 `transfer_characteristic` u8 code to the
/// named OETF it identifies. Returns `None` for the
/// "unknown/unspecified" codes (0 and 2) and every reserved code in
/// the remainder of `[3..=255]` (specifically `3..=15`, `17`, and
/// `19..=255`).
///
/// The three named codes are the only ones §6.1.1 spells out in
/// prose; the spec explicitly notes that the named code numbers agree
/// with ITU-T H.273 Table 3 to avoid inconsistency with that
/// Recommendation.
pub fn transfer_characteristic_from_code(code: u8) -> Option<TransferCharacteristic> {
    match code {
        1 => Some(TransferCharacteristic::Bt1886),
        16 => Some(TransferCharacteristic::St2084),
        18 => Some(TransferCharacteristic::Hlg),
        // 0 + 2 = unknown/unspecified; 3..=15, 17, 19..=255 = reserved.
        _ => None,
    }
}

/// Named values of the RDD 36 §6.1.1 / Table 7 `alpha_channel_type`
/// field. Only three codes are defined; everything else is reserved.
///
/// Decoders use this to decide how to scale the entropy-decoded alpha
/// values into an output pixel sample (per §7.5.2) — the 8-bit and
/// 16-bit cases differ in run-length symbol width and per-pixel
/// storage, but both ride the §7.1.2 + Tables 12-14 entropy coder.
#[derive(Copy, Clone, Debug, Eq, PartialEq)]
pub enum AlphaChannelType {
    /// Code 0 — no encoded alpha plane is present.
    None = 0,
    /// Code 1 — 8-bit integral alpha (one byte/sample on output).
    Bits8 = 1,
    /// Code 2 — 16-bit integral alpha (two bytes/sample on output).
    Bits16 = 2,
}

impl AlphaChannelType {
    /// The on-the-wire u8 code for this variant.
    pub fn code(self) -> u8 {
        self as u8
    }

    /// `true` for the named "has alpha" variants (`Bits8` / `Bits16`).
    /// Mirrors the `alpha_channel_type != 0` guard the §5.3 slice
    /// parser uses to decide whether to read the trailing
    /// `scanned_alpha()` block. Provided so a caller can use the
    /// returned [`AlphaChannelType`] as a boolean predicate without
    /// switching on every variant.
    pub fn has_alpha(self) -> bool {
        !matches!(self, Self::None)
    }
}

/// Map an RDD 36 §6.1.1 / Table 7 `alpha_channel_type` u4 code to the
/// named variant it identifies. Returns `None` for the reserved codes
/// (`3..=15` per Table 7) — note that callers must mask the low nibble
/// out of the packed `reserved(4) + alpha_channel_type(4)` byte before
/// passing it in (`parse_frame_header` already does so).
pub fn alpha_channel_type_from_code(code: u8) -> Option<AlphaChannelType> {
    match code {
        0 => Some(AlphaChannelType::None),
        1 => Some(AlphaChannelType::Bits8),
        2 => Some(AlphaChannelType::Bits16),
        // 3..=15 = reserved per Table 7.
        _ => None,
    }
}

/// Write a complete frame header (frame_size + 'icpf' + frame_header())
/// with `alpha_channel_type` defaulting to 0 and all metadata fields
/// zeroed ("unknown"). Forwards to [`write_frame_with_meta`].
#[allow(clippy::too_many_arguments)]
pub fn write_frame(
    out: &mut Vec<u8>,
    total_frame_size: u32,
    width: u16,
    height: u16,
    chroma_format: ChromaFormat,
    interlace_mode: u8,
    luma_qmat: &[u8; 64],
    chroma_qmat: &[u8; 64],
    load_luma: bool,
    load_chroma: bool,
) {
    write_frame_with_meta(
        out,
        total_frame_size,
        width,
        height,
        chroma_format,
        interlace_mode,
        luma_qmat,
        chroma_qmat,
        load_luma,
        load_chroma,
        0,
        FrameMeta::default(),
    )
}

/// Write a complete frame header with an explicit `alpha_channel_type`
/// code, all other metadata zeroed. Kept for back-compat with callers
/// that don't carry frame_rate / aspect_ratio info; new code should use
/// [`write_frame_with_meta`].
#[allow(clippy::too_many_arguments)]
pub fn write_frame_with_alpha(
    out: &mut Vec<u8>,
    total_frame_size: u32,
    width: u16,
    height: u16,
    chroma_format: ChromaFormat,
    interlace_mode: u8,
    luma_qmat: &[u8; 64],
    chroma_qmat: &[u8; 64],
    load_luma: bool,
    load_chroma: bool,
    alpha_channel_type: u8,
) {
    write_frame_with_meta(
        out,
        total_frame_size,
        width,
        height,
        chroma_format,
        interlace_mode,
        luma_qmat,
        chroma_qmat,
        load_luma,
        load_chroma,
        alpha_channel_type,
        FrameMeta::default(),
    )
}

/// Write a complete frame header with explicit alpha + descriptive
/// metadata (aspect_ratio_information, frame_rate_code,
/// color_primaries, transfer_characteristic, matrix_coefficients).
///
/// `alpha_channel_type == 0` means no alpha plane; values 1 and 2
/// signal 8-bit and 16-bit alpha respectively (see RDD 36 §5.3.3).
/// When `alpha_channel_type != 0` the bitstream version is forced to 1
/// (alpha is a v1 feature per §6.4).
///
/// All `meta` fields are written verbatim into the corresponding header
/// bytes; only the low 4 bits of `aspect_ratio_information` and
/// `frame_rate_code` are honoured (those are u4 fields per §5.1.1).
#[allow(clippy::too_many_arguments)]
pub fn write_frame_with_meta(
    out: &mut Vec<u8>,
    total_frame_size: u32,
    width: u16,
    height: u16,
    chroma_format: ChromaFormat,
    interlace_mode: u8,
    luma_qmat: &[u8; 64],
    chroma_qmat: &[u8; 64],
    load_luma: bool,
    load_chroma: bool,
    alpha_channel_type: u8,
    meta: FrameMeta,
) {
    debug_assert!(alpha_channel_type <= 2);
    // RDD 36 §6.1.1 Table 2: interlace_mode == 3 is reserved. Refuse to
    // emit it from the writer side too (the decoder enforces the same
    // constraint when parsing).
    debug_assert!(
        interlace_mode <= 2,
        "prores: interlace_mode {interlace_mode} is reserved (RDD 36 §6.1.1 Table 2)"
    );
    // RDD 36 §6.1.1 (luma/chroma_quantization_matrix): "Each entry of
    // the matrix will be in the range 2, 3, …, 63." Refuse to emit an
    // out-of-range custom matrix.
    if load_luma {
        debug_assert!(
            luma_qmat.iter().all(|&w| (2..=63).contains(&w)),
            "prores: luma_qmat entry out of range 2..=63 (RDD 36 §6.1.1)"
        );
    }
    if load_chroma {
        debug_assert!(
            chroma_qmat.iter().all(|&w| (2..=63).contains(&w)),
            "prores: chroma_qmat entry out of range 2..=63 (RDD 36 §6.1.1)"
        );
    }
    // frame_size + magic
    out.extend_from_slice(&total_frame_size.to_be_bytes());
    out.extend_from_slice(FRAME_IDENTIFIER);
    // frame_header()
    let fh_size: u16 = 20 + if load_luma { 64 } else { 0 } + if load_chroma { 64 } else { 0 };
    out.extend_from_slice(&fh_size.to_be_bytes());
    out.push(0); // reserved
                 // RDD 36 §6.4: bitstream_version 0 requires chroma_format == 4:2:2
                 // AND alpha_channel_type == 0. Pick the lowest legal version so
                 // downstream legacy decoders accept the maximum number of streams
                 // (the spec also recommends this: "encoders should use the lowest
                 // bitstream version appropriate for the frame being encoded").
    let bitstream_version: u8 = if alpha_channel_type != 0 {
        1
    } else {
        match chroma_format {
            ChromaFormat::Y422 => 0,
            ChromaFormat::Y444 => 1,
        }
    };
    out.push(bitstream_version);
    out.extend_from_slice(ENCODER_IDENTIFIER);
    out.extend_from_slice(&width.to_be_bytes());
    out.extend_from_slice(&height.to_be_bytes());
    // chroma(2) + reserved(2) + interlace(2) + reserved(2)
    out.push((chroma_format.code() << 6) | ((interlace_mode & 0x3) << 2));
    // aspect_ratio_information(4) + frame_rate_code(4)
    out.push(((meta.aspect_ratio_information & 0x0F) << 4) | (meta.frame_rate_code & 0x0F));
    out.push(meta.color_primaries);
    out.push(meta.transfer_characteristic);
    out.push(meta.matrix_coefficients);
    out.push(alpha_channel_type & 0x0F); // reserved(4) + alpha_channel_type(4)
    out.push(0); // reserved (high 8 of the 14)
                 // last byte: 6 reserved bits + load_luma(1) + load_chroma(1)
    let lb = ((load_luma as u8) << 1) | (load_chroma as u8);
    out.push(lb);
    if load_luma {
        out.extend_from_slice(luma_qmat);
    }
    if load_chroma {
        out.extend_from_slice(chroma_qmat);
    }
}

/// Parsed picture_header().
#[derive(Clone, Debug)]
pub struct PictureHeader {
    pub picture_header_size: u8,
    pub picture_size: u32,
    pub deprecated_number_of_slices: u16,
    pub log2_desired_slice_size_in_mb: u8,
}

impl PictureHeader {
    /// Typed accessor for the RDD 36 §5.3 / §6.3 picture-header
    /// `log2_desired_slice_size_in_mb` field. Returns the actual
    /// macroblocks-per-slice value (`1`, `2`, `4`, or `8`) when the
    /// stream's u2 code is one of the four defined values
    /// (`0` → 1 MB, `1` → 2 MBs, `2` → 4 MBs, `3` → 8 MBs), and `None`
    /// for any out-of-range value a hand-built `PictureHeader` could
    /// carry (`4..=255`).
    ///
    /// The raw `log2_desired_slice_size_in_mb` field on this struct is
    /// the u2 code as it appeared on the wire (masked to the two-bit
    /// width by [`parse_picture_header`] — bits 4..=5 of byte 7 of the
    /// picture header). Call this accessor when a downstream pipeline
    /// stage wants the slice width directly (the same `1 / 2 / 4 / 8`
    /// surface that the encoder side exposes through
    /// [`crate::encoder::EncoderConfig::mbs_per_slice`] and the same
    /// per-row template [`compute_slice_sizes`] consumes as its
    /// `1 << log2_desired_slice_size_in_mb` seed value) rather than
    /// re-deriving the `1 << code` shift at every call site.
    ///
    /// The u8 return mirrors the `Option<u8>` shape that
    /// [`crate::encoder::EncoderConfig::mbs_per_slice`] already uses on
    /// the encoder side: the inner value is the slice width in
    /// macroblocks (always a power of two in `{1, 2, 4, 8}`), and the
    /// outer-Option `None` carries the "the field carried an
    /// out-of-range code that does not correspond to any defined slice
    /// width" signal — distinct from `Some(1)` (which is the smallest
    /// defined slice width and a legitimate wire value).
    ///
    /// Because `parse_picture_header` masks the field to two bits
    /// before storing it, every successfully-parsed `PictureHeader`
    /// satisfies `log2_desired_slice_size_in_mb in 0..=3` and the
    /// accessor returns `Some(_)` unconditionally; the `None` arm only
    /// fires for a hand-assembled struct (a downstream probe stage
    /// that bypassed `parse_picture_header` could carry an
    /// out-of-range value). The accessor is the natural mirror of the
    /// [`FrameHeader::interlace_kind`] / [`FrameHeader::alpha_kind`]
    /// surface: same outer-Option discriminant, same wire-faithful
    /// `None` for out-of-range codes, and the returned slice width is
    /// the exact same `1 / 2 / 4 / 8` surface
    /// [`crate::encoder::EncoderConfig::mbs_per_slice`] consumes —
    /// so a caller parsing a picture header can call
    /// `ph.mbs_per_slice()` and forward the result straight into an
    /// `EncoderConfig` for a transcode without an intermediate
    /// `1 << code` conversion.
    pub fn mbs_per_slice(&self) -> Option<u8> {
        match self.log2_desired_slice_size_in_mb {
            0 => Some(1),
            1 => Some(2),
            2 => Some(4),
            3 => Some(8),
            _ => None,
        }
    }

    /// The RDD 36 §6.3 picture_header `deprecated_number_of_slices`
    /// field as it appeared on the wire.
    ///
    /// Per the RDD 36 corpus notes this field is computed by some
    /// encoders but **ignored** by Apple's and other decoders, which
    /// recompute the slice count from the picture geometry instead (see
    /// [`slice_count`]). This crate's decode path likewise derives the
    /// slice count from `width_in_mb` / `log2_desired_slice_size_in_mb`
    /// / `height_in_mb` and never trusts this field; the accessor is
    /// provided for callers that want to *inspect* the value a stream
    /// declared — e.g. to flag an encoder whose declared count
    /// disagrees with the geometry — without reading the raw struct
    /// field. Pair it with [`slice_count`] to cross-check: a
    /// well-formed stream satisfies `ph.deprecated_slice_count() as
    /// usize == slice_count(width_in_mb, ph.log2_desired_slice_size_in_mb,
    /// height_in_mb)`, but a stream that violates this is still decoded
    /// correctly because the decoder uses the geometry, not this field.
    pub fn deprecated_slice_count(&self) -> u16 {
        self.deprecated_number_of_slices
    }
}

/// Parse a picture_header(). Returns the header and a slice that begins
/// at the slice_table().
pub fn parse_picture_header(data: &[u8]) -> Result<(PictureHeader, &[u8])> {
    if data.len() < 8 {
        return Err(Error::invalid("prores: picture header truncated"));
    }
    // byte 0: picture_header_size(5) + reserved(3)
    let b0 = data[0];
    let picture_header_size = (b0 >> 3) & 0x1F;
    if picture_header_size < 8 {
        return Err(Error::invalid("prores: picture_header_size < 8"));
    }
    let picture_size = u32::from_be_bytes(data[1..5].try_into().unwrap());
    let deprecated_number_of_slices = u16::from_be_bytes(data[5..7].try_into().unwrap());
    // byte 7: reserved(2) + log2_desired_slice_size_in_mb(2) + reserved(4)
    let b7 = data[7];
    let log2_desired_slice_size_in_mb = (b7 >> 4) & 0x3;
    if data.len() < picture_header_size as usize {
        return Err(Error::invalid("prores: picture header overruns buffer"));
    }
    Ok((
        PictureHeader {
            picture_header_size,
            picture_size,
            deprecated_number_of_slices,
            log2_desired_slice_size_in_mb,
        },
        &data[picture_header_size as usize..],
    ))
}

pub fn write_picture_header(
    out: &mut Vec<u8>,
    picture_size: u32,
    deprecated_number_of_slices: u16,
    log2_desired_slice_size_in_mb: u8,
) {
    let picture_header_size: u8 = 8;
    out.push(picture_header_size << 3);
    out.extend_from_slice(&picture_size.to_be_bytes());
    out.extend_from_slice(&deprecated_number_of_slices.to_be_bytes());
    // reserved(2) + log2(2) + reserved(4)
    out.push((log2_desired_slice_size_in_mb & 0x3) << 4);
}

/// Parsed slice_header().
#[derive(Clone, Debug)]
pub struct SliceHeader {
    pub slice_header_size: u8,
    pub quantization_index: u8,
    pub coded_size_of_y_data: u16,
    pub coded_size_of_cb_data: u16,
    /// Present only when `alpha_channel_type != 0`. When absent the
    /// caller derives it from `coded_size_of_slice - header - y - cb`.
    pub coded_size_of_cr_data: Option<u16>,
}

impl SliceHeader {
    /// Typed accessor for the quantisation scale factor `qScale` this
    /// slice's `quantization_index` selects, per RDD 36 §7.3 Table 15.
    ///
    /// The raw `quantization_index` field on this struct is the u8 code
    /// as it appeared on the wire. §6.3.1 restricts it to `1..=224` (all
    /// other values are reserved), and §7.3 derives `qScale` from it by
    /// the two-segment piecewise map of Table 15: `qScale =
    /// quantization_index` for `1 ≤ quantization_index ≤ 128`, and
    /// `qScale = 128 + 4 * (quantization_index − 128)` for
    /// `129 ≤ quantization_index ≤ 224` (so the index range `1..=224`
    /// spans `qScale` values `1..=512`). `qScale` is the per-slice
    /// overall quantisation level that §7.3's dequantisation formula
    /// `F[v][u] = (QF[v][u] * W[v][u] * qScale) ÷ 8` scales every
    /// coefficient by.
    ///
    /// Returns `Some(qScale)` for the defined index range and `None`
    /// for the reserved range (`0` and `225..=255`) a hand-assembled
    /// `SliceHeader` could carry — mirroring the outer-Option
    /// discriminant of [`PictureHeader::mbs_per_slice`] /
    /// [`FrameHeader::interlace_kind`]: `None` carries the "the field
    /// held a reserved code with no defined `qScale`" signal, distinct
    /// from `Some(1)` (the finest defined scale and a legitimate wire
    /// value). Because [`parse_slice_header`] already rejects any
    /// out-of-range `quantization_index` at parse time, every
    /// successfully-parsed `SliceHeader` returns `Some(_)`; the `None`
    /// arm only fires for a struct built by hand outside the parser.
    ///
    /// This folds the Table 15 derivation that the dequantisation path
    /// ([`crate::quant::qscale`], used by the decoder per block) seeds
    /// with into a single method on the parsed header, so a
    /// stream-inspection or rate-analysis stage reading a parsed slice
    /// header can read the effective scale directly off
    /// `sh.qscale()` without re-deriving the piecewise map — the
    /// natural mirror of [`PictureHeader::mbs_per_slice`] on the slice
    /// header.
    pub fn qscale(&self) -> Option<i32> {
        if (1..=224).contains(&self.quantization_index) {
            Some(crate::quant::qscale(self.quantization_index))
        } else {
            None
        }
    }
}

/// Parse one slice_header(). `has_alpha` controls whether the
/// `coded_size_of_cr_data` field is present.
pub fn parse_slice_header(data: &[u8], has_alpha: bool) -> Result<(SliceHeader, &[u8])> {
    let min = if has_alpha { 8 } else { 6 };
    if data.len() < min {
        return Err(Error::invalid("prores: slice header truncated"));
    }
    let b0 = data[0];
    let slice_header_size = (b0 >> 3) & 0x1F;
    let quantization_index = data[1];
    if !(1..=224).contains(&quantization_index) {
        return Err(Error::invalid(
            "prores: quantization_index out of range (1..=224)",
        ));
    }
    let coded_size_of_y_data = u16::from_be_bytes(data[2..4].try_into().unwrap());
    let coded_size_of_cb_data = u16::from_be_bytes(data[4..6].try_into().unwrap());
    let coded_size_of_cr_data = if has_alpha {
        Some(u16::from_be_bytes(data[6..8].try_into().unwrap()))
    } else {
        None
    };
    let consumed = slice_header_size as usize;
    if consumed < min {
        return Err(Error::invalid("prores: slice_header_size < required"));
    }
    if data.len() < consumed {
        return Err(Error::invalid("prores: slice header overruns buffer"));
    }
    Ok((
        SliceHeader {
            slice_header_size,
            quantization_index,
            coded_size_of_y_data,
            coded_size_of_cb_data,
            coded_size_of_cr_data,
        },
        &data[consumed..],
    ))
}

pub fn write_slice_header(
    out: &mut Vec<u8>,
    quantization_index: u8,
    coded_size_of_y_data: u16,
    coded_size_of_cb_data: u16,
    coded_size_of_cr_data: Option<u16>,
) {
    let slice_header_size: u8 = if coded_size_of_cr_data.is_some() {
        8
    } else {
        6
    };
    out.push(slice_header_size << 3);
    out.push(quantization_index);
    out.extend_from_slice(&coded_size_of_y_data.to_be_bytes());
    out.extend_from_slice(&coded_size_of_cb_data.to_be_bytes());
    if let Some(cr) = coded_size_of_cr_data {
        out.extend_from_slice(&cr.to_be_bytes());
    }
}

/// Compute the slice_size_in_mb array per §6.2 (the same array applies
/// to every macroblock row). For the typical case `width=128` and
/// `log2_desired_slice_size_in_mb=3`, this returns `[8]` (1 slice per row,
/// 8 MBs each). Returns `(slice_sizes, slice_count)`.
pub fn compute_slice_sizes(width_in_mb: usize, log2_desired_slice_size_in_mb: u8) -> Vec<usize> {
    let mut sizes = Vec::new();
    let mut slice_size = 1usize << log2_desired_slice_size_in_mb;
    let mut remaining = width_in_mb;
    loop {
        while remaining >= slice_size {
            sizes.push(slice_size);
            remaining -= slice_size;
        }
        slice_size /= 2;
        if remaining == 0 {
            break;
        }
        if slice_size == 0 {
            // Defensive — should not occur for width_in_mb > 0.
            break;
        }
    }
    sizes
}

/// Total number of slices a single picture is partitioned into, per
/// RDD 36 §7 (the slice partitioning the §6.3 `slice_table()` indexes).
///
/// A picture is `height_in_mb` macroblock rows tall, and every row is
/// split into the same per-row template of widths produced by
/// [`compute_slice_sizes`] (one slice per template entry). The total is
/// therefore `compute_slice_sizes(width_in_mb, log2).len() * height_in_mb`.
///
/// This is the value the §6.3 picture_header's
/// `deprecated_number_of_slices` field nominally carries, but RDD 36
/// directs decoders to recompute it from the geometry rather than trust
/// the wire field (which Apple's and other decoders ignore). Expose the
/// recomputation here so a caller can cross-check
/// [`PictureHeader::deprecated_slice_count`] against the geometry
/// without re-deriving the per-row split, and so the decode path and any
/// external probe stage share one source of truth for the count.
///
/// The worked geometries in the RDD 36 corpus notes hold:
/// `1920×1080` progressive (`width_in_mb = 120`, `height_in_mb = 68`,
/// 8-MB slices) → `15 * 68 = 1020`; `1280×720` (`80 × 45`) →
/// `10 * 45 = 450`; `320×240` (`20 × 15`) → `3 * 15 = 45` (each row
/// ends in an `8 + 8 + 4` tail per [`compute_slice_sizes`]); and each
/// field of an interlaced `1920×1080` picture (`height_in_mb =
/// (540 + 15) >> 4 = 34`) → `15 * 34 = 510`.
pub fn slice_count(
    width_in_mb: usize,
    log2_desired_slice_size_in_mb: u8,
    height_in_mb: usize,
) -> usize {
    compute_slice_sizes(width_in_mb, log2_desired_slice_size_in_mb).len() * height_in_mb
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_frame_rejects_prores_raw_marker() {
        // frame_size=16, in-stream marker 'aprh' (ProRes RAW), padding.
        let mut buf = Vec::new();
        buf.extend_from_slice(&16u32.to_be_bytes());
        buf.extend_from_slice(PRORES_RAW_FRAME_IDENTIFIER);
        buf.extend_from_slice(&[0u8; 8]);
        let err = parse_frame(&buf).expect_err("ProRes RAW must be rejected");
        assert!(
            err.to_string().contains("ProRes RAW"),
            "error should name ProRes RAW, got: {err}"
        );
    }

    #[test]
    fn parse_frame_generic_magic_mismatch_is_not_reported_as_raw() {
        let mut buf = Vec::new();
        buf.extend_from_slice(&16u32.to_be_bytes());
        buf.extend_from_slice(b"junk");
        buf.extend_from_slice(&[0u8; 8]);
        let err = parse_frame(&buf).expect_err("non-ProRes bytes must be rejected");
        let msg = err.to_string();
        assert!(msg.contains("magic mismatch"), "got: {msg}");
        assert!(!msg.contains("ProRes RAW"), "got: {msg}");
    }

    #[test]
    fn frame_roundtrip_422() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let mut buf = Vec::new();
        // size unknown; we'll write 0 and patch later.
        write_frame(
            &mut buf,
            0,
            128,
            128,
            ChromaFormat::Y422,
            0,
            &luma,
            &chroma,
            false,
            false,
        );
        // Patch frame_size.
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(fh.width, 128);
        assert_eq!(fh.height, 128);
        assert_eq!(fh.chroma_format, ChromaFormat::Y422);
        assert_eq!(fh.bitstream_version, 0);
        assert_eq!(fh.luma_qmat, [4u8; 64]);
        assert_eq!(fh.chroma_qmat, [4u8; 64]);
    }

    #[test]
    fn frame_roundtrip_444_with_qmats() {
        // RDD 36 §6.1.1: every entry must be in 2..=63. Pick two
        // distinct patterns that both span most of the legal range so
        // the roundtrip exercises non-default matrices.
        let mut luma = [0u8; 64];
        let mut chroma = [0u8; 64];
        for i in 0..64 {
            luma[i] = 2 + (i as u8 % 62); // 2..=63
            chroma[i] = 2 + ((i as u8 + 31) % 62); // shifted permutation
        }
        let mut buf = Vec::new();
        write_frame(
            &mut buf,
            0,
            64,
            64,
            ChromaFormat::Y444,
            0,
            &luma,
            &chroma,
            true,
            true,
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(fh.chroma_format, ChromaFormat::Y444);
        assert_eq!(fh.bitstream_version, 1);
        assert_eq!(fh.luma_qmat, luma);
        assert_eq!(fh.chroma_qmat, chroma);
    }

    #[test]
    fn picture_header_roundtrip() {
        let mut buf = Vec::new();
        write_picture_header(&mut buf, 1234, 12, 3);
        let (ph, _) = parse_picture_header(&buf).unwrap();
        assert_eq!(ph.picture_header_size, 8);
        assert_eq!(ph.picture_size, 1234);
        assert_eq!(ph.deprecated_number_of_slices, 12);
        assert_eq!(ph.log2_desired_slice_size_in_mb, 3);
    }

    #[test]
    fn slice_header_roundtrip_no_alpha() {
        let mut buf = Vec::new();
        write_slice_header(&mut buf, 4, 100, 50, None);
        let (sh, _) = parse_slice_header(&buf, false).unwrap();
        assert_eq!(sh.slice_header_size, 6);
        assert_eq!(sh.quantization_index, 4);
        assert_eq!(sh.coded_size_of_y_data, 100);
        assert_eq!(sh.coded_size_of_cb_data, 50);
        assert!(sh.coded_size_of_cr_data.is_none());
    }

    #[test]
    fn slice_header_qscale_accessor_matches_table15() {
        // RDD 36 §7.3 Table 15: the two-segment piecewise map, folded
        // onto a parsed slice header. Identity segment 1..=128, then
        // 128 + 4 * (i - 128) up to 224 (so 1..=224 spans qScale
        // 1..=512). A parsed header always has an in-range index, so
        // qscale() is always Some(_).
        for (qi, want) in [
            (1u8, 1i32),
            (2, 2),
            (128, 128),
            (129, 132),
            (130, 136),
            (223, 508),
            (224, 512),
        ] {
            let mut buf = Vec::new();
            write_slice_header(&mut buf, qi, 100, 50, None);
            let (sh, _) = parse_slice_header(&buf, false).unwrap();
            assert_eq!(sh.quantization_index, qi);
            assert_eq!(sh.qscale(), Some(want), "qi={qi}");
            // The accessor folds the same Table 15 map the decode-side
            // dequantisation seeds with.
            assert_eq!(sh.qscale(), Some(crate::quant::qscale(qi)));
        }
    }

    #[test]
    fn slice_header_qscale_none_for_reserved_codes() {
        // A hand-assembled SliceHeader can carry a reserved
        // quantization_index (0 or 225..=255) that parse_slice_header
        // would reject; the accessor surfaces that as None (the
        // wire-faithful "no defined qScale" signal), distinct from
        // Some(1) at the finest defined scale.
        for qi in [0u8, 225, 255] {
            let sh = SliceHeader {
                slice_header_size: 6,
                quantization_index: qi,
                coded_size_of_y_data: 0,
                coded_size_of_cb_data: 0,
                coded_size_of_cr_data: None,
            };
            assert_eq!(sh.qscale(), None, "qi={qi}");
        }
        let sh = SliceHeader {
            slice_header_size: 6,
            quantization_index: 1,
            coded_size_of_y_data: 0,
            coded_size_of_cb_data: 0,
            coded_size_of_cr_data: None,
        };
        assert_eq!(sh.qscale(), Some(1));
    }

    #[test]
    fn compute_slice_sizes_examples() {
        // RDD 36 example: width=720→ 45 MBs, slice size 8 → [8,8,8,8,8,4,1].
        assert_eq!(compute_slice_sizes(45, 3), vec![8, 8, 8, 8, 8, 4, 1]);
        // 8 MBs flat
        assert_eq!(compute_slice_sizes(8, 3), vec![8]);
        // 1 MB
        assert_eq!(compute_slice_sizes(1, 3), vec![1]);
        // log2=0 → all 1s
        assert_eq!(compute_slice_sizes(5, 0), vec![1, 1, 1, 1, 1]);
    }

    #[test]
    fn slice_count_matches_rdd36_corpus_geometries() {
        // The worked per-picture slice counts from the RDD 36 fixture
        // corpus notes. width_in_mb = ceil(w/16), height_in_mb =
        // ceil(picture_height/16); 8-MB slices (log2 = 3) everywhere.

        // 1920×1080 progressive: 120 × 68 MBs → 15 slices/row × 68 = 1020.
        assert_eq!(slice_count(120, 3, 68), 1020);
        // 1280×720: 80 × 45 → 10 × 45 = 450.
        assert_eq!(slice_count(80, 3, 45), 450);
        // 320×240: 20 × 15 → each row is an 8+8+4 tail (3 slices) × 15 = 45.
        assert_eq!(compute_slice_sizes(20, 3), vec![8, 8, 4]);
        assert_eq!(slice_count(20, 3, 15), 45);
        // Interlaced 1920×1080: each field picture is 540px tall →
        // height_in_mb = ceil(540/16) = 34; 15 × 34 = 510 per field.
        assert_eq!(slice_count(120, 3, 34), 510);
        // The product is exactly compute_slice_sizes(...).len() × rows.
        assert_eq!(
            slice_count(120, 3, 68),
            compute_slice_sizes(120, 3).len() * 68
        );
    }

    /// Minimal `FrameHeader` for geometry tests — only the fields
    /// [`FrameHeader::picture_geometry`] consults (`width`, `height`,
    /// `interlace_mode`) matter; the rest take inert defaults.
    fn geom_header(width: u16, height: u16, interlace_mode: u8) -> FrameHeader {
        FrameHeader {
            frame_size: 0,
            frame_header_size: 20,
            bitstream_version: if interlace_mode == 0 { 0 } else { 1 },
            encoder_identifier: *b"oxav",
            width,
            height,
            chroma_format: ChromaFormat::Y422,
            interlace_mode,
            aspect_ratio_information: 0,
            frame_rate_code: 0,
            color_primaries: 0,
            transfer_characteristic: 0,
            matrix_coefficients: 0,
            alpha_channel_type: 0,
            load_luma_quantization_matrix: false,
            load_chroma_quantization_matrix: false,
            luma_qmat: [4u8; 64],
            chroma_qmat: [4u8; 64],
        }
    }

    #[test]
    fn picture_geometry_progressive_corpus() {
        // 1920×1080 progressive: 120 × 68 MBs. Width is flush to 16 but
        // 1080 is not (67.5 → 68 MB rows = 1088 coded), so the bottom 8
        // rows are §6.2 padding to be cropped on decode. One picture,
        // 1020 8-MB slices.
        let g = geom_header(1920, 1080, 0).picture_geometry();
        assert_eq!(g.width_in_mb, 120);
        assert_eq!(g.height_in_mb, 68);
        assert_eq!(g.picture_vertical_size, 1080);
        assert_eq!(g.second_picture_vertical_size, None);
        assert_eq!(g.picture_count, 1);
        assert_eq!(g.right_crop, 0);
        assert_eq!(g.bottom_crop, 68 * 16 - 1080); // 1088 − 1080 = 8
        assert_eq!(g.slices_per_mb_row(3), 15);
        assert_eq!(g.slice_count(3), 1020);

        // 1280×720: 80 × 45, also flush to 16.
        let g = geom_header(1280, 720, 0).picture_geometry();
        assert_eq!((g.width_in_mb, g.height_in_mb), (80, 45));
        assert_eq!((g.right_crop, g.bottom_crop), (0, 0));
        assert_eq!(g.slice_count(3), 450);

        // 320×240: 20 × 15, each row an 8+8+4 tail (3 slices/row).
        let g = geom_header(320, 240, 0).picture_geometry();
        assert_eq!(g.slices_per_mb_row(3), 3);
        assert_eq!(g.slice_count(3), 45);
    }

    #[test]
    fn picture_geometry_interlaced_field_split() {
        // 1920×1080 TFF: each field is 540 luma rows → height_in_mb =
        // ceil(540/16) = 34; bottom crop = 34*16 − 540 = 4; 510 slices.
        let g = geom_header(1920, 1080, 1).picture_geometry();
        assert_eq!(g.width_in_mb, 120);
        assert_eq!(g.height_in_mb, 34);
        assert_eq!(g.picture_vertical_size, 540); // top field (= bottom here, even height)
        assert_eq!(g.second_picture_vertical_size, Some(540));
        assert_eq!(g.picture_count, 2);
        assert_eq!(g.right_crop, 0);
        assert_eq!(g.bottom_crop, 34 * 16 - 540);
        assert_eq!(g.slice_count(3), 510);

        // BFF on the same frame: leading picture is the bottom field, but
        // for an even height both fields are 540 so the split is symmetric.
        let g = geom_header(1920, 1080, 2).picture_geometry();
        assert_eq!(g.picture_vertical_size, 540);
        assert_eq!(g.second_picture_vertical_size, Some(540));
    }

    #[test]
    fn picture_geometry_odd_height_puts_extra_row_in_top_field() {
        // §6.2: topFieldVerticalSize = (vertical_size + 1) / 2,
        // bottomFieldVerticalSize = vertical_size / 2. For an odd height
        // (e.g. 487) the top field gets the extra row (244 vs 243).
        let g = geom_header(640, 487, 1).picture_geometry(); // TFF → top leads
        assert_eq!(g.picture_vertical_size, 244); // (487 + 1) / 2
        assert_eq!(g.second_picture_vertical_size, Some(243)); // 487 / 2

        let g = geom_header(640, 487, 2).picture_geometry(); // BFF → bottom leads
        assert_eq!(g.picture_vertical_size, 243); // 487 / 2
        assert_eq!(g.second_picture_vertical_size, Some(244)); // (487 + 1) / 2
    }

    #[test]
    fn picture_geometry_crops_non_multiple_of_16() {
        // 1920×1088 is the classic "1080 padded to 1088" capture height,
        // but a genuinely odd progressive frame — say 1366×766 — exercises
        // both right and bottom crop. width_in_mb = ceil(1366/16) = 86
        // (→ 1376 coded, crop 10); height_in_mb = ceil(766/16) = 48
        // (→ 768 coded, crop 2).
        let g = geom_header(1366, 766, 0).picture_geometry();
        assert_eq!(g.width_in_mb, 86);
        assert_eq!(g.height_in_mb, 48);
        assert_eq!(g.right_crop, 86 * 16 - 1366);
        assert_eq!(g.bottom_crop, 48 * 16 - 766);
    }

    #[test]
    fn deprecated_slice_count_accessor_surfaces_wire_field() {
        // Round-trip a picture header carrying a declared slice count
        // and confirm the accessor surfaces the raw wire value verbatim.
        let mut buf = Vec::new();
        write_picture_header(
            &mut buf, /* picture_size */ 64, /* slices */ 1020, /* log2 */ 3,
        );
        let (ph, _) = parse_picture_header(&buf).unwrap();
        assert_eq!(ph.deprecated_slice_count(), 1020);
        assert_eq!(ph.deprecated_number_of_slices, 1020);
        // For a well-formed 1920×1080 progressive picture the declared
        // count agrees with the geometry-derived count.
        assert_eq!(
            ph.deprecated_slice_count() as usize,
            slice_count(120, ph.log2_desired_slice_size_in_mb, 68)
        );

        // A stream whose declared count *disagrees* with the geometry is
        // still parsed (the decoder recomputes from geometry and ignores
        // this field) — the accessor faithfully reports the bogus value.
        let mut bogus = Vec::new();
        write_picture_header(&mut bogus, 64, 7, 3);
        let (ph2, _) = parse_picture_header(&bogus).unwrap();
        assert_eq!(ph2.deprecated_slice_count(), 7);
        assert_ne!(
            ph2.deprecated_slice_count() as usize,
            slice_count(120, ph2.log2_desired_slice_size_in_mb, 68)
        );
    }

    #[test]
    fn frame_rate_code_named_rates_match_spec_table_4() {
        use oxideav_core::Rational;
        // RDD 36 §6.2 / Table 4 — every named rate must map to its code.
        let cases: &[(Rational, u8)] = &[
            (Rational::new(24_000, 1001), 1),
            (Rational::new(24, 1), 2),
            (Rational::new(25, 1), 3),
            (Rational::new(30_000, 1001), 4),
            (Rational::new(30, 1), 5),
            (Rational::new(50, 1), 6),
            (Rational::new(60_000, 1001), 7),
            (Rational::new(60, 1), 8),
            (Rational::new(100, 1), 9),
            (Rational::new(120_000, 1001), 10),
            (Rational::new(120, 1), 11),
        ];
        for &(r, expected) in cases {
            let got = frame_rate_code_from_rational(r);
            assert_eq!(
                got, expected,
                "rate {}/{} must map to {expected}",
                r.num, r.den
            );
        }
    }

    #[test]
    fn frame_rate_code_unnormalised_fractions_match() {
        use oxideav_core::Rational;
        // 60/2 == 30 → code 5; 50000/1000 == 50 → code 6.
        assert_eq!(frame_rate_code_from_rational(Rational::new(60, 2)), 5);
        assert_eq!(
            frame_rate_code_from_rational(Rational::new(50_000, 1000)),
            6
        );
        // Doubling the 1.001 fraction: 48000/1001 != 24000/1001 (not the same rate).
        assert_eq!(
            frame_rate_code_from_rational(Rational::new(48_000, 1001)),
            0
        );
    }

    #[test]
    fn frame_rate_code_unknown_rates_map_to_zero() {
        use oxideav_core::Rational;
        // 48 fps, 90 fps, 0/0, negative — all "unknown".
        assert_eq!(frame_rate_code_from_rational(Rational::new(48, 1)), 0);
        assert_eq!(frame_rate_code_from_rational(Rational::new(90, 1)), 0);
        assert_eq!(frame_rate_code_from_rational(Rational::new(0, 0)), 0);
        assert_eq!(frame_rate_code_from_rational(Rational::new(-30, 1)), 0);
    }

    #[test]
    fn frame_meta_is_unknown_helpers() {
        assert!(FrameMeta::default().is_unknown());
        assert!(FrameMeta::unknown().is_unknown());
        let m = FrameMeta {
            frame_rate_code: 5,
            ..FrameMeta::default()
        };
        assert!(!m.is_unknown());
    }

    #[test]
    fn frame_with_meta_roundtrips_all_fields() {
        // Pack a non-trivial FrameMeta into a frame header and verify
        // the parser pulls every byte back out unchanged.
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let meta = FrameMeta {
            aspect_ratio_information: 3, // 16:9 per Table 3
            frame_rate_code: 4,          // 30/1.001 per Table 4
            color_primaries: 9,          // BT.2020 (H.273)
            transfer_characteristic: 16, // SMPTE ST 2084 (H.273)
            matrix_coefficients: 9,      // BT.2020 non-constant luminance
        };
        let mut buf = Vec::new();
        write_frame_with_meta(
            &mut buf,
            0,
            1920,
            1080,
            ChromaFormat::Y422,
            0,
            &luma,
            &chroma,
            false,
            false,
            0,
            meta,
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(fh.aspect_ratio_information, meta.aspect_ratio_information);
        assert_eq!(fh.frame_rate_code, meta.frame_rate_code);
        assert_eq!(fh.color_primaries, meta.color_primaries);
        assert_eq!(fh.transfer_characteristic, meta.transfer_characteristic);
        assert_eq!(fh.matrix_coefficients, meta.matrix_coefficients);
    }

    #[test]
    fn rational_from_frame_rate_code_table_4_round_trip() {
        use oxideav_core::Rational;
        // Every Table 4 named code must round-trip through both halves of
        // the symmetric pair. The fractions are returned in their exact
        // spec form (e.g. 30000/1001, not 30000/1001 reduced), so a
        // structural equality survives the forward+reverse pass.
        let cases: &[(u8, Rational)] = &[
            (1, Rational::new(24_000, 1001)),
            (2, Rational::new(24, 1)),
            (3, Rational::new(25, 1)),
            (4, Rational::new(30_000, 1001)),
            (5, Rational::new(30, 1)),
            (6, Rational::new(50, 1)),
            (7, Rational::new(60_000, 1001)),
            (8, Rational::new(60, 1)),
            (9, Rational::new(100, 1)),
            (10, Rational::new(120_000, 1001)),
            (11, Rational::new(120, 1)),
        ];
        for &(code, expected) in cases {
            let got = rational_from_frame_rate_code(code).unwrap_or_else(|| {
                panic!("code {code} must resolve to Some(_)");
            });
            assert_eq!(
                got, expected,
                "code {code} must map to {}/{} verbatim",
                expected.num, expected.den
            );
            // Symmetric inverse: forward of the reverse must yield the
            // same code (defends against a typo that splits the two
            // tables — the SHA-only-flipper of metadata work).
            assert_eq!(
                frame_rate_code_from_rational(got),
                code,
                "code {code} must symmetrically reverse",
            );
        }
    }

    #[test]
    fn rational_from_frame_rate_code_unknown_and_reserved_are_none() {
        // Code 0 is "unknown/unspecified" per Table 4 — distinct from any
        // named rate, so it must yield None (callers distinguish
        // "missing" from "explicit 24 fps" by the Option discriminant).
        assert!(rational_from_frame_rate_code(0).is_none());
        // Codes 12..=15 are reserved per Table 4 — also None.
        for reserved in 12u8..=15 {
            assert!(
                rational_from_frame_rate_code(reserved).is_none(),
                "code {reserved} is reserved and must be None"
            );
        }
        // The function takes a u8 but documents the field as u4 — anything
        // above 15 is an out-of-domain bit-pattern, also None.
        assert!(rational_from_frame_rate_code(16).is_none());
        assert!(rational_from_frame_rate_code(255).is_none());
    }

    #[test]
    fn aspect_ratio_from_code_table_3_named_values() {
        use oxideav_core::Rational;
        // RDD 36 §6.2 / Table 3.
        assert_eq!(aspect_ratio_from_code(1), Some(Rational::new(1, 1)));
        assert_eq!(aspect_ratio_from_code(2), Some(Rational::new(4, 3)));
        assert_eq!(aspect_ratio_from_code(3), Some(Rational::new(16, 9)));
    }

    #[test]
    fn aspect_ratio_from_code_unknown_and_reserved_are_none() {
        // Code 0 = unknown, codes 4..=15 = reserved per Table 3.
        assert!(aspect_ratio_from_code(0).is_none());
        for reserved in 4u8..=15 {
            assert!(
                aspect_ratio_from_code(reserved).is_none(),
                "code {reserved} is reserved and must be None"
            );
        }
        // Out-of-domain (above the u4 range) is also None.
        assert!(aspect_ratio_from_code(16).is_none());
        assert!(aspect_ratio_from_code(255).is_none());
    }

    #[test]
    fn parsed_frame_header_meta_decodes_to_rational() {
        use oxideav_core::Rational;
        // End-to-end: write a frame header with a known FrameMeta
        // (16:9, 60 fps), parse it back, and convert the parsed u4
        // codes into Rationals through the new helpers. This is the
        // canonical downstream-pipeline usage: a decoder reads a packet
        // and wants to forward `frame_rate` along an oxideav_core graph,
        // and aspect_ratio for a UI overlay.
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let meta = FrameMeta {
            aspect_ratio_information: 3, // 16:9
            frame_rate_code: 8,          // 60 fps
            color_primaries: 1,
            transfer_characteristic: 1,
            matrix_coefficients: 1,
        };
        let mut buf = Vec::new();
        write_frame_with_meta(
            &mut buf,
            0,
            1920,
            1080,
            ChromaFormat::Y422,
            0,
            &luma,
            &chroma,
            false,
            false,
            0,
            meta,
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(
            rational_from_frame_rate_code(fh.frame_rate_code),
            Some(Rational::new(60, 1)),
        );
        assert_eq!(
            aspect_ratio_from_code(fh.aspect_ratio_information),
            Some(Rational::new(16, 9)),
        );
    }

    #[test]
    fn parsed_frame_header_unknown_meta_is_none_through_helpers() {
        // Symmetric anti-coverage: a packet emitted with zeroed
        // FrameMeta (the legacy back-compat path) must surface as
        // None through both helpers — distinguishing a stream that
        // says "rate unknown" from one that says "rate is 24 fps".
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let mut buf = Vec::new();
        write_frame_with_meta(
            &mut buf,
            0,
            64,
            48,
            ChromaFormat::Y422,
            0,
            &luma,
            &chroma,
            false,
            false,
            0,
            FrameMeta::default(),
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(rational_from_frame_rate_code(fh.frame_rate_code), None);
        assert_eq!(aspect_ratio_from_code(fh.aspect_ratio_information), None);
    }

    #[test]
    fn frame_with_alpha_back_compat_zeros_meta() {
        // The legacy `write_frame_with_alpha` shim must leave every
        // metadata field at 0 (preserving the byte-exact behaviour the
        // pre-FrameMeta callers depended on).
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let mut buf = Vec::new();
        write_frame_with_alpha(
            &mut buf,
            0,
            64,
            64,
            ChromaFormat::Y444,
            0,
            &luma,
            &chroma,
            false,
            false,
            2, // 16-bit alpha
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(fh.aspect_ratio_information, 0);
        assert_eq!(fh.frame_rate_code, 0);
        assert_eq!(fh.color_primaries, 0);
        assert_eq!(fh.transfer_characteristic, 0);
        assert_eq!(fh.matrix_coefficients, 0);
        assert_eq!(fh.alpha_channel_type, 2);
    }

    #[test]
    fn color_primaries_from_code_named_codes_table_5() {
        // RDD 36 §6.1.1 Table 5: nonreserved codes 1, 5, 6, 9, 11, 12.
        assert_eq!(color_primaries_from_code(1), Some(ColorPrimaries::Bt709));
        assert_eq!(
            color_primaries_from_code(5),
            Some(ColorPrimaries::Bt601_625)
        );
        assert_eq!(
            color_primaries_from_code(6),
            Some(ColorPrimaries::Bt601_525)
        );
        assert_eq!(color_primaries_from_code(9), Some(ColorPrimaries::Bt2020));
        assert_eq!(color_primaries_from_code(11), Some(ColorPrimaries::DciP3));
        assert_eq!(color_primaries_from_code(12), Some(ColorPrimaries::P3D65));
    }

    #[test]
    fn color_primaries_from_code_unknown_and_reserved_are_none() {
        // Codes 0 and 2 are "Unknown/unspecified" per Table 5; codes 3,
        // 4, 7, 8, 10, 13..=255 are reserved.
        assert!(color_primaries_from_code(0).is_none());
        assert!(color_primaries_from_code(2).is_none());
        for reserved in [3u8, 4, 7, 8, 10] {
            assert!(
                color_primaries_from_code(reserved).is_none(),
                "code {reserved} is reserved per Table 5"
            );
        }
        for code in 13u16..=255 {
            assert!(color_primaries_from_code(code as u8).is_none());
        }
    }

    #[test]
    fn color_primaries_code_round_trip() {
        // Every named variant's `code()` must reverse through
        // `from_code` to the same variant — guards a typo that
        // splits the two tables (e.g. swaps Bt2020's 9 with BT.709's
        // 1 in the match arm).
        for v in [
            ColorPrimaries::Bt709,
            ColorPrimaries::Bt601_625,
            ColorPrimaries::Bt601_525,
            ColorPrimaries::Bt2020,
            ColorPrimaries::DciP3,
            ColorPrimaries::P3D65,
        ] {
            assert_eq!(color_primaries_from_code(v.code()), Some(v));
        }
    }

    #[test]
    fn matrix_coefficients_from_code_named_codes_table_6() {
        // RDD 36 §6.1.1 Table 6: nonreserved codes 1, 6, 9.
        assert_eq!(
            matrix_coefficients_from_code(1),
            Some(MatrixCoefficients::Bt709)
        );
        assert_eq!(
            matrix_coefficients_from_code(6),
            Some(MatrixCoefficients::Bt601)
        );
        assert_eq!(
            matrix_coefficients_from_code(9),
            Some(MatrixCoefficients::Bt2020Ncl)
        );
    }

    #[test]
    fn matrix_coefficients_from_code_unknown_and_reserved_are_none() {
        // Codes 0 and 2 are "Unknown/unspecified" per Table 6; codes
        // 3..=5, 7..=8, 10..=255 are reserved.
        assert!(matrix_coefficients_from_code(0).is_none());
        assert!(matrix_coefficients_from_code(2).is_none());
        for reserved in [3u8, 4, 5, 7, 8] {
            assert!(
                matrix_coefficients_from_code(reserved).is_none(),
                "code {reserved} is reserved per Table 6"
            );
        }
        for code in 10u16..=255 {
            assert!(matrix_coefficients_from_code(code as u8).is_none());
        }
    }

    #[test]
    fn matrix_coefficients_code_round_trip() {
        for v in [
            MatrixCoefficients::Bt709,
            MatrixCoefficients::Bt601,
            MatrixCoefficients::Bt2020Ncl,
        ] {
            assert_eq!(matrix_coefficients_from_code(v.code()), Some(v));
        }
    }

    #[test]
    fn matrix_coefficients_luma_coefficients_match_table_6() {
        // Spot-check the spec's exact decimals — a typo (e.g. K_G
        // = 0.7050 instead of 0.7152 for BT.709, dropped from the
        // §6.1.1 listing) would lose the symbolic precision that
        // motivated returning f64 instead of constructing the YCbCr
        // transform here.
        assert_eq!(
            MatrixCoefficients::Bt709.luma_coefficients(),
            (0.2126, 0.7152, 0.0722),
        );
        assert_eq!(
            MatrixCoefficients::Bt601.luma_coefficients(),
            (0.299, 0.587, 0.114),
        );
        assert_eq!(
            MatrixCoefficients::Bt2020Ncl.luma_coefficients(),
            (0.2627, 0.6780, 0.0593),
        );
        // K_R + K_G + K_B = 1 by definition (the §6.1.1 derivation
        // forces it; a regression where any single K is bumped would
        // surface here). Float-tolerance is f64 epsilon scale, not
        // arbitrary — the spec values are exact decimals.
        for v in [
            MatrixCoefficients::Bt709,
            MatrixCoefficients::Bt601,
            MatrixCoefficients::Bt2020Ncl,
        ] {
            let (k_r, k_g, k_b) = v.luma_coefficients();
            assert!(
                (k_r + k_g + k_b - 1.0).abs() < 1e-12,
                "{v:?}: K_R + K_G + K_B = {} but must = 1",
                k_r + k_g + k_b,
            );
        }
    }

    #[test]
    fn alpha_channel_type_from_code_named_codes_table_7() {
        assert_eq!(
            alpha_channel_type_from_code(0),
            Some(AlphaChannelType::None)
        );
        assert_eq!(
            alpha_channel_type_from_code(1),
            Some(AlphaChannelType::Bits8)
        );
        assert_eq!(
            alpha_channel_type_from_code(2),
            Some(AlphaChannelType::Bits16)
        );
    }

    #[test]
    fn alpha_channel_type_from_code_reserved_are_none() {
        // Codes 3..=15 are reserved per Table 7 (the field is u4 so
        // 15 is the upper bound after masking). Out-of-domain values
        // are also None.
        for reserved in 3u8..=15 {
            assert!(
                alpha_channel_type_from_code(reserved).is_none(),
                "code {reserved} is reserved per Table 7"
            );
        }
        assert!(alpha_channel_type_from_code(16).is_none());
        assert!(alpha_channel_type_from_code(255).is_none());
    }

    #[test]
    fn alpha_channel_type_has_alpha_predicate() {
        assert!(!AlphaChannelType::None.has_alpha());
        assert!(AlphaChannelType::Bits8.has_alpha());
        assert!(AlphaChannelType::Bits16.has_alpha());
    }

    #[test]
    fn alpha_channel_type_code_round_trip() {
        for v in [
            AlphaChannelType::None,
            AlphaChannelType::Bits8,
            AlphaChannelType::Bits16,
        ] {
            assert_eq!(alpha_channel_type_from_code(v.code()), Some(v));
        }
    }

    #[test]
    fn parsed_frame_header_color_metadata_decodes_to_named_variants() {
        // End-to-end: write a frame header with a known FrameMeta
        // (BT.2020 primaries / SMPTE ST 2084 transfer / BT.2020 NCL
        // matrix), parse it back, and convert the parsed u8 codes
        // into named enum variants through the new helpers. This is
        // the canonical downstream-pipeline usage: a decoder reads a
        // packet and surfaces the source's color metadata to a
        // colour-management stage without re-implementing Tables 5,
        // 6, 7 itself.
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let meta = FrameMeta {
            aspect_ratio_information: 3,
            frame_rate_code: 8,
            color_primaries: 9,          // BT.2020
            transfer_characteristic: 16, // (no helper — see comment below)
            matrix_coefficients: 9,      // BT.2020 NCL
        };
        let mut buf = Vec::new();
        write_frame_with_meta(
            &mut buf,
            0,
            1920,
            1080,
            ChromaFormat::Y422,
            0,
            &luma,
            &chroma,
            false,
            false,
            1, // 8-bit alpha — exercise the alpha_channel_type round-trip too.
            meta,
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(
            color_primaries_from_code(fh.color_primaries),
            Some(ColorPrimaries::Bt2020),
        );
        assert_eq!(
            matrix_coefficients_from_code(fh.matrix_coefficients),
            Some(MatrixCoefficients::Bt2020Ncl),
        );
        assert_eq!(
            alpha_channel_type_from_code(fh.alpha_channel_type),
            Some(AlphaChannelType::Bits8),
        );
        // `transfer_characteristic` byte made it through verbatim; the
        // spec carries the formulae for codes 1, 16 (PQ), 18 (HLG)
        // inline (Table is implicit) — no enum helper here because the
        // §6.1.1 text only names three of the H.273 transfer codes.
        assert_eq!(fh.transfer_characteristic, 16);
    }

    #[test]
    fn parsed_frame_header_unknown_color_metadata_is_none_through_helpers() {
        // Symmetric anti-coverage: a packet emitted with zeroed
        // color metadata must surface as `None` through every
        // helper. The `alpha_channel_type` helper returns
        // `Some(AlphaChannelType::None)` (not the outer Option's
        // `None`) for the 0 code — that one is named, not unknown.
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let mut buf = Vec::new();
        write_frame_with_meta(
            &mut buf,
            0,
            64,
            48,
            ChromaFormat::Y422,
            0,
            &luma,
            &chroma,
            false,
            false,
            0,
            FrameMeta::default(),
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(color_primaries_from_code(fh.color_primaries), None);
        assert_eq!(matrix_coefficients_from_code(fh.matrix_coefficients), None);
        assert_eq!(
            alpha_channel_type_from_code(fh.alpha_channel_type),
            Some(AlphaChannelType::None),
        );
        assert!(!AlphaChannelType::None.has_alpha());
    }

    /// Helper for the `alpha_kind()` accessor tests: build a frame
    /// header with the given `alpha_channel_type` code and the
    /// chroma format that's spec-compatible with it. Code 0 is legal
    /// under both ChromaFormat::Y422 (version 0) and Y444 (version 1);
    /// codes 1 and 2 require Y444 (version 1) because §6.4 forbids
    /// non-zero alpha under bitstream_version 0.
    fn build_with_alpha(code: u8, chroma: ChromaFormat) -> Vec<u8> {
        let luma = [4u8; 64];
        let cma = [4u8; 64];
        let mut buf = Vec::new();
        write_frame_with_alpha(
            &mut buf, 0, 64, 48, chroma, 0, // progressive
            &luma, &cma, false, false, code,
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        buf
    }

    /// `FrameHeader::alpha_kind()` returns the named variant for each
    /// of the three defined Table 7 codes. The typed accessor is the
    /// canonical entry point for downstream code that needs to switch
    /// on alpha-plane storage width without re-deriving Table 7 — it
    /// folds the `alpha_channel_type_from_code(fh.alpha_channel_type)`
    /// boilerplate every call site previously needed into a single
    /// method on `FrameHeader`.
    #[test]
    fn alpha_kind_accessor_recognises_all_three_named_codes() {
        // Code 0 (`None`) — works under both chroma formats.
        let buf0 = build_with_alpha(0, ChromaFormat::Y422);
        let (fh0, _) = parse_frame(&buf0).unwrap();
        assert_eq!(fh0.alpha_kind(), Some(AlphaChannelType::None));
        assert_eq!(fh0.alpha_channel_type, 0);
        assert_eq!(fh0.bitstream_version, 0);
        assert!(!fh0.alpha_kind().unwrap().has_alpha());

        // Code 1 (`Bits8`) — must be Y444 (forces bitstream_version 1).
        let buf1 = build_with_alpha(1, ChromaFormat::Y444);
        let (fh1, _) = parse_frame(&buf1).unwrap();
        assert_eq!(fh1.alpha_kind(), Some(AlphaChannelType::Bits8));
        assert_eq!(fh1.alpha_channel_type, 1);
        assert_eq!(fh1.bitstream_version, 1);
        assert!(fh1.alpha_kind().unwrap().has_alpha());

        // Code 2 (`Bits16`) — must be Y444 (forces bitstream_version 1).
        let buf2 = build_with_alpha(2, ChromaFormat::Y444);
        let (fh2, _) = parse_frame(&buf2).unwrap();
        assert_eq!(fh2.alpha_kind(), Some(AlphaChannelType::Bits16));
        assert_eq!(fh2.alpha_channel_type, 2);
        assert_eq!(fh2.bitstream_version, 1);
        assert!(fh2.alpha_kind().unwrap().has_alpha());

        // Symmetric: every named variant's `code()` round-trips back to
        // the u8 the accessor read out of the wire.
        assert_eq!(fh0.alpha_kind().unwrap().code(), fh0.alpha_channel_type);
        assert_eq!(fh1.alpha_kind().unwrap().code(), fh1.alpha_channel_type);
        assert_eq!(fh2.alpha_kind().unwrap().code(), fh2.alpha_channel_type);
    }

    /// Accessor surfaces the outer-Option `None` for reserved Table 7
    /// codes `3..=15`. The frame-header parser itself never returns
    /// such a code today (the u4 is read from a 4-bit field, so every
    /// value 0..=15 is reachable; the parser does not reject 3..=15
    /// at the frame-header level — only at §6.4 cross-checks for
    /// version 0). The `FrameHeader` struct is also publicly
    /// constructible, so a downstream caller that hand-builds one
    /// with a reserved code in `alpha_channel_type` needs the
    /// accessor to distinguish "reserved" from "named". We exercise
    /// that path here directly on the struct rather than via the
    /// bitstream (the writer rejects > 2 in debug, and parse_frame
    /// never emits 3..=15 from any well-formed input).
    #[test]
    fn alpha_kind_accessor_returns_none_for_reserved_codes() {
        // Hand-build a FrameHeader with a reserved code so the
        // accessor's reserved-code branch is reached.
        let fh_reserved = FrameHeader {
            frame_size: 0,
            frame_header_size: 20,
            bitstream_version: 1,
            encoder_identifier: *ENCODER_IDENTIFIER,
            width: 64,
            height: 48,
            chroma_format: ChromaFormat::Y444,
            interlace_mode: 0,
            aspect_ratio_information: 0,
            frame_rate_code: 0,
            color_primaries: 0,
            transfer_characteristic: 0,
            matrix_coefficients: 0,
            alpha_channel_type: 7, // reserved per Table 7
            load_luma_quantization_matrix: false,
            load_chroma_quantization_matrix: false,
            luma_qmat: [4u8; 64],
            chroma_qmat: [4u8; 64],
        };
        assert_eq!(fh_reserved.alpha_kind(), None);

        // Boundary checks: every reserved code 3..=15 surfaces as None.
        for code in 3u8..=15 {
            let mut fh = fh_reserved.clone();
            fh.alpha_channel_type = code;
            assert_eq!(
                fh.alpha_kind(),
                None,
                "reserved code {code} must surface as outer-Option None",
            );
        }
    }

    /// `FrameHeader::interlace_kind()` returns the named Table 2 variant
    /// for each of the three defined codes (0/1/2) after parsing a
    /// frame that was emitted with that wire field. `picture_count()`
    /// agrees: 1 picture for progressive, 2 for either interlaced
    /// scan order — the same predicate `is_interlaced()` exposes on
    /// the variant.
    #[test]
    fn interlace_kind_accessor_recognises_all_three_named_codes() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |mode: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                mode,
                &luma,
                &chroma,
                false,
                false,
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        // Code 0 — progressive.
        let buf0 = build(0);
        let (fh0, _) = parse_frame(&buf0).unwrap();
        assert_eq!(fh0.interlace_kind(), Some(InterlaceMode::Progressive));
        assert_eq!(fh0.interlace_mode, 0);
        assert_eq!(fh0.picture_count(), 1);
        assert!(!fh0.interlace_kind().unwrap().is_interlaced());

        // Code 1 — TFF.
        let buf1 = build(1);
        let (fh1, _) = parse_frame(&buf1).unwrap();
        assert_eq!(fh1.interlace_kind(), Some(InterlaceMode::TopFieldFirst));
        assert_eq!(fh1.interlace_mode, 1);
        assert_eq!(fh1.picture_count(), 2);
        assert!(fh1.interlace_kind().unwrap().is_interlaced());

        // Code 2 — BFF.
        let buf2 = build(2);
        let (fh2, _) = parse_frame(&buf2).unwrap();
        assert_eq!(fh2.interlace_kind(), Some(InterlaceMode::BottomFieldFirst));
        assert_eq!(fh2.interlace_mode, 2);
        assert_eq!(fh2.picture_count(), 2);
        assert!(fh2.interlace_kind().unwrap().is_interlaced());

        // Symmetric: every named variant's `code()` round-trips back to
        // the u8 the accessor read out of the wire.
        assert_eq!(fh0.interlace_kind().unwrap().code(), fh0.interlace_mode,);
        assert_eq!(fh1.interlace_kind().unwrap().code(), fh1.interlace_mode,);
        assert_eq!(fh2.interlace_kind().unwrap().code(), fh2.interlace_mode,);
    }

    /// The reverse helper `interlace_mode_from_code` mirrors the
    /// accessor: 0/1/2 → named variants; 3 → None (reserved per
    /// Table 2); 4..=255 → None (above the u2 wire-field width). The
    /// parser refuses code 3 outright, so this branch is only
    /// reachable when a caller hand-builds a `FrameHeader` or calls
    /// the standalone helper directly.
    #[test]
    fn interlace_mode_from_code_reserved_and_out_of_range_are_none() {
        assert_eq!(
            interlace_mode_from_code(0),
            Some(InterlaceMode::Progressive),
        );
        assert_eq!(
            interlace_mode_from_code(1),
            Some(InterlaceMode::TopFieldFirst),
        );
        assert_eq!(
            interlace_mode_from_code(2),
            Some(InterlaceMode::BottomFieldFirst),
        );
        // Code 3 = reserved per Table 2.
        assert_eq!(interlace_mode_from_code(3), None);
        // Above-u2 codes: cannot appear in a parsed wire field, but the
        // helper is total and surfaces None for every byte value.
        for code in 4u8..=255 {
            assert_eq!(
                interlace_mode_from_code(code),
                None,
                "out-of-u2 code {code} must surface as None",
            );
        }

        // Accessor branch on a hand-built header carrying the reserved
        // code — the parser would have rejected this byte before
        // assembling the struct, but the accessor must still surface
        // outer-Option None per its documented contract.
        let fh_reserved = FrameHeader {
            frame_size: 0,
            frame_header_size: 20,
            bitstream_version: 1,
            encoder_identifier: *ENCODER_IDENTIFIER,
            width: 64,
            height: 48,
            chroma_format: ChromaFormat::Y444,
            interlace_mode: 3, // reserved per Table 2
            aspect_ratio_information: 0,
            frame_rate_code: 0,
            color_primaries: 0,
            transfer_characteristic: 0,
            matrix_coefficients: 0,
            alpha_channel_type: 0,
            load_luma_quantization_matrix: false,
            load_chroma_quantization_matrix: false,
            luma_qmat: [4u8; 64],
            chroma_qmat: [4u8; 64],
        };
        assert_eq!(fh_reserved.interlace_kind(), None);
    }

    /// `parse_frame_header` is the authoritative refusal point for the
    /// reserved Table 2 code (`3`). The accessor's reserved branch is
    /// therefore unreachable from a parsed-from-bytes header — verify
    /// that the byte 12 `(3 << 2)` encoding hits the parser's
    /// rejection path with the exact §6.1.1 / Table 2 citation.
    ///
    /// The `write_frame` writer debug-asserts `interlace_mode <= 2` so
    /// the reserved code cannot be emitted through the normal writer
    /// path; we build a minimal valid frame from a legal mode and then
    /// poke byte 12 to flip the u2 field to `3`, which is exactly the
    /// shape the parser must refuse if it ever sees it.
    #[test]
    fn parse_frame_header_rejects_interlace_mode_3() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let mut buf = Vec::new();
        write_frame(
            &mut buf,
            0,
            64,
            48,
            ChromaFormat::Y422,
            0, // start from progressive (legal)
            &luma,
            &chroma,
            false,
            false,
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        // Byte layout: 8 bytes of frame_size + 'icpf' magic, then the
        // frame_header starting at offset 8. Byte 12 of the
        // frame_header is at absolute offset 8 + 12 = 20 in the buffer
        // (`parse_frame_header` reads `data[12]` after the caller
        // already consumed the 8-byte size+magic prefix). The u2
        // interlace field lives in bits 3..2; flip them to `3` while
        // keeping chroma_format (bits 7..6) intact.
        let byte_12 = &mut buf[20];
        *byte_12 = (*byte_12 & !0b0000_1100) | (3 << 2);
        let err = parse_frame(&buf).expect_err("interlace_mode 3 must be rejected");
        assert!(
            err.to_string().contains("interlace_mode 3"),
            "error should cite the reserved interlace_mode, got: {err}"
        );
    }

    /// `FrameHeader::color_primaries_kind()` returns the named Table 5
    /// variant for each of the six defined codes (1/5/6/9/11/12) after
    /// parsing a frame that was emitted with that wire field. The raw
    /// `color_primaries` u8 stays on the struct (wire-level fidelity);
    /// the accessor folds the `color_primaries_from_code(fh.color_primaries)`
    /// boilerplate every call site previously needed into a single
    /// method on `FrameHeader`. We exercise the writer/parser round
    /// trip rather than constructing the struct directly so the test
    /// also verifies that `write_frame_with_meta` lays the byte at the
    /// right header offset and `parse_frame_header` reads it back at
    /// full byte width (Table 5 is a full u8 field — no mask is
    /// involved).
    #[test]
    fn color_primaries_kind_accessor_recognises_all_six_named_codes() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |code: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                FrameMeta {
                    color_primaries: code,
                    ..FrameMeta::default()
                },
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        let cases = [
            (1u8, ColorPrimaries::Bt709),
            (5u8, ColorPrimaries::Bt601_625),
            (6u8, ColorPrimaries::Bt601_525),
            (9u8, ColorPrimaries::Bt2020),
            (11u8, ColorPrimaries::DciP3),
            (12u8, ColorPrimaries::P3D65),
        ];
        for (code, named) in cases {
            let buf = build(code);
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.color_primaries, code);
            assert_eq!(
                fh.color_primaries_kind(),
                Some(named),
                "code {code} should surface as {named:?} via the accessor",
            );
            // Symmetric: every named variant's `code()` round-trips
            // back to the u8 the accessor read out of the wire.
            assert_eq!(
                fh.color_primaries_kind().unwrap().code(),
                fh.color_primaries
            );
        }
    }

    /// Accessor surfaces the outer-Option `None` for every "unknown /
    /// unspecified" + reserved Table 5 code. The frame-header parser
    /// reads `color_primaries` as a verbatim u8 (no masking), so any
    /// value `0..=255` is reachable from a well-formed wire packet.
    /// We assert at the byte level rather than by hand-building the
    /// struct: this exercises the same code path a real decoder would
    /// hit when handed a stream that pinned "unknown" or one of the
    /// reserved codes.
    #[test]
    fn color_primaries_kind_accessor_returns_none_for_unknown_and_reserved_codes() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |code: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                FrameMeta {
                    color_primaries: code,
                    ..FrameMeta::default()
                },
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        // Every byte that is not one of the six named codes must
        // surface as outer-Option `None` from the typed accessor. The
        // helper [`color_primaries_from_code`] already enumerates the
        // reserved set; we cross-check via the FrameHeader path here.
        for code in 0u8..=255 {
            let is_named = matches!(code, 1 | 5 | 6 | 9 | 11 | 12);
            if is_named {
                continue;
            }
            let buf = build(code);
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.color_primaries, code);
            assert_eq!(
                fh.color_primaries_kind(),
                None,
                "unknown/reserved code {code} must surface as None via the accessor",
            );
        }

        // Hand-built struct path: same outer-Option `None` semantics
        // when a downstream caller assembles a `FrameHeader` directly
        // (e.g. a probe stage that didn't go through `parse_frame`).
        let fh_unknown = FrameHeader {
            frame_size: 0,
            frame_header_size: 20,
            bitstream_version: 1,
            encoder_identifier: *ENCODER_IDENTIFIER,
            width: 64,
            height: 48,
            chroma_format: ChromaFormat::Y444,
            interlace_mode: 0,
            aspect_ratio_information: 0,
            frame_rate_code: 0,
            color_primaries: 0, // unknown per Table 5
            transfer_characteristic: 0,
            matrix_coefficients: 0,
            alpha_channel_type: 0,
            load_luma_quantization_matrix: false,
            load_chroma_quantization_matrix: false,
            luma_qmat: [4u8; 64],
            chroma_qmat: [4u8; 64],
        };
        assert_eq!(fh_unknown.color_primaries_kind(), None);
    }

    /// `FrameHeader::matrix_coefficients_kind()` returns the named
    /// Table 6 variant for each of the three defined codes (1/6/9)
    /// after parsing a frame that was emitted with that wire field.
    /// The raw `matrix_coefficients` u8 stays on the struct (wire-level
    /// fidelity); the accessor folds the
    /// `matrix_coefficients_from_code(fh.matrix_coefficients)`
    /// boilerplate every call site previously needed into a single
    /// method on `FrameHeader`. We exercise the writer/parser round
    /// trip rather than constructing the struct directly so the test
    /// also verifies that `write_frame_with_meta` lays the byte at the
    /// right header offset and `parse_frame_header` reads it back at
    /// full byte width (Table 6 is a full u8 field — no mask is
    /// involved).
    #[test]
    fn matrix_coefficients_kind_accessor_recognises_all_three_named_codes() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |code: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                FrameMeta {
                    matrix_coefficients: code,
                    ..FrameMeta::default()
                },
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        let cases = [
            (1u8, MatrixCoefficients::Bt709),
            (6u8, MatrixCoefficients::Bt601),
            (9u8, MatrixCoefficients::Bt2020Ncl),
        ];
        for (code, named) in cases {
            let buf = build(code);
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.matrix_coefficients, code);
            assert_eq!(
                fh.matrix_coefficients_kind(),
                Some(named),
                "code {code} should surface as {named:?} via the accessor",
            );
            // Symmetric: every named variant's `code()` round-trips
            // back to the u8 the accessor read out of the wire.
            assert_eq!(
                fh.matrix_coefficients_kind().unwrap().code(),
                fh.matrix_coefficients
            );
            // The accessor result carries the same K_R/K_G/K_B triple
            // as the free reverse helper — confirms that a downstream
            // Y'CbCr → R'G'B' stage can read the luma coefficients
            // straight off the typed accessor.
            assert_eq!(
                fh.matrix_coefficients_kind().unwrap().luma_coefficients(),
                named.luma_coefficients()
            );
        }
    }

    /// Accessor surfaces the outer-Option `None` for every "unknown /
    /// unspecified" + reserved Table 6 code. The frame-header parser
    /// reads `matrix_coefficients` as a verbatim u8 (no masking), so
    /// any value `0..=255` is reachable from a well-formed wire packet.
    /// We assert at the byte level rather than by hand-building the
    /// struct: this exercises the same code path a real decoder would
    /// hit when handed a stream that pinned "unknown" or one of the
    /// reserved codes.
    #[test]
    fn matrix_coefficients_kind_accessor_returns_none_for_unknown_and_reserved_codes() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |code: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                FrameMeta {
                    matrix_coefficients: code,
                    ..FrameMeta::default()
                },
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        // Every byte that is not one of the three named codes must
        // surface as outer-Option `None` from the typed accessor. The
        // helper [`matrix_coefficients_from_code`] already enumerates
        // the reserved set; we cross-check via the FrameHeader path
        // here.
        for code in 0u8..=255 {
            let is_named = matches!(code, 1 | 6 | 9);
            if is_named {
                continue;
            }
            let buf = build(code);
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.matrix_coefficients, code);
            assert_eq!(
                fh.matrix_coefficients_kind(),
                None,
                "unknown/reserved code {code} must surface as None via the accessor",
            );
        }

        // Hand-built struct path: same outer-Option `None` semantics
        // when a downstream caller assembles a `FrameHeader` directly
        // (e.g. a probe stage that didn't go through `parse_frame`).
        let fh_unknown = FrameHeader {
            frame_size: 0,
            frame_header_size: 20,
            bitstream_version: 1,
            encoder_identifier: *ENCODER_IDENTIFIER,
            width: 64,
            height: 48,
            chroma_format: ChromaFormat::Y444,
            interlace_mode: 0,
            aspect_ratio_information: 0,
            frame_rate_code: 0,
            color_primaries: 0,
            transfer_characteristic: 0,
            matrix_coefficients: 0, // unknown per Table 6
            alpha_channel_type: 0,
            load_luma_quantization_matrix: false,
            load_chroma_quantization_matrix: false,
            luma_qmat: [4u8; 64],
            chroma_qmat: [4u8; 64],
        };
        assert_eq!(fh_unknown.matrix_coefficients_kind(), None);
    }

    /// `FrameHeader::transfer_characteristic_kind()` returns the
    /// named §6.1.1 variant for each of the three defined codes
    /// (1 / 16 / 18) after parsing a frame that was emitted with that
    /// wire field. The raw `transfer_characteristic` u8 stays on the
    /// struct (wire-level fidelity); the accessor folds the
    /// `transfer_characteristic_from_code(fh.transfer_characteristic)`
    /// boilerplate every call site previously needed into a single
    /// method on `FrameHeader`. We exercise the writer/parser round
    /// trip rather than constructing the struct directly so the test
    /// also verifies that `write_frame_with_meta` lays the byte at
    /// the right header offset and `parse_frame_header` reads it back
    /// at full byte width (the field is a full u8 — no mask is
    /// involved).
    #[test]
    fn transfer_characteristic_kind_accessor_recognises_all_three_named_codes() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |code: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                FrameMeta {
                    transfer_characteristic: code,
                    ..FrameMeta::default()
                },
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        let cases = [
            (1u8, TransferCharacteristic::Bt1886),
            (16u8, TransferCharacteristic::St2084),
            (18u8, TransferCharacteristic::Hlg),
        ];
        for (code, named) in cases {
            let buf = build(code);
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.transfer_characteristic, code);
            assert_eq!(
                fh.transfer_characteristic_kind(),
                Some(named),
                "code {code} should surface as {named:?} via the accessor",
            );
            // Symmetric: every named variant's `code()` round-trips
            // back to the u8 the accessor read out of the wire.
            assert_eq!(
                fh.transfer_characteristic_kind().unwrap().code(),
                fh.transfer_characteristic
            );
        }
    }

    /// Accessor surfaces the outer-Option `None` for every
    /// "unknown / unspecified" + reserved §6.1.1
    /// `transfer_characteristic` code. The frame-header parser reads
    /// the field as a verbatim u8 (no masking), so any value
    /// `0..=255` is reachable from a well-formed wire packet. We
    /// assert at the byte level rather than by hand-building the
    /// struct: this exercises the same code path a real decoder would
    /// hit when handed a stream that pinned "unknown" or one of the
    /// reserved codes.
    #[test]
    fn transfer_characteristic_kind_accessor_returns_none_for_unknown_and_reserved_codes() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |code: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                FrameMeta {
                    transfer_characteristic: code,
                    ..FrameMeta::default()
                },
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        // Every byte that is not one of the three named codes must
        // surface as outer-Option `None` from the typed accessor. The
        // helper [`transfer_characteristic_from_code`] already
        // enumerates the unknown + reserved set; we cross-check via
        // the FrameHeader path here.
        for code in 0u8..=255 {
            let is_named = matches!(code, 1 | 16 | 18);
            if is_named {
                continue;
            }
            let buf = build(code);
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.transfer_characteristic, code);
            assert_eq!(
                fh.transfer_characteristic_kind(),
                None,
                "unknown/reserved code {code} must surface as None via the accessor",
            );
        }

        // Hand-built struct path: same outer-Option `None` semantics
        // when a downstream caller assembles a `FrameHeader` directly
        // (e.g. a probe stage that didn't go through `parse_frame`).
        let fh_unknown = FrameHeader {
            frame_size: 0,
            frame_header_size: 20,
            bitstream_version: 1,
            encoder_identifier: *ENCODER_IDENTIFIER,
            width: 64,
            height: 48,
            chroma_format: ChromaFormat::Y444,
            interlace_mode: 0,
            aspect_ratio_information: 0,
            frame_rate_code: 0,
            color_primaries: 0,
            transfer_characteristic: 0, // unknown per §6.1.1
            matrix_coefficients: 0,
            alpha_channel_type: 0,
            load_luma_quantization_matrix: false,
            load_chroma_quantization_matrix: false,
            luma_qmat: [4u8; 64],
            chroma_qmat: [4u8; 64],
        };
        assert_eq!(fh_unknown.transfer_characteristic_kind(), None);
    }

    /// `FrameHeader::frame_rate()` returns the named §6.2 Table 4 rate
    /// for each of the eleven defined codes after parsing a frame that
    /// was emitted with that wire field. The raw `frame_rate_code` u4
    /// stays on the struct (wire-level fidelity); the accessor folds
    /// the `rational_from_frame_rate_code(fh.frame_rate_code)`
    /// boilerplate every call site previously needed into a single
    /// method on `FrameHeader`. We exercise the writer/parser round
    /// trip rather than constructing the struct directly so the test
    /// also verifies that `write_frame_with_meta` lays the nibble at
    /// the right header offset (low nibble of byte 13) and
    /// `parse_frame_header` reads it back with the correct mask.
    #[test]
    fn frame_rate_accessor_recognises_all_eleven_named_codes() {
        use oxideav_core::Rational;
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |code: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                FrameMeta {
                    frame_rate_code: code,
                    ..FrameMeta::default()
                },
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        let cases = [
            (1u8, Rational::new(24_000, 1001)),
            (2u8, Rational::new(24, 1)),
            (3u8, Rational::new(25, 1)),
            (4u8, Rational::new(30_000, 1001)),
            (5u8, Rational::new(30, 1)),
            (6u8, Rational::new(50, 1)),
            (7u8, Rational::new(60_000, 1001)),
            (8u8, Rational::new(60, 1)),
            (9u8, Rational::new(100, 1)),
            (10u8, Rational::new(120_000, 1001)),
            (11u8, Rational::new(120, 1)),
        ];
        for (code, named) in cases {
            let buf = build(code);
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.frame_rate_code, code);
            assert_eq!(
                fh.frame_rate(),
                Some(named),
                "code {code} should surface as {named:?} via the accessor",
            );
            // Symmetric: the named rate round-trips back through
            // `frame_rate_code_from_rational` to the on-wire u4.
            assert_eq!(
                frame_rate_code_from_rational(fh.frame_rate().unwrap()),
                fh.frame_rate_code
            );
        }
    }

    /// Accessor surfaces the outer-Option `None` for the
    /// "unknown / unspecified" code 0 and every reserved code in
    /// `12..=15`. The frame-header parser reads `frame_rate_code` as a
    /// 4-bit nibble (low half of byte 13), so every value `0..=15` is
    /// reachable from a well-formed wire packet. We exercise the
    /// byte-level path through `parse_frame` for the unknown + reserved
    /// codes plus the hand-built `FrameHeader` path so a downstream
    /// caller that assembles a struct directly (e.g. a probe stage that
    /// didn't go through `parse_frame`) also gets the same outer-Option
    /// `None` semantics.
    #[test]
    fn frame_rate_accessor_returns_none_for_unknown_and_reserved_codes() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |code: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                FrameMeta {
                    frame_rate_code: code,
                    ..FrameMeta::default()
                },
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        // Code 0 — unknown/unspecified.
        let buf = build(0);
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(fh.frame_rate_code, 0);
        assert_eq!(fh.frame_rate(), None);

        // Codes 12..=15 — reserved per Table 4. `write_frame_with_meta`
        // packs the low nibble into byte 13; `parse_frame_header` masks
        // it back out with `& 0x0F`, so every reserved value is
        // reachable from a well-formed wire packet.
        for code in 12u8..=15 {
            let buf = build(code);
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.frame_rate_code, code);
            assert_eq!(
                fh.frame_rate(),
                None,
                "reserved code {code} must surface as outer-Option None",
            );
        }

        // Hand-built struct path: same outer-Option `None` semantics
        // when a downstream caller assembles a `FrameHeader` directly
        // with a reserved code. Also exercises codes above the u4 width
        // (`16..=255`) that a hand-built struct could carry even though
        // `parse_frame_header` would never emit them.
        for code in [0u8, 12, 13, 14, 15, 16, 100, 255] {
            let fh = FrameHeader {
                frame_size: 0,
                frame_header_size: 20,
                bitstream_version: 1,
                encoder_identifier: *ENCODER_IDENTIFIER,
                width: 64,
                height: 48,
                chroma_format: ChromaFormat::Y444,
                interlace_mode: 0,
                aspect_ratio_information: 0,
                frame_rate_code: code,
                color_primaries: 0,
                transfer_characteristic: 0,
                matrix_coefficients: 0,
                alpha_channel_type: 0,
                load_luma_quantization_matrix: false,
                load_chroma_quantization_matrix: false,
                luma_qmat: [4u8; 64],
                chroma_qmat: [4u8; 64],
            };
            assert_eq!(
                fh.frame_rate(),
                None,
                "code {code} must surface as outer-Option None on hand-built struct",
            );
        }
    }

    /// `FrameHeader::aspect_ratio()` — every named §6.2 / Table 3 code
    /// must round-trip through `parse_frame` to the spec's exact
    /// fraction. The encoder packs `aspect_ratio_information` into the
    /// high nibble of byte 13 via `write_frame_with_meta`; the parser
    /// masks it back out and lands it on the struct verbatim, so a
    /// `FrameMeta { aspect_ratio_information: c, .. }` write + parse
    /// reproduces the on-wire code and the typed accessor lifts it to
    /// the named [`oxideav_core::Rational`]. We exercise the byte-level
    /// path (write_frame_with_meta → parse_frame → fh.aspect_ratio())
    /// so the test covers the same packing the encoder uses on a real
    /// packet; in particular code `1` (square pixels) lands on
    /// `Some(Rational::new(1, 1))` distinct from `None` (unknown), and
    /// the returned `Rational` agrees with [`aspect_ratio_from_code`]
    /// for every named code.
    #[test]
    fn aspect_ratio_accessor_named_codes_round_trip_through_parse() {
        use oxideav_core::Rational;
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let cases: &[(u8, Rational)] = &[
            (1, Rational::new(1, 1)),
            (2, Rational::new(4, 3)),
            (3, Rational::new(16, 9)),
        ];
        for &(code, expected) in cases {
            let meta = FrameMeta {
                aspect_ratio_information: code,
                ..FrameMeta::default()
            };
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                1920,
                1080,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                meta,
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.aspect_ratio_information, code);
            assert_eq!(
                fh.aspect_ratio(),
                Some(expected),
                "code {code} must lift to {}/{} via the typed accessor",
                expected.num,
                expected.den,
            );
            // Symmetric reverse: the wire-level `aspect_ratio_from_code`
            // and the typed accessor must agree (defends against a
            // typo that splits the two surfaces apart).
            assert_eq!(
                aspect_ratio_from_code(fh.aspect_ratio_information),
                fh.aspect_ratio(),
                "typed accessor must agree with aspect_ratio_from_code for code {code}",
            );
        }
    }

    /// Accessor surfaces the outer-Option `None` for the
    /// "unknown / unspecified" code `0` and every reserved code in
    /// `4..=15`. `aspect_ratio_information` lives in the high nibble of
    /// byte 13, so every value `0..=15` is reachable from a well-formed
    /// wire packet. We exercise the byte-level path through
    /// `parse_frame` for the unknown + reserved codes plus the
    /// hand-built `FrameHeader` path (for above-u4 values that
    /// `parse_frame_header` masks away but a hand-assembled struct could
    /// carry) so a downstream caller that assembles a struct directly
    /// also gets the same outer-Option `None` semantics.
    #[test]
    fn aspect_ratio_accessor_returns_none_for_unknown_and_reserved_codes() {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let build = |code: u8| -> Vec<u8> {
            let mut buf = Vec::new();
            write_frame_with_meta(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y422,
                0,
                &luma,
                &chroma,
                false,
                false,
                0,
                FrameMeta {
                    aspect_ratio_information: code,
                    ..FrameMeta::default()
                },
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            buf
        };

        // Code 0 — unknown/unspecified, distinct from any named ratio.
        let buf = build(0);
        let (fh, _) = parse_frame(&buf).unwrap();
        assert_eq!(fh.aspect_ratio_information, 0);
        assert_eq!(fh.aspect_ratio(), None);

        // Codes 4..=15 — reserved per Table 3. `write_frame_with_meta`
        // packs the low nibble of the value into the high nibble of
        // byte 13; `parse_frame_header` shifts it back out with
        // `& 0xF`, so every reserved value is reachable from a
        // well-formed wire packet.
        for code in 4u8..=15 {
            let buf = build(code);
            let (fh, _) = parse_frame(&buf).unwrap();
            assert_eq!(fh.aspect_ratio_information, code);
            assert_eq!(
                fh.aspect_ratio(),
                None,
                "reserved code {code} must surface as outer-Option None",
            );
        }

        // Hand-built struct path: same outer-Option `None` semantics
        // when a downstream caller assembles a `FrameHeader` directly
        // with a reserved code. Also exercises codes above the u4 width
        // (`16..=255`) that a hand-built struct could carry even though
        // `parse_frame_header` would never emit them.
        for code in [0u8, 4, 5, 14, 15, 16, 100, 255] {
            let fh = FrameHeader {
                frame_size: 0,
                frame_header_size: 20,
                bitstream_version: 1,
                encoder_identifier: *ENCODER_IDENTIFIER,
                width: 64,
                height: 48,
                chroma_format: ChromaFormat::Y444,
                interlace_mode: 0,
                aspect_ratio_information: code,
                frame_rate_code: 0,
                color_primaries: 0,
                transfer_characteristic: 0,
                matrix_coefficients: 0,
                alpha_channel_type: 0,
                load_luma_quantization_matrix: false,
                load_chroma_quantization_matrix: false,
                luma_qmat: [4u8; 64],
                chroma_qmat: [4u8; 64],
            };
            assert_eq!(
                fh.aspect_ratio(),
                None,
                "code {code} must surface as outer-Option None on hand-built struct",
            );
        }
    }

    /// `PictureHeader::mbs_per_slice()` lifts the raw u2
    /// `log2_desired_slice_size_in_mb` field into the actual
    /// macroblocks-per-slice value (`1 << code`) for each of the four
    /// defined codes. We exercise the writer/parser round trip rather
    /// than constructing the struct directly so the test also verifies
    /// that `write_picture_header` lays the field into bits 4..=5 of
    /// byte 7 of the picture header and `parse_picture_header` reads
    /// it back with the correct mask.
    #[test]
    fn mbs_per_slice_accessor_recognises_all_four_named_codes() {
        let cases = [(0u8, 1u8), (1, 2), (2, 4), (3, 8)];
        for (code, expected_mbs) in cases {
            let mut buf = Vec::new();
            write_picture_header(&mut buf, 4096, 1, code);
            let (ph, _) = parse_picture_header(&buf).unwrap();
            assert_eq!(ph.log2_desired_slice_size_in_mb, code);
            assert_eq!(
                ph.mbs_per_slice(),
                Some(expected_mbs),
                "code {code} should surface as {expected_mbs}-MBs-per-slice via the accessor",
            );
            // Cross-check: the accessor and the inverse
            // `1 << log2_desired_slice_size_in_mb` derivation
            // [`compute_slice_sizes`] seeds with must agree for every
            // wire-reachable code (defends against a typo that would
            // split the accessor from the slice-table derivation).
            assert_eq!(
                ph.mbs_per_slice().unwrap(),
                1u8 << ph.log2_desired_slice_size_in_mb,
                "accessor must agree with `1 << log2_desired_slice_size_in_mb` for code {code}",
            );
        }
    }

    /// Accessor surfaces the outer-Option `None` for any out-of-range
    /// value a hand-assembled `PictureHeader` could carry. The
    /// `parse_picture_header` path masks the field to two bits before
    /// storing it, so a parsed struct always satisfies the `0..=3`
    /// invariant and the accessor unconditionally returns `Some(_)`;
    /// the `None` arm only fires when a downstream caller assembles a
    /// `PictureHeader` directly with a code in `4..=255`.
    #[test]
    fn mbs_per_slice_accessor_returns_none_for_out_of_range_codes() {
        for code in [4u8, 5, 7, 8, 15, 16, 100, 255] {
            let ph = PictureHeader {
                picture_header_size: 8,
                picture_size: 0,
                deprecated_number_of_slices: 0,
                log2_desired_slice_size_in_mb: code,
            };
            assert_eq!(
                ph.mbs_per_slice(),
                None,
                "code {code} must surface as outer-Option None on hand-built struct",
            );
        }

        // Every parsed `PictureHeader` is wire-clean (the parser masks
        // the field to two bits), so the accessor returns `Some(_)`
        // for every value the parser can emit. This pin documents
        // that invariant.
        for code in 0u8..=3 {
            let mut buf = Vec::new();
            write_picture_header(&mut buf, 0, 0, code);
            let (ph, _) = parse_picture_header(&buf).unwrap();
            assert!(
                ph.mbs_per_slice().is_some(),
                "every parsed picture header should have a defined slice width (code {code})",
            );
        }
    }

    /// Helper for the `FrameHeader::meta()` tests: emit a frame header
    /// carrying `meta` via `write_frame_with_meta` (flat default qmats,
    /// progressive 4:2:2, no alpha) and parse it back.
    fn parse_header_with_meta(meta: FrameMeta) -> FrameHeader {
        let luma = [4u8; 64];
        let chroma = [4u8; 64];
        let mut buf = Vec::new();
        write_frame_with_meta(
            &mut buf,
            0,
            64,
            48,
            ChromaFormat::Y422,
            0,
            &luma,
            &chroma,
            false,
            false,
            0,
            meta,
        );
        let total = buf.len() as u32;
        buf[0..4].copy_from_slice(&total.to_be_bytes());
        let (fh, _) = parse_frame(&buf).unwrap();
        fh
    }

    /// `FrameHeader::meta()` round-trips a fully-populated `FrameMeta`
    /// (RDD 36 §5.1.1 / §6.2 Tables 3 + 4, §6.1.1 Tables 5 + 6) through
    /// the writer + parser bit-exactly, and the lifted per-field typed
    /// accessors stay consistent with the folded struct — so a
    /// transcode pipeline can hand `fh.meta()` to
    /// `EncoderConfig::with_meta` and lose nothing relative to copying
    /// the five raw fields by hand.
    #[test]
    fn meta_accessor_round_trips_named_codes_through_parse() {
        use oxideav_core::Rational;
        // BT.2020 / ST 2084 PQ HDR profile at 16:9, 60 fps — every
        // field a named (nonreserved) code: aspect 3 = 16:9 (Table 3),
        // rate 8 = 60 fps (Table 4), primaries 9 = BT.2020 (Table 5),
        // transfer 16 = SMPTE ST 2084 (§6.1.1), matrix 9 = BT.2020 NCL
        // (Table 6).
        let src = FrameMeta {
            aspect_ratio_information: 3,
            frame_rate_code: 8,
            color_primaries: 9,
            transfer_characteristic: 16,
            matrix_coefficients: 9,
        };
        let fh = parse_header_with_meta(src);
        let meta = fh.meta();
        assert_eq!(meta, src, "fh.meta() must equal the written FrameMeta");
        // The folded struct and the raw wire-level fields must agree
        // field-by-field (defends against a typo that swaps two arms
        // of the fold).
        assert_eq!(meta.aspect_ratio_information, fh.aspect_ratio_information);
        assert_eq!(meta.frame_rate_code, fh.frame_rate_code);
        assert_eq!(meta.color_primaries, fh.color_primaries);
        assert_eq!(meta.transfer_characteristic, fh.transfer_characteristic);
        assert_eq!(meta.matrix_coefficients, fh.matrix_coefficients);
        // Consistency with the per-field typed accessors: the lifted
        // named values must match what the folded raw bytes lift to.
        assert_eq!(fh.aspect_ratio(), Some(Rational::new(16, 9)));
        assert_eq!(fh.frame_rate(), Some(Rational::new(60, 1)));
        assert_eq!(fh.color_primaries_kind(), Some(ColorPrimaries::Bt2020));
        assert_eq!(
            fh.transfer_characteristic_kind(),
            Some(TransferCharacteristic::St2084)
        );
        assert_eq!(
            fh.matrix_coefficients_kind(),
            Some(MatrixCoefficients::Bt2020Ncl)
        );
        assert!(!meta.is_unknown());
    }

    /// `FrameHeader::meta()` is a verbatim fold: reserved / unknown
    /// codes (which the per-field typed accessors surface as `None`)
    /// are preserved bit-exactly, and the all-zero header folds to the
    /// encoder's `FrameMeta::unknown()` no-op default. §5.1.1 documents
    /// these fields as descriptive hints a decoder passes through
    /// rather than validates, so the re-encode direction must not
    /// filter them.
    #[test]
    fn meta_accessor_preserves_unknown_and_reserved_codes_verbatim() {
        // All-zero header — "unknown / unspecified" on every field.
        let fh = parse_header_with_meta(FrameMeta::default());
        assert_eq!(fh.meta(), FrameMeta::unknown());
        assert!(fh.meta().is_unknown());

        // Reserved codes on every field: aspect 15 (Table 3 reserves
        // 4..=15), rate 12 (Table 4 reserves 12..=15), primaries 3
        // (reserved per Table 5), transfer 17 (reserved per §6.1.1),
        // matrix 4 (reserved per Table 6). Each per-field typed
        // accessor lifts to `None`, yet the fold must carry the raw
        // bytes through unchanged.
        let src = FrameMeta {
            aspect_ratio_information: 15,
            frame_rate_code: 12,
            color_primaries: 3,
            transfer_characteristic: 17,
            matrix_coefficients: 4,
        };
        let fh = parse_header_with_meta(src);
        assert_eq!(fh.meta(), src, "reserved codes must fold through verbatim",);
        assert_eq!(fh.aspect_ratio(), None);
        assert_eq!(fh.frame_rate(), None);
        assert_eq!(fh.color_primaries_kind(), None);
        assert_eq!(fh.transfer_characteristic_kind(), None);
        assert_eq!(fh.matrix_coefficients_kind(), None);
        assert!(!fh.meta().is_unknown());
    }

    /// The RDD 36 §6.1.1 `encoder_identifier` (frame-header bytes 4..8)
    /// round-trips from the writer through `parse_frame_header`: a frame
    /// emitted by [`write_frame_with_meta`] carries the crate's
    /// [`ENCODER_IDENTIFIER`] (`b"oxav"`), and the parsed header surfaces
    /// it byte-exactly through both [`FrameHeader::encoder_identifier`]
    /// and the printable-ASCII [`FrameHeader::encoder_identifier_str`]
    /// accessor. This pins that the field — previously read and
    /// discarded by the parser — is now exposed; a regression that
    /// dropped it (or read the wrong four bytes) flips the test red.
    #[test]
    fn encoder_identifier_round_trips_through_parse() {
        let fh = parse_header_with_meta(FrameMeta::default());
        assert_eq!(
            fh.encoder_identifier(),
            *ENCODER_IDENTIFIER,
            "parsed header must surface the written encoder_identifier verbatim"
        );
        assert_eq!(
            fh.encoder_identifier_str(),
            Some("oxav"),
            "printable-ASCII encoder_identifier lifts to its FourCC string"
        );
        // The field sits at frame-header bytes 4..8, distinct from the
        // descriptive §6.2 metadata bytes — populating those must not
        // perturb it.
        let fh2 = parse_header_with_meta(FrameMeta {
            aspect_ratio_information: 3,
            frame_rate_code: 8,
            color_primaries: 9,
            transfer_characteristic: 16,
            matrix_coefficients: 9,
        });
        assert_eq!(fh2.encoder_identifier(), *ENCODER_IDENTIFIER);
        assert_eq!(fh2.encoder_identifier_str(), Some("oxav"));
    }

    /// [`FrameHeader::encoder_identifier_str`] returns the raw bytes only
    /// when all four are printable ASCII (`0x20..=0x7E`); a header
    /// carrying a non-printable byte (some non-conforming encoder could
    /// write binary here — the spec only says decoders *should ignore*
    /// the field, not that it must be printable) yields `None` from the
    /// string accessor while [`FrameHeader::encoder_identifier`] still
    /// returns the raw four bytes. Drives `parse_frame_header` directly
    /// over a hand-built header so the four `encoder_identifier` bytes
    /// can be set independently of [`write_frame_with_meta`], which only
    /// ever emits the printable [`ENCODER_IDENTIFIER`] constant.
    #[test]
    fn encoder_identifier_str_rejects_non_printable_bytes() {
        // Minimal 20-byte frame_header() with a non-printable byte
        // (0x00) inside the encoder_identifier at bytes 4..8.
        let mut hdr = vec![0u8; 20];
        hdr[0] = 0; // frame_header_size hi
        hdr[1] = 20; // frame_header_size lo = 20
        hdr[2] = 0; // reserved
        hdr[3] = 0; // bitstream_version 0 (4:2:2, no alpha — §6.4 legal)
        hdr[4] = b'A';
        hdr[5] = 0x00; // non-printable
        hdr[6] = b'p';
        hdr[7] = b'l';
        // width/height (bytes 8..12)
        hdr[8] = 0;
        hdr[9] = 64;
        hdr[10] = 0;
        hdr[11] = 48;
        // byte 12: chroma_format=2 (4:2:2) in bits 7..6 → 0b10_00_00_00
        hdr[12] = 0b1000_0000;
        // bytes 13..20 already zero (descriptive meta + reserved +
        // load flags = 0). load_luma = load_chroma = 0 → flat matrices.
        let (fh, _) = parse_frame_header(&hdr).unwrap();
        assert_eq!(
            fh.encoder_identifier(),
            [b'A', 0x00, b'p', b'l'],
            "raw encoder_identifier bytes are surfaced even when non-printable"
        );
        assert_eq!(
            fh.encoder_identifier_str(),
            None,
            "a non-printable byte makes the string accessor return None"
        );
    }

    /// `FrameHeader::quantization_matrix_source()` distinguishes the three
    /// §6.1.1 / §7.2 chroma-matrix derivations from the two
    /// `load_*_quantization_matrix` wire flags, and the raw flags are
    /// surfaced on the parsed struct. We round-trip through the writer /
    /// parser so the test also pins that the flags survive the bit
    /// packing in byte 19 of the frame header.
    #[test]
    fn quantization_matrix_source_reflects_load_flags() {
        // A custom matrix distinct from the §7.2 default (all 4s) so the
        // copy-from-luma case is observable in chroma_qmat too.
        let mut custom_luma = [4u8; 64];
        for (i, w) in custom_luma.iter_mut().enumerate() {
            *w = 2 + (i as u8 % 62); // every entry in 2..=63
        }
        let mut custom_chroma = [4u8; 64];
        for (i, w) in custom_chroma.iter_mut().enumerate() {
            *w = 63 - (i as u8 % 62); // distinct from luma, still 2..=63
        }
        let build = |load_luma: bool, load_chroma: bool| -> FrameHeader {
            let mut buf = Vec::new();
            write_frame(
                &mut buf,
                0,
                64,
                48,
                ChromaFormat::Y444,
                0,
                &custom_luma,
                &custom_chroma,
                load_luma,
                load_chroma,
            );
            let total = buf.len() as u32;
            buf[0..4].copy_from_slice(&total.to_be_bytes());
            parse_frame(&buf).expect("parse").0
        };

        // Both custom → CustomChroma; chroma_qmat is the header chroma matrix.
        let fh = build(true, true);
        assert!(fh.load_luma_quantization_matrix);
        assert!(fh.load_chroma_quantization_matrix);
        assert_eq!(
            fh.quantization_matrix_source(),
            QuantizationMatrixSource::CustomChroma
        );
        assert_eq!(fh.chroma_qmat, custom_chroma);
        assert_eq!(fh.luma_qmat, custom_luma);

        // Custom luma, no chroma flag → LumaCustom; chroma copies luma (§6.1.1).
        let fh = build(true, false);
        assert!(fh.load_luma_quantization_matrix);
        assert!(!fh.load_chroma_quantization_matrix);
        assert_eq!(
            fh.quantization_matrix_source(),
            QuantizationMatrixSource::LumaCustom
        );
        assert_eq!(fh.chroma_qmat, custom_luma);
        assert_eq!(fh.luma_qmat, custom_luma);

        // Neither flag → Default; both matrices are the §7.2 all-4s default.
        let fh = build(false, false);
        assert!(!fh.load_luma_quantization_matrix);
        assert!(!fh.load_chroma_quantization_matrix);
        assert_eq!(
            fh.quantization_matrix_source(),
            QuantizationMatrixSource::Default
        );
        assert_eq!(fh.luma_qmat, [4u8; 64]);
        assert_eq!(fh.chroma_qmat, [4u8; 64]);
    }
}
