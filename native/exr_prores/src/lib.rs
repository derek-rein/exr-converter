//! PyO3 bindings: oxideav-prores 12-bit encode + in-process MOV mux.
//!
//! Used by EXR Converter for experimental cross-platform true 12-bit
//! RDD-36 ProRes 4444 and 4444 XQ (`prores_ox_4444`, `prores_ox_xq`).
//! 422 profiles stay on FFmpeg `prores_ks` and VideoToolbox at 10-bit.
//! No subprocess — the extension links oxideav-prores and writes `.mov`
//! directly so Nuitka can ship it as a normal extension module.

mod color;
mod mov;

use std::path::PathBuf;
use std::sync::Mutex;

use numpy::PyReadonlyArray2;
use numpy::PyReadonlyArray3;
use oxideav_core::{
    CodecId, CodecParameters, Encoder, Frame, MediaType, PixelFormat, Rational, VideoFrame,
    VideoPlane,
};
use oxideav_prores::encoder::{make_encoder_with_config, EncoderConfig};
use oxideav_prores::frame::{frame_rate_code_from_rational, FrameMeta, Profile};
use oxideav_prores::{fourcc_for_profile, CODEC_ID_STR};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyModule;

use crate::color::rgb48_to_yuv444_p12_le;
use crate::mov::{NclcColor, ProResMovWriter, COMPRESSOR_NAME};

/// Extension / crate version string.
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// QuickTime compressor name written into every MOV sample description.
#[pyfunction]
fn compressor_name() -> String {
    String::from_utf8_lossy(COMPRESSOR_NAME).into_owned()
}

#[pyclass(name = "ProResMovWriter")]
struct PyProResMovWriter {
    inner: Mutex<WriterState>,
}

struct WriterState {
    encoder: Box<dyn Encoder>,
    mov: ProResMovWriter,
    width: u32,
    height: u32,
    frame_index: i64,
    finished: bool,
}

#[pymethods]
impl PyProResMovWriter {
    /// Create a progressive 12-bit ProRes 4444 MOV writer.
    ///
    /// *profile*: ``4444`` or ``xq`` (4:4:4). 422 profiles are not offered:
    /// Apple, Resolve, Premiere, and Nuke read 422 fourccs as 10-bit.
    /// *fps_num* / *fps_den*: frame rate as a rational (e.g. 24000/1001).
    /// *color_primaries* / *transfer_characteristic* / *matrix_coefficients*:
    /// H.273 codes written to both the MOV ``colr``/``nclc`` atom and every
    /// ProRes frame header. 2 means "unspecified" (use it for ACES / log
    /// spaces that have no code). The default matrix is BT.709 (1), which is
    /// the matrix the RGB conversion uses.
    #[new]
    #[pyo3(signature = (
        path,
        width,
        height,
        fps_num,
        fps_den,
        profile="4444",
        color_primaries=2,
        transfer_characteristic=2,
        matrix_coefficients=1,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        path: PathBuf,
        width: u32,
        height: u32,
        fps_num: u32,
        fps_den: u32,
        profile: &str,
        color_primaries: u8,
        transfer_characteristic: u8,
        matrix_coefficients: u8,
    ) -> PyResult<Self> {
        let profile = parse_profile(profile)?;
        let fourcc = *fourcc_for_profile(profile);

        let mut params = CodecParameters::video(CodecId::new(CODEC_ID_STR));
        params.media_type = MediaType::Video;
        params.width = Some(width);
        params.height = Some(height);
        params.pixel_format = Some(PixelFormat::Yuv444P12Le);
        params.frame_rate = Some(Rational::new(i64::from(fps_num), i64::from(fps_den)));

        // Frame-header colour fields must agree with the container tag:
        // FFmpeg copies them onto every decoded frame. Leaving them at 0
        // ("unknown") made decoders report an RGB matrix.
        let meta = FrameMeta {
            aspect_ratio_information: 0,
            frame_rate_code: frame_rate_code_from_rational(Rational::new(
                i64::from(fps_num),
                i64::from(fps_den),
            )),
            color_primaries,
            transfer_characteristic,
            matrix_coefficients,
        };
        let cfg = EncoderConfig::signature_for_profile(profile).with_meta(meta);
        let encoder = make_encoder_with_config(&params, cfg).map_err(ox_err)?;

        let color = NclcColor {
            primaries: u16::from(color_primaries),
            transfer: u16::from(transfer_characteristic),
            matrix: u16::from(matrix_coefficients),
        };
        let mov = ProResMovWriter::create(&path, width, height, fps_num, fps_den, fourcc, color)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;

        Ok(Self {
            inner: Mutex::new(WriterState {
                encoder,
                mov,
                width,
                height,
                frame_index: 0,
                finished: false,
            }),
        })
    }

    /// Encode one full-range RGB48 frame (H×W×3 ``uint16``) and append it.
    fn write_rgb48(&mut self, rgb: PyReadonlyArray3<'_, u16>) -> PyResult<()> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("writer lock poisoned"))?;
        if state.finished {
            return Err(PyRuntimeError::new_err("writer already closed"));
        }

        let arr = rgb.as_array();
        let shape = arr.shape();
        if shape.len() != 3 || shape[2] != 3 {
            return Err(PyValueError::new_err(
                "rgb48 frame must have shape (H, W, 3) uint16",
            ));
        }
        let height = shape[0];
        let width = shape[1];
        if width as u32 != state.width || height as u32 != state.height {
            return Err(PyValueError::new_err(format!(
                "frame size {}x{} does not match writer {}x{}",
                width, height, state.width, state.height
            )));
        }

        let flat: Vec<u16> = if let Some(slice) = arr.as_slice() {
            slice.to_vec()
        } else {
            arr.iter().copied().collect()
        };

        let (y, cb, cr) = rgb48_to_yuv444_p12_le(&flat, width, height);
        self.encode_planes(&mut state, width, &y, &cb, &cr)
    }

    /// Encode one planar YUV444P12Le frame (each plane H×W ``uint16``, low 12 bits).
    ///
    /// Used for bit-depth mid-bin tests that inject precision in YUV space
    /// (RGB→YUV already maps a 16-bit +32 mid-bin to only ~2 Y codes).
    fn write_yuv444_p12(
        &mut self,
        y: PyReadonlyArray2<'_, u16>,
        cb: PyReadonlyArray2<'_, u16>,
        cr: PyReadonlyArray2<'_, u16>,
    ) -> PyResult<()> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("writer lock poisoned"))?;
        if state.finished {
            return Err(PyRuntimeError::new_err("writer already closed"));
        }

        let y_a = y.as_array();
        let cb_a = cb.as_array();
        let cr_a = cr.as_array();
        let (height, width) = (y_a.shape()[0], y_a.shape()[1]);
        if cb_a.shape() != [height, width] || cr_a.shape() != [height, width] {
            return Err(PyValueError::new_err(
                "Y/Cb/Cr planes must share the same (H, W) shape",
            ));
        }
        if width as u32 != state.width || height as u32 != state.height {
            return Err(PyValueError::new_err(format!(
                "frame size {}x{} does not match writer {}x{}",
                width, height, state.width, state.height
            )));
        }

        let pack = |plane: numpy::ndarray::ArrayView2<'_, u16>| -> Vec<u8> {
            let mut out = Vec::with_capacity(width * height * 2);
            for v in plane.iter() {
                let clipped = (*v).min(4095);
                out.extend_from_slice(&clipped.to_le_bytes());
            }
            out
        };
        let y_b = pack(y_a);
        let cb_b = pack(cb_a);
        let cr_b = pack(cr_a);
        self.encode_planes(&mut state, width, &y_b, &cb_b, &cr_b)
    }

    /// Flush encoder and finalise the MOV (moov atom).
    fn close(&mut self) -> PyResult<()> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("writer lock poisoned"))?;
        if state.finished {
            return Ok(());
        }
        let _ = state.encoder.flush();
        loop {
            match state.encoder.receive_packet() {
                Ok(pkt) => {
                    state
                        .mov
                        .write_sample(&pkt.data)
                        .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
                }
                Err(oxideav_core::Error::NeedMore) | Err(oxideav_core::Error::Eof) => break,
                Err(_) => break,
            }
        }
        state
            .mov
            .finish()
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        state.finished = true;
        Ok(())
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(
        &mut self,
        _exc_type: Option<&Bound<'_, PyAny>>,
        _exc: Option<&Bound<'_, PyAny>>,
        _tb: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<bool> {
        self.close()?;
        Ok(false)
    }
}

