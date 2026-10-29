# AGENTS.md — context for coding agents

Instructions for AI agents and humans working in this repository. Keep this
file accurate when process or layout changes in ways that affect how work is done.

## What this project is

**EXR Converter** — desktop GUI + CLI for converting **video ↔ OpenEXR** with
**OpenColorIO**, slate / burn-in / watermark overlays, and Nuitka-packaged
binaries for Linux, macOS (arm64 + x86_64), and Windows.

| Item | Value |
|------|--------|
| Package / app | `exr-converter` / EXR Converter |
| Version source of truth | `pyproject.toml` `[project].version` — Help / About / `--version` / EXR metadata read it at runtime (`APP_VERSION` in `src/core/constants.py`). `scripts/bump_app_version.py` edits only this file. |
| Python | **3.13 only** (`requires-python = "==3.13.*"`) |
| Deps / runs | **uv** (`uv sync`, `uv run …`) |
| Entry | `main.py` (GUI with no subcommand; CLI: `video2exr`, `exr2video`) |
| Repo | https://github.com/derek-rein/exr-converter |
| Org settings keys | `QSettings("VFXTools", "EXRConverter")` |

VFX Reference Platform CY2026-ish stack: PySide6 6.9.2 (exact pin on every platform), OCIO 2.5, OIIO, NumPy 2.x, PyAV.
Do not bump PySide6 past 6.9.x while Linux targets RHEL 8: 6.10+ Linux wheels are
manylinux_2_34 (glibc 2.34). Do not use Qt 6.10+ APIs.

## Changelog (required)

**Every user-visible change must update [CHANGELOG.md](./CHANGELOG.md) in the same PR / commit set.**

| Rule | Detail |
|------|--------|
| Where | Add bullets under `## [Unreleased]` using Keep a Changelog sections: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security` |
| Scope | GUI behavior, CLI flags, codecs, OCIO/defaults, packaging that users notice, breaking changes. Skip pure refactors, drive-by renames, and internal-only test plumbing unless they affect contributors. |
| Tone | Short, user-facing sentences. No issue-tracker dump. |
| On release | Move `[Unreleased]` items into a new `## [X.Y.Z] — YYYY-MM-DD` section, leave an empty `[Unreleased]` stub, update compare links at the bottom of the file. |
| Do not | Ship a tagged release without a matching changelog section. Do not rewrite history of already-published versions except to fix factual errors. |

Agents: if you implement a feature or fix and forget the changelog, treat that as incomplete work and add the entry before finishing.

## Documentation (required)

**User-facing docs live in [`docs/`](./docs/)** (Markdown source of truth).
https://vfx.systems/docs/exr-converter loads that folder from this repo; there is
no site build here. Keep each file's front matter (`title`, `description`,
`weight`). Link between pages with relative `./page.md` links; link repo files
outside `docs/` with absolute GitHub URLs. **Help → Documentation** in the app
opens that URL (`DOCS_URL` in `src/core/constants.py`).

| Rule | Detail |
|------|--------|
| When | Any change that affects how users run or integrate the app: CLI flags, GUI behavior, codecs, OCIO/defaults, install/packaging users notice, Nuke/helpers, post-convert prefs, launch flags |
| Where | Update the matching file under `docs/` in the **same PR / commit set** as the code (and [CHANGELOG.md](./CHANGELOG.md) when user-visible) |
| Map | [docs/cli.md](./docs/cli.md) CLI + GUI launch · [docs/gui.md](./docs/gui.md) GUI / prefs / overlays · [docs/quicklook.md](./docs/quicklook.md) macOS Quick Look · [docs/prores.md](./docs/prores.md) VideoToolbox / software / oxideav · [docs/nuke.md](./docs/nuke.md) Nuke menu · [docs/releasing.md](./docs/releasing.md) short release pointer · design notes only when the design changes |
| Do not | Leave docs describing old flags/defaults/codecs. Do not put the only copy of user docs solely in chat or PR text. |

Agents: **before finishing work**, check whether `docs/` still matches the
behavior you shipped. If you changed CLI/GUI/integration surface area and
skipped docs, treat that as incomplete and update them.

