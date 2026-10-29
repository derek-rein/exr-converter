---
title: 12-bit ProRes (oxideav)
weight: 90
description: Experimental RDD-36 ProRes 4444 and XQ via PyO3. 12-bit 4:4:4. 422 stays 10-bit
---

**Status:** Phase 0–2 landed (PyO3 bindings + app wiring + CI/Nuitka include)  
**Date:** 2026-08-21 (plan 2026-08-06; implementation revised from sidecar → bindings)  
**Related code:** `native/exr_prores/`, `src/core/oxideav_prores.py`, `src/core/convert.py`,
`src/core/constants.py`, `tests/test_oxideav_prores.py`

**12-bit** ProRes-compatible encode on Windows / Linux / macOS using the
pure-Rust **oxideav-prores** stack, linked **in-process** through a PyO3
extension (`exr_prores`) and included in **Nuitka** releases. No subprocess
sidecar. **4444 / XQ** are 12-bit to standard decoders. **422** is not
written here: Apple, Resolve, Premiere, Nuke, and `prores_ks` treat 422
Proxy, LT, 422, and HQ as 10-bit 4:2:2 (see [Labeling](#5-labeling)).

User-facing encoder comparison (software vs **Apple VideoToolbox** vs this
path): [ProRes and VideoToolbox](./prores.md).

---

## 1. Goals

| Goal | Notes |
|------|--------|
| True **12-bit** encode precision on 4444 / XQ | Mid-bin proof in YUV domain (see tests). 422 is 10-bit via `prores_ks` and VideoToolbox |
| **Cross-platform** | Win / Linux / macOS in release CI |
| **Nuitka standalone** | `--include-module=exr_prores` (normal extension module) |
| Honest UI | Experimental · RDD-36 · not Apple-certified |
| Keep FFmpeg for everything else | PyAV remains default for 10-bit ProRes, DNxHR, HEVC, … |

**Non-goals (v1):**

- Replacing all ProRes with oxideav  
- Apple-licensed / certified ProRes branding  
- ProRes RAW / alpha on oxideav presets  

---

## 2. Why not FFmpeg / why not a sidecar

FFmpeg `prores_ks` only accepts `*10le` pixel formats. True encode depth is
**10-bit** even when probe labels say 12. See historical mid-bin table in git
history / earlier revisions of this doc.

The original plan preferred a Rust **CLI sidecar** to defer PyO3 wheel
complexity. That was revised: a focused PyO3 module + minimal in-crate MOV
writer is cleaner for Nuitka (one extension next to other native deps, no
helper discovery / orphan processes) and matches how contributors already
build optional native code (`make oxideav-prores`).

`oxideav-mp4` 0.0.x cannot yet emit ProRes sample entries, so the extension
ships a small moov-at-end QuickTime writer (`native/exr_prores/src/mov.rs`)
instead of shelling out.

---

## 3. Architecture (implemented)

```
EXR Converter (Python / Nuitka)
  OIIO + OCIO → display RGB (rgb48le uint16)
  slate / burn-in / watermark (existing path)
        │
        ▼  in-process PyO3
  exr_prores.ProResMovWriter
    RGB48 → BT.709 limited YUV444P12 (4444 / XQ only)
    oxideav-prores encode (signature matrices)
    minimal MOV mux (ftyp + mdat + moov, colr/nclc: BT.709 matrix,
    primaries / transfer from the destination space)
        │
        ▼
  out.mov  (4444/XQ: ap4h / ap4x, probed 12-bit 4:4:4)
```

| Piece | Location |
|-------|----------|
| Rust crate | `native/exr_prores/` (maturin) |
| Python façade | `src/core/oxideav_prores.py` |
| Presets | `prores_ox_4444` / `prores_ox_xq` in `VIDEO_CODECS` |
| Convert branch | `run_exr_to_video` → `_e2v_oxideav` when key ∈ `OXIDEAV_PRORES_KEYS` |
| Build | `make oxideav-prores` → `scripts/build_oxideav_prores.py` |
| Release | Rust toolchain + `scripts/build_oxideav_prores.py` before Nuitka (all OS, including the Rocky Linux 8 build) |
| Tests | `tests/test_oxideav_prores.py` skips when the extension is not built |

Presets are **omitted** from `available_video_codecs()` when the extension is
not importable, so a Rust-less dev checkout still runs.

---

## 4. Acceptance (v1)

| Test | Pass criteria |
|------|----------------|
| YUV mid-bin | Encode Y=2048 vs Y=2080 → decode Δ **> 12** (`tests/test_oxideav_prores.py`) |
| Probe | 4444/XQ: PyAV opens `.mov` as `prores` / `ap4h` or `ap4x` / `yuv444p12le`. 422 family probes as 10-bit (`yuv422p10le`), which is expected |
| Missing extension | Presets hidden; FFmpeg codecs still work |
| Frozen path | Nuitka includes `exr_prores`; experimental presets complete end-to-end |

RGB48 mid-bin (±32 on a 10-bit lattice) is **not** a strong signal after
BT.709 limited conversion to 12-bit Y (~2 codes). Prefer the YUV-domain test.

---

## 5. Labeling

| Surface | Copy |
|---------|------|
| Software FFmpeg ProRes | **10-bit** (`prores_ks`) |
| VideoToolbox 4444/XQ | **12-bit class** (macOS) |
| oxideav 4444 / XQ | **12-bit · experimental · RDD-36 ProRes-compatible** |
| 422 Proxy / LT / 422 / HQ | **10-bit 4:2:2** (`prores_ks` or VideoToolbox). Not an oxideav preset |

Do not say “Apple ProRes certified.”

### Why 422 is not 12-bit

Apple, Resolve, Premiere, Nuke, and FFmpeg `prores_ks` encode 422 Proxy, LT,
422, and HQ as 10-bit 4:2:2. The fourcc (`apco` / `apcs` / `apcn` / `apch`)
is the bit depth. 12-bit is 4444 (`ap4h`) and 4444 XQ (`ap4x`) only, with
alpha only on those two profiles. An earlier oxideav writer put 12-bit
coefficients in a 422 fourcc. That is inside RDD 36, and decoders still
returned the top 10 bits. The app no longer offers that path.

```bash
exr_converter exr2video -i seq/frame.0001.exr -o out.mov --codec prores_ox_4444
ffprobe -show_entries stream=pix_fmt,bits_per_raw_sample,codec_tag_string out.mov
# yuv444p12le, 12, ap4h
```

---

## 6. Remaining work

- [ ] Broader NLE interop checklist (Resolve / Premiere / FCP) before dropping
  “experimental”
- [ ] Optional alpha (`Yuva444P12Le`) if product needs 4444+alpha on this path
- [ ] Pipe/raw protocol / speed pass if RGB→YUV or encode becomes hot
- [ ] Promote UI copy from experimental → stable when interop is solid

---

## 7. Decision log

| Date | Decision |
|------|----------|
| 2026-08-06 | Confirmed FFmpeg `prores_ks` cannot encode true 12-bit; labels fixed |
| 2026-08-06 | Chose oxideav for experimental cross-platform 12-bit ProRes-compatible output |
| 2026-08-21 | **Revised:** PyO3 bindings + in-process MOV writer (not subprocess sidecar); Nuitka `--include-module=exr_prores` |
| 2026-09-28 | **422 labels:** keep 12-bit coefficient coding; UI/docs say third-party decode is 10-bit (`apco`/`apcs`/`apcn`/`apch`). 12-bit delivery other apps read remains 4444/XQ |
| 2026-10-29 | **Drop 12-bit 422.** oxideav writes 4444 / XQ only. 422 Proxy / LT / 422 / HQ are 10-bit via `prores_ks` and VideoToolbox |

---

## 8. References

- `native/exr_prores/`: PyO3 extension  
- [oxideav-prores](https://crates.io/crates/oxideav-prores) · SMPTE **RDD 36**  
- [ASWF Encode ProRes](https://academysoftwarefoundation.github.io/EncodingGuidelines/EncodeProres.html)  
- FFmpeg Trac [#8054](https://trac.ffmpeg.org/ticket/8054)  
