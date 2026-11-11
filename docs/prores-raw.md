---
title: ProRes RAW
description: Convert Apple ProRes RAW clips (Atomos, Nikon, DJI and others) to scene-linear EXR.
---

# ProRes RAW

**Video → EXR** (`video2exr`) reads **Apple ProRes RAW** and **ProRes RAW HQ**
`.mov` clips, from external recorders and from cameras that record it
internally. The browser shows the codec as `ProRes RAW`, Inspect lists the
recorder, white balance, gain and levels, and thumbnails and the player work
as for any clip.

No camera SDK or licence is needed. Decoding uses FFmpeg's `prores_raw`
decoder, which ships inside the app.

## What you get

Frames come out as scene-linear **ACES2065-1** (ACES AP0, D60). The source
color space is picked automatically, so `video2exr -i clip.mov` converts
straight to your working space:

```bash
uv run python main.py video2exr -i A001C002.mov -o /tmp/exr_out
```

The develop steps:

1. FFmpeg decodes the clip's linearization curve to a **16-bit linear** RGGB
   mosaic (FFmpeg 9 or newer; older FFmpeg stops at 12 bits and keeps the
   camera's encoding, so the app refuses it).
2. Black and white level from the frame header. Nothing is clipped: noise
   under black stays negative and highlights above white stay above 1.
3. The as-shot white balance from the header.
4. Bilinear demosaic. A convert at half size or smaller bins each 2x2 Bayer
   quad into one pixel instead, which is cleaner and much faster.
5. The header's camera-to-XYZ matrix and gain, then Bradford D65 to D60 and
   XYZ to ACES AP0.
6. The header's recommended crop (the margins the recorder marks as not
   part of the picture).

Everything after the decoder is float32. The EXRs are half float, as for
every other source.

Each EXR carries `exrconverter:proresraw:*` attributes: codec, recorder,
white balance (CCT and red/blue gains), gain, black/white level and start
timecode.

## Checks

The convert log prints the sensor size, the crop, the levels, the white
balance and the gain. It warns when the header's color matrix is unusable
(the frame is then treated as Rec.709) or when it maps neutral grey more
than 0.02 in xy away from D65.

## Limits

- Not on Video → Proxy or Transcode. Convert to EXR first.
- RGGB sensors only. FFmpeg does not decode the other Bayer layouts yet.
- No exposure, ISO or white balance controls; the clip's as-shot values
  are used.
- The demosaic is bilinear, the same as CinemaDNG. It is fast but softer
  than a camera vendor's own debayer.
