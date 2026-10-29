---
title: CinemaDNG
description: Read CinemaDNG frame sequences, including DJI Inspire 3, into EXR or video.
---

# CinemaDNG

EXR Converter reads **CinemaDNG frame sequences** (`.dng`). A folder of frames,
or any one frame in that folder, is a sequence. The same files work on
**EXR → Video** (a video proxy) and on **Video → EXR** (`video2exr`).

This is a Bayer sequence reader. It is not a still-photo workflow, and it does
not add a separate stills browser.

## Color

DJI Inspire 3 and Zenmuse X9 frames (Make or Model contains `DJI` or `Zenmuse`)
are developed to **scene-linear P3-D60** with the **+1.4 stops** DJI recommends
for CinemaDNG. The pixels are tagged **Linear P3-D60**.

That exposure is `BaselineExposure` from the file, plus 1.4. If a clip's
`BaselineExposure` is already about 1.4, the image will look another 1.4 stops
hot. Compare a grey chip with Resolve set to P3-D60, Linear, and +1.4. If this
app is a stop and a half brighter, check `BaselineExposure` before adding gain
again in OCIO.

From Linear P3-D60, use the color spaces already in the bundled ACES Studio
config:

| Destination | What you get |
|-------------|----------------|
| `Linear D-Gamut` | Scene-linear DJI D-Gamut |
| `D-Log D-Gamut` | DJI D-Log in D-Gamut (`ocio:djilog_dgamut_scene`) |
| `ACEScg` or `ACES2065-1` | ACES working space |

The app does not add a second D-Log curve. `D-Log - Curve` in the config is the
existing one.

Other CinemaDNG sequences (not DJI) use the file's `ForwardMatrix` when it is
present, otherwise `ColorMatrix1` and `CalibrationIlluminant1`, into
**ACES2065-1**. Only `BaselineExposure` is applied, not the extra 1.4 stops.
If a non-DJI frame has neither matrix, the white-balanced camera RGB is tagged
ACES2065-1.

White balance uses `AsShotNeutral`. The demosaic is bilinear. Compare color on
a flat chip. Fine detail will not match Resolve's demosaic.

## What the reader accepts

| Supported | Not supported |
|-----------|----------------|
| Uncompressed Bayer (compression 1), 8 to 16 bit, including packed 10/12/14 | Lossy JPEG, JPEG XL, JPEG 2000 |
| Lossless JPEG Bayer (compression 7, SOF3, one component) | Previews that are already RGB |
| Strips or tiles, classic TIFF (not BigTIFF) | A single DNG still as a photo workflow |
| ActiveArea and DefaultCrop | Files larger than 512 MB |

Frame rate comes from the CinemaDNG `FrameRate` tag when it is present, otherwise 24.

## GUI

Drop a `.dng` frame or a folder of frames onto the window. It opens on
**EXR → Video**. The source color space is filled from the frame
(`Linear P3-D60` or `ACES2065-1`). Pick a destination such as `Linear D-Gamut`,
`D-Log D-Gamut`, or a display space, then encode a proxy.

The sequence browser lists `.dng` with the other image sequences. Mixed folders
prefer EXR, then CinemaDNG, then DPX.

## CLI

Proxy from a sequence (EXR → video). The `+1.4` is already in the pixels, so
do not add it again in the grade:

```bash
uv run python main.py exr2video -i /path/to/shot \
  --src "Linear P3-D60" --dst "D-Log D-Gamut" \
  -o /tmp/proxy.mov --codec prores_hq
```

EXR from the same sequence (`video2exr`). Pass the folder or the first frame.
`--src` can be omitted. The app probes the frame and selects Linear P3-D60 for
DJI, or ACES2065-1 otherwise.

```bash
uv run python main.py video2exr -i /path/to/shot/shot.0001.dng -o /tmp/exr_out \
  --src "Linear P3-D60" --dst ACEScg
```

To land in D-Gamut instead:

```bash
uv run python main.py video2exr -i /path/to/shot -o /tmp/exr_out \
  --src "Linear P3-D60" --dst "Linear D-Gamut"
```

Written EXRs carry `exrconverter:cinemadng:make`, `model`, `colorspace`, and the
exposure scale that was baked into the pixels.

## Build

The reader is one C file, `native/cinemadng/cinemadng.c`, linked only to the C
runtime. `make cinemadng`, `make test`, and `make bundle` build it.
`EXR_CONVERTER_CINEMADNG_LIB` can point at the shared library if it is not
under `build/cinemadng/` or next to the app.

Release builds copy that library next to the executable. On macOS it is signed
and notarized with the rest of the app.

This product includes DNG technology under license by Adobe. The Adobe DNG SDK
is not linked or shipped. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
