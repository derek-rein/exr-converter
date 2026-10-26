---
title: Documentation
description: Guides for the EXR Converter GUI, CLI, proxies, ProRes, and optional camera RAW.
---

Guides for running and integrating **EXR Converter**. Edit these files under
`docs/` in the repo. The site is generated with Astro (`site/`) and published at
[exr-converter.derekvfx.ca](https://exr-converter.derekvfx.ca/).

The app writes a **video file** or an **OpenEXR sequence**. PNG, JPEG, WebP,
and DPX can be sequence inputs on EXR → Video. They are not outputs.

macOS release builds (Apple Silicon and Intel) are signed with a Developer ID
Application certificate for **Medeu Global LLC**, then notarized and stapled.
The app opens normally. First launch may still show the standard "downloaded
from the internet" prompt.

When you change CLI flags, GUI behavior, codecs, OCIO defaults, packaging that
users see, or integrations, update the matching guide here in the **same** PR
as the code (and [CHANGELOG.md](../CHANGELOG.md)). See
[AGENTS.md: Documentation](../AGENTS.md#documentation-required).

## Guides

| Guide | Topics |
|-------|--------|
| [CLI](./cli.md) | `video2exr`, `exr2video`, `proxy` (`--watch`, `--watermark`), `transcode`, GUI launch flags |
| [GUI](./gui.md) | Tabs, Video → Proxy and the watch folder, Transcode, invisible watermark, browsers, player, overlays, preferences, post-convert |
| [Quick Look on macOS](./quicklook.md) | Spacebar previews and Finder icons for EXR, DPX, and optional stills |
| [ProRes and VideoToolbox](./prores.md) | Software vs Apple VideoToolbox vs oxideav, bit depths |
| [Nuke](./nuke.md) | Nuke menu integration |
| [R3D / N-RAW](./r3d.md) | Optional RED R3D SDK (proprietary; build and license) |
| [Blackmagic RAW](./braw.md) | Optional BRAW SDK (proprietary; Linear ACES AP0; Release CI feed) |
| [Releasing](./releasing.md) | Pointer to maintainer release process |
| [Microsoft Store](./msix.md) | Partner Center .msixupload and self-distribution .msix |
| [12-bit ProRes (oxideav)](./plan-12bit-prores-oxideav.md) | Experimental RDD-36 ProRes. 4444 and XQ are 12-bit. 422 decodes as 10-bit |