impl PyProResMovWriter {
    fn encode_planes(
        &self,
        state: &mut WriterState,
        width: usize,
        y: &[u8],
        cb: &[u8],
        cr: &[u8],
    ) -> PyResult<()> {
        let y_stride = width * 2;
        let c_stride = width * 2;
        let video = VideoFrame {
            pts: Some(state.frame_index),
            planes: vec![
                VideoPlane {
                    stride: y_stride,
                    data: y.to_vec(),
                },
                VideoPlane {
                    stride: c_stride,
                    data: cb.to_vec(),
                },
                VideoPlane {
                    stride: c_stride,
                    data: cr.to_vec(),
                },
            ],
        };

        state
            .encoder
            .send_frame(&Frame::Video(video))
            .map_err(ox_err)?;

        loop {
            match state.encoder.receive_packet() {
                Ok(pkt) => {
                    state
                        .mov
                        .write_sample(&pkt.data)
                        .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
                }
                Err(oxideav_core::Error::NeedMore) | Err(oxideav_core::Error::Eof) => break,
                Err(e) => return Err(ox_err(e)),
            }
        }

        state.frame_index += 1;
        Ok(())
    }
}

fn parse_profile(s: &str) -> PyResult<Profile> {
    match s.trim().to_ascii_lowercase().as_str() {
        "4444" | "ap4h" | "prores_4444" | "prores_ox_4444" => Ok(Profile::Prores4444),
        "xq" | "4444xq" | "4444_xq" | "ap4x" | "prores_xq" | "prores_ox_xq" => {
            Ok(Profile::Prores4444Xq)
        }
        other => Err(PyValueError::new_err(format!(
            "unknown oxideav ProRes profile {other:?} \
             (expected 4444 or xq; 422 profiles are 10-bit via prores_ks or VideoToolbox)"
        ))),
    }
}

fn ox_err(e: oxideav_core::Error) -> PyErr {
    PyRuntimeError::new_err(e.to_string())
}

#[pymodule]
fn exr_prores(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(compressor_name, m)?)?;
    m.add_class::<PyProResMovWriter>()?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
