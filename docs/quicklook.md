---
title: Quick Look on macOS
weight: 16
description: Spacebar previews and Finder icons for EXR, DPX, and optional stills.
---

Release builds of **EXR Converter** on macOS include two app extensions inside
`EXR Converter.app`:

- **Quick Look** — the Spacebar / column preview
- **Thumbnails** — the Finder icon

They are macOS 12 and later. The Windows and Linux builds do not include them.
The extensions are small and link system frameworks (ImageIO, Core Image, the
system Swift runtime). They do not include OpenColorIO or any camera SDK.

## Turn the extension on

macOS does not enable an embedded Quick Look extension until you allow it:

**System Settings → General → Login Items & Extensions → Quick Look**
(and **Finder** / thumbnails, if that list is separate on your macOS version).

Enable **EXR Converter Quick Look** and **EXR Converter Thumbnails**.
Preferences has a **Login Items & Extensions…** button that opens this pane.
On macOS Ventura the same list was under Login Items. The button uses the
current Extensions URL; if it opens the wrong page, use the path above.

The extension only registers after the app has been installed (drag the app
to `/Applications` from the DMG). A copy that only exists inside a disk image
may not show up in the list.

## Which formats

| Format | Default | Decoder |
|--------|---------|---------|
| OpenEXR (`.exr`) | on | ImageIO. Scene-linear pictures get a display approximation (below). |
| DPX (`.dpx`) | on | Built-in reader. See the DPX limits. |
| TIFF (`.tif`, `.tiff`) | off | ImageIO. Float / linear TIFF is tone-mapped; 8-bit TIFF is shown as-is. |
| PNG, JPEG, WebP | off | ImageIO, as display-referred images. |
| DNG / CinemaDNG (`.dng`) | off | macOS RAW decoder (Core Image). Best effort. |

PNG, JPEG, WebP, TIFF, and DNG already have a system preview. They stay off so
Finder does not replace that preview. Turn one on when you want the sequence
label on numbered frames.

**Not previewed**

- Movies the system already handles: ProRes, H.264, HEVC, and containers such
  as `.mov`, `.mp4`, `.mkv`, `.mxf`. Claiming them would fight QuickTime.
- **BRAW, RED R3D / N-RAW, and ARRIRAW.** Those need the proprietary SDKs, which
  are large, license-restricted, and are not linked into this extension. The
  app can still convert BRAW and R3D when that bridge is built. ARRIRAW is not
  a convert input. CinemaDNG is the one camera-RAW still ImageIO can open
  without those SDKs, and it is optional above. The RAW develop is the system
  default, not the app’s camera pipeline, so the look can differ.

The convert pipeline’s sequence inputs are still OpenEXR, DPX, PNG, JPEG, and
WebP. TIFF and DNG previews do not add those formats as convert inputs.

## Per-format toggles

`QLSupportedContentTypes` in the extension’s Info.plist is fixed at build time.
Preferences cannot edit that list. Instead, **File → Preferences… → Quick Look**
writes a small JSON file the extension reads each time it runs:

1. **App Group container** (preferred). The group id is
   `83546T4BT8.com.vfxtools.exrconverter`. That is a macOS-style id (team id
   prefix). It is not the iOS `group.` form, so the Developer ID signature does
   not need a provisioning profile. The host app is not sandboxed. The
   container path is available when the signed app is running
   (`containerURLForSecurityApplicationGroupIdentifier:`). A source checkout
   (`uv run`) and an ad-hoc bundle are not signed with that entitlement, so
   this path is missing there.
2. **Fallback file**, `~/Library/Application Support/EXR Converter/quicklook.json`.
   The sandboxed extension has a read-only home-relative exception for that
   one file, and it opens the real home directory (a sandboxed “home” is the
   extension’s own container, which is a different folder). The app writes the
   file on every launch and whenever you press OK in Preferences. If the App
   Group file is missing, the extension uses this copy.

If neither file exists, OpenEXR and DPX are on and the other formats are off.

For a format that is off, the extension **declines**. When macOS has another
preview (PNG, JPEG, TIFF, WebP, many DNG stills), that preview is what you
should get back. Apple does not document a single error code that guarantees
fallback on every OS version; if a release shows an error thumbnail instead,
turn the whole extension off in Login Items & Extensions. OpenEXR and DPX have
no system preview, so turning them off leaves Quick Look empty, which is the
same as before this extension existed.

Finder caches previews. After you change a checkbox, use **Clear Quick Look
cache** in Preferences (this runs `qlmanage -r cache`) or the old thumbnail
can stick.

## Sequences

A name like `shot.1001.exr` or `shot_1001.dpx` is treated as one frame of a
sequence. The preview draws a label with the sequence name and the frame
number. `beauty.exr` is a single still and has no label.

The frame **range** (`1001–1100`) needs a directory listing of the sibling
frames. Quick Look’s sandbox can read the file it was given. Sibling frames
are often not readable, and the extension does not request broad filesystem
access. When the listing fails, or the folder has more than 50,000 names, the
label is the frame number only. The range is never guessed from a partial
listing.

## Color

Scene-linear OpenEXR (when ImageIO returns a float or linear buffer) and
linear / printing-density / log DPX are shown through a small Rec.709-style
curve (the Narkowicz ACES filmic fit). The app’s own default EXR → video view
is OCIO `scene_linear` → `Output - Rec.709` on the bundled ACES config. The
extension does **not** load that config. Plates in ACEScg will be recognizable
and will not match a custom OCIO view, a show LUT, or the built-in player.

ImageIO sometimes returns an 8-bit EXR it has already mapped. That buffer is
shown as-is so it is not tone-mapped twice.

DPX transfer handling:

| Header transfer | Preview |
|-----------------|---------|
| Printing density, logarithmic, ADX | Cineon-style log to linear, then the same curve. 10-bit code 685 is the reference white. |
| Linear, or user-defined / unset | Full-range code values, then the same curve. Many log scans leave this field unset and will look wrong. |
| Video (Rec.709, Rec.601, and the other display values) | Treated as already display-encoded. |

DPX decode supports uncompressed RGB (and RGBA, ignoring alpha) at 8-bit,
16-bit, and 10-bit samples packed into 32-bit words (method A, the usual VFX
write). Other bit depths, YCbCr, and run-length compression get a short error
in the preview instead of a blank window. The long edge is capped around
1920 pixels. Deep, multipart, and extra EXR layers are not listed; ImageIO’s
default RGB is what you see.

DNG uses the system RAW filter. It is not a Blackmagic or ARRI develop.

## What you will not get

- A color-critical review. Use the app’s viewer for OCIO.
- Playback of a whole sequence. One frame, the one you selected.
- ProRes, H.264, HEVC, BRAW, R3D, or ARRIRAW in this extension.
- The app’s own player. Spacebar stays inside Quick Look. It does not open
  EXR Converter. The extension is a separate sandboxed process and cannot run
  the Python / Qt / OCIO viewer, and it cannot launch the app (the Quick Look
  sandbox has no Launch Services `open`). A full sequence with the same OCIO
  view is the built-in player, which can read the other frames in the folder.
