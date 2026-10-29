---
title: Documentation
description: Guides for the EXR Converter GUI, CLI, proxies, ProRes, and optional camera RAW.
---

Guides for running and integrating **EXR Converter**. Edit these files under
`docs/` in the repo. They are published at
[vfx.systems/docs/exr-converter](https://vfx.systems/docs/exr-converter), which
loads this folder. In the app, **Help → Documentation** opens that page.

EXR Converter is a desktop app and CLI for **video and OpenEXR** with
**OpenColorIO**, slate / burn-in / watermark overlays, and editing proxies with
a watch folder. Packaged builds ship for Linux, macOS, and Windows.

The app writes a **video file** or an **OpenEXR sequence**. PNG, JPEG, WebP,
and DPX can be sequence inputs on EXR → Video. They are not outputs.

## What it does

| Feature | What it does |
|---------|--------------|
| **Video → EXR** | Decode a movie through OCIO into an EXR sequence. Ingest only: no slate, burn-in, or watermark. CinemaDNG `.dng` sequences use the built-in reader. RED R3D / N-RAW (`.r3d`, `.nev`) and Blackmagic RAW (`.braw`) work when that SDK bridge is in the build. |
| **EXR → Video** | Turn an OpenEXR sequence, or CinemaDNG, DPX, PNG, JPEG, or WebP, into ProRes, DNxHR, CineForm, H.264, HEVC, or FFV1. Optional slate, burn-in, and visible text watermark in the same pass. |
| **Video → Proxy** | Editing proxies. Default is ProRes 422 Proxy at half size, in a `Proxy` folder next to each clip, with the same name so editors can relink. Proxies keep the source look, timecode, and audio. A watch folder converts new clips once they finish copying. [Details](./gui.md#video--proxy). |
| **Transcode** | Batch video → video (ProRes, DNxHR, H.264, HEVC, AV1). No color change by default; set a source and destination space for an OCIO transform. |
| **Invisible watermark** | Optional, off by default, on Video → Proxy, Transcode, and EXR → Video. Writes an [Adobe TrustMark](https://github.com/adobe/trustmark) mark of up to 8 ASCII characters into every frame. On EXR → Video the payload is a Preferences template (`{user}`, `{date}`, …). The encoder model downloads once on first use. It is not encryption, and heavy re-encoding, scaling, or cropping can remove it. [Details and limits](./gui.md#invisible-watermark). |
| **Color** | Bundled **ACES Studio Config v4** plus extra camera logs (Nikon N-Log, Fujifilm F-Log family, Leica L-Log, Sony S-Log / S-Log2, Canon Log, GoPro, Blackmagic Film Gen 4, OPPO O-Log, Apple Log 2, and more). Also `$OCIO`, a custom config, or a local Nuke config. |
| **ProRes** | Software ProRes is 10-bit on every profile (default **422 HQ**). macOS VideoToolbox is the hardware encoder. Experimental oxideav **4444 and XQ are 12-bit**; its 422 profiles decode as **10-bit**. [Details](./prores.md). |
| **Browsers and player** | Sequence and video browsers with list, grid, and preview. GPU OCIO on the playback path. Built-in player with a RAM cache and frame-accurate scrub. |
| **Quick Look (macOS)** | Spacebar previews and Finder thumbnails for EXR and DPX. Off by default; turn them on in **File → Quick Look…**. [Details](./quicklook.md). |

No subcommand opens the GUI. The CLI converts with `video2exr`, `exr2video`,
`proxy`, and `transcode`. See the [CLI reference](./cli.md).

## Download

Get the latest build from
[GitHub Releases](https://github.com/derek-rein/exr-converter/releases/latest):

| Platform | File |
|----------|------|
| macOS Apple Silicon | [exr_converter-macos-arm64.dmg](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-macos-arm64.dmg) |
| macOS Intel | [exr_converter-macos-x86_64.dmg](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-macos-x86_64.dmg) |
| Windows x64 | [exr_converter-windows-x86_64-setup.exe](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-windows-x86_64-setup.exe) |
| Linux x86_64 | [AppImage](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.AppImage), [tarball](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.tar.gz), or [RPM](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.rpm) (RHEL / Rocky 8 and newer). See [Linux](./linux.md). |

The Windows `.msix` and `.msixupload` files on a release are for the
Microsoft Store ([details](./msix.md)). For a direct install, use the setup
`.exe`.

Every release file has a Sigstore Cosign `.sigstore.json` bundle and a GitHub
build provenance attestation. The releases page lists the `cosign verify-blob`
and `gh attestation verify` commands.

macOS release builds (Apple Silicon and Intel) are signed with a Developer ID
Application certificate for **Medeu Global LLC**, then notarized and stapled.
The app opens normally. First launch may still show the standard "downloaded
from the internet" prompt.

From a source checkout (Python 3.13 and [uv](https://docs.astral.sh/uv/)):
`uv run python main.py`.

When you change CLI flags, GUI behavior, codecs, OCIO defaults, packaging that
users see, or integrations, update the matching guide here in the **same** PR
as the code (and [CHANGELOG.md](https://github.com/derek-rein/exr-converter/blob/main/CHANGELOG.md)). See
[AGENTS.md: Documentation](https://github.com/derek-rein/exr-converter/blob/main/AGENTS.md#documentation-required).

## Guides

| Guide | Topics |
|-------|--------|
| [CLI](./cli.md) | `video2exr`, `exr2video`, `proxy` (`--watch`, `--watermark`), `transcode`, GUI launch flags |
| [Linux](./linux.md) | AppImage, relocatable tarball, and RHEL / Rocky RPM |
| [GUI](./gui.md) | Tabs, Video → Proxy and the watch folder, Transcode, invisible watermark, browsers, player, overlays, preferences, post-convert |
| [Quick Look on macOS](./quicklook.md) | Spacebar previews and Finder icons for EXR, DPX, and optional stills |
| [ProRes and VideoToolbox](./prores.md) | Software vs Apple VideoToolbox vs oxideav, bit depths |
| [Nuke](./nuke.md) | Nuke menu integration |
| [R3D / N-RAW](./r3d.md) | Optional RED R3D SDK (proprietary; build and license) |
| [Blackmagic RAW](./braw.md) | Optional BRAW SDK (proprietary; Linear ACES AP0; Release CI feed) |
| [CinemaDNG](./cinemadng.md) | `.dng` frame sequences, DJI Inspire 3 as Linear P3-D60 + 1.4 stops |
| [Releasing](./releasing.md) | Pointer to maintainer release process |
| [Microsoft Store](./msix.md) | Partner Center .msixupload and self-distribution .msix |
| [12-bit ProRes (oxideav)](./plan-12bit-prores-oxideav.md) | Experimental RDD-36 ProRes. 4444 and XQ are 12-bit. 422 decodes as 10-bit |
