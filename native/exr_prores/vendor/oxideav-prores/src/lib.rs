// Parallel-array index loops are idiomatic in codec/DCT code; skip the lint.
#![allow(clippy::needless_range_loop)]

//! Apple ProRes codec — pure-Rust decoder + encoder following SMPTE
//! RDD 36:2022.
//!
//! Scope: all six ProRes video profiles, dispatched by container FourCC:
//!
//! * 422 Proxy / LT / Standard / HQ for 4:2:2 Y'CbCr at 8-, 10-, 12-,
//!   or 16-bit depth (`Yuv422P{,10Le,12Le,16Le}`, fourccs
//!   `apco`/`apcs`/`apcn`/`apch`).
//! * 4444 + 4444 XQ for 4:4:4 Y'CbCr at 8-/10-/12-/16-bit
//!   (`Yuv444P{,10Le,12Le,16Le}`, fourccs `ap4h`/`ap4x`). These
//!   profiles also carry an optional per-pixel alpha plane (RDD 36
//!   §5.3.3) coded losslessly via the raster-scan run-length code from
//!   §7.1.2 (Tables 12-14). The decoded alpha lands as a 4th
//!   `VideoPlane` on the output `VideoFrame`; the encoder accepts the
//!   same shape on input through
//!   [`encoder::encode_frame_with_alpha`].
//! * Alpha-typed surfaces on both codec directions: request
//!   `PixelFormat::Yuva4(2|4)4P{,10Le,12Le,16Le}` in `CodecParameters`
//!   to make the 4-plane layout part of the format contract — the
//!   decoder then always emits 4 planes (coded alpha converted to the
//!   surface depth per §7.5.2, exact at the 16-bit surfaces; a
//!   no-alpha stream gets a synthesised opaque plane), and the encoder
//!   requires 4-plane input and codes alpha on every frame (bitstream
//!   version 1 per §6.4) — 16-bit coded alpha for the deep formats, so
//!   no input precision is dropped on the wire. See
//!   [`decoder::decode_packet_with_format`].
//!
//! ### Bitstream
//!
//! * `frame() { frame_size, 'icpf', frame_header(), picture()+ }` per
//!   RDD 36 §5.1.
//! * `picture() { picture_header(), slice_table(), slice()+ }` per §5.2.
//! * `slice() { slice_header(), Y' data, Cb data, Cr data, [A' data] }`
//!   per §5.3, with each color component coded by §7.1.1's run/level/
//!   sign entropy coder and the optional alpha array coded by §7.1.2's
//!   run-length / VLC code.
//!
//! Bitstream syntax, entropy coder, slice + block scans, alpha entropy
//! coder, and inverse quantization are bit-exact with the spec. The
//! IDCT is a textbook float implementation (§7.4 allows fixed- or
//! floating-point, subject to Annex A accuracy) — sufficient for visual
//! fidelity.
//!
//! ### Interlaced (RDD 36 §5.1, §6.2, §7.5.3)
//!
//! Interlaced frames carry two pictures (one per field) sharing one
//! frame_header(). The encoder splits the source into top + bottom
//! fields per §7.5.3 (top field = source rows 0, 2, 4, …; bottom field
//! = rows 1, 3, 5, …) and emits them in the order indicated by
//! `interlace_mode` (1 = top-field-first, 2 = bottom-field-first).
//! Each picture uses the interlaced block scan (§7.2 Figure 5) instead
//! of the progressive one (Figure 4). The decoder reverses the
//! deinterleave on output. See [`encoder::encode_frame_interlaced`].
//!
//! ### Module layout
//!
//! * [`bitstream`] — MSB-first bit reader/writer.
//! * [`entropy`]   — RDD 36 Golomb-Rice / exp-Golomb combination codes
//!   plus the adaptive run/level/sign coefficient coder.
//! * [`alpha`]     — Run-length + diff-VLC alpha entropy coder
//!   (Tables 12-14) + alpha pixel-sample mapping (§7.5.2).
//! * [`dct`]       — Textbook f32 8x8 forward/inverse DCT.
//! * [`quant`]     — Default quant matrices, qScale table, block scans.
//! * [`slice`]     — Per-slice pack/unpack: per-component encode +
//!   inverse slice scan into natural-order blocks.
//! * [`frame`]     — Frame / picture / slice header layouts.
//! * [`decoder`]   — `Packet -> VideoFrame`
//!   (Yuv4(2|4)4P{,10Le,12Le,16Le} / Yuva4(2|4)4P{,10Le,12Le,16Le},
//!   optional 4th alpha plane).
//! * [`encoder`]   — `VideoFrame` -> `Packet`, with optional alpha.

