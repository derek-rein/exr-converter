---
name: exr-converter
description: Convert video to OpenEXR sequences and back, make editing proxies, and transcode video with OpenColorIO, using the EXR Converter command line. Use when asked to turn a clip into EXR frames, turn an EXR / DPX / PNG sequence into a movie, make proxies, or transcode between ProRes, DNxHR, H.264, HEVC, and AV1.
---

# EXR Converter command line

EXR Converter is a desktop app with a full command line. Four subcommands do the
work, with OpenColorIO for color. No system FFmpeg is needed; video I/O is built in.

<!-- install:start -->
## Install

Run the app as `uv run python main.py` from a source checkout (the repository
root), or use the packaged binary (`exr_converter`, or `exr-converter` on Linux).
Every example below starts with `uv run python main.py`; replace that with the
command for this machine.
<!-- install:end -->

Run `uv run python main.py <subcommand> --help` for every flag. With no
subcommand the same program opens the GUI, so always give a subcommand.

## Pick the subcommand

| Goal | Subcommand | Output |
|------|------------|--------|
| Video → OpenEXR frames | `video2exr` | `<out>/<stem>.####.exr` |
| EXR / DPX / PNG / JPEG / WebP sequence → video | `exr2video` | one video file |
| Light editing proxies, batch or watch folder | `proxy` | `<clip dir>/Proxy/<clip>.<ext>` |
| Video → video, any codec, optional color transform | `transcode` | `<clip>_<codec>.<ext>` |

The app writes EXR as stills and video files. It does not write PNG or JPEG.

## video2exr

```bash
uv run python main.py video2exr -i plate.mov
uv run python main.py video2exr -i plate.mov -o /tmp/exr --exr-compression zip --frame-range 1-100
uv run python main.py video2exr -i clip.mov --src "sRGB Encoded Rec.709 (sRGB)" --dst ACEScg --scale 0.5
```

- `-i` input video (required). `-o` output folder (default `<input dir>/<stem>/`).
- `--src` / `--dst` OCIO color spaces. `--dst` defaults to ACEScg. `--src` is auto-detected.
- `--exr-compression` none, rle, zip, zips, piz, pxr24, b44, b44a, dwaa (default), dwab.
  `--dwa-level`, `--zip-level` tune it.
- `--scale 0.5`, `--frame-range 1-100` or `1-50x2`, `--start-frame` (default 1001),
  `--padding` (default 4), `--pixel-aspect auto|1|1.3|2`, `--deinterlace auto|on|off`.
- `--decoder auto|software`, `--ocio PATH` (default bundled ACES Studio config or `$OCIO`).

## exr2video

```bash
uv run python main.py exr2video -i ./plate -o review.mov --fps 24
uv run python main.py exr2video -i "./plate.1001.exr" --codec h264 --crf 18 --preset medium
uv run python main.py exr2video -i ./plate --src ACEScg --dst "Output - Rec.709" --frame-range 1001-1100
```

- `-i` a sequence folder, or any one existing frame of the sequence. A literal
  `####` path that is not on disk is not accepted; point at a real frame or the folder.
- `-o` output video (default `<parent>/<name>.mov`; extension follows the codec).
- `--fps` number or ratio (`24`, `23.976`, `24000/1001`; default 24).
- `--codec` prores (default, 422 HQ), prores_proxy, prores_lt, prores_422, prores_4444,
  prores_xq, cineform, cineform_rgb, dnxhr_lb/sq/hq/hqx/444, h264, hevc, hevc_8,
  hevc_12, ffv1, ffv1_12. `--crf`, `--preset` apply to h264 / hevc.
- `--src` / `--dst`, `--scale`, `--frame-range`, `--pixel-aspect`, `--encoder auto|software`.
- `--watermark TEXT` adds an invisible TrustMark payload (up to 8 ASCII characters).

## proxy

```bash
uv run python main.py proxy -i A001C003.mov
uv run python main.py proxy -i ./card --recursive --resolution quarter --codec h264 -o /proxies
uv run python main.py proxy --watch /Volumes/Ingest --existing     # runs until Ctrl-C
```

- No color transform: proxies keep the look, timecode, and audio.
- `-i` takes files and folders. `--codec` prores_proxy (default), prores_lt, dnxhr_lb,
  dnxhr_sq, h264, hevc_8. `--resolution` half (default), quarter, eighth, original,
  1080, 720, 540. It never upscales.
- Existing proxies are skipped unless `--overwrite`. `--suffix`, `--no-audio`, `--recursive`.
- `--watch DIR` (repeatable) proxies each new clip once it stops growing. It never
  returns on its own, so run it in the background. `--existing`, `--interval`, `--settle`.

## transcode

```bash
uv run python main.py transcode -i plate.mov --codec h264 --crf 18 --resolution 1080
uv run python main.py transcode -i ./day1 --recursive -o /deliveries --codec dnxhr_hqx --container mxf
uv run python main.py transcode -i log.mov --src "S-Log3 S-Gamut3.Cine" --dst "Rec.1886 Rec.709 - Display"
```

- No color change unless both `--src` and `--dst` are given.
- `--codec` prores (default), the ProRes and DNxHR family, h264, hevc, hevc_8, hevc_12, av1.
  `--container` mov, mxf, mp4, mkv.
- `--resolution` original, 75, half, quarter, eighth, 2160, 1080, 720, 540, custom
  (`--width` / `--height`, 0 keeps aspect). `--fps N` conforms timing and drops audio.
- `--audio copy|aac|pcm|none`, `--crf`, `--bitrate` (Mb/s), `--preset`.
- `--name` pattern, default `<name>_<codec>`. Tokens: `<name> <reel> <srcresolution>
  <srcwidth> <srcheight> <srcfps> <timecode> <parent> <codec> <resolution> <width>
  <height> <fps> <date>`.
- `--if-exists skip|overwrite|rename` (default skip).

## Global flags

`--version`, and `--workers N` (0 = auto, 1 = serial) either before or after the
subcommand. `--open PATH`, `--gui-ocio PATH`, `--mode …` only apply to the GUI.

## Tips for agents

- Quote paths and color-space names; many names contain spaces and parentheses.
- Exit status: 0 success, 1 some items failed, 2 bad arguments, 130 interrupted.
- Check the result exists: count `*.exr` in the output folder, or probe the video.
- Color-space names must match the OCIO config exactly. If a name is rejected,
  omit `--src` and let it auto-detect, or pass `--ocio` with the studio's config.
- Do not guess flags: `--help` on the subcommand is authoritative for this version.
- Full reference: https://vfx.systems/docs/exr-converter
