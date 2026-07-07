# Integration test media

End-to-end conversion tests live in `tests/test_integration_conversions.py`.
They always run against tiny **synthetic** clips generated at test time, so CI
passes without any files in this folder.

To test real production media (ProRes, DNxHR, DWAA EXRs, etc.), drop files here
and register them in `manifest.json`.

## Layout

```
tests/fixtures/
  manifest.json          ← list your files here
  media/
    video/               ← .mov / .mp4 / .mxf clips
    exr/                 ← EXR sequences (plate.1001.exr, plate.1002.exr, …)
```

## Adding a video clip

1. Copy the file to `media/video/` (keep it small — a few seconds is enough).
2. Add an entry to `manifest.json`:

```json
{
  "videos": [
    {
      "id": "prores_sample",
      "path": "media/video/prores_sample.mov",
      "description": "ProRes 422 HQ review clip",
      "expected_frames": 48,
      "min_width": 1920,
      "min_height": 1080,
      "src": "sRGB Encoded Rec.709 (sRGB)",
      "dst": "ACEScg"
    }
  ]
}
```

## Adding an EXR sequence

1. Copy frames to `media/exr/` using a consistent pattern, e.g.
   `beauty.1001.exr`, `beauty.1002.exr`, …
2. Add an entry using `####` for the frame digits:

```json
{
  "exr_sequences": [
    {
      "id": "beauty_plate",
      "path": "media/exr/beauty.####.exr",
      "description": "ACEScg DWAA beauty pass",
      "frame_count": 24,
      "src": "ACEScg",
      "dst": "Rec.1886 Rec.709 - Display"
    }
  ]
}
```

## Large files

If clips are too big for plain git, use [Git LFS](https://git-lfs.com/) for
`tests/fixtures/media/**` and keep the manifest in regular git.

Tests skip manifest entries whose files are missing, so you can commit the
manifest before the media lands.