pub mod alpha;
pub mod bitstream;
pub mod dct;
pub mod decoder;
pub mod encoder;
pub mod entropy;
pub mod frame;
pub mod quant;
pub mod slice;

use oxideav_core::{CodecCapabilities, CodecId, CodecTag, PixelFormat};
use oxideav_core::{CodecInfo, CodecRegistry, RuntimeContext};

/// Public codec id.
pub const CODEC_ID_STR: &str = "prores";

/// All six MP4 / MOV `VisualSampleEntry` FourCCs that identify a
/// ProRes bitstream. Canonical lower-case spelling as defined by
/// Apple's ProRes white paper (April 2022):
///
/// | fourcc | profile                                     |
/// |--------|---------------------------------------------|
/// | apco   | Apple ProRes 422 Proxy                      |
/// | apcs   | Apple ProRes 422 LT                         |
/// | apcn   | Apple ProRes 422 (Standard)                 |
/// | apch   | Apple ProRes 422 HQ                         |
/// | ap4h   | Apple ProRes 4444                           |
/// | ap4x   | Apple ProRes 4444 XQ                        |
pub const PRORES_FOURCCS: [&[u8; 4]; 6] = [b"apco", b"apcs", b"apcn", b"apch", b"ap4h", b"ap4x"];

/// MP4 / MOV `VisualSampleEntry` FourCCs that identify an Apple
/// **ProRes RAW** bitstream. ProRes RAW is a *separate* Apple format
/// that wraps single-plane Bayer/CFA sensor data; it is NOT one of the
/// six RDD 36 YUV/RGB profiles this crate decodes and uses an
/// incompatible sample structure. It has **no public bitstream
/// specification**: no SMPTE-registered document covers it, and Apple's
/// only published ProRes RAW document is a marketing white paper with
/// no frame/slice syntax, entropy coding, transform, or quantisation
/// description — there is no normative source a decoder could be
/// implemented from. These FourCCs deliberately resolve to
/// neither a [`CodecId`] nor a [`frame::Profile`] here —
/// [`is_prores_raw_fourcc`] lets a caller tell "ProRes RAW, which we
/// don't decode" apart from "not ProRes at all".
///
/// | fourcc | profile          |
/// |--------|------------------|
/// | aprn   | Apple ProRes RAW |
/// | aprh   | Apple ProRes RAW HQ |
pub const PRORES_RAW_FOURCCS: [&[u8; 4]; 2] = [b"aprn", b"aprh"];

/// Returns `true` if `fourcc` (case-insensitive) is an Apple ProRes RAW
/// `VisualSampleEntry` FourCC (`aprn` / `aprh`).
///
/// ProRes RAW is out of scope for this crate (see [`PRORES_RAW_FOURCCS`]).
/// A demuxer/dispatcher that sees a ProRes RAW track should surface a
/// clear unsupported-format error rather than route it to the standard
/// ProRes decoder, whose bitstream layout is incompatible.
pub fn is_prores_raw_fourcc(fourcc: &[u8; 4]) -> bool {
    let mut upper = [0u8; 4];
    for i in 0..4 {
        upper[i] = fourcc[i].to_ascii_uppercase();
    }
    matches!(&upper, b"APRN" | b"APRH")
}

/// Returns `Some(CodecId::new("prores"))` if `fourcc` (case-insensitive)
/// is one of the six ProRes MP4/MOV `VisualSampleEntry` FourCCs.
pub fn codec_id_for_fourcc(fourcc: &[u8; 4]) -> Option<CodecId> {
    let mut upper = [0u8; 4];
    for i in 0..4 {
        upper[i] = fourcc[i].to_ascii_uppercase();
    }
    match &upper {
        b"APCO" | b"APCS" | b"APCN" | b"APCH" | b"AP4H" | b"AP4X" => {
            Some(CodecId::new(CODEC_ID_STR))
        }
        _ => None,
    }
}

