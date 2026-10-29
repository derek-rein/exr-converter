---
title: ProRes and VideoToolbox
weight: 18
description: Software vs VideoToolbox vs experimental oxideav, bit depths and codec keys
---

**EXR → Video** can write ProRes three different ways. They are **not**
interchangeable: bit depth, platform, and encoder implementation all differ.
This page is the user-facing guide; CLI keys are listed in
[CLI: codecs](./cli.md#codecs-honest-bit-depths).

| Encoder | Where | Bit depth (this app) | When to use |
|---------|-------|----------------------|-------------|
| **Software** (`prores_ks`) | All platforms | **Always 10-bit**, including 4444 / XQ | Default, portable dailies (`prores` = 422 HQ) |
| **VideoToolbox** (`prores_vt_*`) | **macOS only** | 422 family **10-bit**; 4444 / XQ **~12-bit class** | Fast hardware encode; 12-bit-class 4444 on a Mac |
| **oxideav** (`prores_ox_4444`, `prores_ox_xq`) | All platforms when built | **4444 / XQ only, true 12-bit 4:4:4** | Cross-platform 12-bit 4:4:4; experimental |

Default codec is always software **`prores`** (422 HQ, 10-bit), including on
macOS. VideoToolbox is opt-in.

Related: [GUI codecs](./gui.md#codecs) · [oxideav design notes](./plan-12bit-prores-oxideav.md)

---

## VideoToolbox (macOS)

**VideoToolbox** is the hardware video encode and decode framework on macOS.
EXR Converter reaches it through PyAV / FFmpeg’s `prores_videotoolbox`
encoder. That is the hardware encoder, not the portable FFmpeg software
encoder (`prores_ks`).

What that means in practice:

- **macOS only.** Windows and Linux builds never list these presets. Passing
  `--codec prores_vt_hq` on those platforms is an error.
- **Faster** than software ProRes on Apple Silicon and Intel Macs with
  hardware encode.
- **422 profiles stay 10-bit** (`p210le`): Proxy, LT, 422, HQ. Same nominal
  depth as software ProRes.
- **4444 and XQ are ~12-bit class.** The app feeds a wide `ayuv64le`
  intermediate; the hardware encoder keeps mid-tone steps that software
  `prores_ks` 4444/XQ **quantizes away**. Labels say 12-bit because that is
  the precision we measured, not because FFmpeg software ProRes grew a 12-bit
  mode.
- Quality and rate control can differ slightly from `prores_ks` even at the
  same named profile (HQ vs HQ, etc.). Treat them as sibling presets, not
  bit-identical.

### Keys

| Key | Profile | Encode (this app) |
|-----|---------|-------------------|
| `prores_vt_proxy` | 422 Proxy | 10-bit 4:2:2 |
| `prores_vt_lt` | 422 LT | 10-bit 4:2:2 |
| `prores_vt_422` | 422 | 10-bit 4:2:2 |
| `prores_vt_hq` | 422 HQ | 10-bit 4:2:2 |
| `prores_vt_4444` | 4444 | ~12-bit 4:4:4:4 |
| `prores_vt_xq` | 4444 XQ | ~12-bit 4:4:4:4 |

GUI: **EXR → Video → Codec → ProRes (VideoToolbox · macOS)**, a nested submenu,
macOS only.

CLI (packaged app or source):

```bash
uv run python main.py exr2video -i ./plate -o review.mov --codec prores_vt_hq --fps 24
uv run python main.py exr2video -i ./plate -o review.mov --codec prores_vt_4444 --fps 24
```

If VideoToolbox is missing on a given Mac (rare; older OS / encoder not in the
bundled FFmpeg), convert fails with a codec error rather than silently falling
back to software ProRes.

---

## Software ProRes (FFmpeg)

Cross-platform FFmpeg `prores_ks`. Every profile encodes **10-bit**, including
**4444** and **XQ**. RGB is converted to BT.709 limited-range YCbCr
in the app (exact float math) and the stream is tagged BT.709 matrix. An alpha
plane is written only when the source frames have alpha. Those files may *probe* as 12-bit after decode (FFmpeg
presentation); mid-bin round-trips still collapse to a 10-bit lattice. Do not
treat software 4444/XQ as true 12-bit.

| Key | Profile |
|-----|---------|
| `prores_proxy` | 422 Proxy |
| `prores_lt` | 422 LT |
| `prores_422` | 422 |
| `prores` | 422 HQ (**default**) |
| `prores_4444` | 4444 (10-bit encode) |
| `prores_xq` | 4444 XQ (10-bit encode) |

On macOS, prefer **`prores_vt_4444` / `prores_vt_xq`** when 12-bit-class
precision matters. On other OSes, experimental **oxideav 4444 / XQ** is the
cross-platform 12-bit path other applications read as 12-bit. Software ProRes
stays 10-bit.

---

## Experimental oxideav (cross-platform RDD 36)

Pure-Rust **SMPTE RDD 36** encode via the optional `exr_prores` PyO3
extension (`make oxideav-prores`). Release binaries include it. Hidden from
the codec list when the extension is not built. Do not describe this path as
a certified or licensed encode.

**4444 and XQ are true 12-bit** to FFmpeg, Resolve, and QuickTime (`ap4h` /
`ap4x`, `yuv444p12le`). There is no alpha plane on this path. Software and
VideoToolbox 4444 / XQ write alpha only when the source has it.

**422 is not an oxideav profile.** Apple, Resolve, Premiere, Nuke, and
`prores_ks` treat 422 Proxy, LT, 422, and HQ as **10-bit 4:2:2** (`apco` /
`apcs` / `apcn` / `apch`). An earlier build wrote 12-bit coefficients into
those fourccs. Decoders still returned 10-bit, so the presets are gone. Use
the software or VideoToolbox 422 keys for 422, and **`prores_ox_4444` or
`prores_ox_xq`** when another application must see 12-bit.

| Key | Profile | What other apps see |
|-----|---------|---------------------|
| `prores_ox_4444` | 4444 (`ap4h`) | 12-bit 4:4:4 |
| `prores_ox_xq` | 4444 XQ (`ap4x`) | 12-bit 4:4:4 |

Implementation status and tests:
[plan-12bit-prores-oxideav.md](./plan-12bit-prores-oxideav.md).

The built-in player, browser thumbs, and Video→EXR decode these MOVs through
PyAV.

Files written by EXR Converter name their compressor `RDD 36 ProRes-compatible`
in the MOV sample description. The frame headers and the MOV `colr` atom carry
BT.709 matrix coefficients. Primaries and transfer follow the destination
colour space when it has an H.273 code (for example Rec.1886 Rec.709 is
BT.709 / BT.709, sRGB is BT.709 / sRGB). Log and ACES encodings such as
ACEScct have no H.273 code, so primaries and transfer are written as
unspecified. Older oxideav files (0.12.4 and earlier) tag everything BT.709
or leave the frame header colour fields at zero; the app still strips the
RGB colorspace tag FFmpeg derives from those before converting to RGB.

The app builds `oxideav-prores` 0.1.1 from a vendored, patched copy in
`native/exr_prores/vendor/oxideav-prores`. The patch writes and reads 4:4:4
chroma blocks in the column order FFmpeg and Apple decoders use. 0.12.4 and
earlier wrote them in luma order, which swapped the top-right and bottom-left
8x8 chroma blocks of every macroblock in 4444 and XQ files (blocky colour
errors when decoded by FFmpeg, Resolve, or QuickTime). Re-encode 4444 and XQ
files made with those versions.

---

## Quick picks

| Goal | Pick |
|------|------|
| Portable 10-bit HQ (default) | `prores` |
| Fast macOS dailies, 10-bit HQ | `prores_vt_hq` |
| Fast macOS 4444 with ~12-bit precision | `prores_vt_4444` |
| Cross-platform true 12-bit 4:4:4 | `prores_ox_4444` (experimental) |
| 10-bit 422 HQ that every NLE reads | `prores` (software) or `prores_vt_hq` (macOS) |
