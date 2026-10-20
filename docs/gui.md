---
title: GUI
weight: 15
description: Tabs, Video to Proxy, browsers, player, overlays, and preferences
---

Desktop UI for **video ↔ OpenEXR** with **OpenColorIO**. Launch with no
subcommand:

```bash
uv run python main.py
# or packaged:
exr_converter
"/Applications/EXR Converter.app/Contents/MacOS/exr_converter"
```

Optional launch flags (`--open`, `--gui-ocio`, `--mode`) are documented in
[CLI: GUI launch](./cli.md#gui-launch-for-shells--nuke). Nuke can fill them in
from a Read. See [nuke.md](./nuke.md).

---

## Tabs

| Tab | Direction | Notes |
|-----|-----------|--------|
| **Video → EXR** | Decode video → OCIO → EXR sequence | **Ingest only.** Slate, burn-in, and watermark are never applied. Output is an EXR sequence, not a video or a still. The output field uses ``name.####.exr``; that basename is written (not forced to the video stem). Accepts common video containers plus optional **`.r3d` / `.nev`** when the [R3D SDK bridge](./r3d.md) is available (browser thumbs and player preview use low-res R3D decode; convert is full quality: **Metal GPU** on macOS, **CUDA / OpenCL** on Windows and Linux; camera and timecode metadata lands on the EXRs) and optional **`.braw`** when the [Blackmagic RAW bridge](./braw.md) is available (Linear ACES AP0 / `ACES2065-1`; **Metal** on macOS, **CUDA / OpenCL** on Windows and Linux, CPU fallback). |
| **EXR → Video** | Image sequence → OCIO → video | OpenEXR primary; also CinemaDNG (`.dng`), DPX, PNG, JPEG, and WebP as **inputs**. Output is a video file. Slate, burn-in, and watermark use that tab’s controls. Sequences may use ``name.####.ext`` or ``name_####.ext`` pads. DJI CinemaDNG is tagged Linear P3-D60 with +1.4 stops already applied. See [CinemaDNG](./cinemadng.md). |
| **Video → Proxy** | Video → smaller video (no OCIO) | Batch editing proxies plus a **watch folder**, modelled on DaVinci Resolve proxy generation. See [Video → Proxy](#video--proxy). |

Dragging the **Log** splitter up does not squash Input / Output / Options
fields. Those controls keep a fixed readable height, and the convert form
scrolls if the pane is short. **Convert** and **Cancel** share that height;
the progress bar matches the input rows.

Mode can be forced with `--mode video2exr|exr2video|proxy`, or inferred from `--open`
(`auto`: image-sequence paths open **EXR → Video**, common video extensions open
**Video → EXR**).

### Input / output paths

Right-click the **Input** or **Output** path field for:

| Action | Behavior |
|--------|----------|
| **Cut / Copy / Paste / Select All** | Standard line-edit edit commands (same as ⌘/Ctrl+X/C/V/A) |
| **Copy File Path** | Full text from the field |
| **Copy Folder Path** | Containing directory (or the path itself when it is a folder / sequence pattern) |
| **Open in Finder** / **Show in Explorer** / **Open Containing Folder** | Open that folder via the system file manager (`QDesktopServices`) |

**Browse…** opens the normal browser or file dialog. With a valid path in the
field, **⌘-click** (macOS) or **Ctrl-click** (Windows/Linux) on **Browse…**
reveals that path in the OS file manager instead.

**Paste paths:** You can paste a Nuke-style sequence path
(``/show/shot.####.exr`` or ``%04d``) into the convert Input field. The app
resolves the sequence on disk, fills the frame range, and auto-detects source
color space. Pasting a folder, frame file, video, or ``####`` pattern into a
browser’s **Folder** field navigates there, highlights the matching item, and
opens **Preview** so you can hit **Open** immediately.

**Sequence browser** lists every supported still sequence in a folder
(``name.####.ext`` or ``name_####.ext``). Mixed folders prefer EXR, then CinemaDNG, then DPX.
Display-encoded stills (PNG/JPEG/WebP) auto-suggest an **sRGB** source color
space; EXR, CinemaDNG, and DPX default toward scene-linear / metadata.

**Views:** the top bar has **List | Grid | Preview** plus **Inspect** (always
available). **List** is the metadata table; **Grid** shows first-frame
thumbnails (async OIIO downscale; EXR/DPX get a cheap display curve).
**Preview** plays the **currently selected** sequence (or the first listed row
if nothing is selected). To preview another sequence, return to List/Grid,
select it, then enter Preview again. Folder tree and path stay visible.
Browser Preview uses the same GPU OCIO `SequencePlayer` as the slate editor
(the GL widget is created with the dialog before show). **Space** toggles
Preview ↔ last list/grid mode; **Esc** leaves Preview; **Left/Right** step
frames. Double-click or **Open** commits the **selected** sequence via its
**first-frame path** (not the folder alone), so multi-sequence directories keep
the chosen basename through convert, restore, and slate. Typing only a
directory still resolves a default sequence on disk (EXR first, then DPX;
prefers a basename matching the folder name when several sequences share the
folder). The slate editor reuses the player with live burn-in/watermark overlays.

**Video browser filters:** only real media extensions are listed (``.r3d``,
``.nev``, ``.braw``, ``.ari``, ``.arx``, ``.mov``, ``.mp4``, …). macOS **AppleDouble** Finder sidecars
(``._clip.R3D``) that appear next to clips on network shares or non-HFS
volumes are hidden. They share the media extension but are resource-fork
metadata, not video. RED **`.RMD`** metadata files are never listed (not a
video extension). Selecting **`.r3d` / `.nev` / `.braw`** uses the optional
SDK bridges for listing, **Inspect**, Preview, and grid thumbs. Never
PyAV/FFmpeg (those containers are not libav-safe). Without the SDK, the clip
still appears; Inspect shows SDK missing. **`.ari` / `.arx`** are listed as
unsupported camera RAW and are never opened with PyAV.

**Cloud placeholders (Dropbox / iCloud / OneDrive):** folders managed by a
cloud client often contain “online-only” stubs (macOS File Provider dataless
files, Windows recall-on-open attributes, ``*.partial`` downloads). The
browsers **list those names** but do **not** open them for sequence headers,
video probes, or grid thumbnails. That would hang the UI while the client
downloads. Online-only videos show **Online-only** in the Codec column.
Inspect / Preview / convert of a stub still needs the client to hydrate the
file (open it once in Finder, or pin it locally). Search skips ``.dropbox``
cache directories and does not recurse into dataless folders.

Right-click a row in **List** or a tile in **Grid** (sequence and video
browsers) for:

| Action | Behavior |
|--------|----------|
| **Preview** | Switch to Preview for the selected item (**Space**) |
| **Open** | Accept the selection (same as the Open button) |
| **Copy File Path** | First-frame path (sequence) or video file path |
| **Copy Folder Path** | Containing directory |

(Same **Copy File Path** / **Copy Folder Path** labels as the Input/Output
path-field menu.)

**Volumes / drives:** both browsers show mounted volumes as **top-level** rows
in the folder tree and under a **Volumes** heading in the places sidebar
(system disk first, then other mounts by name). That covers:

| Platform | What you see |
|----------|----------------|
| **macOS** | Boot volume (e.g. Macintosh HD) plus each entry under `/Volumes` (USB, network, disk images). macOS hides `/Volumes` from a plain “root `/`” file model, so sticks would otherwise be invisible. |
| **Windows** | Each drive letter (`C:\`, `D:\`, …), not only the system drive. |
| **Linux** | `/` plus user-facing mounts (typically `/media/…`, `/mnt/…`, `/run/media/…`); pseudo filesystems (`proc`, `sysfs`, …) are omitted. |

The list refreshes every few seconds while Browse is open so plug-in / eject
updates without restarting the dialog.

**Browser layout memory** (both input browsers):

| What | Shared or separate? | Keys |
|------|---------------------|------|
| Window size + position | **Shared** (Video ↔ Sequence) | `ui/browser_geometry` |
| List \| Grid \| Preview, Inspect on/off, outer/content splitters, list column widths | **Per mode** | `ui/sequence_browser_*` / `ui/video_browser_*` |
| Folder tree expansion, tree scroll, last folder + selection | **Per mode**, restored when Browse reopens the **same** start directory as last time | `…_tree_expanded`, `…_tree_vscroll`, `…_last_dir`, `…_selected` |

If the convert tab’s current path maps to a **different** folder than the last
browse session, layout prefs (size, splitters, view, Inspect, columns) still
restore, but the tree focuses that new folder instead of replaying the previous
expansion set.

**Video browser** (Video → EXR input) mirrors the sequence browser with
**List | Grid | Preview**. **Grid** shows first-frame video thumbnails (PyAV).
**Preview** plays the selected file (or the first file in the folder) with the
same player transport, cache strip, and OCIO controls. Video decode seeks to
the previous keyframe then decodes forward to the exact frame (so scrubbing and
play stay frame-accurate on long GOPs). **Space** toggles Preview; **Esc**
returns to list/grid. Folder path fields never expand the dialog when paths are
long (text elides; width follows the layout).

---

## Color (OCIO)

- Config picker: bundled **ACES Studio Config v4** (recommended default), other
  built-ins, `$OCIO`, a custom `.ocio` file, or a local Nuke install config
  (path reference only). Incompatible Nuke or library configs may appear
  **greyed out** with a tooltip when the linked OpenColorIO cannot load them.
- The bundled config keeps every stock ACES Studio v4 name and adds camera
  logs on top:
  - **Nikon N-Log** (N-Gamut / Rec.2020). This is the published curve, a
    generated 1D LUT, not Nikon's downloadable cube.
  - **Fujifilm F-Log**, **F-Log2**, and **F-Log2 C**
  - **Leica L-Log** (Rec.2020, and Rec.709 for the SL Typ 601)
  - **Sony S-Log** and **S-Log2** (S-Gamut)
  - **Canon Log** (Cinema Gamut, Rec.2020, Rec.709) and **Canon Log 2 / Log 3**
    in Rec.2020 and Rec.709
  - **KineLOG3**
  - **GoPro Protune**, **GP-Log**, and **GP-Log2**
  - **Blackmagic Film Gen 4** (Broadcast, Pocket 4K, Pocket 6K)
  - **OPPO O-Log** (Rec.2020; 18% reflectance stays on linear 0.18)
  - **Apple Log 2** (Apple Wide Gamut, same curve as Apple Log)
- Stock ACES Studio v4 spaces already include Apple Log, ARRI LogC3 and LogC4,
  RED Log3G10, Sony S-Log3 / Venice, Canon Log 2/3 Cinema Gamut, Panasonic
  V-Log, DJI D-Log, and Blackmagic Film Gen 5. DJI D-Log M, Samsung Log, and
  Z CAM Z-Log2 are not included: no scene-linear equation was available to
  check. Sources, measured error, and the full camera table (including logs
  left out) are in
  [resources/ocio/CAMERAS.md](../resources/ocio/CAMERAS.md).
- Source / destination spaces follow the **active (user) config**; aliases are
  remapped where possible (`find_equivalent_space`).
- **EXR → Video destination:** if the dest name is an OCIO **display**
  (``sRGB - Display``, ``Rec.1886 Rec.709 - Display``, …) convert applies that
  display plus the config’s default **view** for the working space. On ACES
  2.0 Studio that is **ACES 2.0 - SDR 100 nits (Rec.709)**, matching Nuke /
  DJV. A colorspace-only hop to a display uses the config
  ``default_view_transform`` (**Un-tone-mapped**) and will lift shadows / clip
  highlights. Utility dests such as ``sRGB Encoded Rec.709 (sRGB)`` stay
  colorimetric (no view). The convert log shows ``display / view`` when a
  view is applied. The built-in player already used display/view; export now
  matches.
- **App-internal paint** (slate / burn-in / watermark) is linearised on a private
  ACES **app anchor** config (CG/Studio built-in) with guaranteed `texture_paint`
  and `aces_interchange` (ACES2065-1), then bridged into the user compositing
  space via interchange when the user config provides it. Convert of *your*
  footage still uses only the selected config.
- Bundled ACES Studio needs **OpenColorIO 2.5+**. From source, run
  `make ensure-ocio` if the runtime library is older than 2.5.

---

## Presets (`Presets` menu)

Named **convert recipes** (JSON under app data): color spaces, scale, codec key,
EXR compression, OCIO source. **Not** included: input/output paths, window
geometry, player/cache prefs, or full slate text (slate fields still use last
session via Preferences / QSettings).

Files are versioned (`schema_version`); older presets still load.

---

## Slate / burn-in / watermark

Available on **EXR → Video** only (checkboxes on that tab).

| Feature | Behavior |
|---------|----------|
| **Prepend slate** | One-frame slate composited before the sequence |
| **Burn-in** | Per-frame text overlay |
| **Watermark** | Text watermark (default **40%** opacity, **tiled** across frame; size/angle editable) |

Overlays are authored in a display/sRGB-oriented space, linearised into a
scene-linear **compositing space** (prefer ACES2065-1 via `aces_interchange`,
else `scene_linear`), alpha-composited, then OCIO-transformed to the encode
display space. Rendering is **QPainter** (no embedded browser).

**Slate thumbnail** (EXR → video): which source frame is used for the slate
still is set under **File → Preferences…**: first, middle, or last frame of the
sequence. Extraction uses the known EXR frame list (not a video seek).

---

## Codecs

Same honest bit-depth ladder as the CLI. Default is software **ProRes 422 HQ**
(`prores`, **10-bit**). The EXR → Video **Codec** control is a nested family
menu: open it, then pick a family (ProRes software, VideoToolbox, oxideav,
CineForm, DNxHR, H.264/HEVC, FFV1) and a profile inside that submenu.

**VideoToolbox** is Apple’s **hardware ProRes encoder**, **macOS only**. That
submenu is omitted on Windows and Linux. 422 profiles stay 10-bit; **4444 /
XQ** are **~12-bit class** (unlike software 4444/XQ, which encode 10-bit).
See [ProRes and VideoToolbox](./prores.md) for why the three ProRes families
exist and which key to pick.

Experimental **oxideav** keys (`prores_ox_proxy` / `lt` / `422` / `hq` /
`4444` / `xq`) appear when the `exr_prores` extension is built
(`make oxideav-prores`; included in release binaries). **4444 / XQ** are
12-bit to other applications. The **422** profiles say **12-bit internal,
10-bit on decode**: RDD 36 422 fourccs are read as 10-bit by FFmpeg, Resolve,
and QuickTime. Full key list:
[CLI codecs](./cli.md#codecs-honest-bit-depths) and
[ProRes: oxideav](./prores.md#experimental-oxideav-cross-platform-rdd-36).

---

## Video → Proxy

Makes lightweight editing proxies from camera clips, the way DaVinci Resolve's
**Generate Proxy Media** and proxy watch folders do. There is **no color
transform**: a proxy keeps the source look (log stays log), the source
**timecode**, and the source **audio**, so an editor can relink it to the
original.

| Section | Control | Default | Notes |
|---------|---------|---------|-------|
| **Source clips** | Add Clips… / Add Folder… / Remove / Clear | empty | Drop videos or folders on the window while this tab is open to add them. RAW (`.r3d`, `.nev`, `.braw`, ARRIRAW) is not offered here. |
| | Include subfolders | off | **Add Folder…** includes nested folders, and the watch folder follows them too. `Proxy` folders are skipped. |
| **Output** | Location | **Proxy folder next to each clip** | Writes `<clip folder>/Proxy/<clip name>.<ext>`, the layout Resolve relinks. **Custom folder** reveals a folder field and puts every proxy in that one folder. |
| | Codec | **Apple ProRes 422 Proxy** | Also ProRes 422 LT, VideoToolbox ProRes Proxy / LT (macOS), DNxHR LB / SQ, H.264, and 8-bit HEVC. H.264 / HEVC write `.mp4`; the rest write `.mov`. Shares a row with Resolution. |
| | Resolution | **Half** | Quarter, One-eighth, Original, or fit to 1080p / 720p / 540p height. Never upscales. |
| | Audio | on | On the resolution row. PCM in `.mov`, AAC in `.mp4`. |
| | Name suffix | none | Added to the proxy filename. Leave empty to keep the source name, which relinks best. |
| | Skip clips that already have a proxy | on | On the suffix row. Off re-makes them. |
| | Watermark | off | **Enable** writes an invisible [TrustMark](https://github.com/adobe/trustmark) mark into every proxy frame. Payload is up to 8 ASCII characters (TrustMark text mode, so the official decoder can read it). Strength defaults to 1.00 (range 0.10 to 2.00). The encoder model downloads once into a per-user cache and is checked with SHA256. The mark is applied to the resized RGB frame the codec encodes. Proxies still have no OCIO view transform. |

Press **Convert** to queue every listed clip. Proxies are made one at a time
in the background. You can keep adding clips (Convert again queues the new
ones), and **Cancel** stops the running proxy and clears the queue. A proxy
is written to a hidden `.name.partial.ext` file and renamed when it is
complete, so a half-made proxy never sits under the final name. Finished clips
get a ✓ in the list.

### Watch folder

The watch folder is a collapsed section under Output, so the tab stays the
same height as Video → EXR and EXR → Video. The header always shows the
status (**Not watching**, or copying / queued / done / failed) and
**Start Watching**. Open the section for the folder path and the option below.

Pick a folder and press **Start Watching**. Every new clip that appears there
is proxied with the settings above, joining the same queue as a batch.

- A clip is picked up once its size has stayed the same for a few seconds, so
  clips still copying from a card or a network share are left alone until the
  copy finishes.
- **Include subfolders** (on the source-clip row) also watches nested folders.
  `Proxy` folders and the custom output folder are never watched, so proxies
  are not re-proxied.
- **Also convert clips already there** proxies what is already in the folder
  when you start. Off, only clips that arrive afterwards are converted.
- The custom output folder cannot be the watched folder itself.

For a headless watch folder (a render box or ingest station), use
[`main.py proxy --watch`](./cli.md#proxy-video-to-editing-proxies).

---

## After convert

Checkboxes under the convert actions (persisted in `QSettings`). They are
hidden on the **Video → Proxy** tab, which runs batches:

| Toggle | Default | Action |
|--------|---------|--------|
| **Copy path** | on | Copy the output path to the clipboard |
| **Open result** | off | **EXR → Video:** open the finished file with the preferred video player (Preferences; default **built-in**). **Video → EXR:** open the sequence in the built-in player window (GPU OCIO when available, with the playback cache strip). |
| **Show in folder** | off | Reveal the file or sequence folder in the OS file manager |

### Preferences (`File → Preferences…`)

| Setting | Purpose |
|---------|---------|
| **Video player** | **Built-in player** (default), **System default**, or a **custom** app/CLI path (IINA, VLC, mpv, …). Used by **Open result** after EXR → Video. Built-in uses the same `SequencePlayer` as sequence Preview (GPU OCIO + cache strip). |
| **Slate thumbnail frame** | First / middle / last frame of the EXR sequence for the slate still |
| **Playback cache budget** | % of system RAM for decoded frames (slate, browser Preview, post-convert built-in player) |

Settings org/app: `QSettings("VFXTools", "EXRConverter")`.

---

## Slate & overlay editor

Opened from **EXR → Video** when editing slate / burn-in / watermark.

- **Timeline + cache:** shot frames are prefetched into a RAM cache as
  working-space pixels (when OCIO is configured). Playback is **cache-first**
  (stalls until the next frame is in RAM).
- **Display:** prefers **GPU OCIO** (full-resolution texture + GLSL display/view
  transform). **Gain** is pre-display (exposure stops); **gamma** is Nuke-style
  post-display ``pow(rgb, 1/γ)`` (1 = identity; not the sRGB/Rec.1886 encode).
  Falls back to CPU OCIO if OpenGL is unavailable. The preview canvas
  (letterbox around the frame) is **black**, with a 1px white format box
  (Nuke-style) and the plate resolution right-justified under the
  bottom-right of the frame. Export is unchanged.
- **Overlays:** burn-in and watermark are composited in scene-linear working
  space so the preview matches the convert path.
- **Undo:** ⌘/Ctrl+Z and Shift+⌘/Ctrl+Z undo/redo **feature toggles** and
  **Fill from slate** (burn-in bulk fill). Free-text / spinner edits still
  persist live without stacking one undo step per keystroke. Convert paths
  and OCIO are outside the undo stack.
- **Video monitoring view:** for video Preview and Video→EXR **Open result**,
  the player asks the OCIO config for a view via viewing rules / video encodings
  (``getDefaultView(display, videoColorSpace)``) when available. For example, ACES
  configs often resolve that to a colorimetric video view. If the config has no
  such rule, the config-wide default display/view is kept. Scene-linear camera
  EXR in the slate editor always uses the config default.

---

## Help menu

| Item | Behavior |
|------|----------|
| **Check for Updates…** | Opens the [latest GitHub Release](https://github.com/derek-rein/exr-converter/releases/latest) in the system browser (no in-app download) |
| **About EXR Converter** | Title + version header; deps, links, OCIO notes, and license in a scroll area |
| **Version X.Y.Z** | Non-clickable; running app version from `pyproject.toml` (same value as About, `--version`, and EXR `Software` metadata) |

Site links stay in **About** only (not on the Help menu).

---

## macOS notes

- Release DMGs (Apple Silicon and Intel) are signed with a **Developer ID
  Application** certificate for **Medeu Global LLC**, then notarized and
  stapled. The app opens normally. First launch may still show the standard
  "downloaded from the internet" prompt.
- The running Dock icon uses the bundle `.icns`. The bundle identifier is
  `com.vfxtools.exrconverter`. The name on disk stays **EXR Converter**.

---

## See also

- [CLI reference](./cli.md)
- [Nuke integration](./nuke.md)
- [README](../README.md)