/// Returns the matching [`frame::Profile`] for a given MP4/MOV FourCC.
pub fn profile_for_fourcc(fourcc: &[u8; 4]) -> Option<frame::Profile> {
    let mut upper = [0u8; 4];
    for i in 0..4 {
        upper[i] = fourcc[i].to_ascii_uppercase();
    }
    Some(match &upper {
        b"APCO" => frame::Profile::Proxy,
        b"APCS" => frame::Profile::Lt,
        b"APCN" => frame::Profile::Standard,
        b"APCH" => frame::Profile::Hq,
        b"AP4H" => frame::Profile::Prores4444,
        b"AP4X" => frame::Profile::Prores4444Xq,
        _ => return None,
    })
}

/// Returns the canonical (lowercase) MP4/MOV `VisualSampleEntry` FourCC for
/// a given [`frame::Profile`] — the exact inverse of [`profile_for_fourcc`].
///
/// This completes the FourCC routing surface at the crate root: where a
/// demuxer/dispatcher maps an on-wire FourCC to a profile via
/// [`profile_for_fourcc`], a muxer assembling a sample entry (QuickTime
/// `stsd` / MXF Picture Essence Descriptor) maps an encoder-selected
/// profile back to the FourCC it must write. The encoder picks a profile
/// from `(pixel_format, bit_rate)` via [`encoder::pick_profile`] (or an
/// explicit [`encoder::EncoderConfig::with_profile`] override); the FourCC
/// the wrapper carries is then `fourcc_for_profile(profile)`.
///
/// The six FourCCs are the same canonical-lowercase byte strings listed in
/// [`PRORES_FOURCCS`] (and returned by [`frame::Profile::fourcc`]); pass
/// the result through [`profile_for_fourcc`] — which is case-insensitive —
/// to round-trip back to the original profile.
pub fn fourcc_for_profile(profile: frame::Profile) -> &'static [u8; 4] {
    profile.fourcc()
}

/// Register the ProRes decoder + encoder for all six profiles
/// (422 Proxy/LT/Standard/HQ and 4444 / 4444 XQ).
pub fn register_codecs(reg: &mut CodecRegistry) {
    let caps = CodecCapabilities::video("prores_sw")
        .with_lossy(true)
        .with_intra_only(true)
        .with_pixel_format(PixelFormat::Yuv422P)
        .with_pixel_format(PixelFormat::Yuv444P)
        .with_pixel_format(PixelFormat::Yuv422P10Le)
        .with_pixel_format(PixelFormat::Yuv444P10Le)
        .with_pixel_format(PixelFormat::Yuv422P12Le)
        .with_pixel_format(PixelFormat::Yuv444P12Le)
        .with_pixel_format(PixelFormat::Yuv422P16Le)
        .with_pixel_format(PixelFormat::Yuv444P16Le)
        .with_pixel_format(PixelFormat::Yuva422P)
        .with_pixel_format(PixelFormat::Yuva444P)
        .with_pixel_format(PixelFormat::Yuva422P10Le)
        .with_pixel_format(PixelFormat::Yuva444P10Le)
        .with_pixel_format(PixelFormat::Yuva422P12Le)
        .with_pixel_format(PixelFormat::Yuva444P12Le)
        .with_pixel_format(PixelFormat::Yuva422P16Le)
        .with_pixel_format(PixelFormat::Yuva444P16Le);
    reg.register(
        CodecInfo::new(CodecId::new(CODEC_ID_STR))
            .capabilities(caps)
            .decoder(decoder::make_decoder)
            .encoder(encoder::make_encoder)
            .tags([
                CodecTag::fourcc(b"APCO"),
                CodecTag::fourcc(b"APCS"),
                CodecTag::fourcc(b"APCN"),
                CodecTag::fourcc(b"APCH"),
                CodecTag::fourcc(b"AP4H"),
                CodecTag::fourcc(b"AP4X"),
            ]),
    );
}

