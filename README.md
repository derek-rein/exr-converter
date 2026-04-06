# vfx-tools

Open-source **desktop tools** for VFX editorial and delivery workflows.

This repository is a **Python monorepo** ([uv](https://docs.astral.sh/uv/) workspace) targeting the [**VFX Reference Platform CY2026**](https://vfxplatform.com/#reference-platform) — **Python 3.13**, Qt/PySide 6.8, OpenColorIO 2.5, OpenEXR 3.4, NumPy 2.3. Each app has its own `pyproject.toml`, release tags, and GitHub Actions builds.

## Tech stack

| Layer | Notes |
|-------|--------|
| **Language & tooling** | Python 3.13, [uv](https://docs.astral.sh/uv/) for deps and runs, [Ruff](https://docs.astral.sh/ruff/) in CI, [PyInstaller](https://pyinstaller.org/) for standalone bundles |
| **UI** | [PySide6](https://doc.qt.io/qtforpython/) (Qt 6.8), Nuke-inspired dark theme |
| **Imaging & color** | [OpenImageIO](https://openimageio.org/) (`oiio-python`), [OpenColorIO 2.5](https://opencolorio.org/) for display/render transforms |
| **EXR Converter specifics** | [PyAV](https://github.com/PyAV-Org/PyAV) (FFmpeg bindings) for video I/O, [fileseq](https://github.com/justinfx/fileseq) for frame sequences & ranges |
| **Slate Maker specifics** | Qt **WebEngine** for HTML/CSS preview and capture, [Tailwind CSS](https://tailwindcss.com/) in the slate template |

CI runs on **GitHub Actions**; releases publish per-app binaries for Linux, macOS (Apple silicon), and Windows.

## Apps

| App | Summary |
|-----|---------|
| [**exr_converter**](apps/exr_converter) | GUI and CLI: **video ↔ OpenEXR** sequences with OCIO color management. |
| [**slate_maker**](apps/slate_maker) | HTML/CSS slates rendered to **OpenEXR** sequences for review and delivery (no compositor license required for batch slates). |

### EXR Converter

![EXR Converter — OCIO, EXR → Video tab](assets/exr_converter_screenshot.png)

Convert **video → EXR** (`video2exr`) or **EXR → video** (`exr2video`). Run the GUI with no subcommand, or pass a subcommand for batch use.

```bash
cd apps/exr_converter
uv sync
uv run python main.py                    # GUI
uv run python main.py video2exr -i clip.mov -o ./exrs/
uv run python main.py exr2video -i "./plate.####.exr" -o out.mp4
```

Details: [`apps/exr_converter/README.md`](apps/exr_converter/README.md).

### Slate Maker

```bash
cd apps/slate_maker
uv sync
uv run python main.py
```

From the repo root (after `uv sync`): `uv run --project apps/slate_maker python apps/slate_maker/main.py`.

Details: [`apps/slate_maker/README.md`](apps/slate_maker/README.md).

## Releases (versioning)

Apps ship on **independent semver** using **namespaced tags** (not one repo-wide `v1.0.0`):

| App | Tag pattern | Workflow |
|-----|-------------|----------|
| exr_converter | `exr_converter/v1.2.3` | [`.github/workflows/release-exr_converter.yml`](.github/workflows/release-exr_converter.yml) |
| slate_maker | `slate_maker/v0.4.0` | [`.github/workflows/release-slate_maker.yml`](.github/workflows/release-slate_maker.yml) |

**Automated bump, lockfile, commit, and tag** (from repo root):

```bash
make help
make release-exr PART=patch
make release-exr PUSH=1    # push branch + tag (triggers release builds)
```

Same pattern: `make release-slate PART=minor`. CI for lint: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

Pushing a tag creates a **GitHub Release** with platform archives for that app only. CI injects the version from the tag into release binaries.

## License

MIT — see each app’s `pyproject.toml` and [`LICENSE`](LICENSE).

[derekvfx.ca](https://derekvfx.ca)
