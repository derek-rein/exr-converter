# Third-party notices

## Adobe TrustMark

Invisible watermarks use TrustMark, variant Q.

- Project: https://github.com/adobe/trustmark
- Paper: TrustMark: Universal Watermarking for Arbitrary Resolution Images (arXiv:2311.18297)
- Models: https://cai-watermark.adobe.net/watermarking/trustmark-models/
- License: MIT. Copyright 2023 Adobe.

EXR Converter downloads `encoder_Q.onnx` on first use and checks its SHA256.
It does not ship `decoder_Q.onnx`. The helper `scripts/trustmark_detect.py`
downloads the decoder when you ask it to read a mark.

The 100-bit payload uses TrustMark's BCH_5 schema and 7-bit text mode so the
official TrustMark decoder can read it. The BCH math in this repository is an
independent implementation of that schema (GF(2^7), primitive polynomial 137,
t = 5). It is not a copy of the Linux kernel BCH library.

## ONNX Runtime

Inference uses the `onnxruntime` package (CPU execution provider only).

- Project: https://github.com/microsoft/onnxruntime
- License: MIT. Copyright Microsoft Corporation.

Release builds include the ONNX Runtime shared library inside the app. On
macOS that library is signed and notarized with the rest of the application
under Medeu Global LLC.

## oxideav-prores

The experimental cross-platform ProRes writer (`prores_ox_*` codecs) uses the
`oxideav-prores` crate, version 0.1.1, built into the `exr_prores` extension.

- Project: https://github.com/OxideAV/oxideav-prores
- License: MIT. Copyright 2026 Karpelès Lab Inc.

A patched copy of the crate source lives in
`native/exr_prores/vendor/oxideav-prores`. The patch changes the 4:4:4 chroma
block order and is described in `EXR_CONVERTER_PATCH.md` in that folder. The
crate's `LICENSE` file is kept alongside the source.
