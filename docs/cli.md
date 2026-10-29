---
title: CLI reference
weight: 10
description: video2exr, exr2video, proxy, transcode, and GUI launch flags
---

Command-line interface for **video and OpenEXR** conversion with **OpenColorIO**.
The same entry point launches the **GUI** when no convert subcommand is given.

**Outputs:** `video2exr` writes an OpenEXR sequence. `exr2video`, `proxy`, and
`transcode` write a video file. PNG, JPEG, WebP, and DPX are **inputs** for `exr2video`
(image sequences). The app does not write PNG or other still-image output.

Slate, burn-in, the visible text watermark, and post-convert toggles are
**GUI-only**. See [GUI](./gui.md). The invisible proxy watermark is available
from the CLI as `proxy --watermark`, `transcode --watermark`, and
`exr2video --watermark`.

```bash
# From a source checkout
uv run python main.py --help
uv run python main.py video2exr --help
uv run python main.py exr2video --help

# Packaged binary (examples)
exr_converter --help
exr-converter --help
"/Applications/EXR Converter.app/Contents/MacOS/exr_converter" --help
```

On Linux, `exr-converter` is the launcher in the tarball and the RPM. Install options are in [Linux](./linux.md).

Related: [Linux](./linux.md) · [GUI](./gui.md) · [ProRes and VideoToolbox](./prores.md) · [Nuke integration](./nuke.md) · [README](../README.md)

---

## Modes

| Invocation | What it does |
|------------|----------------|
| `main.py` *(no subcommand)* | Open the GUI |
| `main.py video2exr …` | CLI: video → OCIO → EXR sequence |
| `main.py exr2video …` | CLI: image sequence (EXR / PNG / JPG / …) → OCIO → video |
| `main.py proxy …` | CLI: video → editing proxies, as a batch or a `--watch` folder daemon |
| `main.py transcode …` | CLI: video → video (ProRes, DNxHR, H.264, HEVC, AV1), with an optional OCIO transform |

Global flags (before the subcommand; convert flags may also appear after it):

| Flag | Meaning |
|------|---------|
| `-V` / `--version` | Print the app version (`pyproject.toml`) and exit |
| `--workers N` | CLI convert parallelism (`0` = auto, `1` = serial). May appear **before** the subcommand or **after** it (subcommand value wins). |
| `--smoke-test` | CI: launch GUI briefly, verify OCIO/ssl, exit |
| `--open PATH` | **GUI only:** open this media on launch |
| `--gui-ocio PATH` | **GUI only:** load this OCIO config on launch |
| `--mode auto\|video2exr\|exr2video\|proxy\|transcode` | **GUI only:** which tab (`auto` from `--open`; `proxy` = Video → Proxy and `transcode` = Transcode, where `--open` adds that path to the clip list) |

**Interrupt:** during CLI convert, **Ctrl-C** sets a cancel flag so the
pipeline stops cooperatively (pool work is cancelled) and the process exits
with status **130**.

---

## GUI launch (for shells & Nuke)

```bash
# Open GUI with an EXR sequence already loaded (EXR → Video tab)
uv run python main.py --open "/show/shot/plate.####.exr"

# Force Video → EXR tab + custom OCIO
uv run python main.py --open /path/clip.mov --mode video2exr \
  --gui-ocio /path/to/config.ocio

# Packaged app
open -a "EXR Converter" --args --open "/path/to/seq" --gui-ocio "$OCIO"
# or call the binary directly:
"/Applications/EXR Converter.app/Contents/MacOS/exr_converter" \
  --open "/path/to/seq" --gui-ocio "/path/to/config.ocio" --mode exr2video
```

OCIO resolution for the GUI when `--gui-ocio` is set: **custom file** source
(same as picking a config in the UI). Otherwise the app uses its saved preference
/ bundled ACES Studio / `$OCIO` as usual.

For a one-click Nuke menu that fills these in from a **Read** node, see
[nuke.md](./nuke.md).

---

## `video2exr`: video to EXR

