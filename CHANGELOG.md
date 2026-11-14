# Changelog

All notable changes to **EXR Converter** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Agents and contributors:** update this file in the same change set as user-visible
work. See [AGENTS.md](./AGENTS.md#changelog-required). Do not ship a release without
rolling the `[Unreleased]` section into a versioned heading.

---

## [Unreleased]

### Added

- Light and dark themes. **Preferences → Interface → Theme** is System (the default; follows the operating system and changes with it while the app runs), Light, or Dark. System uses dark when the platform does not report a setting. Switching applies at once.
- Windows installer option **Add EXR Converter to PATH**, plus an `exr-converter` command that waits for the app and shows its output and exit code, so `exr-converter proxy -i clip.mov` works from any terminal (and from agents). Uninstalling removes the PATH entry.
- **Help → Agent Skills** shows the `skills.md` instructions that let a coding agent (Claude Code, Codex, Cursor) drive the command line, with the command for this install filled in (the app or AppImage path, or the source checkout), and a **Copy to clipboard** button. The same file is `skills.md` in the repository.
- The input of Video → EXR and EXR → Video is shown as a card: a drop target while empty, then a thumbnail with the clip or sequence name, resolution, frame count, frame rate, and range. It flags missing frames in a sequence. Click it to browse. The path field stays below it.
- Motion: the tab underline slides, progress glides between values, Convert reacts to hover and press, and the source card fades. All of it is under 300 ms. Set `EXR_CONVERTER_REDUCE_MOTION=1` to turn it off.
- The log shows four lines by default under a header with the latest status. Clicking **Log** collapses it to the header line, and dragging its top edge resizes it (the height is remembered). It opens by itself when a conversion fails. No animation.
- Browse dialogs: drawn icons for places, volumes, favorites, and search in place of emoji; a compact folder tree; a table without grid lines; and Open as the accent button.
- Video → Proxy and Transcode show a hint in the empty clip list: drop clips or folders, or use Add Clips… / Add Folder….
- Sections are cards: a raised surface with a title and a one-line description. Labels share one column, so fields line up, and menus are sized to what they hold in place of stretching across the window. Related short fields share a row (Scale with Pixel aspect, Codec with Frame rate, Start frame with Padding).
- Keyboard shortcuts: Ctrl+Return (⌘Return on macOS) starts the conversion, and Ctrl+1 to Ctrl+4 switch tabs. Switching tabs from the keyboard does not animate.

### Changed

- Menus and fields in the forms stretch with the card again (codec, preset, EXR compression, slate fields) in place of a fixed width; only short number boxes stay narrow.
- Form fields grow with the card. The scrollbar sits in a gutter so it no longer overlaps the last column of controls.
- Browser Preview decodes the frame on screen at a size that covers the pane, including screen pixel density and anamorphic squeeze. R3D, N-RAW, and Blackmagic RAW use the SDK reduced-resolution decode. OpenEXR uses a mip or ripmap when the file has one, and reads RGB only. CinemaDNG uses a smaller embedded preview when one covers the pane, or a half-size debayer when the pane fits in half the frame. Video scales in the decoder. The full viewer and convert output are unchanged.

- Refreshed the interface, keeping the neutral dark look. Orange now marks only the primary action, the selection, and keyboard focus. Sections are a title over a line in place of nested boxes, tabs are underlined, and dim text is lighter so small hints are easier to read.
- The OCIO config picker is one header row. The status beside it shows the color space count, and its tooltip has the config name or the load error.
- The progress bar appears only while a job runs. The When done options, Cancel, and Convert share one row, with Convert at the end.
- Color space and codec pickers look like the menus they are: left-aligned text and a chevron.
- Reset range, edit slate, codec and compression settings, and the player transport use drawn icons in place of text symbols, which showed as color emoji on some systems.
- The log starts small (four lines) and remembers whether it is collapsed and the height you dragged it to.

### Fixed

- The form pages' scroll bar is flush with the window edge, with the cards inset 12 px on both sides and the track level with the first and last card. The cards line up with the OCIO row and tabs.
- Windows: the packaged app now prints `--help`, `--version`, and CLI progress to the terminal you ran it from. It printed nothing before.
- About lists every library the app uses with its version: PyAV (and its FFmpeg), NumPy, fileseq, PyOpenGL, ONNX Runtime, and oxideav-prores when built in. `THIRD_PARTY_NOTICES.md` gains a PyAV / FFmpeg entry.
- Transcode: Size (custom width and height) is always shown and only enabled for Custom, so choosing Custom no longer resizes the card and re-lays out the page. Container is disabled when the codec has only one (all ProRes profiles are `.mov`).
- Preferences: the slate thumbnail frame is three radio buttons in a row (First, Middle, Last) in place of a menu.
- Preferences: the OK / Cancel row sits on a full-width bar with a line above it, like the main window's.
- Selecting a tab no longer shifts the others: every tab reserves the width of its bold label.
- The app icon has a transparent background around the wolf head in place of the purple square.
- A small blank window no longer flashes open before the main window appears.
- Convert: Cancel is offered once; after it is pressed the progress line stays until the conversion reports, and Cancel can no longer be pressed again or while the finished conversion is closing. Convert stays disabled until the previous run has fully ended.
- The first time you browse for a file, the main window no longer closes and reopens with the browser.
- The form no longer clips its right edge in a narrow window, the scrollbar is visible again, and the log and the Convert / Cancel row each have a clear top border. The Convert / Cancel row is vertically centred with the When done options.
- Opening an R3D or BRAW clip without that SDK no longer dumps every search path into the console. The status line says the decoder is unavailable in this build.
- `make run` works on Windows (no Unix `env -u`) and does not compile R3D, BRAW, ARRI, CinemaDNG, or oxideav. Those native pieces are fetched or built on GitHub Actions.
- Form fields on Preferences and Transcode keep one label column on Windows. Longer labels wrap so every card's controls stay on the same x.
- Transcode: the naming hint no longer draws over the Pixel aspect menu.
- Dropping a file on the window accepts every video type the app reads (for example `.mts`, `.m2ts`, `.mpg`, `.dv`), not a shorter list of eleven.
- Slate & Overlay Editor: Cancel (or Esc) now discards the edits made in that session. Before, every keystroke was already saved, so Cancel kept them.
- OCIO config: choosing the Custom file row again opens the file picker, so you can switch to another `.ocio` file without selecting a different config first.
- Choosing an input no longer updates the window from a background thread. The async probe applied its result (path, frame range, color space) from the worker thread, which Qt does not allow and which could stall or crash the interface.
- Browse dialogs open wide enough to read folder names and the Name column.
- HDR (PQ, HLG), Rec.2020, sRGB, and linear video clips get the right source colour space guess. They were all read as Rec.709.
- Transcode and proxy no longer overwrite the source when the output name differs only in letter case on a case-insensitive macOS volume (for example `C0001.MOV` to `C0001.mov`).
- `proxy` CLI: clips with the same name in one run (`clip.mov` and `clip.mxf`, or `A/clip.mov` and `B/clip.mov` into one output folder) each get their own proxy, numbered `_1`, `_2`, …. Before, the second was skipped as existing or overwrote the first.
- Watch folders no longer reprocess every clip after a network share drops out for one scan.
- Resizing the browser Preview while a frame decodes no longer stops video prefetch for good, or slows EXR prefetch a little more on each resize.
- EXR → Video writes to a hidden `.name.partial.ext` file and renames it when done. A cancelled or failed encode no longer leaves a truncated video that looks playable.

## [0.14.1] - 2026-11-12

### Added

- Anamorphic footage keeps its squeezed pixels and is tagged so other apps can unsqueeze it. OpenEXR `pixelAspectRatio` is a real float (2.0 means each pixel is twice as wide, the same number Nuke uses). Video, proxy, and transcode output get a matching sample-aspect tag. Auto reads the factor from camera metadata, QuickTime pixel aspect, CinemaDNG `DefaultScale`, and existing EXR or DPX headers. `--pixel-aspect` and the Pixel aspect menu can force it.
- The viewer stretches anamorphic and non-square pictures for display and labels the factor (for example `2x anamorphic` or `PAR 16:11`) on the frame, in Inspect, and in the convert log.
- Slate, burn-in, and watermark text is drawn so it stays round after that unsqueeze.

### Fixed

- A text `pixelAspectRatio` of `2.0` no longer collapses to 1.0 in the EXR header.
- Transcoding a clip that already has a pixel-aspect tag no longer drops that tag.
- Broadcast 16:9 sample aspect (for example 720x576) is not written as a cinema lens squeeze.

## [0.14.0] - 2026-11-12

Published release. v0.13.3 was tagged but not published: the macOS test run crashed in the video player tests.

### Fixed

- The video player's frame prefetcher could be torn down on a decode thread, which crashed the app on macOS. It no longer holds a strong reference from the decode thread, and shutdown stops its timer.

## [0.13.3] - 2026-11-12

### Added

- ARRIRAW and ARRICORE (`.ari` sequences and ARRI `.mxf`) decode again in the release builds for macOS, Windows and Linux, through the ARRI Image SDK, on the GPU when one is available. Help → About shows the ARRI Image SDK and MXF Library versions, and ARRI's notices ship inside the app.

## [0.13.2] - 2026-11-11

Published release. v0.13.1 was tagged but not published: Windows signing found `signtool.exe` and then rejected it. This build includes the 0.13.0 and 0.13.1 notes.

### Fixed

- Windows signing reads the SDK version from the kit folder above `x64` (for example `10.0.26100.0`). The previous check used the `x64` directory name, which is not a version, so the only `signtool` on the runner was skipped.

## [0.13.1] - 2026-11-11

Published release. v0.13.0 was tagged but not published, because the Windows build stopped before it could sign. This build includes that section: ProRes RAW, hardware video encode and decode, faster OCIO and ProRes, signed Windows setup, and no Microsoft Store package.

### Fixed

- Windows signing selects `signtool` by the Windows SDK kit folder version. The binary in `10.0.26100.0` reports file version 4.00, which the previous check rejected.

## [0.13.0] - 2026-11-11

### Added

- Apple ProRes RAW and ProRes RAW HQ (`.mov`) on Video → EXR, Inspect, thumbnails and the player. FFmpeg decodes a 16-bit linear Bayer mosaic; the app applies the clip's black/white level, as-shot white balance, color matrix, gain and recommended crop, demosaics, and writes scene-linear `ACES2065-1`. Nothing is clipped. Half size and smaller converts bin 2x2 Bayer quads. See [docs/prores-raw.md](./docs/prores-raw.md).

- Hardware H.264 / HEVC encoding on EXR → Video, Video → Proxy, and Transcode: NVIDIA NVENC, Intel Quick Sync, or AMD AMF on Windows and Linux, Windows Media Foundation (the OS encoder, as a last resort on Windows), and VideoToolbox on macOS, for 8- and 10-bit output. 10-bit AV1 on Transcode uses NVENC, Quick Sync, AMF, or Media Foundation when the GPU has an AV1 encoder. The app checks that the encoder opens at the output size, logs which one it used, and encodes again in software if the GPU encoder fails. **Preferences → Video encoding** and `--encoder software` (on `exr2video`, `proxy`, and `transcode`) turn it off. See [docs/gui.md](./docs/gui.md#preferences-file--preferences) and [docs/cli.md](./docs/cli.md).

- GPU decoding of H.264, HEVC, AV1, and VP9 files on Video → EXR, Transcode, Video → Proxy, and the player: NVIDIA NVDEC, Direct3D 11 / DXVA2 on Windows (any GPU vendor), VAAPI or Quick Sync on Linux, and VideoToolbox on macOS. Only 8- and 10-bit 4:2:0 streams use it (GPUs cannot decode 4:2:2 / 4:4:4 or 12-bit), the decoded pixels match software decode exactly, and the app decodes in software when the GPU cannot. It helps most on high-bitrate camera files, and costs a little on light ones when the CPU has many cores. **Preferences → Video encoding → Decoding** and `--decoder software` (on `video2exr`, `proxy`, and `transcode`) turn it off. See [docs/gui.md](./docs/gui.md#preferences-file--preferences) and [docs/cli.md](./docs/cli.md).

### Changed

- The Windows setup `.exe` and the app executable inside it are signed with Azure Artifact Signing (publisher Medeu Global LLC) when the release has the `AZURE_*` signing secrets.

- PyAV 19 (FFmpeg 9.0) is now required. FFmpeg 8 decoded ProRes RAW to 12 bits without linearizing it.
- EXR → Video and Transcode run OCIO on every CPU core. ACES 2.0 display transforms were the bottleneck: a 4K EXR → H.264 / HEVC render is now 2 to 4 times faster, with identical pixels.
- Software ProRes (`prores_ks`) and CineForm encode with frame threads, up to about 4 times faster at 4K. DNxHR and FFV1 keep slice threads, which are faster for them.
- 10-bit 4:2:2 and 4:4:4 Y'CbCr conversion (ProRes, DNxHR HQX / 444) runs in parallel strips, about 8 times faster per frame, with identical output.

### Fixed

- Packaged builds launch again. PyAV 19 ships Cython sources next to its compiled modules, so the freeze step either crashed or left a module out (`No module named 'av.utils'`). The build now keeps the compiled modules and includes each of them.
- The Windows signing step uses the Windows SDK `signtool` in each kit's `x64` folder. That binary reports file version 4.00, so the kit folder name (for example 10.0.26100.0) is what selects it.

### Removed

- The Microsoft Store packages (`.msixupload` and `.msix`), the Store publish step, and the Store screenshot generator. Windows releases ship the setup `.exe` only.
- Public release builds no longer decode ARRIRAW or ARRICORE (`.ari` sequences and ARRI `.mxf`). Those files report as unsupported camera RAW.

## [0.12.14] - 2026-11-02

### Added

- Transcode output names and folders take naming variables, in the same `<token>` style as the slate: `<reel>`, `<srcresolution>`, `<srcwidth>`, `<srcheight>`, `<srcfps>`, `<timecode>` and `<parent>` join `<name>`, `<codec>`, `<width>`, `<height>`, `<fps>` and `<date>`, plus `<resolution>` for the output size. The reel comes from the clip's metadata, or from a camera clip name such as `A_0001C001_…`. Right-click the Name or Folder field to insert one.
- A `/` in the Transcode name pattern (or `transcode --name`) makes sub-folders, for example `<reel>/<name>/<srcresolution>/<name>_<codec>`. The custom folder (and `--output-dir`) can use variables too.
- Transcode Name and Folder fields expand Unix-style environment variables on every platform: `$VAR`, `${VAR}`, and `${VAR:-default}`. An unset variable with no default stops that clip with an error instead of writing `$VAR` into the path.
- ARRIRAW and ARRICORE support (`.ari` sequences and ARRI `.mxf`) through the ARRI Image SDK, in the release builds for macOS, Windows and Linux. Help → About shows the ARRI Image SDK and MXF Library versions, and ARRI's notices ship inside the app.
- ARRI clips decode on the GPU when one is available (Metal on Mac, CUDA or OpenCL elsewhere) and fall back to the CPU otherwise.

- New [Variables](./docs/variables.md) docs page listing every `<token>` (Transcode paths, slate, burn-in, watermark), its aliases, and environment variable rules.

### Changed

- Slate and burn-in variables ignore `_` and `-` in names, so `<shot_types>` and `<shot-types>` both work.

---

## [0.12.12] - 2026-10-29

### Added

- The macOS Quick Look preview has the player's controls: exposure gain, gamma, an input color space menu, and an OCIO display / view menu with every SDR view of the sRGB and Display P3 displays. It remembers the view you pick.
- Quick Look reads the EXR header to pick the input color space, in the same order as the player (`exrconverter:dstColorSpace`, `colorInteropID`, chromaticities, `oiio:ColorSpace`). Log and video spaces from the config work as well as linear ones.
- Quick Look caches decoded frames and reads ahead on several threads, with a green cache bar under a frame ruler like the player's, plus previous / play / next buttons and the file's resolution.
- The browsers' folder tree shows media under each folder: image sequences as one row each (`plate.####.exr  1001-1100`) in the sequence browser, video files in the video browser. Click one to select it (and play it in Preview); double-click to open it.
- Browser Preview has **In** and **Out** buttons (keys **I** / **O**) to mark a frame range. Playback loops inside it, and **Open** puts that range in the convert tab's **Frames** field.

### Changed

- Software and VideoToolbox codec labels say ProRes 422 / 4444 (`.mov`) without a vendor name. Docs and the About dialog use the same wording.
- Docs describe `.crm` as camera RAW plugin support and no longer list unsupported camera RAW extensions.
- On a Display P3 screen, Quick Look now opens in the config's Display P3 view (`ACES 2.0 - SDR 100 nits (P3 D65)` on the bundled config) and tags the picture as Display P3. Other screens keep sRGB.
- **Inspect** in the browsers is now called **Metadata**.
- The browsers no longer read the disk on the UI thread. Folders in the tree are listed in the background the first time they open, folder contents show before their resolution, codec, and other columns fill in, and the Metadata pane, Preview loading, and mounted-drive checks run in the background. Slow, network, and Dropbox folders no longer beachball the dialog.
- **Find sequence** indexing and **Search folders** run in a separate background process, so a large search no longer slows down the dialog.
- Re-entering Preview on the same clip keeps its cache and in/out points instead of loading it again.
- **Transcode** always converts color with OCIO. The clip list has an **Input color space** column filled in from each clip's metadata. Set a space on a folder row to apply it to every clip inside, or expand the folder and set clips one by one. One **Output space** applies to every clip.
- **Transcode** shows the output space and invisible watermark in a plain **Color and watermark** section instead of a collapsed one.
- Preferences → Invisible watermark: the payload defaults to `exrcnvrt` and the field only accepts up to 8 printable ASCII characters. Strength is held to 0.10–2.00.
- Preferences: sections no longer stretch to fill the window, so the Slate box has no empty space below **Thumbnail frame**.
- The Video → Proxy watch folder is no longer a collapsed section. Its folder, options, and **Start Watching** are always shown in one row, and the status is in the group title.

### Fixed

- Quick Look sequence playback no longer runs slower than 24 fps. The wait for the next frame used to start after the current frame finished decoding, so decode time was added to every frame.
- Preview flickered while scrubbing because the drive list in the sidebar was rebuilt every 3 seconds; it now changes only when a drive is added or removed.
- The Mac boot drive was listed twice in the folder tree (again under `/Volumes`).
- The clear button in the browser search fields was not vertically centred on macOS.
- **Search folders** in the browsers skipped every subfolder on macOS (APFS folders looked like online-only cloud stubs), so it only found matches at the top level.

---

## [0.12.11] - 2026-10-29

### Added

- **Invisible WM** checkbox on EXR → Video embeds an invisible TrustMark watermark in every frame, slate included. The CLI has `exr2video --watermark` and `--watermark-strength`.
- **Help → Documentation** opens the docs at https://vfx.systems/docs/exr-converter.
- Preferences has an **Invisible watermark** section for the EXR → Video payload and strength. The payload can use `{user}`, `{host}`, `{date}`, `{time}`, and `{src}`, and shows what it will embed. TrustMark text mode holds 8 characters, so longer results are cut.

### Changed

- Quick Look settings moved out of Preferences into **File → Quick Look…**, a menu item that only appears on macOS.
- The docs moved to https://vfx.systems/docs/exr-converter. The About dialog links vfx.systems instead of ocio.cc, and the Windows installer and Linux packages use the new docs URL as the homepage.
- The macOS Quick Look and Finder thumbnail extensions are off until you turn them on in that dialog. Each launch re-applies the choice, so existing installs that had them on will need to turn them on once.

### Removed

- Experimental oxideav ProRes 422 (`prores_ox_proxy`, `prores_ox_lt`, `prores_ox_422`, `prores_ox_hq`). Those presets wrote 12-bit coefficients into a 10-bit 422 fourcc, which Apple, Resolve, Premiere, and Nuke do not read as 12-bit. 422 Proxy, LT, 422, and HQ stay 10-bit 4:2:2 in `.mov` (software ProRes, and VideoToolbox on macOS). 12-bit ProRes is 4444 and 4444 XQ only.

---

## [0.12.10] - 2026-10-29

Published release. v0.12.5 through v0.12.9 were tagged but not published. This build includes those sections: camera RAW plugin support for `.crm`, CinemaDNG frame sequences, the Transcode tab, fuzzy sequence search, and a Microsoft Store package that does not request `unvirtualizedResources`.

### Fixed

- Windows release packages pass the camera RAW plugin to the packager with normal line endings, so the plugin is included in the app.
- macOS no longer quits at launch while publishing Quick Look preferences. The App Group folder lookup passed a null string into Foundation and aborted the app before the window opened.
- The RHEL 8 package leaves out the RED R3D runtime. `REDDecoder-x64.so` and the RED GPU libraries need glibc 2.29, so `.r3d` and `.nev` are unavailable in that package. macOS and Windows packages still include them.

---

## [0.12.9] - 2026-10-29

Published release. v0.12.5, v0.12.6, v0.12.7, and v0.12.8 were tagged but not published. This build includes those sections: camera RAW plugin support for `.crm`, CinemaDNG frame sequences, the Transcode tab, fuzzy sequence search, and a Microsoft Store package that does not request `unvirtualizedResources`.

### Fixed

- Windows release packages pass the camera RAW plugin to the packager with normal line endings, so the plugin is included in the app.
- macOS no longer quits at launch while publishing Quick Look preferences. The App Group folder lookup passed a null string into Foundation and aborted the app before the window opened.
- Linux packages for RHEL 8 leave out RED's OpenCL library, which needs glibc 2.29. R3D decode on Linux uses CUDA when an NVIDIA driver is present, and the CPU otherwise.

---

## [0.12.8] - 2026-10-29

Published release. v0.12.5, v0.12.6, and v0.12.7 were tagged but not published. This build includes those sections: camera RAW plugin support for `.crm`, CinemaDNG frame sequences, the Transcode tab, fuzzy sequence search, and a Microsoft Store package that does not request `unvirtualizedResources`.

### Fixed

- Windows release packages pass the camera RAW plugin to the packager with normal line endings, so the plugin is included in the app.
- macOS no longer quits at launch while publishing Quick Look preferences. The App Group folder lookup passed a null string into Foundation and aborted the app before the window opened.

---

## [0.12.7] - 2026-10-29

Published release. v0.12.5 and v0.12.6 were tagged but not published. This build includes those sections: camera RAW plugin support for `.crm`, CinemaDNG frame sequences, the Transcode tab, fuzzy sequence search, and a Microsoft Store package that does not request `unvirtualizedResources`.

### Fixed

- The Rocky Linux 8 release build no longer stops while printing the glibc version.

---

## [0.12.6] - 2026-10-29

Published release. v0.12.5 was tagged but not published. This build includes that section: camera RAW plugin support for `.crm`, CinemaDNG frame sequences, the Transcode tab, and fuzzy sequence search.

### Changed

- Microsoft Store installs keep settings, presets, and the TrustMark model cache in the package's own virtualized AppData and registry. The package no longer requests the `unvirtualizedResources` capability, and those files are not shared with a setup `.exe` install of the same app.

---

## [0.12.5] - 2026-10-29

### Added

- Camera RAW plugin support for `.crm`. Release builds include the plugin and ship its runtime notices inside the app bundle. Without the plugin, Inspect and convert report `Camera RAW plugin not installed`, and the codec column shows `Camera RAW (plugin not installed)`. These files are never opened with PyAV. `proxy` and `transcode` do not accept them.
- CinemaDNG frame sequences (`.dng`) open on EXR to Video and through `video2exr` / `exr2video`. Uncompressed and lossless JPEG Bayer frames are supported. DJI Inspire 3 and Zenmuse X9 frames are scene-linear P3-D60 with the recommended +1.4 stops already applied. Other CinemaDNG sequences use the file color matrices into ACES2065-1.
- Linux releases are built on Rocky Linux 8 and run on RHEL, Rocky, and Alma 8 and newer (glibc 2.28). The same build publishes the AppImage, a relocatable tarball (`exr_converter-linux-x86_64.tar.gz`) for `/opt`, `/software`, and Rez, and an RPM (`exr_converter-linux-x86_64.rpm`) that installs to `/opt/exr-converter`. Settings and caches stay in the XDG directories, so the install folder can be read-only.
- macOS Quick Look previews scene-linear OpenEXR and DPX through the app's default OCIO display view (a baked 3D LUT, exposure 0 and gamma 1). Numbered frames play in the Spacebar preview when the folder can be read (home, `/Volumes`, and `/tmp`). Notarization accepts those read-only exceptions. **File → Preferences… → Quick Look** still chooses the formats. Movies, BRAW, and R3D are unchanged. See [Quick Look on macOS](docs/quicklook.md).
- New **Transcode** tab for batch video to video transcodes: ProRes 422 Proxy / LT / 422 / HQ / 4444 / 4444 XQ (plus VideoToolbox ProRes on macOS), DNxHR LB / SQ / HQ / HQX / 444, H.264, HEVC 8 / 10 / 12-bit, and AV1 when the FFmpeg build has SVT-AV1. The container follows the codec (`.mov`, `.mxf` for DNxHR, `.mp4`, `.mkv` for AV1). Options for size (keep, 75 / 50 / 25%, fit 2160p / 1080p / 720p, or custom), frame rate (keep or conform), audio (copy, AAC, 24-bit PCM, or none), CRF or target bitrate and speed preset for long-GOP codecs, output folder, a `<name>_<codec>` style file name pattern, and skip / overwrite / add-a-number when the file exists. No color change by default; an optional OCIO transform uses the window's config. The invisible TrustMark watermark from Video → Proxy is available too. Each clip in the list shows its progress, then a check mark or the error. Settings are remembered between sessions.
- `transcode` CLI subcommand with the same options as the tab, and `--mode transcode` to open the GUI on that tab.
- The sequence browser has a **Find sequence** field for typo-tolerant search by sequence name. Folders you open are indexed with their subfolders in the background without freezing the dialog. Click a result or press Enter to jump to that folder with the sequence selected. The index lives only while the browser is open and never downloads cloud online-only files. Adds the `rapidfuzz` dependency.

### Changed

- The bundled ACES Studio config adds Linear P3-D60, so a CinemaDNG sequence can convert to the existing Linear D-Gamut or D-Log D-Gamut spaces.
- Qt is pinned to 6.9.2 (PySide6-Essentials and shiboken6) on macOS, Windows, and Linux, down from 6.11.0. All platforms now ship the same Qt. Qt 6.10 and newer Linux wheels need glibc 2.34, which does not run on RHEL 8, so 6.9.2 is the newest release that covers every supported system.
- Video → Proxy now runs on the same encode engine as the Transcode tab. With the invisible watermark on, the mark is added at 16-bit precision instead of passing frames through 8-bit RGB. HEVC proxies are tagged `hvc1` so QuickTime plays them.
- The docs site describes the Video → Proxy tab in more detail: source formats, each proxy codec with its container and bit depth, what the watch folder skips, and which settings are remembered. It has a new **Invisible watermark** section covering the payload, strength, model download and cache folders, `scripts/trustmark_detect.py`, and what can remove the mark. The home page lists the invisible watermark, shows the latest release version, and notes that the Windows `.msix` / `.msixupload` files are for the Microsoft Store while the setup `.exe` stays the direct download.
- Software video encodes (ProRes, DNxHR, H.264, HEVC, CineForm, and the other YCbCr codecs) convert RGB to YCbCr with BT.709 limited-range math and tag the stream BT.709 matrix. Before, FFmpeg's scaler used its BT.601 default and left the file untagged, so other apps decoded colours slightly off. 10-bit and 12-bit 4:4:4 and 4:2:2 formats are converted with exact float math in the app.
- MOV and MP4 files now always get a `colr` atom. Primaries and transfer follow the destination colour space (for example Rec.1886 Rec.709 is BT.709 / BT.709, sRGB is BT.709 / sRGB). Log and ACES encodings such as ACEScct have no H.273 code and are tagged unspecified.
- The experimental oxideav ProRes writer is built from a vendored, patched copy of `oxideav-prores` 0.1.1 in `native/exr_prores/vendor/oxideav-prores`. See `EXR_CONVERTER_PATCH.md` there and `THIRD_PARTY_NOTICES.md`.

### Fixed

- The color space button stays the same height as the other convert fields on macOS with Qt 6.9.2.
- On Windows, Transcode **Add a number** treats two planned output paths as the same file when they differ only by letter case, so a batch does not write two clips to one name.
- oxideav ProRes 4444 and XQ (`prores_ox_4444`, `prores_ox_xq`) wrote the chroma 8x8 blocks of each macroblock in the wrong order. FFmpeg, Resolve, and QuickTime swapped the top-right and bottom-left chroma blocks, which showed as blocky colour errors (up to 0.18 in ACEScct). The writer and the built-in decoder now use the standard column order. A test decodes the output with FFmpeg and checks the chroma. Re-encode 4444 and XQ files made with 0.12.4 or earlier.
- `exr2video --fps` accepts rational rates such as `24000/1001` and `30000/1001`, and decimals such as `23.976`. Rationals are written to the file exactly.
- Packaged (Nuitka) builds no longer print `the program tried to call itself with '-c' argument` and a `resource_tracker` warning when converting with worker processes. Python 3.13.7 changed how the multiprocessing resource tracker starts, and the build's old rewrite of that command no longer matched. The app now sets up multiprocessing at startup: `freeze_support()`, the `spawn` start method, the app executable for workers, and the resource tracker started through the app's own flag.
- oxideav ProRes files no longer claim BT.709 primaries and transfer for every destination. The frame headers and `colr` atom follow the destination colour space, so an ACEScct file is tagged BT.709 matrix with unspecified primaries and transfer. The frame rate code in the frame header is filled in too.
- EXR sequences without an EXR Converter tag or `colorInteropID` are matched to a source colour space from their `chromaticities` header (Rec.709, ACES AP1, ACES AP0, P3-D65, Rec.2020). When there is no usable metadata, `exr2video` prints a warning that names the colour space it assumes (the OCIO `scene_linear` role, ACEScg on the bundled config) and suggests `--src`.
- Software ProRes 4444 and XQ had a small colour offset after decode (about +2.5 12-bit codes on luma and different offsets on each chroma channel on near-grey content, much larger on saturated colours) because of the BT.601 default above. Decoded values now match the source within normal rounding.
- Software ProRes 4444 and XQ wrote an alpha plane (`yuva444p10le`) even when the source had no alpha. The alpha plane is now written only when the source frames have an alpha channel, and the source alpha is carried through.
- oxideav ProRes files named their compressor with a vendor codec label in the MOV sample description. They now say `RDD 36 ProRes-compatible`, matching the docs.

---

## [0.12.4] - 2026-10-21

### Added

- Windows releases can include two Microsoft Store packages from the same build. `exr_converter-windows-x86_64.msixupload` is the unsigned Partner Center upload (a zip of the `.msix` and, when the build has PDB symbols, an `.appxsym`). `exr_converter-windows-x86_64.msix` is the self-distribution package. It is signed with `signtool` when the Azure Artifact Signing secrets are set, and left unsigned otherwise. An unsigned MSIX does not install. Both files are skipped until the `MSIX_IDENTITY_NAME` and `MSIX_PUBLISHER` repository variables are set. The setup `.exe` is unchanged.
- A release can submit that `.msixupload` to an existing Microsoft Store product when the Store publish secrets and `MSSTORE_APP_ID` are set. The first Store submission is still created in Partner Center. A missing setting or a publish error does not block the GitHub Release.

### Fixed

- The MSIX manifest now passes MakeAppx schema validation. The write-virtualization exclusions sit inside `<Properties>` and the package declares the `unvirtualizedResources` capability.
- A Microsoft Store install keeps settings, presets, and the TrustMark model cache in the user profile. The Store install folder is read-only. The bundled OCIO config is read from the package in place.

---

## [0.12.3] - 2026-10-17

0.12.2 was tagged but never published because its macOS release build failed. 0.12.3 includes all of the 0.12.2 changes listed below.

### Fixed

- macOS release builds include the ONNX Runtime library used by invisible watermarks. Apple Silicon wheels name that library differently from its install id, which stopped Nuitka. Intel Macs use ONNX Runtime 1.23.x, the newest release that still publishes an x86_64 wheel.

---

## [0.12.2] - 2026-10-17

### Added

- **Invisible watermark** on the Video → Proxy tab and the `proxy` CLI, off by default. Enable it, enter up to 8 ASCII characters, and set a strength. Every proxy frame gets an Adobe TrustMark mark the official decoder can read. The encoder model downloads once into a cache folder (SHA256 checked) and is not included in the app. `scripts/trustmark_detect.py` reads a mark back from a video. (#76)

### Fixed

- The Windows desktop and taskbar icon is sharp again: the app now ships every shell icon size instead of scaling one. (#71)

### Changed

- CI type-checks with basedpyright and fails on errors; the 12 existing type errors are fixed. (#62)
- CI runs Linux-only by default and cross-OS tests on code PRs, with concurrency, timeouts, and caching. (#75)
- GitHub Actions dependencies bumped (7 updates). (#68)

---

## [0.12.1] - 2026-10-14

### Changed

- The optional RED R3D and Blackmagic RAW bridge source moved out of this public repository into private SDK repositories (SDK license compliance). Release builds fetch it at build time; no user-facing change otherwise.
- The **Video → Proxy** tab is about the same height as Video → EXR and EXR → Video. Codec and resolution share a row, the custom output folder appears only when that location is chosen, and the watch-folder path folds into a section under **Start Watching**.
- The Windows installer and Add/Remove Programs list **DerekVFX** as the publisher and link to https://exr-converter.derekvfx.ca/. The Python package author name is DerekVFX as well.
- The docs site moved to https://exr-converter.derekvfx.ca/ (hosted on Vercel) and
  is now built with Astro instead of Hugo; `make docs-serve` / `make docs-build` need Node 22.12+.
  Old `derek-rein.github.io/exr-converter` links redirect to the same page there.
- Public docs now match the current app: signed and notarized macOS builds
  (Medeu Global LLC), the Video → Proxy tab, the `proxy` CLI and watch folder,
  expanded camera logs, and ProRes bit depths. Install, platform, and CLI pages
  follow `video2exr`, `exr2video`, and `proxy --help`. Output is described as
  video or EXR only.
- The docs home page has direct downloads for the latest release: Windows x64
  installer, macOS Apple Silicon and Intel DMGs, and the Linux x86_64 AppImage.

### Fixed

- The Windows app, desktop shortcut, and installer icon include 16 px through
  256 px frames, so the desktop icon stays sharp instead of upscaling a single
  16×16 image.

---

## [0.12.0] - 2026-10-10

### Added

- **Video → Proxy** tab for making editing proxies, modelled on DaVinci
  Resolve's proxy generation. Queue clips or whole folders, pick a proxy codec
  (ProRes 422 Proxy by default, also ProRes LT, VideoToolbox ProRes on macOS,
  DNxHR LB / SQ, H.264, HEVC) and size (half by default, down to one-eighth or
  fit to 1080p / 720p / 540p). Proxies go to a `Proxy` folder next to each
  clip with the same name so editors can relink, and keep the source look,
  timecode, and audio.
- Watch folder on the proxy tab: new clips are converted automatically once
  they finish copying, with options for subfolders and for clips already in
  the folder.
- `proxy` CLI subcommand for batch proxies, and `proxy --watch DIR` to run the
  watch folder headless until Ctrl-C. GUI `--mode proxy` opens the new tab.

---

## [0.11.0] - 2026-10-09

### Added

- macOS release DMGs (Apple Silicon and Intel) are signed with a Developer ID
  Application certificate for Medeu Global LLC, then notarized and stapled by
  Apple when the Apple signing secrets are set. The app opens normally, so the
  `xattr -cr` quarantine workaround is no longer needed. First launch may still
  show the standard "downloaded from the internet" prompt.

### Fixed

- macOS notarization records the Apple submission id as soon as the upload
  finishes, then checks that same submission for up to five hours. A rejection
  still prints the notary log. The build does not upload the DMG again.
- Developer ID signing of the macOS app no longer stops on data files that
  Nuitka places under `Contents/MacOS` (OCIO configs, `pyproject.toml`,
  package data). Those files move to `Contents/Resources`, with relative
  symlinks left in place so the app still finds them.

### Changed

- The macOS app bundle identifier is `com.vfxtools.exrconverter` (the on-disk
  name stays **EXR Converter**) so Apple notarization accepts the bundle.
- macOS builds made without the Developer ID certificate secrets keep the
  ad-hoc signature and skip notarization.

---

## [0.10.2] — 2026-10-08

### Added

- Bundled ACES Studio config adds **Nikon N-Log** (N-Gamut / Rec.2020),
  **Fujifilm F-Log**, **F-Log2**, and **F-Log2 C**, **Leica L-Log** (Rec.2020,
  and Rec.709 for the SL Typ 601),   **Sony S-Log** and **S-Log2** (S-Gamut),
  **KineLOG3**, **GoPro Protune / GP-Log / GP-Log2**, and
  **Blackmagic Film Gen 4** (Broadcast, Pocket 4K, Pocket 6K). Existing
  colorspace names are unchanged. N-Log is the published curve (a generated
  1D LUT), not Nikon's downloadable cube. Sources, licenses, and measured
  error versus official equations and LUTs are in `resources/ocio/CAMERAS.md`.
  Rec.709 camera cubes that bake in a vendor tone map are not used as the
  scene-linear transform. DJI D-Log M, Samsung Log, and Z CAM Z-Log2 are not
  included: no scene-linear equation was available to match.
- Bundled ACES Studio config also adds **OPPO O-Log** (Rec.2020; 18%
  reflectance stays on linear 0.18) and **Apple Log 2** (Apple Wide Gamut,
  same curve as Apple Log). `resources/ocio/CAMERAS.md` lists popular
  2016–2026 cameras and whether each log is already in the config, added
  here, or left out because no public equation could be checked.

### Changed

- **oxideav ProRes 422** (`prores_ox_proxy`, `prores_ox_lt`, `prores_ox_422`,
  `prores_ox_hq`): codec names and help now say **12-bit internal, 10-bit on
  decode**. The encoder still writes 12-bit coefficients, and FFmpeg, Resolve,
  and QuickTime read the 422 fourcc (`apco` / `apcs` / `apcn` / `apch`) as
  10-bit. For 12-bit that other applications see, use `prores_ox_4444` or
  `prores_ox_xq`.

---

## [0.10.1] — 2026-09-21

### Fixed

- **Video browser crash on `.r3d` / `.braw`:** selecting a RED or Blackmagic RAW
  clip (Inspect, or a grid thumbnail when the SDK is missing) no longer hands
  the file to PyAV. FFmpeg cannot demux those containers and SIGSEGV’d in
  `stream.so`. Inspect and thumbs use the SDK bridges; without a bridge the
  clip still lists and Inspect reports SDK missing.
- **BRAW bridge compiles on macOS and Windows:** the C ABI wrapper now uses
  SDK-accurate types per platform (Mac `CFUUIDBytes` / `CFStringRef`, Windows
  `VARIANT` / `BSTR` / `BOOL`, and the Win dispatch factory entry point).
  Linux is unchanged.
- **Windows BRAW bridge link:** compile MIDL ``BlackmagicRawAPI_i.c`` so
  the SDK COM GUIDs resolve (header + dispatch obj only
  declared them).
- **macOS BRAW codesign:** ship ``BlackmagicRawAPI.framework`` under
  ``Contents/Frameworks/braw/`` (not ``Contents/MacOS/braw/``). Ad-hoc
  re-sign now signs Mach-Os inside that framework, then the ``.framework``
  and the outer ``.app``, **without** ``codesign --deep``. ``--deep`` on
  the SDK 6.0 framework itself still reports *bundle format is ambiguous*
  (flattened ``Versions/Current``, top-level ``Resources/``, or
  ``Contents/``) even after the PR #54 placement fix. GPU decoder libs
  stay with the framework.

---

## [0.10.0] — 2026-09-20

### Fixed

- **EXR → Video ACES 2.0 Rec.709 looks lifted / blown:** destination names such
  as **sRGB - Display** or **Rec.1886 Rec.709 - Display** are OCIO *displays*,
  not the old ACES 1.x ``Output - Rec.709`` (RRT+ODT) colorspace. Convert used
  a colorspace transform, which on ACES 2.0 Studio/CG configs applies the
  config ``default_view_transform`` (**Un-tone-mapped**): shadows lift and
  values above 1.0 clip to white. Nuke 17 / DJV use **ACES 2.0 - SDR 100 nits
  (Rec.709)** on that display. Export now uses the same
  ``DisplayViewTransform`` as the built-in player when the dest is a display
  (viewing-rule default for the working space). Utility encodings
  (``sRGB Encoded Rec.709 (sRGB)``) stay colorimetric. Convert log prints
  ``display / view``.

### Added

- **Blackmagic RAW (`.braw`):** optional SDK bridge (same class of feature as
  R3D). With the official Blackmagic RAW SDK, Video → EXR decodes `.braw` to
  Linear ACES AP0 (`ACES2065-1` on the bundled Studio config). Without the
  bridge, `.braw` is listed but conversion fails with a clear SDK-missing
  message. GPU decode when the SDK reports a supported pipeline (**Metal** on
  macOS; **CUDA** then **OpenCL** on Windows/Linux), with automatic CPU
  fallback. Force CPU with ``EXR_CONVERTER_BRAW_CPU=1``. See
  [docs/braw.md](docs/braw.md).
- **BRAW CI feed:** public Release builds can fetch the full SDK from a private
  GitHub Release (`scripts/fetch_braw_sdk.py` + secret `BRAW_SDK_READ_TOKEN`),
  build the bridge, and ship only runtime libraries + `libbraw_bridge` under a
  private app `braw/` folder. Without the secret, Release still ships and
  `.braw` convert reports SDK missing.

### Fixed

- **File browsers:** Dropbox / iCloud / OneDrive online-only placeholders no
  longer hang or error-spam the sequence and video browsers. Listing uses
  names and metadata only; OIIO / PyAV / R3D / BRAW probes and grid thumbnails
  run only on fully local files. Online-only videos still appear (Codec:
  **Online-only**). The folder tree no longer watches cloud directories for
  every placeholder change.

---

## [0.9.13] — 2026-09-11

### Changed

- **OpenImageIO:** official ASWF wheels (`OpenImageIO` ≥ 3.1.17) instead of
  unofficial `oiio-python`. Same `import OpenImageIO as oiio`; EXR / DPX / PNG /
  JPEG / WebP unchanged. App color management stays on independent
  **OpenColorIO 2.5.2+** (`opencolorio`). NumPy is no longer capped at 2.2.6.

---

## [0.9.12] — 2026-09-05

### Fixed

- **Built-in player / preview:** oxideav ProRes MOVs open again. Decoded YUV
  frames were tagged ``colorspace=RGB``, so libswscale refused ``rgb48le`` /
  ``rgb24`` (``ENOTSUP``) and the viewer stayed empty. Preview, thumbs, and
  Video→EXR now copy planes into a swscale-compatible frame when that happens.

### Changed

- **R3D → EXR GPU decode:** macOS uses Metal for
  classic R3D (including KOMODO 6K). Windows and Linux use the SDK
  ``R3DDecoder`` (CUDA, then OpenCL). Convert log reports ``Metal GPU`` /
  ``CUDA GPU`` / ``OpenCL GPU`` vs ``CPU``. Set ``EXR_CONVERTER_R3D_CPU=1``
  to force the CPU path.

---

## [0.9.11] — 2026-08-26

### Added

- **Docs:** [ProRes and VideoToolbox](docs/prores.md) explains the
  hardware encoder (`prores_vt_*`, macOS only), why software 4444/XQ stay
  10-bit, and the experimental oxideav 12-bit ladder.

### Changed

- **EXR → Video codec picker:** nested family menus instead of flat heading
  rows. Experimental oxideav ProRes now includes the full 12-bit ladder
  (Proxy / LT / 422 / HQ / 4444 / XQ), not only 4444 and XQ.
- **App version:** Help → Version, About, ``--version``, and EXR ``Software``
  metadata all read ``pyproject.toml`` (the only written copy). Frozen builds
  ship that file with the binary.
- **Viewer canvas:** slate editor, browser Preview, and the built-in player
  use a black surround around the frame, a 1px white format box, and the
  plate resolution right-justified under the frame's bottom-right corner.

### Fixed

- **Convert form:** Input, Output, Options, and color-space rows keep a
  readable fixed height when the window or log splitter is short. Browse
  and other row buttons match the field height. The form still scrolls
  instead of collapsing those controls into unreadable slivers. Convert
  matches Cancel; the progress bar matches the input row height.

---

## [0.9.10] — 2026-08-25

### Changed

- **CLI:** Ctrl-C cooperatively cancels `video2exr` / `exr2video` (exit 130)
  instead of leaving pool workers hanging.
- **Convert:** Video→EXR parallel path uses a thread pool so decoded RGB
  frames are not pickled across processes; EXR→Video ordered-encode staging
  is capped (~256 MiB) to limit out-of-order frame memory.

---

## [0.9.9] — 2026-08-25

### Security

- **oxideav PyO3 extension:** bump ``pyo3`` 0.25 → 0.29 (fixes
  GHSA-36hh-v3qg-5jq4 iterator OOB read) and matching ``numpy`` crate.

### Changed

- **README:** clearer upfront bullets for dailies (slate / burn-in / watermark),
  ProRes ladder, multi-core OCIO speed, and configurable OCIO configs.

---

## [0.9.8] — 2026-08-24

### Added

- **Experimental true 12-bit ProRes** (`prores_ox_4444` / `prores_ox_xq`):
  cross-platform RDD-36 ProRes-compatible encode via in-process
  **oxideav-prores** PyO3 bindings (``exr_prores``), packaged with Nuitka.
  Hidden when the extension is not built
  (`make oxideav-prores`). Software FFmpeg ProRes remains honest 10-bit.

### Changed

- **EXR → Video codec picker:** codecs are grouped in the dropdown (ProRes
  software / VideoToolbox / oxideav, CineForm, DNxHR, H.264/HEVC, FFV1).

---

## [0.9.7] — 2026-08-11

### Fixed

- **Video browser:** hide macOS AppleDouble metadata sidecars (``._clip.R3D``
  and similar) that share a media extension but are not real clips. The same
  filter applies to drag/drop, paste, R3D path detection, CLI probe, and
  convert ingest; forced sidecar paths fail with a clear error.

---

## [0.9.6] — 2026-08-11

### Changed

- **Internal media pipeline:** video and R3D decode share a common frame-source
  layer for convert and player prefetch (same OCIO/EXR write path; R3D scrub
  resolution follows the preview decode ladder).
- **Browser path paste:** shared helpers for ``file://``, quoted paths, and
  Nuke-style sequence navigation (sequence + video browsers, convert input).

### Fixed

- **Packaging:** never strip RED redistributables under ``r3d/`` on macOS/Linux
  release slim steps; R3D build/install scripts use shared ASCII-safe logging
  and macOS junk filters.

---

## [0.9.5] — 2026-08-11

### Fixed

- **R3D in packaged apps:** find ``libr3d_bridge`` / RED redistributables next to
  the binary even when Nuitka does not set ``sys.frozen`` (v0.9.4 shipped the
  ``r3d/`` folder but runtime discovery missed it).

---

## [0.9.4] — 2026-08-10

### Fixed

- **Windows Release scripts:** ASCII-only console logs in R3D install/fetch and
  OCIO helper scripts (no more `UnicodeEncodeError` on cp1252 after a successful
  R3D bundle install).

---

## [0.9.3] — 2026-08-10

### Added

- **Nuke-style path paste:** Pasting ``name.####.exr`` (or ``%04d``) into the
  EXR → Video input field resolves the sequence, frame range, and source color
  space the same way as Browse. In the sequence/video browser Folder field,
  paste navigates to the parent folder, selects the matching item, and switches
  to Preview so **Open** is ready.

### Fixed

- **R3D redistributable packaging:** skip macOS AppleDouble (``._*``) junk when
  copying RED Redistributable libraries so Linux `strip` does not fail in CI.

---

## [0.9.2] — 2026-08-10

### Fixed

- **Windows R3D bridge CI log:** use ASCII arrows in build script output so
  cp1252 consoles do not raise UnicodeEncodeError after a successful link.

---

## [0.9.1] — 2026-08-10

### Fixed

- **Windows R3D bridge build:** compile/link the RED bridge with `/MD` so it
  matches `R3DSDK-*-MD.lib` (fixes LNK2038 RuntimeLibrary mismatch and unresolved
  CRT imports on GitHub Actions Release).

---

## [0.9.0] — 2026-08-10

### Added

- **Optional RED R3D / N-RAW decode:** Video → EXR accepts `.r3d` and `.nev` when
  the R3D SDK bridge is built (`make r3d-bridge` + official RED R3D SDK).
  Decodes with IPP2 primary development (REDWideGamutRGB + Log3G10) for OCIO
  pipelines. See [docs/r3d.md](./docs/r3d.md). The proprietary SDK is not
  redistributed in source form.
- **R3D CI feed:** Release builds can fetch the full SDK from a private GitHub
  Release (`scripts/fetch_r3d_sdk.py` + secret `R3D_SDK_READ_TOKEN`), link the
  bridge, and ship only Redistributable libs + `libr3d_bridge` under a private
  app `r3d/` folder.
- **R3D preview & thumbnails:** Video browser grid and sequence-player scrub use
  low-res R3D SDK decode (not full premium). Convert remains full-quality.
- **R3D → EXR metadata:** Camera, ISO, lens, FPS, and per-frame timecode written
  as `exrconverter:r3d:*` attributes on output EXRs.
- **About dialog:** RED Redistributable end-user notice (button opens full text)
  when R3D support is present.
- **File browser row context menu:** List/Grid right-click on sequence and video
  browsers includes **Copy File Path** and **Copy Folder Path** (same actions as
  the Input/Output path fields). Video browser also gains Preview / Open on that
  menu for parity with the sequence browser.

### Fixed

- **File browser close while previewing:** the window close button (X) and Cancel
  now close the sequence and video browsers immediately, even when Preview is
  active. Escape still leaves Preview and returns to List/Grid first.
- **Input color space highlight:** the amber auto-detect cue only appears when
  media probe sets the source space, then clears on a timer (or immediately if
  you pick a space manually / load a preset). It no longer stays stuck on.

---

## [0.8.2] — 2026-08-10

### Fixed

- **GitHub Release publish when SignPath is off:** optional Windows Authenticode
  job no longer blocks the publish step (v0.8.1 built and Cosigned all platform
  binaries but never created the GitHub Release). Re-ships the 0.8.1 product
  fixes and release-pipeline hardening with a working publish path.

---

## [0.8.1] — 2026-08-10

### Changed

- **Release pipeline hardening:** tag/CHANGELOG gates, per-tag concurrency,
  pinned actions + uv 0.12.3, appimagetool 1.9.1 with SHA-256, Cosign identity
  bound to `release.yml`, GitHub build provenance attestations, CHANGELOG
  section in GitHub Release notes, optional SignPath Authenticode job (off until
  configured). Auto-tag uses `--ref main -f tag=vX.Y.Z` and skips re-dispatch
  while a Release run is already active.

### Fixed

- **Underscore EXR sequences load again:** folder browse / EXR → Video input
  re-accepts ``name_####.ext`` pads (e.g. ``04_5d_00000.exr``) that 0.7+ had
  ignored while standardizing **writes** on ``name.####.ext``. Frame range
  auto-fill and the sequence browser list both sequences when a folder mixes
  pads. Opening a folder prefers a sequence whose name matches the folder
  name. Video → EXR output still uses dot pads only.
- **Browser volumes / drives at top level:** the sequence and video file trees
  no longer hide external disks. On macOS, ``QFileSystemModel`` treats
  ``/Volumes`` as hidden under ``/``, so sticks never appeared; on Windows,
  only the system drive was rooted. Both browsers now use a multi-root tree
  (each mount is a top-level row) plus a **Volumes** section in the places
  sidebar. Mounts refresh every few seconds while Browse is open.

---

## [0.8.0] — 2026-08-09

### Changed

- **OCIO app anchor for overlays:** slate / burn-in / watermark paint is always
  linearised on a private ACES CG/Studio config the app controls (guaranteed
  `texture_paint` + `aces_interchange` / ACES2065-1), then bridged into the
  user config’s compositing space via interchange when available. User
  source/destination convert still uses only the selected (Nuke / show / file)
  config — internal transforms no longer depend on it having sRGB or AP0.

### Added

- **Built-in video player in Preferences:** **File → Preferences → Video player**
  offers **Built-in player** (default), System default, or Custom application.
  With **Open result** after EXR → Video, built-in opens the finished file in the
  same OCIO `SequencePlayer` window used for sequences (cache strip, GPU display).
- **AppSettings service:** process-wide typed QSettings façade
  (`src/services/app_settings.py`) with a key registry; browsers and the main
  window share one backend instead of ad-hoc `QSettings` factories.
- **Versioned convert presets:** JSON presets carry `schema_version` / `kind`;
  load normalizes legacy files and strips accidental I/O paths.
- **Slate undo/redo:** in the slate editor, ⌘/Ctrl+Z and Shift+⌘/Ctrl+Z undo/redo
  **feature toggles** (slate / burn-in / watermark) and **Fill from slate**
  (burn-in bulk fill) via a model-owned `QUndoStack`. Free-text / spinner field
  edits persist live but are not stacked as document commands. Convert paths,
  OCIO config, and app preferences are not on the stack. Shortcuts do not steal
  text-field undo while a line edit has focus.

### Removed

- **Slate menu** on the main menu bar. Edit slate / burn-in / watermark from
  the **EXR → Video** tab (checkbox + edit button) as before.

### Fixed

- **Video / V2E open-result looks crushed dark:** convert Rec.709 → ACEScg was
  correct; the player used the config-wide default view (often ACES 2.0 SDR
  RRT), which crushes Rec.709-originated SDR. Video preview and Video→EXR
  **Open result** now soft-prefer a **video-monitoring** view from the config
  when viewing rules / ``sdr-video`` encodings provide one
  (``getDefaultView(display, videoCS)`` — no hard-coded view names). If the
  config has no such view, the config default is unchanged. Slate/camera EXR
  still uses the config default.
- **Multi-sequence identity:** convert tab, async open, and session restore keep
  the selected sequence via its first-frame path (not the parent folder alone);
  source color auto-detect probes that sequence, not sorted ``[0]`` in mixed
  folders. Sequence browser Preview follows the current list/grid selection.
- **Slate editor undo vs typing:** ⌘/Ctrl+Z prefers the focused line edit’s
  text undo before document commands.
- **Sequence preview colour:** unmapped OIIO tags no longer skip ``src→working``.
- **File browser state restore:** Video → EXR and EXR → Video input browsers now
  persist full layout and session separately (`ui/video_browser_*` vs
  `ui/sequence_browser_*`): List/Grid/Preview, Inspect on/off, splitters, table
  columns, last folder, selection, and directory-tree expansion/scroll. Window
  **size and position** are shared (`ui/browser_geometry`). When Browse opens
  the same folder as last time, the dialog reopens as left (tree, tabs, Inspect).
  The video browser now saves layout on close (it previously only remembered
  view mode).
- **Video playback / scrubbing:** browser and sequence-player video decode no
  longer labels the post-seek **keyframe** as the requested frame. Seek lands
  on the previous I-frame, then the decoder walks forward by presentation time
  to the exact index (FFmpeg/PyAV standard path). Forward play stays sequential;
  scrub coalesces the queue and limits reverse lookback so scrubbing does not
  thrash GOPs or jump frames.
- **Video preview colour:** `load_video` resolves a real OCIO source space
  (tab selection, else file metadata, else Rec.709) before building the same
  src→working worker transform the slate editor uses, so display/view is no
  longer applied to display-encoded video as if it were scene-linear.
- **Preview cache OCIO failures:** if `src→working` raises or returns `None`,
  the frame is not stored as float working-space (avoids washed/crushed
  display). Applies to video and EXR prefetch.
- **Browser Preview OCIO:** file browsers take an explicit preview context
  (config + source space + fps) instead of reading private attributes from
  `parent()`. Video browser cache strip matches sequence browser (Preferences).

---

## [0.7.0] — 2026-08-09

### Added

- **Image sequences beyond EXR → video:** `exr2video` (CLI + GUI) accepts
  OpenEXR plus **DPX**, and display stills via OpenImageIO — **PNG**,
  **JPEG** (`.jpg` / `.jpeg`), and **WebP**. Sequence browse, drag-and-drop,
  and folder scan pick these up; mixed folders prefer EXR, then DPX.
  EXR/DPX default toward scene-linear; PNG/JPEG/WebP toward **sRGB**.
- **Sequence browser List | Grid | Preview:** segmented control switches
  between the metadata table, a thumbnail grid (async OIIO first-frame
  previews), and in-dialog playback of the **first** sequence in the folder
  (`SequencePlayer`). Inspect stays available; **Space** toggles Preview,
  **Esc** returns to list/grid. Mode is stored in `QSettings`
  (`ui/sequence_browser_view`).
- **Playback cache budget** lives in **File → Preferences** (not in browser
  Preview). Left/Right arrows step frames in the player.
- **Sequence browser layout memory:** window geometry, splitter sizes, and
  resizable list columns are stored in `QSettings`.
- **Reusable sequence player** (`src/gui/player/`): shared transport, RAM cache,
  and GPU/CPU OCIO display path used by the slate editor and browser preview.
- **Input/output path QoL:** path fields use a custom context menu with standard
  Cut / Copy / Paste / Select All plus **Copy File Path**, **Copy Folder Path**,
  and Open in Finder / Explorer (via `QDesktopServices`). ⌘-click (macOS) or
  Ctrl-click (Windows/Linux) on **Browse…** opens the folder when the path is
  valid.
- **Open result (Video → EXR):** opens the written sequence in a standalone
  built-in player window (cache strip shown; same GPU OCIO path as the slate
  editor). Opens the sequence named from the source video stem (not a mixed
  folder scan), with OCIO source locked to the convert destination space.
  Window geometry is clamped on-screen. **EXR → Video** still uses the preferred
  external video player.
- **Video → EXR is ingest-only:** slate / burn-in / watermark are never applied
  (removed from the convert API; **Slate** menu disabled on that tab).
- **Video → EXR output name:** the field pattern ``name.####.exr`` is respected
  (writes ``name.1001.exr``, …). Sequence discovery and writes use **dot pads
  only** (``name.####.ext``); underscore pads (``name_####.ext``) are ignored.
- **Video → EXR → player colour:** post-convert playback uses convert
  **destination** as OCIO source (e.g. ACEScg → working → display). File probes
  prefer ``exrconverter:dstColorSpace`` over OIIO’s often-wrong
  ``oiio:ColorSpace`` (commonly rewritten to ``lin_rec709``).
- **Video browser List | Grid | Preview:** first-frame video thumbs (PyAV),
  in-dialog playback via ``SequencePlayer``, and path fields that no longer
  grow the dialog when paths are long.

### Fixed

- **Sequence browser Preview crash:** two related Qt/PySide issues —
  (1) adding the first `QOpenGLWidget` to an *already shown* top-level window
  recreates the native surface (`RasterSurface` → `OpenGLSurface`, Qt 6.4+) and
  can SIGSEGV on macOS — the player/GPU plane is now created in the browse
  dialog constructor before show (same pattern as the slate editor);
  (2) tearing down with `setParent(None)` + `deleteLater()` while dropping the
  last Python ref double-destroyed the C++ QObject. GL is released explicitly;
  the player stays parented to the dialog. Viewer gain/gamma live in
  `nuke_slider.py`.

---

## [0.6.1] — 2026-08-08

### Fixed

- **Release CI:** run ``ensure_ocio`` before the Release workflow test matrix
  (same as CI). Without it, ``oiio-python`` can leave PyOpenColorIO on 2.4 and
  the bundled ACES Studio 2.5 config fails to load, aborting the gate.

---

## [0.6.0] — 2026-08-08

### Added

- **GPU OCIO slate/shot preview:** playback uses OpenColorIO’s GPU processor
  (GLSL 4.x via `QOpenGLWidget` + PyOpenGL). Full-resolution working-space
  frames from the RAM cache are texture-uploaded (float16 when available);
  burn-in/watermark are a separate cached overlay texture composited in the
  fragment shader; display/view and live gain/gamma are dynamic uniforms —
  not per-frame CPU `applyRGB` or full-plate alpha-over. CPU display remains
  as fallback when OpenGL is unavailable.

### Fixed

- **Slate viewer highlight headroom:** shot frames are read as unclamped
  float32 EXR (not uint16). UINT16 loads clamp values above 1.0, so exposing
  *down* could not recover jacket/highlight detail that Nuke still shows.
  Cache still uses float16 HDR working-space after ``src → working``.
- **EXR sequence browser Open:** selecting a sequence now opens *that*
  sequence (via its first frame path). Previously only the parent folder was
  returned, so multi-sequence folders always loaded sorted ``[0]`` — toggling
  Inspect could mask a stale/missing table selection. Open is enabled as soon
  as sequences are listed (first/prior row auto-selected).
- **Viewer gamma (Nuke-aligned):** slate/shot preview applies Nuke’s post-display
  power ``pow(rgb, 1/γ)`` *after* the OCIO display/view transform (GPU fragment
  uniform and CPU numpy). Previously gamma went through OCIO
  ``ExposureContrastTransform`` *before* display, which crushed mid-grays
  differently (wrong at extreme γ like 0.01). Gain remains pre-display
  (exposure stops). Slider still shows Nuke-style γ (1 = neutral).
- **Input restore on launch:** saved EXR/video path is re-probed into the
  validated model (not only shown in the field). ``textChanged`` no longer
  drops a loaded sequence when the field still shows the same source or the
  ``####`` display pattern. Input prefs are flushed with ``QSettings.sync()``
  so hard restarts keep the last path.
- **Submit Notes typing:** form→model updates no longer echo
  ``setPlainText`` on every keystroke (which reset the caret to the start).

### Changed

- **Viewer gain/gamma sliders:** procedural curves (log gain; asinh pivot-at-1
  gamma for denser resolution near neutral) with range-driven 1–2–5 nice ticks
  that thin by track width — no hard-coded tick tables. Numeric readouts turn
  red when the value is not exactly 1.0 (Nuke-style). Display/view combo width
  is capped so long OCIO labels no longer crush the tracks.
- **Slate/shot preview sampling:** nearest-neighbor (raw pixel grid) when zooming
  — no bilinear filtering on the plate/overlay textures (GPU) or pixmap transform
  (CPU fallback).
- **Viewer strip:** removed the decorative f-stop (aperture) control; gain/gamma
  and display/view remain.
- **Watermark defaults:** 40% opacity and **Tile across frame** on (was 35% /
  single stamp). Existing saved watermark prefs are unchanged.
- **Check for Updates:** opens the latest GitHub Releases page in the system
  browser (`QDesktopServices`) instead of downloading installers via Python
  HTTPS (avoids frozen-app SSL/CA issues).
- **Help menu:** non-clickable **Version X.Y.Z**; no site-link items
  (derekvfx.ca / ocio.cc remain in About).
- **About dialog:** dependency versions move into the scroll area; only the
  title and app version stay in the header.
- **Dependency:** `PyOpenGL` for GPU OCIO preview.

---

## [0.5.4] — 2026-08-08

### Added

- **Docs site (GitHub Pages):** write Markdown under `docs/`; Hugo builds from
  `site/` and the **Docs** workflow publishes to
  `https://derek-rein.github.io/exr-converter/`. Local preview: `make docs-serve`.
- **GUI docs:** [docs/gui.md](docs/gui.md) covers tabs, slate/burn-in/watermark,
  preferences, and post-convert actions.

### Changed

- **Docs accuracy:** CLI codec ladder (default `prores`, honest bit depths,
  default extensions), Nuke “Open EXR Converter only…”, and AGENTS.md now
  require updating `docs/` with user-visible work (same rule as the changelog).

### Fixed

- **Check for Updates on macOS packages:** Nuitka no longer excludes OpenSSL
  (`libssl` / `libcrypto`), which left Python without HTTPS and produced
  `urlopen error unknown url type: https`. If SSL is still unavailable, the app
  opens the GitHub releases page in the browser instead of only showing an error.
- **macOS Dock icon corners while running:** stop overriding the Dock tile with
  `setWindowIcon(:/icon.png)`. The running app now keeps the bundle `.icns` so
  open and closed Dock tiles match (no sharp square PNG override).

---

## [0.5.3] — 2026-08-06

### Added

- **CLI / GUI launch docs:** richer `-h` epilog; [docs/cli.md](docs/cli.md);
  README links.
- **GUI launch flags:** `--open`, `--gui-ocio`, `--mode` pre-fill media and OCIO
  when starting the app (no convert subcommand).
- **Nuke integration:** [integrations/nuke/](integrations/nuke/) menu script to
  open the selected Read with session OCIO; [docs/nuke.md](docs/nuke.md).
- **Slate thumbnail frame** preference (first / middle / last), integer in
  QSettings, editable under File → Preferences.

### Fixed

- **Slate thumbnail extraction (EXR → video only):** pick first/mid/last from the
  known EXR frame list (no video seek). OCIO ``src → slate authoring (sRGB)`` when
  a config is available; QImage buffer ownership / JPEG encode fixed. Video → EXR
  never uses slate/burn-in/watermark.
- **GUI `--open` / Nuke launch:** no longer overwritten by deferred QSettings
  input restore.
- **CLI `--workers`:** works before or after the subcommand.

---

## [0.5.2] — 2026-08-06

### Fixed

- **Windows Nuitka OpenImageIO LoadLibrary:** ship `OpenColorIO_2_4.dll` for OIIO
  (from env/cache or PyPI oiio-python wheel) while app OCIO stays on **2.5**.
  Re-running Release for `v0.5.1` kept failing because that tag predates the fix;
  packaging scripts live on the **tag commit** that is built.
- **CI/CD chain after merge:** Auto-tag dispatches Release via `workflow_dispatch`
  (`GITHUB_TOKEN` tag pushes do not start other workflows).
- Release dispatch documents `source_ref` for emergency rebuilds from another ref.

---

## [0.5.1] — 2026-08-06

### Fixed

- **Packaged app OpenColorIO 2.4 bug:** Nuitka was linking `PyOpenColorIO` to
  oiio-python’s vendored OCIO **2.4** (and could ship a 2.4-ABI extension), so the
  bundled ACES Studio v4 config (profile **2.5**) failed at convert while the UI
  could still look green. Release/local bundle now runs
  `scripts/fix_bundle_ocio.py` (restore 2.5 `.so` + dylib), `ensure_ocio` before
  Nuitka, and the smoke test loads the bundled config.
- **Nuke OCIO configs** that the linked OpenColorIO cannot load stay visible in
  the combo but are **greyed out** with a tooltip explaining why.
- Bundled ACES Studio no longer silently falls back to a library config on version
  mismatch (status matched convert failures).
- Note: Windows OIIO DLL packaging for this release was incomplete; use **0.5.2+**.

---

## [0.5.0] — 2026-08-06

### Added

- **Preferences** dialog (`File → Preferences…`) for the video player used by
  **Open result**: system default or a custom app/CLI path (IINA, VLC, mpv, etc.),
  with detected-player quick picks when present.
- **Show in folder** post-convert checkbox — reveals the output file or sequence
  folder in the OS file manager (`open -R` / Explorer select when possible).
- Unit tests for player preference helpers (`tests/test_player_prefs.py`).
- **[CHANGELOG.md](./CHANGELOG.md)** and **[AGENTS.md](./AGENTS.md)** — project
  history and agent/release process (release steps moved out of
  `docs/releasing.md`).

### Changed

- **Copy path** remains **on by default** for new installs; setting is still
  persisted after toggle.
- **Open result** uses the preferred player from Preferences instead of always
  using the OS default handler (often unreliable QuickTime for pro codecs).
  For EXR sequences, open-result is video-oriented; use **Show in folder**.

---

## [0.4.0] — 2026-08-06

### Added

- Honest codec bit-depth labeling in the UI and codec ladder.
- HEVC and FFV1 **12-bit** encode options where the stack supports them.
- Release documentation (`docs/releasing.md`) and protected-`main` PR-based
  release process notes.
- Plan for cross-platform 12-bit ProRes via oxideav
  (`docs/plan-12bit-prores-oxideav.md`) — not shipped; design only.

### Changed

- Codec help / display names reflect real encode depths (software ProRes is
  10-bit; VideoToolbox 4444/XQ ~12-bit on macOS).

### Fixed / CI

- Release workflow and Makefile polish around version inject and packaging.

---

## [0.3.0] — 2026-08-05

### Added

- Expanded codec ladder and presets (ProRes / DNxHR / CineForm / HEVC / H.264 /
  FFV1; VideoToolbox ProRes on macOS).
- Broader CLI surface and defaults for `video2exr` / `exr2video`.
- Nuke OCIO config discovery (`nuke_discover`) when local Foundry installs exist.
- Integration test suite for media conversions (`tests/test_integration_conversions.py`,
  fixtures under `tests/fixtures/`).
- `scripts/ensure_ocio.py` and `make ensure-ocio` / `make sync` to keep
  OpenColorIO **2.5+** linkage after `oiio-python` upgrades.
- CI: full pytest (unit + integration) on Linux, macOS, Windows; aggregate
  `ci-ok` gate for branch protection.

### Changed

- Project layout under `src/core`, `src/gui`, `src/render`, `src/services`.
- OCIO workflow and color-space UI improvements.

### Fixed

- OCIO version re-check after repair in CI.
- Ruff format / softened flaky watermark and VideoToolbox asserts in CI.

---

## [0.2.2] — 2026-08

### Fixed

- CI: `apt-get update` before installing `libegl1` on Linux runners.

---

## [0.2.1] — 2026-08

### Fixed

- Nuitka 4.x: bundle OCIO config with `--include-data-dir`.

---

## [0.2.0] — 2026-08

### Added

- **Deinterlace** interlaced video sources so EXR plates stay progressive
  (`src/core/video.py`, tests).

---

## [0.1.26] — 2026

### Fixed

- Bundle ACES Studio OCIO config in release builds.

---

## [0.1.25] — 2026

### Fixed

- Re-sign macOS bundle ad-hoc after slimming; repo naming / run docs updates.

---

## [0.1.24] — 2026

### Fixed

- macOS bundle-slim path so Release builds do not fail.

---

## [0.1.23] — 2026

### Added

- Tiled watermark, compositing-space overlay workflow, UI polish.
- Project restructure: `resources/`, `core`/`gui` split, expanded CI tests.

---

## [0.1.22] — 2026

### Fixed

- macOS install name: **EXR Converter.app** (not `exr_converter.app`).

---

## [0.1.21] — 2026

### Added

- Bundled ACES Studio OCIO config and playback/cache tooling.

---

## [0.1.20] and earlier

Early 0.1.x series established the app baseline:

- Video ↔ EXR conversion with OCIO
- QPainter slate (replacing QWebEngine), burn-in, watermark
- Slate model MVC and working-space overlay compositing
- Timeline scrubber / async shot preview
- EXR display-window handling, sequence browser, in-app update check
- Nuitka packaging for Linux AppImage, macOS DMG, Windows installer
- Cosign-signed release artifacts

Patch tags `v0.1.12`–`v0.1.19` were largely packaging, CI, and thumbnail path
hardening (QImage/QBuffer; exclude PIL from bundles).

---

## Links

- Releases: https://github.com/derek-rein/exr-converter/releases
- Compare tags: `https://github.com/derek-rein/exr-converter/compare/vA.B.C...vX.Y.Z`

[Unreleased]: https://github.com/derek-rein/exr-converter/compare/v0.14.1...HEAD
[0.14.1]: https://github.com/derek-rein/exr-converter/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/derek-rein/exr-converter/compare/v0.13.3...v0.14.0
[0.13.3]: https://github.com/derek-rein/exr-converter/compare/v0.13.2...v0.13.3
[0.13.2]: https://github.com/derek-rein/exr-converter/compare/v0.13.1...v0.13.2
[0.13.1]: https://github.com/derek-rein/exr-converter/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/derek-rein/exr-converter/compare/v0.12.14...v0.13.0
[0.12.14]: https://github.com/derek-rein/exr-converter/compare/v0.12.12...v0.12.14
[0.12.12]: https://github.com/derek-rein/exr-converter/compare/v0.12.11...v0.12.12
[0.12.11]: https://github.com/derek-rein/exr-converter/compare/v0.12.10...v0.12.11
[0.12.10]: https://github.com/derek-rein/exr-converter/compare/v0.12.9...v0.12.10
[0.12.9]: https://github.com/derek-rein/exr-converter/compare/v0.12.8...v0.12.9
[0.12.8]: https://github.com/derek-rein/exr-converter/compare/v0.12.7...v0.12.8
[0.12.7]: https://github.com/derek-rein/exr-converter/compare/v0.12.6...v0.12.7
[0.12.6]: https://github.com/derek-rein/exr-converter/compare/v0.12.5...v0.12.6
[0.12.5]: https://github.com/derek-rein/exr-converter/compare/v0.12.4...v0.12.5
[0.12.4]: https://github.com/derek-rein/exr-converter/compare/v0.12.3...v0.12.4
[0.12.3]: https://github.com/derek-rein/exr-converter/compare/v0.12.2...v0.12.3
[0.12.2]: https://github.com/derek-rein/exr-converter/compare/v0.12.1...v0.12.2
[0.12.1]: https://github.com/derek-rein/exr-converter/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/derek-rein/exr-converter/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/derek-rein/exr-converter/compare/v0.10.2...v0.11.0
[0.10.2]: https://github.com/derek-rein/exr-converter/compare/v0.10.1...v0.10.2
[0.10.1]: https://github.com/derek-rein/exr-converter/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/derek-rein/exr-converter/compare/v0.9.13...v0.10.0
[0.9.13]: https://github.com/derek-rein/exr-converter/compare/v0.9.12...v0.9.13
[0.9.12]: https://github.com/derek-rein/exr-converter/compare/v0.9.11...v0.9.12
[0.9.11]: https://github.com/derek-rein/exr-converter/compare/v0.9.10...v0.9.11
[0.9.10]: https://github.com/derek-rein/exr-converter/compare/v0.9.9...v0.9.10
[0.9.9]: https://github.com/derek-rein/exr-converter/compare/v0.9.8...v0.9.9
[0.9.8]: https://github.com/derek-rein/exr-converter/compare/v0.9.7...v0.9.8
[0.9.7]: https://github.com/derek-rein/exr-converter/compare/v0.9.6...v0.9.7
[0.9.6]: https://github.com/derek-rein/exr-converter/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/derek-rein/exr-converter/compare/v0.9.4...v0.9.5
[0.9.4]: https://github.com/derek-rein/exr-converter/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/derek-rein/exr-converter/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/derek-rein/exr-converter/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/derek-rein/exr-converter/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/derek-rein/exr-converter/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/derek-rein/exr-converter/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/derek-rein/exr-converter/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/derek-rein/exr-converter/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/derek-rein/exr-converter/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/derek-rein/exr-converter/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/derek-rein/exr-converter/compare/v0.5.4...v0.6.0
[0.5.4]: https://github.com/derek-rein/exr-converter/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/derek-rein/exr-converter/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/derek-rein/exr-converter/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/derek-rein/exr-converter/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/derek-rein/exr-converter/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/derek-rein/exr-converter/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/derek-rein/exr-converter/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/derek-rein/exr-converter/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/derek-rein/exr-converter/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/derek-rein/exr-converter/compare/v0.1.26...v0.2.0
[0.1.26]: https://github.com/derek-rein/exr-converter/compare/v0.1.25...v0.1.26
[0.1.25]: https://github.com/derek-rein/exr-converter/compare/v0.1.24...v0.1.25
[0.1.24]: https://github.com/derek-rein/exr-converter/compare/v0.1.23...v0.1.24
[0.1.23]: https://github.com/derek-rein/exr-converter/compare/v0.1.22...v0.1.23
[0.1.22]: https://github.com/derek-rein/exr-converter/compare/v0.1.21...v0.1.22
[0.1.21]: https://github.com/derek-rein/exr-converter/releases/tag/v0.1.21
