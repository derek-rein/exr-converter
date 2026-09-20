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
| GPU | **macOS Metal** (then OpenCL if Metal is unavailable). **Windows / Linux:** CUDA, then OpenCL. CPU fallback if GPU setup fails or decoder libs are missing. Force CPU with `EXR_CONVERTER_BRAW_CPU=1`. |
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

## Where the full SDK lives (maintainers)

| Location | Purpose |
|----------|---------|
| `~/.braw-sdk/` or `~/code/braw-sdk-private/BlackmagicRAWSDK-6.0/` | Local full SDK unpack (headers + runtime Libraries) |
| Private GitHub repo [`derek-rein/braw-sdk-private`](https://github.com/derek-rein/braw-sdk-private) | README + package script only (**no SDK blobs in git**) |
| Private Release tag `sdk-6.0` asset `BlackmagicRAWSDK-6.0-full.tar.gz` | **CI build-time feed** for public `exr-converter` Releases |

Public **GitHub Release artifacts** for EXR Converter may include only:

- `libbraw_bridge.{dylib,so,dll}`
- Blackmagic `Libraries/` runtime dynamic libraries (and the macOS `.framework` binary)

…under a private app folder (`…/braw/`), never headers, samples, `profile.braw`,
or SDK documentation.

### Refresh the private CI feed

```bash
# After unpacking a new official SDK as BlackmagicRAWSDK-6.0/
cd ~/code/braw-sdk-private
./scripts/package_release.sh
# retags/uploads BlackmagicRAWSDK-6.0-full.tar.gz to release sdk-6.0
```

---

## Developer setup (local)

```bash
# Unpack the official SDK (example slim layout):
#   ~/.braw-sdk/slim/Linux/Include/BlackmagicRawAPI.h
#   ~/.braw-sdk/slim/Linux/Libraries/libBlackmagicRawAPI.so

export BRAW_SDK_ROOT="$HOME/.braw-sdk/slim"
cd /path/to/exr-converter
make braw-bridge
# → build/braw/libbraw_bridge.so + build/braw/redistributable/

# Or pull the same tarball CI uses (needs gh auth or BRAW_SDK_READ_TOKEN):
make braw-sdk-fetch   # → .braw-sdk/BlackmagicRAWSDK-6.0 (gitignored)
make braw-bridge
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
| `BRAW_SDK_READ_TOKEN` | Dedicated PAT that can download the private BRAW Release asset |
| `EXR_CONVERTER_BRAW_CPU` | Set to `1` to skip GPU decode (CPU only) |

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
bridge — including GPU decoder libs (`libDecoderCUDA` / `libDecoderOpenCL` /
`DecoderMetal`). Never headers, samples, `profile.braw`, or SDK documentation.

---

## CI / GitHub Release builds

The **Release** workflow (Nuitka multi-OS):

1. If secret **`BRAW_SDK_READ_TOKEN`** is set → download `sdk-6.0` /
   `BlackmagicRAWSDK-6.0-full.tar.gz` from the private repo.
2. Build `libbraw_bridge` (macOS / Linux / Windows + MSVC). On Windows the
   official SDK ships `BlackmagicRawAPI.idl`; the build runs `midl` to
   generate `BlackmagicRawAPI.h` into `build/braw/win_include/` (not into
   the SDK tree).
3. After Nuitka, copy **bridge + runtime Libraries only** into `…/braw/` next
   to the executable.
4. Refuse the build if headers / `.a` / `.lib` appear under that folder.

If the secret is missing, Release still publishes the app **without** BRAW
support (`.braw` convert reports SDK missing — same as R3D).

The bridge must match each SDK ABI: Linux `Variant` / `const char*`, macOS
`CFUUIDBytes` + `CFStringRef`, Windows COM `VARIANT` / `BSTR` / `BOOL` plus
`BlackmagicRawAPIDispatch.h` for `CreateBlackmagicRawFactoryInstanceFromPath`.
A tag-only re-run of Release rebuilds the tagged commit. To pick up a
bridge compile hotfix already on `main` without moving the tag:

```bash
gh workflow run Release --ref main -f tag=v0.10.0 -f source_ref=main
```

```bash
# Dedicated fine-grained PAT with read on derek-rein/braw-sdk-private Releases
gh secret set BRAW_SDK_READ_TOKEN --repo derek-rein/exr-converter
```

Local `make bundle` builds and installs the runtime when `BRAW_SDK_ROOT` is
set or after `make braw-sdk-fetch`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `BRAW bridge library not found` | `make braw-bridge` / set `EXR_CONVERTER_BRAW_BRIDGE` |
| `Blackmagic RAW runtime libraries not found` | Set `EXR_CONVERTER_BRAW_LIBS` to the SDK `Libraries` folder |
| CI skips BRAW | Secret `BRAW_SDK_READ_TOKEN` not set or cannot read private release |
| `CreateBlackmagicRawFactoryInstanceFromPath failed` | Wrong folder (must contain `libBlackmagicRawAPI.so` / `.dylib` / `.dll`) |
| Wrong colors | Use ACES2065-1 source — decode is Linear AP0, not BMD Film |
| `._….braw` in browser | macOS AppleDouble metadata — hidden from the video browser |
| Convert log says `CPU` on a GPU machine | GPU init failed (Metal / CUDA / OpenCL). Rebuild `make braw-bridge`. CUDA needs an NVIDIA driver + `libDecoderCUDA`; OpenCL needs a GPU ICD + `libDecoderOpenCL`; macOS needs `DecoderMetal` in the framework. Force CPU with `EXR_CONVERTER_BRAW_CPU=1` to compare. |

Official sample clip (from the SDK package, not this repo): `profile.braw`.
