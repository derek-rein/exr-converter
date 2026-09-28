---
title: ProRes and VideoToolbox
weight: 18
description: Software vs Apple VideoToolbox vs experimental oxideav — bit depths and codec keys
---

**EXR → Video** can write ProRes three different ways. They are **not**
interchangeable: bit depth, platform, and encoder implementation all differ.
This page is the user-facing guide; CLI keys are listed in
[CLI — codecs](./cli.md#codecs-honest-bit-depths).

| Encoder | Where | Bit depth (this app) | When to use |
|---------|-------|----------------------|-------------|
| **Software** (`prores_ks`) | All platforms | **Always 10-bit**, including 4444 / XQ | Default, portable dailies (`prores` = 422 HQ) |
| **VideoToolbox** (`prores_vt_*`) | **macOS only** | 422 family **10-bit**; 4444 / XQ **~12-bit class** | Fast hardware encode; 12-bit-class 4444 on a Mac |
| **oxideav** (`prores_ox_*`) | All platforms when built | **4444 / XQ: true 12-bit.** 422 family: 12-bit internal, **10-bit** to other apps | Cross-platform 12-bit 4:4:4; experimental |

Default codec is always software **`prores`** (422 HQ, 10-bit) — including on
macOS. VideoToolbox is opt-in.

Related: [GUI codecs](./gui.md#codecs) · [oxideav design notes](./plan-12bit-prores-oxideav.md)

---

## Apple VideoToolbox (macOS)

**VideoToolbox** is Apple’s hardware video encode/decode framework on macOS.
EXR Converter reaches it through PyAV / FFmpeg’s `prores_videotoolbox`
encoder. That is **Apple’s ProRes encoder**, not the portable FFmpeg software
encoder (`prores_ks`).

What that means in practice:

- **macOS only.** Windows and Linux builds never list these presets. Passing
  `--codec prores_vt_hq` on those platforms is an error.
- **Faster** than software ProRes on Apple Silicon and Intel Macs with
  hardware encode.
- **422 profiles stay 10-bit** (`p210le`): Proxy, LT, 422, HQ. Same nominal
  depth as software ProRes.
- **4444 and XQ are ~12-bit class.** The app feeds a wide `ayuv64le`
  intermediate; Apple’s encoder keeps mid-tone steps that software
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

GUI: **EXR → Video → Codec → ProRes (VideoToolbox · macOS)** — nested submenu,
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
**4444** and **XQ**. Those files may *probe* as 12-bit after decode (FFmpeg
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
the codec list when the extension is not built. **Not Apple-certified** — do not
call it “Apple ProRes.”

**4444 and XQ are true 12-bit** to FFmpeg, Resolve, and QuickTime (`ap4h` /
`ap4x`, `yuv444p12le`).

**422 Proxy, LT, 422, and HQ are 12-bit inside the encoder and 10-bit to
everyone else.** RDD 36 has no bit-depth field for 422 profiles; depth is
implied by the fourcc (`apco` / `apcs` / `apcn` / `apch` = 10-bit). Apple
defines 12-bit only at 4:4:4. The writer still quantizes on a 12-bit lattice,
and a 10-bit decode returns the top 10 bits (correct pictures, two bits
unreachable). `ffprobe` reports `yuv422p10le` and `bits_per_raw_sample=10`.
That is expected. Use **`prores_ox_4444` or `prores_ox_xq`** when another
application must see 12-bit.

| Key | Profile | What other apps see |
|-----|---------|---------------------|
| `prores_ox_proxy` | 422 Proxy (`apco`) | 10-bit 4:2:2 (12-bit internal) |
| `prores_ox_lt` | 422 LT (`apcs`) | 10-bit 4:2:2 (12-bit internal) |
| `prores_ox_422` | 422 (`apcn`) | 10-bit 4:2:2 (12-bit internal) |
| `prores_ox_hq` | 422 HQ (`apch`) | 10-bit 4:2:2 (12-bit internal) |
| `prores_ox_4444` | 4444 (`ap4h`) | 12-bit 4:4:4 |
| `prores_ox_xq` | 4444 XQ (`ap4x`) | 12-bit 4:4:4 |

Implementation status and tests:
[plan-12bit-prores-oxideav.md](./plan-12bit-prores-oxideav.md).

The built-in player, browser thumbs, and Video→EXR decode these MOVs through
PyAV. FFmpeg may present oxideav frames as YUV with an RGB colorspace tag;
the app strips that before RGB convert so preview is not blank.

---

## Quick picks

| Goal | Pick |
|------|------|
| Portable 10-bit HQ (default) | `prores` |
| Fast macOS dailies, 10-bit HQ | `prores_vt_hq` |
| Fast macOS 4444 with ~12-bit precision | `prores_vt_4444` |
| Cross-platform true 12-bit 4:4:4 | `prores_ox_4444` (experimental) |
| 422 HQ with finer internal quantize (other apps still see 10-bit) | `prores_ox_hq` (experimental) |
