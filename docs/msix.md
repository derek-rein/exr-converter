---
title: Microsoft Store
weight: 32
description: Unsigned MSIX for Partner Center submission
---

The Windows release build can pack an **unsigned** `.msix` from the same Nuitka app as the setup `.exe`. Partner Center (product type **MSIX or PWA app**) signs it. This repository does not have a code-signing certificate and must not sign the package.

The setup `.exe` is unchanged. Download that for a direct install. The `.msix` is only for Store submission.

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

- Actions artifact `exr_converter-windows-x86_64-msix`
- GitHub Release asset `exr_converter-windows-x86_64.msix`

A detached `.sigstore.json` may sit next to the asset. That is provenance for the file bytes. It is not an MSIX signature. Do not upload it to Partner Center.

## Submit in Partner Center

1. Create the app as **MSIX or PWA app** and reserve the name.
2. Copy the identity name, the `CN=...` publisher, and the publisher display name into the repository variables above.
3. Ship a release (merge to `main` so Auto-tag runs, or dispatch Release). Download `exr_converter-windows-x86_64.msix` from the GitHub Release.
4. Create a submission and upload that `.msix`. Do not sign it first with a local certificate, SignPath, or `signtool`. `Add-AppxPackage` refuses the unsigned file; that is expected.
5. Store listing images (screenshots, the 300×300 icon) are uploaded in Partner Center. They are separate from the tile logos inside the package.

The package is x64, full trust (`runFullTrust`), with entry point `exr_converter.exe` (`Windows.FullTrustApplication`) and device family `Windows.Desktop` (minimum 10.0.19041.0). It does not register file associations or protocol handlers. Open media from inside the app. An execution alias registers `exr_converter.exe` for a new terminal so the CLI works after a Store install.

## Where the app writes

The install folder under `WindowsApps` is read-only. The app reads the bundled OCIO config and LUTs from `resources/ocio` in the package and does not copy them out. Settings (`HKCU\Software\VFXTools`), presets (`%APPDATA%\VFXTools\EXRConverter\presets`), and the TrustMark model cache (`%LOCALAPPDATA%\VFXTools\EXRConverter\Cache\trustmark`) stay in the user profile. The manifest excludes those locations from MSIX write virtualization so they match the setup `.exe`. Converted media is written only where you chose an output path. A Start-menu launch starts in the package folder; the app switches to your user profile before any relative write.
