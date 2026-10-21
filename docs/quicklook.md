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
system Swift runtime). They do not include OpenColorIO or any camera SDK. The
OCIO view is a LUT baked by the app, applied in the extension with Core Image.

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
| OpenEXR (`.exr`) | on | ImageIO. Scene-linear pictures use the OCIO view LUT (below). |
| DPX (`.dpx`) | on | Built-in reader. Log and linear headers use the same LUT. |
| TIFF (`.tif`, `.tiff`) | off | ImageIO. Float / linear TIFF uses the LUT; 8-bit TIFF is shown as-is. |
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

A name like `shot.1001.exr` or `shot_1001.dpx` is one frame of a sequence.
`beauty.exr` is a still and has no frame label.

When the extension can read the folder, Spacebar **plays** the frames at 24 fps
in the Quick Look window. It starts on the file you selected and loops. Click
the picture to pause. The slider and the left and right arrow keys scrub when
Quick Look delivers those events. The label is the sequence name, the current
frame, and the first–last frame (`plate  ·  1002  ·  1001–1003`). Playback
decodes one frame at a time and keeps the long edge around 960 pixels. It does
not preload the sequence.

The sandbox is allowed to read, and only read:

- your home directory
- `/Volumes/` (external and network volumes mounted there)
- `/tmp` and `/private/tmp`

Those are temporary-exception entitlements on the extension, not a Finder
permission dialog. macOS privacy protection can still refuse Desktop,
Documents, and Downloads, and any folder outside that list (a studio root such
as `/jobs` is not covered). When the listing fails, or the folder has more
than 50,000 names, you get the **selected frame** only, at a longer edge around
1920. The range is never guessed from a partial listing.

## Color

Scene-linear OpenEXR (when ImageIO returns a float or linear buffer), linear /
printing-density / log DPX, and float TIFF go through the app’s **default OCIO
display view**:

- source role `scene_linear` (ACEScg on the bundled ACES Studio config)
- the config’s default display and view (bundled config:
  `sRGB - Display` / `ACES 2.0 - SDR 100 nits (Rec.709)`)
- exposure 0, gamma 1, which is the sequence viewer’s default

That is the same transform the sequence player uses for an ACEScg plate at
those defaults (ACEScg → ACES2065-1 → that display view). The app bakes it to
a 33³ LUT with a log2 shaper from about 0.001 to 64 and writes `view.lut`
when the OCIO config loads, into the App Group container and into
`~/Library/Application Support/EXR Converter/view.lut`. The extension applies
the shaper and then Core Image `CIColorCube`. The copy baked into the extension
at build time is the bundled ACES view, used when neither file is there. If
the LUT cannot be read, the preview falls back to a small Rec.709 filmic curve.

The cube is the default view only. It does not follow the viewer’s exposure or
gamma sliders, and it does not switch when you pick a different display view
in the player. Changing the OCIO config in the main window rebakes the LUT.

ImageIO sometimes returns an 8-bit EXR it has already mapped. That buffer is
shown as-is so it is not passed through the LUT twice. ImageIO may also convert
an EXR that carries chromaticities into extended linear sRGB before the LUT.
Many VFX EXRs omit chromaticities, so the stored RGB matches ACEScg code values
and the LUT matches the player. A file that ImageIO has already converted will
not. Multipart files show ImageIO’s first image. Deep and extra channels are
not listed. DWAA and DWAB are whatever the system ImageIO / OpenEXR reader on
that macOS supports. The extension does not ship its own OpenEXR.

DPX transfer handling:

| Header transfer | Preview |
|-----------------|---------|
| Printing density, logarithmic, ADX | Cineon-style log to linear, then the OCIO LUT. 10-bit code 685 is reference white. |
| Linear, or user-defined / unset | Full-range code values, then the LUT. Many log scans leave this field unset and will look wrong. |
| Video (Rec.709, Rec.601, and the other display values) | Treated as already display-encoded. The LUT is not applied. |

DPX decode supports uncompressed RGB (and RGBA, ignoring alpha) at 8-bit,
16-bit, and 10-bit samples packed into 32-bit words (method A, the usual VFX
write). Other bit depths, YCbCr, and run-length compression get a short error
in the preview instead of a blank window.

DNG uses the system RAW filter. It is not a Blackmagic or ARRI develop.

## What you will not get

- The viewer’s exposure, gamma, or a display view you picked in the player.
  The LUT is the config default at exposure 0 and gamma 1.
- A color-managed match for an EXR whose chromaticities ImageIO converted, or
  for channels other than the default RGB.
- Playback of a folder the sandbox cannot list. That preview is the selected
  frame.
- ProRes, H.264, HEVC, BRAW, R3D, or ARRIRAW in this extension.
- The app opening from Spacebar. Quick Look stays inside the extension.