```bash
uv run python main.py video2exr -i plate.mov
uv run python main.py video2exr -i plate.mov -o /tmp/exr_out --exr-compression zip
uv run python main.py video2exr -i plate.mov --frame-range 1-100 --workers 4
# Optional RED R3D / N-RAW (requires local R3D SDK bridge; see docs/r3d.md):
uv run python main.py video2exr -i clip.R3D -o /tmp/exr_out \
  --src "Log3G10 REDWideGamutRGB" --dst ACEScg
# Optional Blackmagic RAW (requires local BRAW SDK bridge; see docs/braw.md):
uv run python main.py video2exr -i clip.braw -o /tmp/exr_out \
  --src ACES2065-1 --dst ACEScg
# CinemaDNG sequence (DJI is Linear P3-D60 with +1.4 already in the pixels):
uv run python main.py video2exr -i /path/to/shot -o /tmp/exr_out \
  --src "Linear P3-D60" --dst ACEScg
```

| Option | Default | Notes |
|--------|---------|--------|
| `-i` / `--input` | *(required)* | Input video file (also `.r3d` / `.nev` when R3D support is built; `.braw` when BRAW support is built; a `.dng` frame or a folder of CinemaDNG frames) |
| `-o` / `--output-dir` | `<input_dir>/<stem>/` | Directory for EXR frames |
| `--ocio` | bundled / `$OCIO` | Config file path |
| `--src` | auto | Auto-detect from the video (stream color tags and codec ranking), then **`Output - Rec.709`**. `--help` calls that fallback Rec.709-ish. Names are alias-resolved. R3D/N-RAW defaults toward **Log3G10 REDWideGamutRGB**. BRAW defaults toward **ACES2065-1** (Linear ACES AP0). Not an OIIO still probe. |
| `--dst` | `ACEScg` / scene_linear | Destination scene space (role fallbacks; not media probing) |
| `--exr-compression` | `dwaa` | `none`, `rle`, `zip`, `zips`, `piz`, `pxr24`, `b44`, `b44a`, `dwaa`, `dwab` |
| `--dwa-level` | library | DWA level for `dwaa`/`dwab` (`0` = lossless) |
| `--zip-level` | library | ZIP level 1–9 for `zip`/`zips` |
| `--scale` | `1.0` | e.g. `0.5` half-res |
| `--padding` | `4` | Frame zero-pad width |
| `--start-frame` | `1001` | First output frame number |
| `--frame-range` | all | Nuke-style, e.g. `1-100`, `1-50x2` |
| `--deinterlace` | `auto` | `auto` / `on` / `off` |
| `--workers` | global | Parallel workers (`0` = auto, `1` = serial); overrides global `--workers` |

**Output naming:** `stem.####.exr` inside the output directory (pad width from
`--padding`). Default directory is `<input_parent>/<stem>/` (same idea as the GUI).

**RED R3D / N-RAW:** optional. Requires building the R3D bridge against the
official proprietary SDK. See [r3d.md](./r3d.md). Without it, `.r3d` / `.nev`
inputs error with a clear missing-SDK message.

**Blackmagic RAW:** optional. Requires building the BRAW bridge against the
official proprietary SDK. See [braw.md](./braw.md). Public Releases ship
the bridge when secret `BRAW_SDK_READ_TOKEN` is set. Without it, `.braw`
inputs error with a clear missing-SDK message. Decode is Linear ACES AP0
(Metal / CUDA / OpenCL when available, otherwise CPU).

**`.crm` camera RAW:** the video browser lists these files. Without a source
plugin, Inspect and `video2exr` report `Camera RAW plugin not installed`. The
file is never opened with PyAV. `proxy` and `transcode` do not accept it.
Release builds include the optional plugin when its private feed is
configured, and then ship that plugin's runtime notices inside the app.

---

## `exr2video`: image sequence to video

Primary path is **OpenEXR**. Also accepted as input: **CinemaDNG** (`.dng`),
**DPX**, and display stills **PNG**, **JPEG** (`.jpg` / `.jpeg`), **WebP**.
Mixed folders prefer EXR, then CinemaDNG, then DPX, then display stills.
The command always encodes a **video file**. DJI CinemaDNG is
`Linear P3-D60` (the +1.4 stops are already in the pixels). See
[cinemadng.md](./cinemadng.md).

