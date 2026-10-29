---
title: Linux
weight: 12
description: AppImage for artists, a relocatable tarball for studio deploy and Rez, and an RPM for RHEL and Rocky.
---

Linux releases are **x86_64** builds made on **Rocky Linux 8**. They run on
RHEL, Rocky, and Alma **8 and newer** (glibc **2.28**). The same folder is
packed three ways. There is no Snap package and no Flatpak package.

The frozen app bundles Qt, OpenColorIO, OpenImageIO, PyAV, and ONNX Runtime.
It loads OpenGL, X11, fontconfig, and D-Bus from the host. Every platform
uses **Qt 6.9** (PySide6 6.9.2), pinned in `pyproject.toml` and `uv.lock`.
Qt 6.10 and newer Linux wheels need glibc 2.34, which is RHEL 9, so the
project stays on 6.9.2 until RHEL 8 support is dropped.

Settings go to `$XDG_CONFIG_HOME` (default `~/.config`, Qt organization
**VFXTools**, application **EXRConverter**). Presets go to `$XDG_DATA_HOME`
(default `~/.local/share`). Caches, including the optional TrustMark encoder,
go to `$XDG_CACHE_HOME` (default `~/.cache`). The install folder is not written.

Each file has a Cosign `.sigstore.json` bundle and a GitHub Attestations
record, same as the other release assets. Commands are in the release notes.

## AppImage (artists)

One file. Download
[exr_converter-linux-x86_64.AppImage](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.AppImage).

```bash
chmod +x exr_converter-linux-x86_64.AppImage
./exr_converter-linux-x86_64.AppImage
```

If FUSE is not available (common on locked-down hosts):

```bash
./exr_converter-linux-x86_64.AppImage --appimage-extract-and-run
```

## Tarball (studio deployment and Rez)

Download
[exr_converter-linux-x86_64.tar.gz](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.tar.gz).
Extract it on a local disk, under `/opt` or `/software`, or on a read-only
network share. The top folder is `exr-converter-<version>/`.

```bash
tar -xzf exr_converter-linux-x86_64.tar.gz -C /software
/software/exr-converter-0.12.3/exr-converter --version
```

Use the version printed in that release. The command is `exr-converter`. It
resolves its own path, including through a symlink, and runs the `exr_converter`
binary next to it. It does not change directory and it does not write beside
itself. The folder also contains `LICENSE`, a short `README`,
`package.py`, a `.desktop` file, and an icon.

**Rez.** `package.py` is named `exr_converter` at the app version and appends
the package root to `PATH`:

```python
def commands():
    env.PATH.append("{root}")
```

Point the package root at the extracted folder (or publish that folder on a
Rez package path). Then `rez env exr_converter` puts `exr-converter` on `PATH`.

## RPM (IT, RHEL and Rocky)

Download
[exr_converter-linux-x86_64.rpm](https://github.com/derek-rein/exr-converter/releases/latest/download/exr_converter-linux-x86_64.rpm).

```bash
sudo dnf install ./exr_converter-linux-x86_64.rpm
exr-converter
```

| Field | Value |
| --- | --- |
| Install path | `/opt/exr-converter` |
| Command | `/usr/bin/exr-converter` (symlink to the launcher) |
| Desktop file | `/usr/share/applications/exr-converter.desktop` |
| Icon | `/usr/share/icons/hicolor/256x256/apps/exr-converter.png` |
| Vendor | Medeu Global LLC |
| License | MIT |

The package requires the usual 64-bit host libraries: `libEGL.so.1`,
`libGL.so.1`, `libxkbcommon.so.0`, `libxkbcommon-x11.so.0`, `libX11.so.6`,
`libxcb.so.1`, `libglib-2.0.so.0`, `libfontconfig.so.1`, `libfreetype.so.6`,
`libdbus-1.so.3`, and `libstdc++.so.6`. Those come from the RHEL 8 base and
AppStream repositories (`libglvnd`, `mesa`, `xkbcommon`, and so on).

## Optional camera RAW

When the release is built with the private Blackmagic runtime, those
libraries sit inside the same prefix (`braw/`). They are not a separate
download. A build without that secret still runs; `.braw` inputs are
unavailable until the bridge is present. The RHEL 8 package does not include
the RED runtime. `REDDecoder-x64.so` and RED's GPU libraries need glibc 2.29,
so `.r3d` and `.nev` are unavailable in this package. macOS and Windows
packages still include them. See
[R3D / N-RAW](./r3d.md) and [Blackmagic RAW](./braw.md).

## From a source checkout

The packages above are the Release builds. Developing from git still uses
Python 3.13 and uv:

```bash
uv run python main.py
```

That environment follows `uv.lock`, which pins the same PySide6 6.9.2 as the
release packages.
