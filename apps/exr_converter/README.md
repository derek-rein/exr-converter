# EXR Converter

Desktop app and CLI for converting between **video** and **OpenEXR** sequences with **OpenColorIO** color management. Uses **PyAV** for decode/encode, **OpenImageIO** for EXR I/O, and **PySide6** for the GUI.

## Requirements

- **Python 3.11**
- [uv](https://docs.astral.sh/uv/) (recommended) or another PEP 621–compatible installer

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

## Development

From `apps/exr_converter`:

| Target | Purpose |
|--------|---------|
| `make run` | Start the GUI |
| `make lint` / `make fmt` | Ruff check / format |
| `make resources` | Regenerate `src/rc_resources.py` from `resources.qrc` (needed after icon changes) |
| `make bundle` | PyInstaller one-folder bundle under `dist/exr_converter` |
| `make bundle-app` | Same with `--windowed` (macOS `.app`-friendly) |

Icons live under `public/` (`icon.icns` / `icon.ico` for bundling via `ICON=...`).

## Releases

Use a **namespaced tag** (not a global `v1.0.0`): `exr_converter/v1.2.3`. Pushing that tag runs [`.github/workflows/release-exr_converter.yml`](../../.github/workflows/release-exr_converter.yml) and publishes a GitHub Release with Linux, macOS (arm64), and Windows archives.

## License

MIT — see [`LICENSE`](../../LICENSE) at the repository root.