/// Unified registration entry point: install the ProRes codec
/// factories into the codec sub-registry of a [`RuntimeContext`].
///
/// This is the preferred entry point for new code — it matches the
/// convention every sibling crate now follows. Direct callers that need
/// only the codec sub-registry can keep using [`register_codecs`].
pub fn register(ctx: &mut RuntimeContext) {
    register_codecs(&mut ctx.codecs);
}

oxideav_core::register!("prores", register);

#[cfg(test)]
mod tests {
    use super::*;
    use oxideav_core::frame::VideoPlane;
    use oxideav_core::{CodecId, CodecParameters, Frame, MediaType, PixelFormat, VideoFrame};

    /// Build a 64x48 gradient in Yuv422P. Values are deliberately
    /// smooth so the codec's lossy path can hit reasonable PSNR at
    /// `quant_index = 4`.
    fn synthetic_gradient(width: u32, height: u32) -> VideoFrame {
        let w = width as usize;
        let h = height as usize;
        let cw = w / 2;
        let mut y = vec![0u8; w * h];
        let mut cb = vec![0u8; cw * h];
        let mut cr = vec![0u8; cw * h];
        for j in 0..h {
            for i in 0..w {
                y[j * w + i] = ((i + j) * 255 / (w + h)).min(255) as u8;
            }
        }
        for j in 0..h {
            for i in 0..cw {
                cb[j * cw + i] = (128 + ((i as i32 - cw as i32 / 2) * 2).clamp(-64, 64)) as u8;
                cr[j * cw + i] = (128 + ((j as i32 - h as i32 / 2) * 2).clamp(-64, 64)) as u8;
            }
        }
        let _ = width;
        let _ = height;
        VideoFrame {
            pts: Some(0),
            planes: vec![
                VideoPlane { stride: w, data: y },
                VideoPlane {
                    stride: cw,
                    data: cb,
                },
                VideoPlane {
                    stride: cw,
                    data: cr,
                },
            ],
        }
    }

    fn psnr(orig: &[u8], decoded: &[u8]) -> f64 {
        assert_eq!(orig.len(), decoded.len());
        let mut mse = 0.0f64;
        for (a, b) in orig.iter().zip(decoded.iter()) {
            let d = *a as f64 - *b as f64;
            mse += d * d;
        }
        mse /= orig.len() as f64;
        if mse == 0.0 {
            return 120.0;
        }
        10.0 * (255.0f64 * 255.0 / mse).log10()
    }

    #[test]
    fn rdd36_encoder_decoder_roundtrip_psnr() {
        let width = 64u32;
        let height = 48u32;
        let original = synthetic_gradient(width, height);

        let mut enc_params = CodecParameters::video(CodecId::new(CODEC_ID_STR));
        enc_params.media_type = MediaType::Video;
        enc_params.width = Some(width);
        enc_params.height = Some(height);
        enc_params.pixel_format = Some(PixelFormat::Yuv422P);

        let mut reg = oxideav_core::CodecRegistry::new();
        register_codecs(&mut reg);
        let mut encoder = reg.first_encoder(&enc_params).expect("make_encoder");
        encoder
            .send_frame(&Frame::Video(original.clone()))
            .expect("send_frame");
        let pkt = encoder.receive_packet().expect("receive_packet");

        let dec_params = enc_params.clone();
        let mut decoder = reg.first_decoder(&dec_params).expect("make_decoder");
        decoder.send_packet(&pkt).expect("send_packet");
        let frame = decoder.receive_frame().expect("receive_frame");
        let decoded = match frame {
            Frame::Video(v) => v,
            _ => panic!("expected video frame"),
        };

        assert_eq!(decoded.planes.len(), 3);
        for (i, (o, d)) in original
            .planes
            .iter()
            .zip(decoded.planes.iter())
            .enumerate()
        {
            assert_eq!(o.data.len(), d.data.len(), "plane {i} size mismatch");
            let p = psnr(&o.data, &d.data);
            assert!(p > 30.0, "plane {i} PSNR too low: {p:.2} dB (want > 30)");
            eprintln!("plane {i} PSNR = {p:.2} dB");
        }
    }