```bash
uv run python main.py exr2video -i ./plate
uv run python main.py exr2video -i ./plate -o review.mov --fps 24
uv run python main.py exr2video -i ./plate/plate.1001.exr -o review.mov --fps 24
uv run python main.py exr2video -i ./png_seq -o review.mp4 --codec h264 --fps 24
uv run python main.py exr2video -i ./plate --codec h264 --crf 18
```

| Option | Default | Notes |
|--------|---------|--------|
| `-i` / `--input` | *(required)* | Sequence **directory** or any **existing frame** from the sequence (``name.####.ext`` or ``name_####.ext``; not a literal `####` path that does not exist on disk) |
| `-o` / `--output` | next to sequence | Video path; default extension follows codec family (below) |
| `--fps` | `24` | Frame rate. Accepts whole numbers, decimals (`23.976`, `29.97`), and exact rationals (`24000/1001`, `30000/1001`). A rational is written to the file exactly. |
| `--ocio` | bundled / `$OCIO` | Config file path |
| `--src` | format-aware | EXR/DPX: image metadata (EXR Converter tag, `colorInteropID`, then EXR `chromaticities`: Rec.709, ACES AP1, ACES AP0, P3-D65, and Rec.2020 primaries are recognised). With no usable metadata, the OCIO `scene_linear` role is assumed and a warning names that colour space. Pass `--src` when the warning appears. PNG/JPEG/WebP: sRGB-ish display space |
| `--dst` | `Output - Rec.709` | Display / delivery space. On ACES 2.0 configs that name remaps to **Rec.1886 Rec.709 - Display**; convert then applies the viewing-rule default view (**ACES 2.0 - SDR 100 nits (Rec.709)** for ACEScg), not Un-tone-mapped. Pass a utility encoding (``sRGB Encoded Rec.709 (sRGB)``) for colorimetric-only. |
| `--scale` | `1.0` | Output scale |
| `--codec` | `prores` | Codec key. See the ladder below (default = ProRes 422 HQ, **10-bit** software) |
| `--crf` | codec default | H.264 / HEVC quality |
| `--preset` | codec default | x264 / x265 preset name |
| `--frame-range` | all | e.g. `1001-1100` |
| `--workers` | global | Same meaning as `video2exr` |
| `--watermark TEXT` | off | Invisible TrustMark payload, up to 8 printable ASCII characters, on every frame after the display transform. Same model and cache as `proxy --watermark`. A bad payload exits with code `2` |
| `--watermark-strength N` | `1.0` | `0.1` to `2.0`. Used only with `--watermark` |

**Default output path** when `-o` is omitted: sibling of the sequence folder,
named after the folder, with a codec-appropriate extension:

| Codec family | Default extension |
|--------------|-------------------|
| ProRes / CineForm (default path) | `.mov` |
| `h264`, `hevc`, `hevc_8`, `hevc_12` | `.mp4` |
| `dnxhr_*` | `.mxf` |
| `ffv1`, `ffv1_12` | `.mkv` |

### Codecs (honest bit depths)

Keys match `exr2video --codec`. Software ProRes is **always 10-bit** encode
(`prores_ks`); do not treat 4444/XQ as true 12-bit on that path.

**VideoToolbox** (`prores_vt_*`) is **Apple’s hardware ProRes encoder** on
**macOS only.** It is faster than software, and 4444/XQ keep **~12-bit class**
precision that `prores_ks` does not. Experimental **oxideav** 4444/XQ
(`prores_ox_4444`, `prores_ox_xq`) are true 12-bit on every OS. oxideav
**422** profiles (`prores_ox_proxy` / `lt` / `422` / `hq`) code 12-bit
coefficients and present as **10-bit** (`apco` / `apcs` / `apcn` / `apch`)
to FFmpeg, Resolve, and QuickTime. Full explanation:
[ProRes and VideoToolbox](./prores.md).

| Key | Encode (this app) | Notes |
|-----|-------------------|--------|
| `prores_proxy` | 10-bit 4:2:2 | Software ProRes Proxy |
| `prores_lt` | 10-bit 4:2:2 | Software ProRes LT |
| `prores_422` | 10-bit 4:2:2 | Software ProRes 422 |
| `prores` | 10-bit 4:2:2 | **Default.** Software ProRes 422 HQ |
| `prores_4444` | 10-bit 4:4:4:4 | Software; not true 12-bit |
| `prores_xq` | 10-bit 4:4:4:4 | Software; not true 12-bit |
| `prores_vt_proxy` | 10-bit 4:2:2 | VideoToolbox 422 Proxy (macOS) |
| `prores_vt_lt` | 10-bit 4:2:2 | VideoToolbox 422 LT (macOS) |
| `prores_vt_422` | 10-bit 4:2:2 | VideoToolbox 422 (macOS) |
| `prores_vt_hq` | 10-bit 4:2:2 | VideoToolbox 422 HQ (macOS) |
| `prores_vt_4444` | ~12-bit 4:4:4:4 | VideoToolbox 4444 (macOS) |
| `prores_vt_xq` | ~12-bit 4:4:4:4 | VideoToolbox 4444 XQ (macOS) |
| `prores_ox_proxy` | 12-bit internal, **10-bit decode** · 4:2:2 | Experimental RDD-36 (`apco`). Other apps read 10-bit. Requires `make oxideav-prores` |
| `prores_ox_lt` | 12-bit internal, **10-bit decode** · 4:2:2 | Experimental RDD-36 (`apcs`). Other apps read 10-bit |
| `prores_ox_422` | 12-bit internal, **10-bit decode** · 4:2:2 | Experimental RDD-36 (`apcn`). Other apps read 10-bit |
| `prores_ox_hq` | 12-bit internal, **10-bit decode** · 4:2:2 | Experimental RDD-36 (`apch`). Other apps read 10-bit |
| `prores_ox_4444` | **12-bit** 4:4:4 | Experimental RDD-36 (`ap4h`). Other apps read 12-bit |
| `prores_ox_xq` | **12-bit** 4:4:4 | Experimental RDD-36 (`ap4x`). Other apps read 12-bit |
| `cineform` | 10-bit 4:2:2 | GoPro CineForm |
| `cineform_rgb` | 12-bit RGB | CineForm RGB |
| `dnxhr_lb` / `sq` / `hq` | 8-bit 4:2:2 | DNxHR |
| `dnxhr_hqx` | 10-bit 4:2:2 | DNxHR HQX |
| `dnxhr_444` | 10-bit 4:4:4 | DNxHR 444 |
| `h264` | 8-bit 4:2:0 | CRF / preset apply |
| `hevc` | 10-bit 4:2:0 | Default HEVC key |
| `hevc_8` | 8-bit 4:2:0 | |
| `hevc_12` | 12-bit 4:2:0 | |
| `ffv1` | 10-bit 4:4:4 | Lossless |
| `ffv1_12` | 12-bit 4:4:4 | Lossless |

List keys on your build. `--help` omits macOS-only VideoToolbox codecs on
other operating systems, and omits `prores_ox_*` when the oxideav extension
is not built:

```bash
uv run python main.py exr2video --help
```

User guide for the three ProRes encoders (software vs VideoToolbox vs oxideav):
[ProRes and VideoToolbox](./prores.md). Research notes for the oxideav path:
[plan-12bit-prores-oxideav.md](./plan-12bit-prores-oxideav.md). Experimental
presets `prores_ox_*` use in-process oxideav PyO3 bindings when `exr_prores` is
built (`make oxideav-prores`); they are omitted from `--codec` choices
otherwise.

---

## Color management notes

- **Transforms** are applied with **PyOpenColorIO** (not OIIO’s colorconvert).
- Omitted **`--src`**: **video2exr** uses stream TRC/primaries/codec candidates
  then **`Output - Rec.709`**; **exr2video** uses still metadata / format
  (scene-linear vs sRGB-ish). Omitted **`--dst`**: fixed defaults + roles
  (`ACEScg` / scene_linear for video→EXR; Rec.709 for EXR→video), not media
  probing. Names remap via `find_equivalent_space` when needed. **exr2video**
  dest names that are OCIO **displays** bake the viewing-rule default view
  (ACES 2.0 SDR Rec.709 on current Studio configs), matching Nuke/DJV, not
  Un-tone-mapped.
- Packaged app needs **OpenColorIO 2.5+** for the bundled ACES Studio v4 config.
  From source: `make ensure-ocio` if the runtime library is older than 2.5.

---

## `proxy`: video to editing proxies

Lightweight proxies for editing, like DaVinci Resolve's proxy generation. No
OCIO step: proxies keep the source look, timecode, and audio. Same engine as
the GUI's **Video → Proxy** tab.

```bash
# One clip → ./Proxy/A001C003.mov (ProRes 422 Proxy, half resolution)
uv run python main.py proxy -i A001C003.mov

# Every clip in a card folder, quarter-res H.264
uv run python main.py proxy -i ./A001_card --resolution quarter --codec h264

# Whole shoot day into one folder
uv run python main.py proxy -i ./day1 --recursive -o /proxies

# Watch folder daemon: proxy each new clip once it finishes copying (Ctrl-C stops)
uv run python main.py proxy --watch /Volumes/Ingest
uv run python main.py proxy --watch /Volumes/Ingest --recursive --existing --codec dnxhr_lb

# Invisible TrustMark watermark on every proxy frame (off unless --watermark is given)
uv run python main.py proxy -i ./A001_card --watermark ABCD1234 --watermark-strength 1.0
```

Flags match `proxy --help`. The `--codec` list in that help text is whatever
this operating system can encode, so `prores_vt_proxy` and `prores_vt_lt`
appear on macOS only.

| Flag | Default | Meaning |
|------|---------|---------|
| `-i`, `--input PATH …` | (none) | Clips and/or folders of clips. Give this and/or `--watch` |
| `--watch DIR` | (none) | Watch folder (repeatable). Runs after any `-i` batch, until Ctrl-C |
| `-o`, `--output-dir DIR` | `Proxy` folder next to each clip | One folder for all proxies. Cannot be a watched folder itself |
| `--codec KEY` | `prores_proxy` | `prores_proxy`, `prores_lt`, `prores_vt_proxy` / `prores_vt_lt` (macOS), `dnxhr_lb`, `dnxhr_sq`, `h264`, `hevc_8` |
| `--resolution KEY` | `half` | `half`, `quarter`, `eighth`, `original`, `1080`, `720`, `540` (fit height). Never upscales |
| `--suffix TEXT` | none | Added to proxy filenames. Empty keeps the source name for relinking |
| `--no-audio` | off | Drop audio (otherwise PCM in `.mov`, AAC in `.mp4`) |
| `--overwrite` | off | Re-make proxies that already exist (default: skip them) |
| `--recursive` | off | Include subfolders of `-i` folders and watch folders. `Proxy` folders are skipped |
| `--existing` | off | `--watch`: also proxy clips already in the folder at start |
| `--interval SECONDS` | `2` | `--watch`: time between folder scans |
| `--settle SECONDS` | `3` | `--watch`: how long a file's size must stay the same before it is converted |
| `--watermark TEXT` | off | Invisible TrustMark payload, up to 8 printable ASCII characters. Omitted means no watermark. The encoder model (`encoder_Q.onnx`) downloads once into a per-user cache and is checked with SHA256 |
| `--watermark-strength N` | `1.0` | Strength from `0.1` to `2.0`. Used only when `--watermark` is set |

The watermark is embedded in the resized RGB frame, on every frame, before the
codec. Proxies do not run an OCIO view transform, so that RGB image is the
picture the proxy shows. A payload that is too long or not printable ASCII,
or a `--watermark-strength` outside `0.1` to `2.0` (checked only with
`--watermark`), exits with code `2` before any clip is
converted. If the model cannot be downloaded, each clip fails with an error (a batch
exits with code `1`).

To read a mark back, use
`uv run python scripts/trustmark_detect.py PROXY.mov` (add `--samples N` to
read more frames; the default is 9). That helper downloads `decoder_Q.onnx`
(not shipped with the app), samples frames, and majority-votes the bits. It
prints the payload and exits `0`, or prints `detected: no` and exits `1`.
Cache locations, what survives re-encoding, and other limits:
[GUI: Invisible watermark](./gui.md#invisible-watermark).

The watch folder polls instead of relying on OS file events, so it works on
network shares and removable volumes. Proxies are written to a hidden
`.name.partial.ext` file and renamed when complete. RAW clips (`.r3d`, `.nev`,
`.braw`, ARRIRAW, `.crm`) are not supported by `proxy`.

---

## `transcode`: video to video

Batch transcodes for VFX and post: camera or editorial files into ProRes,
DNxHR, H.264, HEVC, or AV1. Same engine as the GUI's **Transcode** tab (and
the one `proxy` runs on). By default there is **no color change**: the
picture, timecode, and the source color tags pass through. Give `--src` and
`--dst` for an OCIO transform.

```bash
# One clip → ./A001C003_ProRes422HQ.mov (ProRes 422 HQ, source size, audio copied)
uv run python main.py transcode -i A001C003.mxf

# A folder into DNxHR HQX MXF in one delivery folder
uv run python main.py transcode -i ./day1 --recursive -o /deliveries --codec dnxhr_hqx --container mxf

# H.264 review copy, fit to 1080p, CRF 18, slow preset
uv run python main.py transcode -i plate.mov --codec h264 --crf 18 --preset slow --resolution 1080

# HEVC at a target bitrate, AAC audio
uv run python main.py transcode -i plate.mov --codec hevc --bitrate 25 --audio aac

# Custom width (height from the aspect ratio), conformed to 24 fps
uv run python main.py transcode -i clip.mov --codec prores_4444 --width 2048 --fps 24

# Log footage to a Rec.709 display with the bundled ACES config
uv run python main.py transcode -i log.mov --src "ARRI LogC3 (EI800)" --dst "Rec.1886 Rec.709 - Display"
```

| Flag | Default | Meaning |
|------|---------|---------|
| `-i`, `--input PATH …` | (required) | Clips and/or folders of clips |
| `-o`, `--output-dir DIR` | next to each clip | One folder for all outputs |
| `--codec KEY` | `prores` (ProRes 422 HQ) | See the codec table below. The list in `--help` is what this machine can encode |
| `--container EXT` | codec default | `mov`, `mxf`, `mp4`, or `mkv`, where the codec allows it (table below) |
| `--resolution KEY` | `original` | `original`, `75`, `half`, `quarter`, `eighth` (scale), `2160`, `1080`, `720`, `540` (fit to that height), or `custom`. Scale and fit never upscale. Sizes round to even numbers |
| `--width N` / `--height N` | `0` | Custom size (implies `--resolution custom`). Set one side to `0` to keep the source aspect ratio. Custom sizes can upscale |
| `--fps N` | `0` (keep) | Conform: the same frames play at the new rate, so the clip gets shorter or longer. Audio is dropped when the rate changes |
| `--audio MODE` | `copy` | `copy` (stream copy when the container can hold the source codec, else AAC in `.mp4` / `.mkv` and 24-bit PCM in `.mov` / `.mxf`), `aac`, `pcm` (24-bit), or `none` |
| `--crf N` | 18 (H.264), 20 (HEVC), 28 (AV1) | Constant quality for long-GOP codecs. `0` to `51` (AV1 `0` to `63`). Ignored for ProRes and DNxHR |
| `--bitrate MBPS` | off | Target bitrate in Mb/s for long-GOP codecs, instead of CRF |
| `--preset NAME` | `medium` | x264 / x265 speed preset. For AV1 it maps to the matching SVT-AV1 preset number |
| `--name PATTERN` | `<name>_<codec>` | File name without the extension. Tokens: `<name>` (source name), `<codec>` (for example `ProRes422HQ`, `DNxHR_HQX`, `H264`, `HEVC10`, `AV1`), `<width>`, `<height>`, `<fps>`, `<date>` |
| `--if-exists MODE` | `skip` | `skip`, `overwrite`, or `rename` (adds `_1`, `_2`, …). The output can never replace its own source |
| `--ocio PATH` | bundled / `$OCIO` | OCIO config for `--src` / `--dst` |
| `--src NAME` / `--dst NAME` | (none) | Both together turn on the OCIO transform. `--dst` may be a display, which uses that display's default view |
| `--watermark TEXT` | off | Invisible TrustMark payload, the same as `proxy --watermark` |
| `--watermark-strength N` | `1.0` | `0.1` to `2.0` |
| `--recursive` | off | Include subfolders of `-i` folders (`Proxy` folders are skipped) |

| Codec key | Codec | Containers (default first) | Bit depth |
|-----------|-------|----------------------------|-----------|
| `prores_proxy`, `prores_lt`, `prores_422`, `prores` (HQ) | Apple ProRes 422 family (software) | `.mov` | 10-bit 4:2:2 |
| `prores_4444`, `prores_xq` | Apple ProRes 4444 / 4444 XQ (software) | `.mov` | 10-bit 4:4:4:4 |
| `prores_vt_*` | VideoToolbox ProRes (macOS only) | `.mov` | as in [Codecs](#codecs-honest-bit-depths) |
| `dnxhr_lb`, `dnxhr_sq`, `dnxhr_hq` | DNxHR LB / SQ / HQ | `.mov`, `.mxf` | 8-bit 4:2:2 |
| `dnxhr_hqx` | DNxHR HQX | `.mov`, `.mxf` | 10-bit 4:2:2 |
| `dnxhr_444` | DNxHR 444 | `.mov`, `.mxf` | 10-bit 4:4:4 |
| `h264` | H.264 (x264) | `.mp4`, `.mov` | 8-bit 4:2:0 |
| `hevc_8`, `hevc`, `hevc_12` | H.265 / HEVC (x265), tagged `hvc1` | `.mp4`, `.mov` | 8 / 10 / 12-bit 4:2:0 |
| `av1` | AV1 (SVT-AV1), only when the FFmpeg build has it | `.mp4`, `.mkv` | 10-bit 4:2:0 |

Software ProRes is 10-bit for every profile, the same as `exr2video`. The
experimental oxideav ProRes keys are not offered for transcodes. MXF takes
the broadcast frame rates only (23.976, 24, 25, 29.97, 30, 50, 59.94, 60).

**Color.** Without `--src` / `--dst`, YUV sources go straight to the codec's
pixel format with no RGB round trip, and the source matrix, primaries, and
transfer tags are copied when the source has them. With an OCIO transform,
frames are converted to 16-bit RGB, transformed, and converted back with the
source's YUV matrix. Only that matrix tag is written, since the primaries and
transfer of the new picture are no longer the source's.

**Timecode and audio.** Source timecode is carried across. Drop-frame
timecode becomes non-drop when the output rate is not 29.97 or 59.94. The
first audio track is carried according to `--audio`.

**Camera RAW.** `.r3d` / `.nev` and `.braw` are accepted when the
[R3D](./r3d.md) or [BRAW](./braw.md) bridge is available. They decode to float
RGB in the camera space the SDK reports (R3D Log3G10 / REDWideGamutRGB, BRAW
Linear ACES AP0), have no audio, and usually want `--src` / `--dst`. ARRIRAW and
`.crm` are not supported.

Outputs are written to a hidden `.name.partial.ext` file and renamed when
complete. A clip that fails is reported and the batch continues (exit code
`1` at the end). Bad options (an unknown container for the codec, `--src`
without `--dst`, a bad watermark) exit with code `2` before anything is
converted. Ctrl-C exits with `130`.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Convert / argument / codec error (`proxy` / `transcode`: at least one clip failed, or no clips found) |
| `2` | `proxy`: no `-i` / `--watch`, a bad watch folder, or a bad `--watermark` payload / strength. `transcode`: invalid options (container, sizes, CRF, color spaces, watermark) |
| `2` | Smoke test: OCIO runtime &lt; 2.5 (GUI `--smoke-test`) |
| `3` | Smoke test: bundled config failed to load |
| `130` | `proxy` / `transcode`: Ctrl-C during the `-i` batch or while a clip is converting. Ctrl-C while `proxy --watch` is waiting exits `0` |

---

## See also

- `main.py --help`, `video2exr --help`, `exr2video --help`, `proxy --help`, `transcode --help` (always current)
- [GUI](./gui.md)
- [Nuke integration](./nuke.md)
- [Releasing](./releasing.md)
