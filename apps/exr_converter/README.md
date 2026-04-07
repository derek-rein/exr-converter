# EXR Converter

Desktop app and CLI for converting between **video** and **OpenEXR** sequences with **OpenColorIO** color management. Uses **PyAV** for decode/encode, **OpenImageIO** for EXR I/O, and **PySide6** for the GUI.

Targets the [VFX Reference Platform CY2026](https://vfxplatform.com/#reference-platform): Python 3.13, Qt/PySide 6.8, OpenColorIO 2.5, OpenEXR 3.4, NumPy 2.3.

## Downloads

[![Latest release](https://img.shields.io/github/v/release/derek-rein/vfx-tools?filter=exr_converter/*&label=latest)](https://github.com/derek-rein/vfx-tools/releases?q=exr_converter)

Pre-built binaries are available on the [releases page](https://github.com/derek-rein/vfx-tools/releases?q=exr_converter):

| Platform | Format |
|----------|--------|
| Windows x64 | Installer (`.exe`) via Inno Setup |
| macOS Apple Silicon | `.dmg` |
| macOS Intel | `.dmg` |
| Linux x86_64 | `.AppImage` |

## Requirements (running from source)

- **Python 3.13**
- [uv](https://docs.astral.sh/uv/) (recommended) or another PEP 621-compatible installer

```bash
cd apps/exr_converter
uv sync
```

## GUI

![EXR Converter — EXR → Video](../../assets/exr_converter_screenshot.png)

```bash
uv run python main.py
```

No subcommand — opens the main window. OCIO resolution follows `$OCIO` when set, otherwise built-in configs (see in-app / CLI `--ocio`).

## CLI

Use the `video2exr` or `exr2video` subcommand (optional `--headless` is only a flag alias; subcommands run without opening a GUI).

**Video → EXR**

```bash
uv run python main.py video2exr -i clip.mov -o ./exr_out/
```

**EXR → video**

```bash
uv run python main.py exr2video -i "./plate.####.exr" -o review.mov --fps 24
```

Common options:

| Option | Applies to | Notes |
|--------|------------|--------|
| `--ocio PATH` | both | OCIO config file (overrides `$OCIO`) |
| `--src` / `--dst` | both | OCIO display / scene color space names |
| `--workers N` | both | `0` = auto, `1` = single-threaded |
| `--scale FACTOR` | both | e.g. `0.5` for half resolution |
| `--exr-compression NAME` | `video2exr` | e.g. `dwaa`, `zip`, `none` (see `--help`) |
| `--codec KEY` | `exr2video` | e.g. `prores`, `h264`, `prores_4444`, `dnxhr_hq`, `ffv1` |

Run `uv run python main.py video2exr --help` or `exr2video --help` for the full list.

## Building from source

Prerequisites: **Python 3.13**, [**uv**](https://docs.astral.sh/uv/), and a C compiler (Xcode CLT on macOS, MSVC on Windows, gcc on Linux).

```bash
cd apps/exr_converter
uv sync
make bundle
```

This uses [Nuitka](https://nuitka.net/) to produce a standalone distributable:

| Platform | Output |
|----------|--------|
| macOS | `dist/exr_converter.app` |
| Linux | `dist/exr_converter` (single binary) |
| Windows | `dist\main.dist\` (folder with `exr_converter.exe` + dependencies) |

Nuitka will auto-download `ccache` on first run. See the `Makefile` for the full set of flags.

## Development

From `apps/exr_converter`:

| Target | Purpose |
|--------|---------|
| `make run` | Start the GUI |
| `make lint` / `make fmt` | Ruff check / format |
| `make resources` | Regenerate `src/rc_resources.py` from `resources.qrc` (needed after icon changes) |
| `make bundle` | Nuitka standalone bundle under `dist/` |
| `make clean` | Remove all build artifacts |

Icons live under `public/` (`icon.icns` / `icon.ico` / `icon.png`).

## Releases

Use a **namespaced tag** (not a global `v1.0.0`): `exr_converter/v1.2.3`. Pushing that tag runs [`.github/workflows/release-exr_converter.yml`](../../.github/workflows/release-exr_converter.yml) and publishes a GitHub Release with Linux AppImage, macOS DMGs (ARM64 + Intel), and a Windows installer.

From the repo root:

```bash
make release-exr PART=patch PUSH=1
```

## License

MIT — see [`LICENSE`](../../LICENSE) at the repository root.