    #[test]
    fn registry_caps_advertise_every_supported_pixel_format() {
        // The advertised capability list is the discovery surface a
        // framework caller negotiates against — it must name every
        // format the factories accept: the 8 colour-only requests plus
        // the 8 alpha-typed ones (8/10/12/16-bit at 4:2:2 and 4:4:4).
        let mut reg = oxideav_core::CodecRegistry::new();
        register_codecs(&mut reg);
        let impls = reg.implementations(&CodecId::new(CODEC_ID_STR));
        assert_eq!(impls.len(), 1);
        let advertised = &impls[0].caps.accepted_pixel_formats;
        let expected = [
            PixelFormat::Yuv422P,
            PixelFormat::Yuv444P,
            PixelFormat::Yuv422P10Le,
            PixelFormat::Yuv444P10Le,
            PixelFormat::Yuv422P12Le,
            PixelFormat::Yuv444P12Le,
            PixelFormat::Yuv422P16Le,
            PixelFormat::Yuv444P16Le,
            PixelFormat::Yuva422P,
            PixelFormat::Yuva444P,
            PixelFormat::Yuva422P10Le,
            PixelFormat::Yuva444P10Le,
            PixelFormat::Yuva422P12Le,
            PixelFormat::Yuva444P12Le,
            PixelFormat::Yuva422P16Le,
            PixelFormat::Yuva444P16Le,
        ];
        assert_eq!(advertised.len(), expected.len());
        for pf in expected {
            assert!(
                advertised.contains(&pf),
                "capabilities must advertise {pf:?}"
            );
            // And every advertised format actually builds both factories.
            let mut p = CodecParameters::video(CodecId::new(CODEC_ID_STR));
            p.media_type = MediaType::Video;
            p.width = Some(64);
            p.height = Some(48);
            p.pixel_format = Some(pf);
            assert!(reg.first_decoder(&p).is_ok(), "decoder for {pf:?}");
            assert!(reg.first_encoder(&p).is_ok(), "encoder for {pf:?}");
        }
    }

    #[test]
    fn decoder_registered() {
        let mut reg = oxideav_core::CodecRegistry::new();
        register_codecs(&mut reg);
        assert!(reg.has_decoder(&CodecId::new(CODEC_ID_STR)));
        assert!(reg.has_encoder(&CodecId::new(CODEC_ID_STR)));
    }

    #[test]
    fn register_via_runtime_context_installs_codec_factory() {
        let mut ctx = oxideav_core::RuntimeContext::new();
        register(&mut ctx);
        assert!(ctx.codecs.has_decoder(&CodecId::new(CODEC_ID_STR)));
        assert!(ctx.codecs.has_encoder(&CodecId::new(CODEC_ID_STR)));
    }

    #[test]
    fn codec_id_for_fourcc_maps_all_six() {
        for fc in PRORES_FOURCCS {
            assert_eq!(codec_id_for_fourcc(fc), Some(CodecId::new(CODEC_ID_STR)));
        }
    }

    #[test]
    fn codec_id_for_fourcc_is_case_insensitive() {
        assert_eq!(
            codec_id_for_fourcc(b"APCH"),
            Some(CodecId::new(CODEC_ID_STR))
        );
        assert_eq!(
            codec_id_for_fourcc(b"apch"),
            Some(CodecId::new(CODEC_ID_STR))
        );
        assert_eq!(
            codec_id_for_fourcc(b"ApCh"),
            Some(CodecId::new(CODEC_ID_STR))
        );
    }

    #[test]
    fn codec_id_for_fourcc_rejects_non_prores() {
        assert_eq!(codec_id_for_fourcc(b"avc1"), None);
        assert_eq!(codec_id_for_fourcc(b"hvc1"), None);
        assert_eq!(codec_id_for_fourcc(b"mp4v"), None);
        assert_eq!(codec_id_for_fourcc(b"alac"), None);
        assert_eq!(codec_id_for_fourcc(b"av01"), None);
    }

    #[test]
    fn prores_raw_fourcc_does_not_resolve_to_standard_prores() {
        // ProRes RAW is out of scope: aprn/aprh must NOT map to the
        // standard prores codec id or any of the six RDD 36 profiles,
        // so a dispatcher never routes a RAW sample to this decoder.
        for fc in PRORES_RAW_FOURCCS {
            assert_eq!(codec_id_for_fourcc(fc), None, "raw fourcc {fc:?}");
            assert_eq!(profile_for_fourcc(fc), None, "raw fourcc {fc:?}");
        }
    }

