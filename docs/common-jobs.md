---
title: Common jobs
weight: 5
description: Short answers for the jobs people do most - EXR to ProRes, EXR to MP4, an ACEScg review file, and camera RAW to EXR
---

Short answers for the jobs people do most. Each one is the GUI path and the matching command. Examples use the installed
`exr_converter` command. On Windows, **Add EXR Converter to PATH** in the installer adds `exr-converter` (with a hyphen). On
macOS the binary is `/Applications/EXR Converter.app/Contents/MacOS/exr_converter`. Every flag is in the
[CLI reference](./cli.md).

## EXR sequence to ProRes on Mac

EXR Converter writes a ProRes `.mov` from an OpenEXR sequence on macOS.

1. Open the **EXR → Video** tab, or run the command below.
2. Choose the sequence folder or any frame in it. The default codec is software ProRes 422 HQ, 10-bit.
3. On a Mac, pick VideoToolbox ProRes, or pass `--codec prores_vt_hq`, for the hardware encoder. The default frame rate is 24.

```bash
exr_converter exr2video -i ./plate -o review.mov --fps 24
```

More on codecs and bit depths: [ProRes and VideoToolbox](./prores.md).

## EXR to MP4

The same **EXR → Video** path writes an MP4 when the codec is H.264 or HEVC.

1. Choose the OpenEXR sequence.
2. Set the codec to H.264 (`h264`). That family defaults to a `.mp4` file. The `hevc` key is 10-bit HEVC, also in a `.mp4`.
3. Convert. The H.264 example adds `--crf 18`. The default frame rate is 24.

```bash
exr_converter exr2video -i ./plate -o review.mp4 --codec h264 --fps 24
```

## ACEScg to Rec.709 review file

The default **EXR → Video** destination applies the ACES 2.0 Rec.709 viewing transform for an ACEScg source.

1. Set the source to ACEScg. If you skip that and the file has no color metadata, the app assumes the `scene_linear` role and names it in a warning.
2. Leave the destination on **Output - Rec.709**. On ACES Studio v4 that is the Rec.1886 Rec.709 display with the view **ACES 2.0 - SDR 100 nits (Rec.709)**.
3. Encode ProRes or H.264. For colorimetric sRGB with no view transform, set the destination to **sRGB Encoded Rec.709 (sRGB)**.

```bash
exr_converter exr2video -i ./plate -o review.mov --fps 24 --src ACEScg --dst "Output - Rec.709"
```

## Camera RAW to EXR

**Video → EXR** writes an OpenEXR sequence. Which camera RAW files open depends on the reader in that build.

1. CinemaDNG frame sequences use the built-in reader ([CinemaDNG](./cinemadng.md)). ProRes RAW `.mov` clips decode through FFmpeg to scene-linear ACES2065-1, with no SDK ([ProRes RAW](./prores-raw.md)).
2. Blackmagic RAW (`.braw`, [details](./braw.md)) and RED R3D or N-RAW (`.r3d`, `.nev`, [details](./r3d.md)) need that SDK bridge in the build. Without it, the command stops with a missing-SDK message.
3. The destination defaults to ACEScg. A BRAW source is ACES2065-1. R3D defaults toward Log3G10 REDWideGamutRGB. Some other camera RAW files need an optional plugin and are not opened as ordinary movies.

```bash
exr_converter video2exr -i clip.braw -o ./exr_out --src ACES2065-1 --dst ACEScg
```
