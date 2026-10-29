---
title: EXR Converter
---

Desktop app and CLI for **video and OpenEXR** with **OpenColorIO**, slate / burn-in / watermark overlays, and editing proxies with a watch folder and an optional invisible watermark. Packaged builds ship for Linux, macOS, and Windows.

The app writes a **video file** or an **OpenEXR sequence**. PNG, JPEG, WebP, and DPX can be **inputs** on the EXR to video path. They are not outputs.

## Downloads

<div class="download-links">
  <a href="https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-windows-x86_64-setup.exe">
    <span class="dl-platform">Windows x64</span>
    <span class="dl-file">Installer (.exe)</span>
  </a>
  <a href="https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-macos-arm64.dmg">
    <span class="dl-platform">macOS Apple Silicon</span>
    <span class="dl-file">DMG</span>
  </a>
  <a href="https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-macos-x86_64.dmg">
    <span class="dl-platform">macOS Intel</span>
    <span class="dl-file">DMG</span>
  </a>
  <a href="https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.AppImage">
    <span class="dl-platform">Linux x86_64</span>
    <span class="dl-file">AppImage</span>
  </a>
  <a href="https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.tar.gz">
    <span class="dl-platform">Linux studio</span>
    <span class="dl-file">Tarball</span>
  </a>
  <a href="https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.rpm">
    <span class="dl-platform">RHEL / Rocky</span>
    <span class="dl-file">RPM</span>
  </a>
</div>

