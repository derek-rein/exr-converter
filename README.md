# vfx-tools

Open-source **desktop tools** for VFX editorial and delivery workflows.

This repository is a **Python monorepo** ([uv](https://docs.astral.sh/uv/) workspace) targeting the [**VFX Reference Platform CY2026**](https://vfxplatform.com/#reference-platform) — **Python 3.13**, Qt/PySide 6.8, OpenColorIO 2.5, OpenEXR 3.4, NumPy 2.3. Each app has its own `pyproject.toml`, release tags, and GitHub Actions builds.

## Tech stack

| Layer | Notes |
|-------|--------|
| **Language & tooling** | Python 3.13, [uv](https://docs.astral.sh/uv/) for deps and runs, [Ruff](https://docs.astral.sh/ruff/) in CI, [Nuitka](https://nuitka.net/) for standalone bundles |
| **UI** | [PySide6](https://doc.qt.io/qtforpython/) (Qt 6.8), Nuke-inspired dark theme |
| **Imaging & color** | [OpenImageIO](https://openimageio.org/) (`oiio-python`), [OpenColorIO 2.5](https://opencolorio.org/) for display/render transforms |
| **Video & sequences** | [PyAV](https://github.com/PyAV-Org/PyAV) (FFmpeg bindings) for video I/O, [fileseq](https://github.com/justinfx/fileseq) for frame sequences & ranges |
| **Slate rendering** | Qt **WebEngine** for HTML/CSS slate preview and capture, [Tailwind CSS](https://tailwindcss.com/) in the slate template |

CI runs on **GitHub Actions**; releases publish per-app binaries for Linux, macOS (Apple Silicon + Intel), and Windows.
All release artifacts are signed with [Sigstore Cosign](https://docs.sigstore.dev/) for supply-chain provenance.

## Downloads

> **Tip:** These links always point to the latest release.

| App | Latest | Downloads |
|-----|--------|-----------|
| **EXR Converter** | [![version](https://img.shields.io/github/v/release/derek-rein/vfx-tools?filter=exr_converter/*&label=)](https://github.com/derek-rein/vfx-tools/releases?q=exr_converter) | [Windows installer](https://github.com/derek-rein/vfx-tools/releases?q=exr_converter) · [macOS DMG](https://github.com/derek-rein/vfx-tools/releases?q=exr_converter) · [Linux AppImage](https://github.com/derek-rein/vfx-tools/releases?q=exr_converter) |

## Apps

| App | Summary |
|-----|---------|
| [**exr_converter**](apps/exr_converter) | GUI and CLI: **video ↔ OpenEXR** sequences with OCIO color management. Includes built-in **slate rendering** — prepend an HTML/CSS slate frame to any conversion. |

### EXR Converter

![EXR Converter — OCIO, EXR → Video tab](assets/exr_converter_screenshot.png)

Convert **video → EXR** (`video2exr`) or **EXR → video** (`exr2video`). Run the GUI with no subcommand, or pass a subcommand for batch use. Enable the "Prepend slate" checkbox to add a 1-frame slate image before the converted output.

```bash
cd apps/exr_converter
uv sync
uv run python main.py                    # GUI
uv run python main.py video2exr -i clip.mov -o ./exrs/
uv run python main.py exr2video -i "./plate.####.exr" -o out.mp4
```

Details: [`apps/exr_converter/README.md`](apps/exr_converter/README.md).

## Building from source

Prerequisites: **Python 3.13** and [**uv**](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/derek-rein/vfx-tools.git
cd vfx-tools
```

### Run an app (no build needed)

```bash
cd apps/exr_converter
uv sync
uv run python main.py
```

### Build a standalone bundle with Nuitka

The app has a `make bundle` target that produces a native distributable (macOS `.app`, Linux binary, Windows folder).

```bash
cd apps/exr_converter
uv sync
make bundle
```

Output lands in `dist/`. On macOS this produces `dist/exr_converter.app`; on Linux `dist/exr_converter`; on Windows `dist\main.dist\` (a folder with `exr_converter.exe` and its dependencies).

A C compiler is required for Nuitka (Xcode CLT on macOS, MSVC or MinGW on Windows, gcc on Linux). Nuitka will auto-download `ccache` on first run.

### Lint and format

```bash
make lint   # ruff check
make fmt    # ruff format + auto-fix
```

## Releases (versioning)

Releases use **namespaced tags** (`exr_converter/v1.2.3`):

| App | Tag pattern | Workflow |
|-----|-------------|----------|
| exr_converter | `exr_converter/v1.2.3` | [`.github/workflows/release-exr_converter.yml`](.github/workflows/release-exr_converter.yml) |

**Automated bump, lockfile, commit, and tag** (from repo root):

```bash
make help
make release-exr PART=patch
make release-exr PUSH=1    # push branch + tag (triggers release builds)
```

CI for lint: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

Pushing a tag creates a **GitHub Release** with platform installers for that app only. CI injects the version from the tag into release binaries.

## License

MIT — see each app's `pyproject.toml` and [`LICENSE`](LICENSE).

[derekvfx.ca](https://derekvfx.ca)