    #[test]
    fn is_prores_raw_fourcc_detects_aprn_aprh_case_insensitive() {
        for fc in PRORES_RAW_FOURCCS {
            assert!(is_prores_raw_fourcc(fc), "lower {fc:?}");
            let mut up = *fc;
            up.make_ascii_uppercase();
            assert!(is_prores_raw_fourcc(&up), "upper {up:?}");
        }
        assert!(is_prores_raw_fourcc(b"ApRh"));
        // Standard ProRes FourCCs are not ProRes RAW.
        for fc in PRORES_FOURCCS {
            assert!(!is_prores_raw_fourcc(fc), "standard {fc:?}");
        }
        // Unrelated FourCCs are not ProRes RAW.
        assert!(!is_prores_raw_fourcc(b"avc1"));
        assert!(!is_prores_raw_fourcc(b"av01"));
        // A near-miss that shares the `apr` prefix but is not a defined
        // ProRes RAW tag must not be misclassified.
        assert!(!is_prores_raw_fourcc(b"aprx"));
    }

    #[test]
    fn decode_packet_rejects_prores_raw_marker_with_unsupported() {
        // A sample whose in-stream marker is `aprh` (ProRes RAW) must
        // produce a clear Unsupported error, distinct from the generic
        // "magic mismatch" Invalid error for non-ProRes bytes.
        let mut raw = Vec::new();
        raw.extend_from_slice(&16u32.to_be_bytes()); // frame_size
        raw.extend_from_slice(b"aprh"); // ProRes RAW in-stream marker
        raw.extend_from_slice(&[0u8; 8]); // padding to frame_size
        let err = decoder::decode_packet(&raw, None).expect_err("must reject ProRes RAW");
        let msg = err.to_string();
        assert!(
            msg.contains("ProRes RAW"),
            "error should name ProRes RAW, got: {msg}"
        );

        // Bytes that are neither ProRes nor ProRes RAW still get the
        // generic magic-mismatch error (and must not mention ProRes RAW).
        let mut other = Vec::new();
        other.extend_from_slice(&16u32.to_be_bytes());
        other.extend_from_slice(b"junk");
        other.extend_from_slice(&[0u8; 8]);
        let err2 = decoder::decode_packet(&other, None).expect_err("must reject non-ProRes");
        assert!(
            !err2.to_string().contains("ProRes RAW"),
            "non-ProRes bytes should not be reported as ProRes RAW"
        );
    }

    #[test]
    fn profile_for_fourcc_roundtrips_via_profile_fourcc() {
        for p in [
            frame::Profile::Proxy,
            frame::Profile::Lt,
            frame::Profile::Standard,
            frame::Profile::Hq,
            frame::Profile::Prores4444,
            frame::Profile::Prores4444Xq,
        ] {
            let fc = p.fourcc();
            assert_eq!(profile_for_fourcc(fc), Some(p), "fourcc roundtrip");
            let mut up = *fc;
            up.make_ascii_uppercase();
            assert_eq!(profile_for_fourcc(&up), Some(p));
        }
        assert_eq!(profile_for_fourcc(b"mp4v"), None);
    }

    #[test]
    fn fourcc_for_profile_inverts_profile_for_fourcc() {
        // Every profile maps to a FourCC that maps back to that profile —
        // `fourcc_for_profile` is the exact inverse of `profile_for_fourcc`.
        for p in [
            frame::Profile::Proxy,
            frame::Profile::Lt,
            frame::Profile::Standard,
            frame::Profile::Hq,
            frame::Profile::Prores4444,
            frame::Profile::Prores4444Xq,
        ] {
            let fc = fourcc_for_profile(p);
            // Canonical (lowercase) form, identical to `Profile::fourcc()`
            // and to the entries of `PRORES_FOURCCS`.
            assert_eq!(fc, p.fourcc(), "canonical fourcc for {p:?}");
            assert!(
                PRORES_FOURCCS.contains(&fc),
                "{fc:?} must be one of the six carriage FourCCs"
            );
            assert_eq!(
                profile_for_fourcc(fc),
                Some(p),
                "fourcc_for_profile -> profile_for_fourcc round-trip for {p:?}"
            );
        }
    }