All release artifacts are [signed with Sigstore Cosign](https://docs.sigstore.dev/) and carry [GitHub build provenance attestations](https://docs.github.com/en/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds). The [releases page](https://github.com/derek-rein/exr-converter/releases) has the `cosign verify-blob` and `gh attestation verify` commands.

### Running on macOS

The macOS build is signed and notarized, so it opens normally. First launch may still show the standard "downloaded from the internet" prompt.

Release DMGs are signed with a Developer ID Application certificate for **Medeu Global LLC**, then notarized and stapled.

Release builds also include Quick Look and Finder thumbnail extensions for OpenEXR and DPX. Spacebar uses the app’s default OCIO display view, and numbered frames play when the folder can be read. Enable the extensions under **System Settings → General → Login Items & Extensions**. See [Quick Look on macOS](docs/quicklook/).

### Install by platform

**macOS** (Apple Silicon and Intel). Open the DMG and drag **EXR Converter** to Applications. The app is `EXR Converter.app`.

| Mac | Download |
| --- | --- |
| Apple Silicon | [exr_converter-macos-arm64.dmg](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-macos-arm64.dmg) |
| Intel | [exr_converter-macos-x86_64.dmg](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-macos-x86_64.dmg) |

```bash
open -a "EXR Converter"
"/Applications/EXR Converter.app/Contents/MacOS/exr_converter" --help
```

**Windows** (x64 installer). Download [exr_converter-windows-x86_64-setup.exe](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-windows-x86_64-setup.exe) and run it.

The installer adds a Start menu shortcut and can add a desktop shortcut. The program is `exr_converter.exe`.

Windows releases also list `exr_converter-windows-x86_64.msix` and `.msixupload` files. Those are for Microsoft Store submission ([details](docs/msix/)). For a direct install, use the setup `.exe` above.

**Linux** (x86_64, RHEL / Rocky / Alma 8 and newer). The release is built on Rocky Linux 8 (glibc 2.28).

Artists: [AppImage](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.AppImage). Studio deploy and Rez: [tarball](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.tar.gz). IT: [RPM](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.rpm) (`/opt/exr-converter`, `/usr/bin/exr-converter`). Details: [Linux](docs/linux/).

```bash
chmod +x exr_converter-linux-x86_64.AppImage
./exr_converter-linux-x86_64.AppImage
```

From a source checkout (Python 3.13 and [uv](https://docs.astral.sh/uv/)): `uv run python main.py`.

## Features

| Feature | What it does |
|---|---|
| **Video → EXR** | Decode a movie through OCIO into an EXR sequence. Ingest only: no slate, burn-in, or watermark. CinemaDNG `.dng` sequences use the built-in reader. Optional RED R3D / N-RAW (`.r3d`, `.nev`) and Blackmagic RAW (`.braw`) when that SDK bridge is in the build. |
| **EXR → Video** | Turn an OpenEXR sequence, or CinemaDNG, DPX, PNG, JPEG, or WebP, into ProRes, DNxHR, CineForm, H.264, HEVC, or FFV1. Optional slate, burn-in, and visible text watermark in the same pass. |
| **Video → Proxy** | Resolve-style editing proxies. Default is ProRes 422 Proxy at half size, in a `Proxy` folder next to each clip, with the same name so editors can relink. Proxies keep the source look (no color transform), timecode, and audio. Also ProRes LT, VideoToolbox ProRes on macOS, DNxHR LB / SQ, H.264, and HEVC, at half, quarter, or one-eighth size, or fit to 1080p / 720p / 540p. A watch folder converts new clips once they finish copying. Same job from the `proxy` CLI, including `proxy --watch`. [Details](docs/gui/#video--proxy). |
| **Invisible watermark** | Optional, off by default, Video → Proxy only. Writes an [Adobe TrustMark](https://github.com/adobe/trustmark) mark with up to 8 ASCII characters into every proxy frame. The encoder model downloads once on first use. A helper script reads the mark back. It is not encryption, and heavy re-encoding, scaling, or cropping can remove it. [Details and limits](docs/gui/#invisible-watermark). |
| **Color** | Bundled **ACES Studio Config v4**, plus extra camera logs: Nikon N-Log, Fujifilm F-Log / F-Log2 / F-Log2 C, Leica L-Log, Sony S-Log and S-Log2, Canon Log in Rec.709 and Rec.2020, KineLOG3, GoPro Protune / GP-Log / GP-Log2, Blackmagic Film Gen 4, OPPO O-Log, and Apple Log 2. Stock ACES spaces cover Apple Log, ARRI LogC3/LogC4, RED Log3G10, Sony S-Log3, and more. Also `$OCIO`, a custom config, or a local Nuke config. |
| **ProRes** | Software ProRes is 10-bit on every profile, including 4444 and XQ (default **422 HQ**). macOS VideoToolbox is the hardware encoder; 422 stays 10-bit, and 4444 / XQ are about 12-bit class. Experimental oxideav **4444 and XQ are true 12-bit**. oxideav 422 profiles are 12-bit inside the encoder and **10-bit** to FFmpeg, Resolve, and QuickTime. |
| **Browsers and player** | Sequence and video browsers with list, grid, and preview. GPU OCIO on the playback path. Built-in player for review, with a RAM cache and frame-accurate video scrub. |
| **Platforms** | Linux x86_64 AppImage, relocatable tarball, and RPM (RHEL / Rocky 8+, glibc 2.28), macOS Apple Silicon and Intel DMGs, and a Windows x64 installer (`.exe`). macOS release DMGs are signed with a Developer ID Application certificate for **Medeu Global LLC**, then notarized and stapled. The app opens normally. First launch may still show the standard "downloaded from the internet" prompt. |

## CLI

No subcommand opens the GUI. Convert with `video2exr`, `exr2video`, or `proxy`.

```bash
uv run python main.py
uv run python main.py video2exr -i clip.mov -o ./exr_out/
uv run python main.py exr2video -i ./plate -o review.mov --fps 24
uv run python main.py proxy -i ./A001_card
uv run python main.py proxy --watch /path/to/ingest
uv run python main.py proxy -i ./A001_card --watermark ABCD1234
```

`video2exr --help`, `exr2video --help`, and `proxy --help` list the flags for that build. VideoToolbox codec keys appear on macOS only. Full reference, codec keys, and bit depths: [CLI reference](docs/cli/).

## Documentation

| Guide | Description |
|---|---|
| [CLI reference](docs/cli/) | `video2exr`, `exr2video`, `proxy` (including `--watch` and `--watermark`), `transcode`, GUI launch flags |
| [Linux](docs/linux/) | AppImage, relocatable tarball, and RHEL / Rocky RPM |
| [GUI](docs/gui/) | Tabs, Video → Proxy, the watch folder, Transcode, the invisible watermark, browsers, player, overlays, preferences |
| [Quick Look on macOS](docs/quicklook/) | Spacebar previews and Finder icons for EXR, DPX, and optional stills |
| [ProRes and VideoToolbox](docs/prores/) | Software vs Apple VideoToolbox vs oxideav, with honest bit depths |
| [RED R3D / N-RAW](docs/r3d/) | Optional RED SDK for `.r3d` and `.nev` |
| [Blackmagic RAW](docs/braw/) | Optional BRAW SDK for `.braw` (Linear ACES AP0) |
| [CinemaDNG](docs/cinemadng/) | `.dng` frame sequences. DJI Inspire 3 is Linear P3-D60 with +1.4 stops |
| [Nuke integration](docs/nuke/) | Menu helpers to open a Read plus the session OCIO config |
| [12-bit ProRes notes](docs/plan-12bit-prores-oxideav/) | Experimental RDD-36 ProRes. 4444 and XQ are 12-bit. 422 decodes as 10-bit |
| [Microsoft Store](docs/msix/) | The Windows `.msixupload` and `.msix` packages. The setup `.exe` is the direct download |
| [Releasing](docs/releasing/) | How releases are tagged and published |

## Source

Development, issues, and the full README live on [github.com/derek-rein/exr-converter](https://github.com/derek-rein/exr-converter).
