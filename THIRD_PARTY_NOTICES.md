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

## DNG technology

This product includes DNG technology under license by Adobe.

CinemaDNG frames are decoded by a small reader in this repository
(`native/cinemadng/cinemadng.c`). It reads uncompressed and lossless JPEG
Bayer frames. The Adobe DNG SDK is not compiled, linked, or shipped.

The DNG SDK license would allow public redistribution of that SDK with
notice retention. Commercial distribution of the SDK also includes an
indemnity in Adobe's favor. Those terms are why this app does not ship the
SDK. The sentence above is the notice for the DNG technology the reader
implements.

## ONNX Runtime

Inference uses the `onnxruntime` package (CPU execution provider only).

- Project: https://github.com/microsoft/onnxruntime
- License: MIT. Copyright Microsoft Corporation.

Release builds include the ONNX Runtime shared library inside the app. On
macOS that library is signed and notarized with the rest of the application
under Medeu Global LLC.