Release process for maintainers stays in this file
([Releasing and deployment](#releasing-and-deployment)); keep `docs/releasing.md`
as a short pointer, not a second full copy.

## Layout (where to edit)

```text
main.py                 # GUI / CLI entry
src/
  core/                 # convert, video, EXR I/O, OCIO, codecs constants, sequences
  gui/                  # PySide6 window, tabs, slate UI, preferences, style
  gui/convert_tab.py    # ConvertTab + codec/compression dialogs
  gui/color_widgets.py  # ColorSpaceButton, FpsCombo, OcioConfigPanel
  gui/sequence_browser.py / video_browser.py / browser_chrome.py
  gui/widgets.py        # thin re-export façade (prefer specific modules)
  gui/player/           # SequencePlayer, ShuttleBar, ImagePreviewView (shared playback)
  render/               # slate, burn-in, watermark, tokens (QPainter)
  services/             # worker thread, presets, cache, slate model, prefetch
  rc_resources.py       # generated from resources.qrc — do not hand-edit
native/
  exr_prores/           # optional oxideav-prores PyO3 extension (12-bit ProRes)
resources/              # icons, style.qss, OCIO config, screenshots
scripts/                # bump_app_version.py, ensure_ocio.py, package_msix.py, build_oxideav_prores.py, build_quicklook_appex.py, trustmark_detect.py
packaging/macos/quicklook/  # Swift Quick Look + thumbnail appex (view LUT + flipbook; macOS release embeds under Contents/PlugIns)
tests/                  # pytest; integration + fixtures under tests/fixtures/
docs/                   # user-facing Markdown (vfx.systems/docs/exr-converter loads it)
.github/workflows/      # ci.yml, docs.yml, release.yml, auto-tag-release.yml
```

### Important modules

| Area | Start here |
|------|------------|
| Convert pipeline | `src/core/convert.py`, `src/core/video.py`, `src/core/exr_io.py` |
| Optional R3D / N-RAW | `src/core/r3d/`, bridge source in private `derek-rein/r3d-sdk-private` (macOS Metal; Win/Linux CUDA then OpenCL; CPU fallback), `scripts/build_r3d_bridge.py`, `scripts/fetch_r3d_sdk.py`, `scripts/install_r3d_into_bundle.py`, [docs/r3d.md](./docs/r3d.md) |
| Optional BRAW | `src/core/braw/`, bridge source in private `derek-rein/braw-sdk-private` (Linear ACES AP0; macOS Metal; Win/Linux CUDA then OpenCL; CPU fallback), `scripts/build_braw_bridge.py`, `scripts/fetch_braw_sdk.py`, `scripts/install_braw_into_bundle.py`, [docs/braw.md](./docs/braw.md) |
| CinemaDNG | `native/cinemadng/cinemadng.c`, `src/core/cinemadng/`, `scripts/build_cinemadng.py`, [docs/cinemadng.md](./docs/cinemadng.md). Uncompressed and lossless JPEG Bayer only. Not the Adobe DNG SDK. |
| Optional source plugins (generic hook) | `src/core/source_plugins.py` — private builds may drop decoder plugins into `src/plugins/` (git-ignored, listed in generated `src/plugins/_enabled.py`). The public repo ships none; dispatch order is sidecar check → plugins → CinemaDNG → R3D/BRAW → PyAV. Tests: `tests/test_source_plugins.py` |
| Optional camera RAW plugin | `scripts/camera_raw_plugin.py`. Release fetches a private feed when `CAMERA_RAW_READ_TOKEN` is set, overlays `src/plugins/camera_raw` (git-ignored), and installs runtime libraries plus notices into the bundle. Missing token keeps the generic `.crm` message. |
| Optional 12-bit ProRes (oxideav) | `native/exr_prores/` (PyO3), `src/core/oxideav_prores.py`, `make oxideav-prores`, [docs/plan-12bit-prores-oxideav.md](./docs/plan-12bit-prores-oxideav.md) |
| Private full SDK (local) | R3D: `~/code/r3d-sdk-private/R3DSDKv9_2_1` — CI feed `derek-rein/r3d-sdk-private` release `sdk-9.2.1` (`R3D_SDK_READ_TOKEN`). BRAW: `~/.braw-sdk/` or `~/code/braw-sdk-private/BlackmagicRAWSDK-6.0` — CI feed `derek-rein/braw-sdk-private` release `sdk-6.0` (`BRAW_SDK_READ_TOKEN`). Never commit either SDK. |
| Codec ladder / bit depth labels | `src/core/constants.py` |
| CLI | `src/cli.py` |
| Main window / post-convert actions | `src/gui/window.py` |
| Player prefs / reveal-in-folder | `src/gui/preferences.py` |
| Convert tabs / codecs UI | `src/gui/convert_tab.py` (façade: `src/gui/widgets.py`) |
| Color / OCIO panel widgets | `src/gui/color_widgets.py` |
| Sequence / video browsers | `src/gui/sequence_browser.py`, `src/gui/video_browser.py`, `src/gui/browser_chrome.py` |
| Sequence player | `src/gui/player/` (`SequencePlayer`, transport, preview view) |
| Slate dialog / viewer | `src/gui/slate_widgets.py` (composes player + overlay hooks), `src/gui/ocio_gpu_plane.py`, `src/services/slate_model.py` |
| Background convert | `src/services/worker.py` |
| Video → Proxy tab / watch folder | `src/gui/proxy_tab.py`, `src/core/proxy.py`, `src/core/watch_folder.py`, `src/services/proxy_queue.py` (CLI `proxy` in `src/cli.py`). Optional invisible watermark: `src/core/trustmark/` (ONNX Runtime, encoder downloaded on first use) and `scripts/trustmark_detect.py` |
| Linux studio packages | `packaging/linux/` (Rocky 8 build, `exr-converter` launcher), `scripts/package_linux.py`, `scripts/check_glibc_floor.py`. Release job `build-linux` only |
| Transcode tab (video → video) | `src/gui/transcode_tab.py`, `src/core/transcode.py` (shared PyAV encode engine: codecs/containers, sizes, audio, naming, OCIO, watermark; `run_video_to_proxy` is a thin wrapper over it), `TranscodeQueue` in `src/services/proxy_queue.py` (CLI `transcode` in `src/cli.py`) |

## Commands (local)

```bash
make sync          # uv sync + ensure OCIO 2.5+ linkage
make run           # GUI
make lint          # ruff check
make fmt           # ruff format + fix
make typecheck     # basedpyright
make test          # full pytest (needs QT_QPA_PLATFORM=offscreen — Makefile sets it)
make test-unit     # skip @pytest.mark.integration
make resources     # regenerate src/rc_resources.py after resources.qrc / icons change
make oxideav-prores # build optional PyO3 12-bit ProRes extension (needs Rust)
make braw-sdk-fetch # download private BRAW SDK (needs BRAW_SDK_READ_TOKEN / gh)
make braw-bridge   # optional Blackmagic RAW bridge (needs BRAW_SDK_ROOT)
make bundle        # local Nuitka standalone (does not publish a GitHub Release)
make clean
```

Prefer `make` / `uv run` over system `python` so the project venv is used.

### OCIO pitfall

Bundled ACES Studio config needs **OpenColorIO 2.5+**, which comes from the
independent `opencolorio` package. Official `OpenImageIO` wheels do **not**
ship `PyOpenColorIO`. Symptom if the runtime is too old: config version 2.5
cannot load. Fix: `make ensure-ocio` or `make sync` (`scripts/ensure_ocio.py`).
Nuitka standalone builds still run `scripts/fix_bundle_ocio.py` so
`PyOpenColorIO` keeps the 2.5 shared library.

macOS Nuitka also runs `scripts/prepare_macos_onnxruntime.py` first. Current
Apple Silicon wheels install-name `libonnxruntime.<version>.dylib` as
`@rpath/libonnxruntime.1.dylib` and do not ship that shorter file, so Nuitka
aborts unless the id is rewritten to the real filename. Intel Macs pin
`onnxruntime<1.24` because newer wheels are arm64-only. The rewritten dylib
is a normal Mach-O; `scripts/macos_codesign.py` signs it with the app.
Pull requests that change that script dispatch the Release workflow with
`macos_only` (both DMGs, no GitHub Release) via
`.github/workflows/macos-nuitka-verify.yml`.

### Qt / tests

- GUI tests and many unit tests need `QT_QPA_PLATFORM=offscreen` (Makefile `test` targets set this).
- Integration tests may skip if optional media is missing from `tests/fixtures/` — missing media is skip, not fail.

## Architecture notes agents should respect

1. **Color pipeline** — User src/dst convert uses the **active user config**
   only. App-authored overlays (slate / burn-in / watermark) linearise on the
   private **app anchor** OCIO config (`get_app_anchor_config()` — guaranteed
   `texture_paint` + `aces_interchange` / ACES2065-1), then bridge into the
   user compositing space via interchange when available
   (`linearize_overlay`). Do not paint sRGB on linear without those helpers;
   do not assume the user/Nuke config has sRGB or AP0.
2. **Video I/O is PyAV** — encode/decode via FFmpeg libs bundled with the `av`
   wheel. **PyAV does not ship `ffplay`.** Do not assume system FFmpeg/ffplay
   exists on user machines. Playback after convert uses the user’s preferred
   player from Preferences (system default or custom path).
3. **Software ProRes is 10-bit** in FFmpeg/`prores_ks`. Profiles match Apple,
   Resolve, Premiere, and Nuke: 422 Proxy / LT / 422 / HQ are 10-bit 4:2:2;
   4444 / 4444 XQ are up to 12-bit 4:4:4:4 with optional alpha, in `.mov`.
   Cross-platform 12-bit is experimental oxideav PyO3 **4444 / XQ only**
   (`prores_ox_4444`, `prores_ox_xq`, `make oxideav-prores`); see
   `docs/plan-12bit-prores-oxideav.md`. Do not offer a 12-bit 422 preset.
   VideoToolbox ProRes (`prores_vt_*`) is **macOS-only** and uses the same
   six profiles (422 at 10-bit, 4444 / XQ ~12-bit). UI must keep honest
   bit-depth labels.
4. **Slate is QPainter** — no Qt WebEngine. Do not reintroduce browser-based slate.
   **Preview display** prefers **GPU OCIO** (`src/gui/ocio_gpu_plane.py`,
   `QOpenGLWidget` + PyOpenGL + OCIO `getDefaultGPUProcessor`). Do not put
   full-res CPU `applyRGB` back on the playback hot path. Export remains CPU.
5. **Generated resources** — edit `resources.qrc` / files under `resources/`, then
   `make resources`. Do not hand-edit `src/rc_resources.py`.
6. **Presets / settings / undo** — Prefer :class:`~src.services.app_settings.AppSettings`
   (`get_app_settings()`) over ad-hoc `QSettings(APP_ORG, APP_NAME)`. Keys live in
   `AppSettings.Keys` / domain modules. Layers: **app prefs** (player, cache,
   post-convert), **session** (last paths/options), **convert presets** (versioned
   JSON under AppData — no I/O paths), **slate document** (`SlateModel` + model-owned
   `QUndoStack`; not convert paths/OCIO). Post-convert: `ui/copy_path_after` (default
   **true**), `ui/open_after`, `ui/show_folder_after`. Open result: EXR→Video
   `player/mode` (`builtin`|`system`|`custom`); Video→EXR uses built-in player window.
7. **Nuitka** — Release builds strip many Qt modules (WebEngine, Svg, Pdf, …).
   Prefer PySide6-Essentials APIs already used in the tree. Bundle includes
   `resources/ocio` and package data for `av`, OIIO, OCIO, fileseq.
   **Check for Updates** opens the GitHub Releases page via
   `QDesktopServices` (system browser) — no in-app HTTPS download. Smoke test
   still checks Python `ssl` is importable in the frozen binary; keep
   `--include-module=ssl` and do **not** `--noinclude-dlls=libssl*` /
   `libcrypto*` if you reintroduce app HTTPS later.
8. **macOS Dock icon** — Do **not** call `setWindowIcon` / set a runtime PNG on
   Darwin. Dock must use the bundle `.icns` (`--macos-app-icon`). A Qt PNG
   override becomes `applicationIconImage` and shows sharp square corners while
   running. Windows/Linux still set `:/icon.png`.

## Coding conventions

- Match existing style: type hints, `from __future__ import annotations`, Ruff
  line length 100, import order as Ruff formats.
- Prefer small, focused diffs. Do not drive-by-refactor unrelated modules.
- Tests: add unit tests next to behavior when practical; use
  `@pytest.mark.integration` only for real/synthetic media round-trips.
- After TypeScript-less Python work: `make lint`, `make typecheck` (CI fails on
  errors; warnings are tolerated) and relevant `make test-unit`
  or targeted `uv run pytest path/to/test.py`.
- Do not commit secrets, large binary fixtures, or local venv paths.

## Releasing and deployment

Canonical automation: **Makefile** (`release`, `bump`) and
**[`.github/workflows/release.yml`](.github/workflows/release.yml)**.
Use **`gh`** to watch CI and inspect GitHub Releases.

User-facing history: **[CHANGELOG.md](./CHANGELOG.md)** (update before tagging).

### Pipeline overview

```text
feature commits (PR → main)
        │
        ▼
 make release PART=patch|minor|major   (on release/X.Y.Z branch, PUSH=0)
        │  1. scripts/bump_app_version.py → pyproject.toml version
        │  2. uv lock
        │  3. commit: "release: X.Y.Z"  (only version files)
        │  4. optional local annotated tag vX.Y.Z  (not required for publish)
        ▼
 PR → main → merge  (CHANGELOG must already have ## [X.Y.Z] + compare link)
        │
 Auto-tag release workflow (on push to main)
        │  refuse if CHANGELOG lacks ## [X.Y.Z]
        │  if pyproject version X.Y.Z has no tag → create + push annotated tag
        │  if no GitHub Release yet and no Release run active →
        │      `gh workflow run Release --ref main -f tag=vX.Y.Z`
        ▼
 GitHub Actions “Release”
   triggers: push tags v*  (human/PAT)  OR  workflow_dispatch (auto-tag)
   concurrency: one run per tag (cancel-in-progress: false)
        │  validate tag↔pyproject + CHANGELOG
        │  lint → test (3 OS) → gate → Nuitka (4 targets)
        │  → optional SignPath (Windows exe) → Cosign + GH attestations → publish
        ▼
 Artifacts: Linux AppImage, macOS DMG (arm64 + x86_64), Windows installer,
            Windows .msixupload and .msix when Store identity variables are set
            (+ .sigstore.json bundles; GH attestations for provenance)
```

Tags are plain semver: **`v1.2.3`**. The tag is the published app version
(the frozen binary ships `pyproject.toml` and reads `[project].version` at runtime).

**Critical GitHub quirk:** a tag created with the default **`GITHUB_TOKEN` does
not start other workflows** (recursion guard). Auto-tag therefore **dispatches**
Release via `workflow_dispatch` after pushing the tag. The dispatch uses
**`--ref main`** so the *workflow file* is latest; the build checks out the tag
via `inputs.tag` (`RELEASE_REF`). Human `git push origin v*` with a real
user/PAT still triggers Release on `push: tags`.

**Do not double-fire:** after merge, prefer Auto-tag only. Do not also push the
local `vX.Y.Z` tag unless Auto-tag failed — a human tag push *and* a late
dispatch can race (concurrency serializes same-tag runs, but wastes minutes).

### Prerequisites

- Feature work committed (or stashed) before `make release`.
- Release commit stages **only**: `pyproject.toml`, `uv.lock`.
- `uv` available; `scripts/bump_app_version.py` is plain Python 3.
- [GitHub CLI](https://cli.github.com/) (`gh auth status`) for checks / releases.
- **`main` is branch-protected** — no direct pushes; use PRs. Do not run
  `make release` with `PUSH=1` on `main`.

### Ship a version (protected main)

```bash
git checkout main && git pull origin main
git checkout -b release/X.Y.Z

# Before tagging: CHANGELOG.md must have [X.Y.Z] notes + bottom compare link
# (Auto-tag and Release gate both refuse to ship without them.)
make release PART=minor PUSH=0    # or patch / major
# → commit "release: X.Y.Z" + local annotated tag vX.Y.Z
# Include CHANGELOG.md in the release PR if not already on main
# (bump only commits version files — commit changelog separately on the branch)

git push -u origin HEAD
gh pr create --base main --title "release: X.Y.Z" --body "…"
gh pr checks
gh pr merge --merge          # or --squash per preference

git checkout main && git pull origin main
# Auto-tag: creates vX.Y.Z if missing → dispatches Release (--ref main -f tag=…).
# Manual fallback if auto-tag is disabled or failed:
#   git tag -a vX.Y.Z -m "release: X.Y.Z" && git push origin vX.Y.Z
#   # or: gh workflow run Release --ref main -f tag=vX.Y.Z

gh run list --workflow="Auto-tag release" --limit 3
gh run list --workflow=Release --limit 3
gh run watch
gh release view vX.Y.Z
```

Historical: `release/0.3.0` → PR #1; `release/0.4.0` → PR #2; `release/0.5.0` → PR #3
(tag was pushed manually after merge; auto-tag added afterward).

### Make variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `PART` | `patch` | Semver segment: `patch` \| `minor` \| `major` |
| `PUSH` | `1` | Push branch + tag — use **`PUSH=0`** under branch protection |

Version-only bump (no git): `make bump PART=patch`.

### Choosing patch vs minor vs major

| Bump | Use when |
|------|----------|
| **patch** | Fixes, CI, docs-only, no new user-facing capabilities |
| **minor** | New codecs/presets, visible features, honest label/option expansions |
| **major** | Breaking CLI/API or intentional 1.0-style cut |

### After push: `gh`

```bash
gh run list --workflow=Release --limit 5
gh run watch
gh run view <run-id> --log-failed

gh release list --limit 10
gh release view vX.Y.Z
gh release download vX.Y.Z --pattern '*.dmg' --dir /tmp/exr-assets

gh run list --workflow=CI --limit 5
gh pr checks
```

### Release workflow jobs

| Job | Role |
|-----|------|
| `lint` | Tag↔pyproject + CHANGELOG gate, then Ruff check + format |
| `test` | Full pytest on ubuntu / macos / windows |
| `gate` | Aborts release if lint or any OS test failed |
| `build` | Nuitka → macOS DMG / Windows setup (3h timeout, 6h on macOS so notarization can finish; LTO + Qt strip aligned with `make bundle`). macOS DMGs are Developer ID-signed, notarized, and stapled here, before upload, when the Apple secrets below are set. Skipped for `linux_only` |
| `build-linux` | Rocky Linux 8 container (`rockylinux:8`, glibc 2.28) → AppImage, relocatable tarball, and RPM from one Nuitka dist. Installs PySide6 from `uv.lock` like the other platforms (the project pins 6.9.2 everywhere because 6.10+ Linux wheels need glibc 2.34) and pins patchelf 0.19 (Rocky 8's patchelf 0.12, and patchelf's RPATH rewrite, mis-align NumPy OpenBLAS; those wheel copies are restored). Skipped for `macos_only` |
| `sign-windows` | Optional SignPath Authenticode (skipped unless vars configured; runs **before** Cosign) |
| `sign` | Assemble final bits → Cosign keyless + `actions/attest-build-provenance` for the AppImage, DMGs, and setup, then the Linux tarball and RPM. Skipped for `macos_only` and for `linux_only` (the matrix build did not run) |
| `release` | CHANGELOG section + verify notes → single GitHub Release upload. Skipped when `publish` is false, `macos_only` is true, or `linux_only` is true |

Local package only: `make bundle` (no GitHub Release). Shared Nuitka strip/LTO
flags match CI; packaging (DMG/AppImage/Inno) is CI-only.

### Actions hygiene

- Third-party actions are **SHA-pinned** with trailing `# vX` comments.
- [`.github/dependabot.yml`](.github/dependabot.yml) opens weekly grouped PRs for
  `github-actions` bumps.
- `astral-sh/setup-uv` pins **uv 0.12.3** (not `latest`).
- AppImage packaging pins **appimagetool 1.9.1** and verifies SHA-256. The Linux job also pins **nfpm 2.41.3** for the RPM and builds inside **Rocky Linux 8**.

### SignPath (optional Windows Authenticode)

Skipped until you set repository **variables** (and matching **secrets**):

| Kind | Name |
|------|------|
| secret | `SIGNPATH_API_TOKEN`, `SIGNPATH_ORGANIZATION_ID` |
| variable | `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_SIGNING_POLICY_SLUG` |
| variable (optional) | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` (default `exr_converter`) |

Also install the SignPath GitHub App and create a project/policy/artifact
configuration in the SignPath dashboard. When `SIGNPATH_PROJECT_SLUG` is empty,
`sign-windows` is skipped and the unsigned Inno setup is published (Cosign still
applies). SignPath is the setup `.exe` only. Do not send the MSIX through it.

### Microsoft Store MSIX

The Windows build job can pack two Release assets with `makeappx`
(`scripts/package_msix.py`) from the same Nuitka folder the Inno setup uses:

- `exr_converter-windows-x86_64.msixupload` — Partner Center upload. A zip of
  the unsigned `.msix` plus an `.appxsym` when that folder contains PDB files.
  No PDBs is still valid. **Do not sign the upload or the package inside it.**
  Partner Center re-signs it.
- `exr_converter-windows-x86_64.msix` — self-distribution. After the upload is
  written, `signtool` signs this file when the Azure Artifact Signing secrets
  below are all set. Otherwise it ships unsigned, and Windows will not install
  it. An unsigned MSIX needs a trusted signature before `Add-AppxPackage` works.

SignPath stays on the setup `.exe` only. A detached Cosign `.sigstore.json`
is provenance for the file bytes, not an Appx signature, and is not uploaded
to the Store.

Optional **secrets** (all of them, or none) for `signtool` on the loose `.msix`:

| Secret | Meaning |
|--------|---------|
| `AZURE_TENANT_ID` | Directory (tenant) id |
| `AZURE_CLIENT_ID` | App registration client id |
| `AZURE_CLIENT_SECRET` | Client secret |
| `AZURE_ARTIFACT_SIGNING_ENDPOINT` | Regional Artifact Signing endpoint |
| `AZURE_ARTIFACT_SIGNING_ACCOUNT` | Artifact Signing account name |
| `AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE` | Certificate profile name |

Optional Store publish, on a real release only (tag push, or
`workflow_dispatch` with publish and not `macos_only`), after the
`.msixupload` artifact is uploaded. Uses `msstore reconfigure` then
`msstore publish <msixupload> --appId`. The action is
`microsoft/microsoft-store-apppublisher` (setup-msstore-cli) at CLI v0.4.3.
Missing values skip with a notice. A publish failure does not fail the job.
The first Partner Center submission is manual. See [docs/msix.md](./docs/msix.md).

| Name | Kind | Meaning |
|------|------|---------|
| `MSSTORE_TENANT_ID` | secret | Directory (tenant) id |
| `MSSTORE_CLIENT_ID` | secret | App registration client id |
| `MSSTORE_CLIENT_SECRET` | secret | Client secret |
| `MSSTORE_SELLER_ID` | secret | Partner Center seller id |
| `MSSTORE_APP_ID` | variable | Store product id of an already published app |

Repository **variables** (not secrets):

| Variable | Required | Meaning |
|----------|----------|---------|
| `MSIX_IDENTITY_NAME` | yes | Partner Center package identity name |
| `MSIX_PUBLISHER` | yes | Exact `CN=...` publisher string |
| `MSIX_PUBLISHER_DISPLAY_NAME` | no | Defaults to **DerekVFX** |

If the required variables are empty, the step warns and skips. The setup
`.exe` still ships. Manifest version is `pyproject.toml` `a.b.c` → `a.b.c.0`.
Full-trust desktop (`runFullTrust` only, `Windows.FullTrustApplication`,
`Windows.Desktop`). No file associations or protocol handlers. Do not declare
`unvirtualizedResources` or file/registry virtualization exclusions. Store
installs use the normal per-package virtualized AppData and registry for
QSettings, presets, and the TrustMark cache. That state is not shared with
the setup `.exe`. Add the capability back only if a future feature must hand
a settings or cache file to a process outside the package. Submission
steps: [docs/msix.md](./docs/msix.md).

### Apple Developer ID (macOS notarization)

Release DMGs are signed with a **Developer ID Application** certificate,
submitted with `notarytool`, and stapled, before Cosign hashes them. The
certificate in use is the G2 cert **Developer ID Application: Medeu Global LLC
(83546T4BT8)**, team **83546T4BT8**, expiring **2031-09-17**.

| Kind | Name |
|------|------|
| secret | `MACOS_CERT_P12_BASE64` — base64 of a PKCS#12 exported with OpenSSL 3 `openssl pkcs12 -export -legacy` (certificate + private key). OpenSSL 3's default AES-256 p12 fails `security import` |
| secret | `MACOS_CERT_PASSWORD` — password for that p12 |
| secret | `MACOS_SIGNING_IDENTITY` — exactly `Developer ID Application: Medeu Global LLC (83546T4BT8)` |
| secret | `ASC_API_KEY_P8` — App Store Connect API key (`.p8` PEM contents, not base64) |
| secret | `ASC_KEY_ID` — key id for that API key |
| secret | `ASC_ISSUER_ID` — issuer UUID |
| secret | `APPLE_TEAM_ID` — `83546T4BT8` |

If the three certificate secrets are unset (forks, or a repo that has not
configured them), the build keeps the ad-hoc signature and skips notarization.
A partial set fails the build. Signing uses `scripts/macos_codesign.py`:
Mach-Os deepest-first, **without** `codesign --deep` (the BRAW framework
layout), with hardened runtime, a secure timestamp, and
`packaging/macos/entitlements.plist` on the main executable. Those
entitlements are `disable-library-validation` (Qt, PyAV, OIIO, OCIO, and the
RED/Blackmagic dylibs), `allow-unsigned-executable-memory` (libffi on Intel),
and `allow-jit` (libffi `MAP_JIT` on Apple Silicon). The bundle id is
`com.vfxtools.exrconverter`.

Before that seal, the script moves non-Mach-O files out of `Contents/MacOS`
into `Contents/Resources` and leaves relative symlinks. A Developer ID
signature treats every file next to the main executable as nested code, so a
yaml or `pyproject.toml` there fails with `code object is not signed at all`.
Ad-hoc signing does not. Directory names that contain `.` and still hold
Mach-O binaries are renamed with `__dot__` and symlinked back. Runtime lookup
follows the symlinks (`runtime_exe_dirs()` also searches `Contents/Resources`).

`notarytool submit` returns the submission id immediately. The job then polls
`notarytool info` on that id about once a minute for up to five hours and does
not upload the DMG again. Each info call has a two-minute timeout and is
retried when Apple or the network fails transiently. `Invalid` / `Rejected`
prints `notarytool log`. macOS build jobs use the 6-hour GitHub-hosted limit
so that poll fits after Nuitka; other OS stay at 3 hours. Without the
certificate secrets the build still ad-hoc signs and skips notarization.

**Signing test without publishing a release** (after this workflow is on the
ref you pass):

```bash
gh workflow run Release --ref <branch> -f macos_only=true -f publish=false
gh run watch
```

That still runs the lint/test gate, then builds only the two macOS DMGs,
notarizes them, and uploads Actions artifacts
`exr_converter-macos-arm64-signed` and `exr_converter-macos-x86_64-signed`.
It does not create a GitHub Release. `macos_only` forces publish off even if
`publish=true` is also set. Download the DMG from the run and check:

```bash
spctl -a -vvv -t install exr_converter-macos-arm64.dmg
codesign --verify --deep --strict --verbose=2 exr_converter-macos-arm64.dmg
```

`source=Notarized Developer ID` means Gatekeeper will open the DMG without
the unidentified-developer warning.

### Checklist before shipping

- [ ] Feature work tested (`make test` / PR CI)
- [ ] **[CHANGELOG.md](./CHANGELOG.md)** updated: `[Unreleased]` rolled into `[X.Y.Z]` **and** bottom compare link `[X.Y.Z]:` updated
- [ ] On branch `release/X.Y.Z` (not direct push to `main`)
- [ ] Working tree free of unrelated unstaged work
- [ ] Correct `PART`; `make release … PUSH=0` → PR → merge
- [ ] Auto-tag (or manual tag / `gh workflow run`) fires Release; `gh run watch` green
- [ ] `gh release view vX.Y.Z` has artifacts + signatures
- [ ] After merge: **do not** also push a local tag unless Auto-tag failed

### Troubleshooting

| Symptom | What to do |
|---------|------------|
| `GH013` / must use pull request | Use `release/X.Y.Z` + PR; `PUSH=0` |
| Required status checks on main | Ship via PR so `ci-ok` runs on the PR head |
| `No changes to commit` from `make release` | Version already bumped; `python3 scripts/bump_app_version.py show` |
| Auto-tag refuses: CHANGELOG missing section | Add `## [X.Y.Z]` + `[X.Y.Z]:` compare link, merge fix PR |
| Tag exists locally not on remote | After merge, retag if needed, `git push origin vX.Y.Z`. Never force-push a published public tag lightly |
| Gate red on Release | Fix forward with a new patch; prefer not rewriting a published tag |
| Wrong version in GUI binary | Tag must be `vX.Y.Z` matching `pyproject.toml`; Nuitka must include that file (`--include-data-files=pyproject.toml=pyproject.toml`) |
| PyOpenColorIO not 2.5+ / Nuitka linkage | `scripts/ensure_ocio.py` / `make ensure-ocio`; bundles: `scripts/fix_bundle_ocio.py` |
| `Unable to reserve cache` (setup-uv) | Benign race if two writers share a key; CI/Release use `cache-suffix` + lint `save-cache: false` |
| Two Release runs for one tag | Concurrency group `release-vX.Y.Z` queues; avoid double-trigger (tag push + dispatch) |
| notarytool / `security import` failed | Build log prints the notary log. The p12 must be `openssl pkcs12 -export -legacy` (cert + key). Identity must be `Developer ID Application: Medeu Global LLC (83546T4BT8)`. Renew that G2 cert before 2031-09-17 |
| `codesign --verify --deep` bundle format is ambiguous | BRAW framework was not normalized. Signing itself never passes `--deep`; deep verify is the notarization check and fails closed |

### Artifact verification (users)

**Cosign** keyless signatures ship as `*.sigstore.json` next to each asset.
Auto-tag dispatches Release from **main**, so the OIDC certificate identity is
typically:

`https://github.com/derek-rein/exr-converter/.github/workflows/release.yml@refs/heads/main`

(A pure human `git push` of the tag uses `@refs/tags/vX.Y.Z` instead.)

```bash
cosign verify-blob <ARTIFACT_FILE> \
  --bundle <ARTIFACT_FILE>.sigstore.json \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp 'https://github.com/derek-rein/exr-converter/\.github/workflows/release\.yml@refs/(heads/main|tags/v.+)'
```

**GitHub Attestations** (SLSA-style build provenance) are also recorded:

```bash
gh attestation verify <ARTIFACT_FILE> -R derek-rein/exr-converter
```

### macOS Gatekeeper

The macOS build is signed and notarized, so it opens normally. First launch
may still show the standard "downloaded from the internet" prompt.

## CI

| Workflow | Trigger | Gate |
|----------|---------|------|
| [CI](.github/workflows/ci.yml) | push / PR to `main` | Ruff + basedpyright (`src/`, `main.py`; errors fail, warnings tolerated) + full pytest on 3 OS; job **`ci-ok`** requires all green |
| [Auto-tag release](.github/workflows/auto-tag-release.yml) | push to `main` | CHANGELOG gate; if no `vX.Y.Z` tag → push tag; if no GitHub Release and no active run → `workflow_dispatch` Release |
| [Release](.github/workflows/release.yml) | tag `v*` **or** `workflow_dispatch` | Tag/CHANGELOG validate + lint + tests via **`gate`** before Nuitka / Cosign / publish. `macos_only` notarizes the DMGs and does not publish. `linux_only` builds the Rocky 8 AppImage, tarball, and RPM and does not publish |

Branch protection on `main` should require `ci-ok`.

## Docs map

| Doc | Purpose |
|-----|---------|
| [AGENTS.md](./AGENTS.md) | This file — agent/process context, release & deploy, **docs rules** |
| [CHANGELOG.md](./CHANGELOG.md) | User-facing history (**must update**) |
| [README.md](./README.md) | Product overview, install, short CLI |
| [docs/](./docs/) | User-facing Markdown (**must update** when behavior changes) |
| [docs/cli.md](./docs/cli.md) | CLI (incl. `proxy` / `--watch`, `transcode`) + GUI launch flags |
| [docs/gui.md](./docs/gui.md) | GUI tabs, overlays, preferences, post-convert |
| [docs/linux.md](./docs/linux.md) | AppImage, relocatable tarball, and RHEL / Rocky RPM |
| [docs/quicklook.md](./docs/quicklook.md) | macOS Quick Look + Finder thumbnails (EXR, DPX, optional stills) |
| [docs/prores.md](./docs/prores.md) | Software vs VideoToolbox vs oxideav ProRes |
| [docs/nuke.md](./docs/nuke.md) | Nuke menu integration |
| [docs/plan-12bit-prores-oxideav.md](./docs/plan-12bit-prores-oxideav.md) | Experimental RDD-36 12-bit ProRes (oxideav; shipping in releases) |
| [docs/r3d.md](./docs/r3d.md) | Optional RED R3D / N-RAW (proprietary SDK; license + build) |
| [docs/braw.md](./docs/braw.md) | Optional Blackmagic RAW (proprietary SDK; Linear ACES AP0) |
| [docs/cinemadng.md](./docs/cinemadng.md) | CinemaDNG sequences (DJI Linear P3-D60 + 1.4 stops) |
| [docs/releasing.md](./docs/releasing.md) | Short pointer here + docs-site note |
| [docs/msix.md](./docs/msix.md) | Windows .msixupload (Store) and .msix (self-distribution) |
| [integrations/nuke/](./integrations/nuke/) | Nuke `menu.py` + helpers |

Public docs: https://vfx.systems/docs/exr-converter (loads `docs/` from this repo).

## What not to do

- Do not assume system `ffmpeg` / `ffplay` / `mpv` are installed for core features.
- Do not publish Snap or Flatpak packages. Linux releases are the AppImage, the relocatable tarball, and the RPM.
- Do not claim software ProRes is 12-bit.
- Do not add oxideav 422 profiles (`prores_ox_proxy` / `lt` / `422` / `hq`)
  or any other 12-bit 4:2:2 ProRes preset. 422 is 10-bit. True 12-bit
  delivery other apps read is 4444 / XQ.
- Do not commit the proprietary RED R3D SDK (headers, static libs, docs, samples)
  or claim the SDK is open-source; only Redistributable dylibs/so/dll may ship,
  in a private app directory, with required EULA terms — see [docs/r3d.md](./docs/r3d.md).
- Do not commit the proprietary Blackmagic RAW SDK (headers, runtime libs,
  samples, `profile.braw`, docs). Public Releases may ship only runtime
  libraries + `libbraw_bridge` under a private `braw/` folder — see
  [docs/braw.md](./docs/braw.md).
- Do not reintroduce Qt WebEngine for slate.
- Do not sign the `.msixupload` or the package inside it (`signtool`, SignPath, or a local cert). Partner Center signs that package. The loose `.msix` may be signed with `signtool` only when the Azure Artifact Signing secrets are set. The Inno `.exe` is the direct download.
- Do not push version tags without a changelog section and green Release gate intent.
- Do not hand-edit `src/rc_resources.py` or force-push published release tags without a deliberate recovery process.
- Do not finish user-visible work without updating **CHANGELOG.md** and **`docs/`** when the surface area changed.