    #[test]
    fn fourcc_for_profile_returns_lowercase_canonical_bytes() {
        // The six canonical FourCCs are the lowercase byte strings the MOV
        // sample entry / MXF descriptor carry (the corpus wrapper FourCCs).
        assert_eq!(fourcc_for_profile(frame::Profile::Proxy), b"apco");
        assert_eq!(fourcc_for_profile(frame::Profile::Lt), b"apcs");
        assert_eq!(fourcc_for_profile(frame::Profile::Standard), b"apcn");
        assert_eq!(fourcc_for_profile(frame::Profile::Hq), b"apch");
        assert_eq!(fourcc_for_profile(frame::Profile::Prores4444), b"ap4h");
        assert_eq!(fourcc_for_profile(frame::Profile::Prores4444Xq), b"ap4x");
    }

    #[test]
    fn registry_recognizes_prores_fourcc_tags() {
        use oxideav_core::stream::{CodecResolver, ProbeContext};
        use oxideav_core::CodecTag;
        let mut reg = oxideav_core::CodecRegistry::new();
        register_codecs(&mut reg);
        for fc in PRORES_FOURCCS {
            let tag = CodecTag::fourcc(fc);
            let ctx = ProbeContext::new(&tag);
            let id = reg.resolve_tag(&ctx).expect("resolve_tag");
            assert_eq!(id, CodecId::new(CODEC_ID_STR), "fourcc {fc:?}");
        }
    }

    fn synthetic_gradient_444(width: u32, height: u32) -> VideoFrame {
        let w = width as usize;
        let h = height as usize;
        let mut y = vec![0u8; w * h];
        let mut cb = vec![0u8; w * h];
        let mut cr = vec![0u8; w * h];
        for j in 0..h {
            for i in 0..w {
                y[j * w + i] = ((i + j) * 255 / (w + h)).min(255) as u8;
                cb[j * w + i] = (128 + ((i as i32 - w as i32 / 2) * 2).clamp(-64, 64)) as u8;
                cr[j * w + i] = (128 + ((j as i32 - h as i32 / 2) * 2).clamp(-64, 64)) as u8;
            }
        }
        VideoFrame {
            pts: Some(0),
            planes: vec![
                VideoPlane { stride: w, data: y },
                VideoPlane {
                    stride: w,
                    data: cb,
                },
                VideoPlane {
                    stride: w,
                    data: cr,
                },
            ],
        }
    }

    #[test]
    fn rdd36_encoder_decoder_roundtrip_psnr_4444() {
        let width = 64u32;
        let height = 48u32;
        let original = synthetic_gradient_444(width, height);

        let mut enc_params = CodecParameters::video(CodecId::new(CODEC_ID_STR));
        enc_params.media_type = MediaType::Video;
        enc_params.width = Some(width);
        enc_params.height = Some(height);
        enc_params.pixel_format = Some(PixelFormat::Yuv444P);

        let mut reg = oxideav_core::CodecRegistry::new();
        register_codecs(&mut reg);
        let mut encoder = reg.first_encoder(&enc_params).expect("make_encoder");
        encoder
            .send_frame(&Frame::Video(original.clone()))
            .expect("send_frame");
        let pkt = encoder.receive_packet().expect("receive_packet");

        let dec_params = enc_params.clone();
        let mut decoder = reg.first_decoder(&dec_params).expect("make_decoder");
        decoder.send_packet(&pkt).expect("send_packet");
        let frame = decoder.receive_frame().expect("receive_frame");
        let decoded = match frame {
            Frame::Video(v) => v,
            _ => panic!("expected video frame"),
        };

        assert_eq!(decoded.planes.len(), 3);
        for (i, (o, d)) in original
            .planes
            .iter()
            .zip(decoded.planes.iter())
            .enumerate()
        {
            assert_eq!(o.data.len(), d.data.len(), "plane {i} size mismatch");
            let p = psnr(&o.data, &d.data);
            assert!(
                p > 30.0,
                "4444 plane {i} PSNR too low: {p:.2} dB (want > 30)"
            );
            eprintln!("4444 plane {i} PSNR = {p:.2} dB");
        }
    }
}
