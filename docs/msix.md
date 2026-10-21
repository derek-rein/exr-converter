---
title: Microsoft Store
weight: 32
description: Unsigned .msixupload for Partner Center submission
---

The Windows release build can pack an **unsigned** `.msixupload` from the same Nuitka app as the setup `.exe`. Partner Center (product type **MSIX or PWA app**) signs the package inside it. This repository does not have a code-signing certificate and must not sign the package.

The setup `.exe` is unchanged. Download that for a direct install. The `.msixupload` is only for Store submission.

## Repository variables

Set these under **Settings → Secrets and variables → Actions → Variables**. They are variables, not secrets.

| Variable | Required | Value |
|----------|----------|--------|
| `MSIX_IDENTITY_NAME` | yes | Partner Center package identity name |
| `MSIX_PUBLISHER` | yes | The publisher string from Partner Center, copied exactly, including the `CN=` prefix |
| `MSIX_PUBLISHER_DISPLAY_NAME` | no | Shown on the tile. Defaults to **DerekVFX**. When you set it, it must match the publisher display name in Partner Center |

If `MSIX_IDENTITY_NAME` or `MSIX_PUBLISHER` is empty, the Windows job prints a warning and skips the MSIX. The setup `.exe` still ships.

The manifest version is `[project].version` from `pyproject.toml` with a fourth segment of `0` (`0.12.3` → `0.12.3.0`). The Store requires that last segment to be 0.

## What the Release workflow publishes

On the same Release run as the other installers (no extra workflow):

- GitHub Release asset `exr_converter-windows-x86_64.msixupload` — upload this to Partner Center
- Actions artifact `exr_converter-windows-x86_64-msix` — the plain `.msix`, for inspection only. It is not a Release asset
- Actions artifact `exr_converter-windows-x86_64-msixupload` — the same upload file, before it is copied into the Release

A detached `.sigstore.json` may sit next to the Release asset. That is provenance for the file bytes. It is not an MSIX signature. Do not upload it to Partner Center.

## What is inside the `.msixupload`

The file is a zip. Its members sit at the **root** of the archive (the same layout as selecting the files and using Send to → Compressed folder, not zipping a parent directory):

- `exr_converter_<version>_x64.msix` — the unsigned package (`0.12.3` is `exr_converter_0.12.3.0_x64.msix`)
- `exr_converter_<version>_x64.appxsym` — only when the Nuitka folder contains `.pdb` files. This is a zip of those symbols, with paths relative to the app directory, for Partner Center crash analytics

Nuitka release builds usually have no PDB files. An upload that contains only the `.msix` is valid. The symbol member uses `.appxsym`. Partner Center rejects a `.msixsym` member. PDB files are not installed inside the `.msix`.

## Submit in Partner Center

1. Create the app as **MSIX or PWA app** and reserve the name.
2. Copy the identity name, the `CN=...` publisher, and the publisher display name into the repository variables above.
3. Ship a release (merge to `main` so Auto-tag runs, or dispatch Release). Download `exr_converter-windows-x86_64.msixupload` from the GitHub Release.
4. Create a submission and upload that `.msixupload`. Do not sign it first with a local certificate, SignPath, or `signtool`. Do not upload the plain `.msix` or the `.sigstore.json`. `Add-AppxPackage` refuses the unsigned package; that is expected.
5. Store listing images (screenshots, the 300×300 icon) are uploaded in Partner Center. They are separate from the tile logos inside the package.

The package is x64, full trust (`runFullTrust`), with entry point `exr_converter.exe` (`Windows.FullTrustApplication`) and device family `Windows.Desktop` (minimum 10.0.19041.0). It does not register file associations or protocol handlers. Open media from inside the app. An execution alias registers `exr_converter.exe` for a new terminal so the CLI works after a Store install.

## Where the app writes

The install folder under `WindowsApps` is read-only. The app reads the bundled OCIO config and LUTs from `resources/ocio` in the package and does not copy them out. Settings (`HKCU\Software\VFXTools`), presets (`%APPDATA%\VFXTools\EXRConverter\presets`), and the TrustMark model cache (`%LOCALAPPDATA%\VFXTools\EXRConverter\Cache\trustmark`) stay in the user profile. The manifest excludes those locations from MSIX write virtualization so they match the setup `.exe`. Converted media is written only where you chose an output path. A Start-menu launch starts in the package folder; the app switches to your user profile before any relative write.
