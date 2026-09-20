---
title: Blackmagic RAW support
weight: 41
description: Optional Blackmagic RAW SDK integration for .braw
---

**EXR Converter** can decode **Blackmagic RAW** (`.braw`) clips when built
against the official **Blackmagic RAW SDK**. Without the SDK bridge, `.braw`
is recognized in the UI but conversion fails with a clear “SDK missing”
message.

Related: [CLI](./cli.md) · [GUI](./gui.md) · [R3D / N-RAW](./r3d.md) ·
[AGENTS.md](../AGENTS.md)

---

## What you get

| Item | Behavior |
|------|----------|
| Formats | `.braw` |
| Decode | Official **OpenEXRTranscode** default: **Linear** gamma + **ACES AP0** gamut, post-3D LUT **disabled**, `RGBF32` |
| OCIO | Auto-detect source space prefers **ACES2065-1** (`lin_ap0`) on the bundled ACES Studio config |
| Output | Same video→EXR pipeline (EXR compression, scale ladder, frame range, workers) |
| GPU | **CPU only** in this first integration. CUDA / OpenCL / Metal can be added later. |
| Preview | Sequence player + video browser (half-res decode for scrub) |
| Thumbnails | Grid thumbs via eighth-res decode |
| Metadata | Clip + per-frame timecode written to EXR as ``exrconverter:braw:*`` attrs |

Scale maps to the SDK resolution ladder (full / half / quarter / eighth). Odd
scale factors may apply a small software resize after decode.

The SDK is **not** used as a camera-look / BMD Film pipeline. We force Linear +
ACES AP0 so the pixels match scene-referred ACES interchange. Clip sidecar
looks (BMD Film, Rec.709, 3D LUT) are **not** applied. If you need those looks,
transcode with DaVinci Resolve / the official sample `-m` path instead.

---

## License (read this)

The Blackmagic RAW SDK is **proprietary** (Blackmagic Design). It is **free to
download** from the [developer site](https://www.blackmagicdesign.com/developer/products/braw)
after accepting their license. Terms live in the **Developer License** that
ships with the SDK package — always verify the copy you downloaded.

Summary of constraints that affect this project (not a substitute for the legal text):

- Use the SDK to build applications that decode `.braw`.
- You may typically redistribute **runtime API libraries** in object form
  together with software built on the SDK (Developer License 1.1(d) in recent
  packages). Confirm that clause in *your* SDK.
- You must **not** redistribute the SDK package as a whole, headers, samples,
  or documentation. Users obtain the SDK from Blackmagic Design.
- Do **not** reverse-engineer the libraries or file format.

**Do not commit the Blackmagic RAW SDK tree into the public git repository.**
This repo only contains *our* bridge source (`native/braw/`) and Python glue
(`src/core/braw/`).

---

## Developer setup (local)

```bash
# Unpack the official Linux SDK (example slim layout):
#   ~/.braw-sdk/slim/Linux/Include/BlackmagicRawAPI.h
#   ~/.braw-sdk/slim/Linux/Libraries/libBlackmagicRawAPI.so

export BRAW_SDK_ROOT="$HOME/.braw-sdk/slim"
cd /path/to/exr-converter
make braw-bridge
# → build/braw/libbraw_bridge.so + build/braw/redistributable/
```

Discovery (first match wins):

| Location | Notes |
|----------|--------|
| `BRAW_SDK_ROOT` | Unpacked SDK root (`slim/` or `Linux/`-style) |
| `~/.braw-sdk/slim` or `~/.braw-sdk` | Local stash |
| `/usr/lib64/blackmagic/BlackmagicRAWSDK` | Conventional Linux install |
| `/usr/lib/blackmagic/BlackmagicRAWSDK` | Alternate FHS path |
| `/opt/blackmagic/BlackmagicRAWSDK` | Optional local drop |

Override at runtime:

| Env var | Meaning |
|---------|---------|
| `BRAW_SDK_ROOT` | Unpacked SDK root for **building** the bridge |
| `EXR_CONVERTER_BRAW_BRIDGE` | Path to `libbraw_bridge.*` (or its directory) |
| `EXR_CONVERTER_BRAW_LIBS` / `BRAW_SDK_LIBS` | Folder containing `libBlackmagicRawAPI.*` |

Convert:

```bash
uv run python main.py video2exr -i /path/clip.braw -o /tmp/exr_out \
  --src ACES2065-1 --dst ACEScg --frame-range 1
```

---

## Color (what the SDK returns)

The bridge sets clip processing attributes **before** decode, matching the
official `OpenEXRTranscode` sample **without** `-m`:

1. `blackmagicRawClipProcessingAttributeGamma` = `"Linear"`
2. `blackmagicRawClipProcessingAttributeGamut` = `"ACES AP0"`
3. `blackmagicRawClipProcessingAttributePost3DLUTMode` = `"Disabled"`
4. Resource format `blackmagicRawResourceFormatRGBF32` (interleaved RGB float)

On the bundled **ACES Studio Config v4**, that working space is **ACES2065-1**
(aliases: `ACES - ACES2065-1`, `lin_ap0`). Auto-detect tries those names in
order.

This is **linear scene-referred AP0**, not Blackmagic Design Film / Wide Gamut
log. Values can be above 1.0.

---

## Packaging layout (shipped)

```text
macOS:  EXR Converter.app/Contents/MacOS/braw/
Linux:  <dist>/braw/
Windows:<dist>/braw/
          libbraw_bridge.*
          libBlackmagicRawAPI.*  libDecoder*.*  libInstructionSetServices*.*
```

Only runtime dynamic libraries from the SDK `Libraries/` folder plus our
bridge. Never headers, samples, `profile.braw`, or SDK documentation.

Release CI does **not** currently fetch a private BRAW SDK (unlike R3D). Local
`make bundle` builds and installs the runtime when `BRAW_SDK_ROOT` is set.
macOS / Windows packaging is the same layout once those SDK trees are present;
only Linux has been smoke-tested in this integration.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `BRAW bridge library not found` | `make braw-bridge` / set `EXR_CONVERTER_BRAW_BRIDGE` |
| `Blackmagic RAW runtime libraries not found` | Set `EXR_CONVERTER_BRAW_LIBS` to the SDK `Libraries` folder |
| `CreateBlackmagicRawFactoryInstanceFromPath failed` | Wrong folder (must contain `libBlackmagicRawAPI.so` / `.dylib` / `.dll`) |
| Wrong colors | Use ACES2065-1 source — decode is Linear AP0, not BMD Film |
| `._….braw` in browser | macOS AppleDouble metadata — hidden from the video browser |
| GPU not used | Expected — this build forces the CPU pipeline |

Official sample clip (from the SDK package, not this repo): `profile.braw`.
