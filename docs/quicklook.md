---
title: Quick Look on macOS
weight: 16
description: Spacebar previews and Finder icons for EXR, DPX, and optional stills.
---

Release builds of **EXR Converter** on macOS include two app extensions inside
`EXR Converter.app`:

- **Quick Look**: the Spacebar / column preview
- **Thumbnails**: the Finder icon

They are macOS 12 and later. The Windows and Linux builds do not include them.
The extensions are small and link system frameworks (ImageIO, Core Image, the
system Swift runtime). They do not include OpenColorIO or any camera SDK. The
OCIO view is a LUT baked by the app, applied in the extension with Core Image.

## Turn the extension on

The extensions are **off by default**. Open **File → Quick Look…** (the menu
item only exists on macOS), tick **Use EXR Converter for Quick Look and Finder
thumbnails**, and press OK. The app asks macOS to elect both extensions with
`pluginkit -e use`; turning the box off runs `pluginkit -e ignore`. Each launch
re-applies your choice, so an update that re-registers the extensions does
not switch them back on.

Under the checkbox the dialog shows what macOS reports: the extensions are
on, off, or not registered yet. The per-format checkboxes are only available
while the main checkbox is ticked.

If the box does not take effect, enable **EXR Converter Quick Look** and
**EXR Converter Thumbnails** by hand under
**System Settings → General → Login Items & Extensions → Quick Look**. The
dialog’s **Login Items & Extensions…** button opens that pane. On macOS
Ventura the same list was under Login Items.

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

The convert pipeline’s sequence inputs are OpenEXR, CinemaDNG, DPX, PNG,
JPEG, and WebP (see [CinemaDNG](./cinemadng.md)). The TIFF preview does not
make TIFF a convert input, and the DNG preview uses the system RAW decoder,
not the app’s CinemaDNG reader.

## Per-format toggles

`QLSupportedContentTypes` in the extension’s Info.plist is fixed at build time.
The app cannot edit that list. Instead, **File → Quick Look…**
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
   file on every launch and whenever you press OK in the Quick Look dialog. If the App
   Group file is missing, the extension uses this copy.

If neither file exists, OpenEXR and DPX are on and the other formats are off.

For a format that is off, the extension **declines**. When macOS has another
preview (PNG, JPEG, TIFF, WebP, many DNG stills), that preview is what you
should get back. Apple does not document a single error code that guarantees
fallback on every OS version; if a release shows an error thumbnail instead,
turn the whole extension off in Login Items & Extensions. OpenEXR and DPX have
no system preview, so turning them off leaves Quick Look empty, which is the
same as before this extension existed.

Finder caches previews. OK in the Quick Look dialog clears that cache
(`qlmanage -r cache`); **Clear Quick Look cache** does it on demand.

## Sequences

A name like `shot.1001.exr` or `shot_1001.dpx` is one frame of a sequence.
`beauty.exr` is a still and has no frame label.

When the extension can read the folder, Spacebar **plays** the frames at 24 fps
in the Quick Look window. It starts on the file you selected and loops. The
bottom bar works like the player's: previous frame, play / pause, next frame,
and a frame ruler you can click or drag. Clicking the picture or pressing Space
also plays and pauses, and the left and right arrow keys step, when Quick Look
delivers those events. Under the picture are the sequence name, the current
frame, the first–last frame (`plate  ·  1002  ·  1001–1003`), and the file's
full resolution.

Frames are read ahead of the playhead on several threads and kept in memory,
with the long edge around 960 pixels. The green bar under the ruler shows which
frames are cached. The cache uses up to an eighth of your Mac's memory (at most
1 GB, about 250 frames at that size). The first pass plays as fast as the files
decode; a loop that fits in the cache then plays at a steady 24 fps. When the
next frame is not ready yet, playback waits for it instead of skipping.

The sandbox is allowed to read, and only read. Developer ID notarization
accepted this set with no issues (notarytool submission
`2a95ec5f-eb21-49d3-b200-d1df08b0e1c4`, status Accepted):

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
printing-density / log DPX, and float TIFF go through an OCIO display view
from the app's config. The bar above the picture has the same controls as the
player:

- **Exposure gain** and **γ** sliders. Gain multiplies the scene-linear pixels
  before the view; gamma is applied after it (`out = in^(1/γ)`, as in Nuke and
  the player). Click a value to reset it to 1.0. Neither one re-reads the file.
- **Input** color space. **Auto** shows what the extension picked (below). Pick
  another space to override it for this preview; the frames are read again.
- **Display / view**. Every SDR view of the config's `sRGB - Display` and
  `Display P3 - Display`. The extension remembers the one you pick.

**Default view.** On a screen that can show Display P3 (most Mac displays
from 2016 on, and Apple's external displays), the
preview opens in `Display P3 - Display` with that display's default view
(bundled config: `ACES 2.0 - SDR 100 nits (P3 D65)`) and tags the picture as
Display P3, so macOS shows it with the full gamut. On other screens it opens in
`sRGB - Display` / `ACES 2.0 - SDR 100 nits (Rec.709)`. HDR (EDR) views are not
offered yet.

**Input detection.** For OpenEXR the extension reads the file header and uses
the same order as the player:

1. `exrconverter:dstColorSpace`, which this app writes on every EXR
2. `colorInteropID` (OpenEXR 3.4 / OCIO 2.5)
3. `chromaticities`: Rec.709 / sRGB, ACES AP1 and AP0, P3-D65, Rec.2020
4. `oiio:ColorSpace`
5. otherwise `scene_linear` (ACEScg on the bundled config), the player's default

Names match the config's color space names, aliases, and interop ids. Log and
video spaces (ARRI LogC4, S-Log3, ACEScct, sRGB-encoded textures, and so on)
are supported as well as linear ones. When ImageIO has already converted an EXR
using its chromaticities, or for a float TIFF with an embedded profile, the
pixels are linear Rec.709 and are treated as such.

**How it is baked.** The extension cannot link OpenColorIO. When the OCIO
config loads, the app writes three files into the App Group container and into
`~/Library/Application Support/EXR Converter/`:

- `views.lut`: one 33³ LUT per view above, from `scene_linear` through that
  display / view, with a log2 shaper from about 0.001 to 64. The extension
  applies the shaper, then Core Image `CIColorCube`.
- `inputs.json`: each scene color space in the config, fitted as a
  per-channel curve and a 3×3 matrix into `scene_linear`. Spaces that do not
  fit that shape within 1% are left out.
- `view.lut`: the default view alone, used by Finder thumbnails.

Copies baked from the bundled ACES Studio config at build time are inside the
extension and are used when those files are missing. Changing the OCIO config
in the main window rebakes all three. If no LUT can be read, the preview falls
back to a small Rec.709 filmic curve.

Finder **thumbnails** use the input detection but always show the config's
default view at gain 1 and gamma 1, in sRGB.

ImageIO sometimes returns an 8-bit EXR it has already mapped. That buffer is
shown as-is so it is not passed through the LUT twice, and the color controls
are hidden. Multipart files show ImageIO’s first image. Deep and extra channels are
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

- HDR (EDR) display views, or display views other than sRGB and Display P3.
- The display view you last picked in the player. Quick Look remembers its own
  choice.
- Channels other than the default RGB.
- Playback of a folder the sandbox cannot list. That preview is the selected
  frame.
- ProRes, H.264, HEVC, BRAW, R3D, or ARRIRAW in this extension.
- The app opening from Spacebar. Quick Look stays inside the extension.
