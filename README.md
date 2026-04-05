# vfx-tools

Personal VFX utilities: desktop tools for EXR workflows, slate generation, and helper scripts for Blender and cloud rendering.

**Requirements:** Python **3.11** (see each app’s `pyproject.toml`). The Python apps use **[uv](https://docs.astral.sh/uv/)** for installs and runs.

## Repository layout

| Path | Description |
|------|-------------|
| [`apps/exr_converter`](apps/exr_converter) | **EXR Converter** — PySide6 GUI and CLI to convert between video and OpenEXR sequences using OpenImageIO, OpenColorIO, and PyAV. |
| [`apps/slate_maker`](apps/slate_maker) | **Slate Maker** — Qt + WebEngine app that renders HTML slates to EXR sequences for editorial and delivery. |
| [`scripts/`](scripts) | Standalone helpers and Blender add-ons (see below). |

## EXR Converter

Convert **video → EXR** (`video2exr`) or **EXR sequence → video** (`exr2video`) with OCIO color management. Launch the GUI with no subcommand, or use `--headless` with a subcommand for batch use.

```bash
cd apps/exr_converter
uv sync
uv run python main.py                    # GUI
uv run python main.py video2exr -i clip.mov -o ./exrs/
uv run python main.py exr2video -i "./plate.####.exr" -o out.mp4
```

See `apps/exr_converter/Makefile` for `make run`, lint/format, and PyInstaller bundle targets.

**Releases:** each app uses its **own git tag** (not a single repo-wide `v1.0.0`). Example: `exr_converter/v1.2.3` or `slate_maker/v0.4.0` — see [Releases (versioning)](#releases-versioning) below. CI: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Slate Maker

```bash
cd apps/slate_maker
uv sync
uv run python main.py
```

From the repo root (after `uv sync`): `uv run --project apps/slate_maker python apps/slate_maker/main.py`. The root [`pyproject.toml`](pyproject.toml) is a **uv workspace** that includes this app — see [`apps/slate_maker/README.md`](apps/slate_maker/README.md).

## Scripts

| Script | Role |
|--------|------|
| [`scripts/aovSetup.py`](scripts/aovSetup.py) | Blender add-on: configures view-layer AOVs and compositor outputs (beauty, data, Cryptomatte EXRs). |
| [`scripts/renderFarm.py`](scripts/renderFarm.py) | Blender add-on integrating **Modal** for cloud rendering (see file for setup). |
| [`scripts/airplane_motion.py`](scripts/airplane_motion.py) | Blender add-on: procedural airplane-style motion for objects. |

Install Blender add-ons via **Edit → Preferences → Add-ons → Install**, then enable them in the list.

## Releases (versioning)

This repo is a **monorepo**: apps ship on **independent semver**, each tied to a **namespaced tag**:

| App | Tag pattern | Workflow |
|-----|-------------|----------|
| exr_converter | `exr_converter/v1.2.3` | [`.github/workflows/release-exr_converter.yml`](.github/workflows/release-exr_converter.yml) |
| slate_maker | `slate_maker/v0.4.0` | [`.github/workflows/release-slate_maker.yml`](.github/workflows/release-slate_maker.yml) |

**Automated bump, lockfile, commit, and tag** (from repo root):

```bash
make help                    # bump / release targets
make release-exr PART=patch  # bumps semver, syncs APP_VERSION, uv lock, commit, tag
make release-exr PUSH=1      # …also git push (runs the GitHub release workflow)
```

Same pattern for slate: `make release-slate PART=minor`. Bump only (no git): `make bump-exr PART=patch`.

**Manual tagging** (if you do not use the Makefile): bump `version` in the app’s `pyproject.toml`, sync `APP_VERSION` in `src/constants.py`, run `uv lock`, then:

```bash
git tag exr_converter/v1.2.3
git push origin exr_converter/v1.2.3
```

That creates a **GitHub Release** for that tag and attaches platform archives. The other app is unaffected. CI still injects the build version into frozen binaries where configured.

## License

MIT — see `pyproject.toml` in each package for author metadata.
