---
title: Microsoft Store
weight: 32
description: Partner Center .msixupload and a self-distribution .msix
---

The Windows release build can pack two extra files from the same Nuitka app as the setup `.exe`:

- `exr_converter-windows-x86_64.msixupload`: upload this to Partner Center (product type **MSIX or PWA app**). The package inside it is unsigned. The Store signs it.
- `exr_converter-windows-x86_64.msix`: self-distribution. Windows installs an MSIX only when a certificate the PC trusts has signed it. Without the signing secrets below, this file is unsigned and will not install.

The setup `.exe` is unchanged. Download that for a direct install that does not need an MSIX signature.

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

On the same Release run as the other installers (no extra workflow), both of these are GitHub Release assets:

- `exr_converter-windows-x86_64.msixupload`: Partner Center
- `exr_converter-windows-x86_64.msix`: self-distribution

The same files are also Actions artifacts (`exr_converter-windows-x86_64-msixupload` and `exr_converter-windows-x86_64-msix`) before they are copied into the Release.

A detached `.sigstore.json` may sit next to each Release asset. That is provenance for the file bytes. It is not an MSIX signature. Do not upload it to Partner Center, and do not treat it as the signature that lets Windows install the package.

## What is inside the `.msixupload`

The file is a zip. Its members sit at the **root** of the archive (the same layout as selecting the files and using Send to → Compressed folder, not zipping a parent directory):

- `exr_converter_<version>_x64.msix`: the unsigned package (`0.12.3` is `exr_converter_0.12.3.0_x64.msix`)
- `exr_converter_<version>_x64.appxsym`: only when the Nuitka folder contains `.pdb` files. This is a zip of those symbols, with paths relative to the app directory, for Partner Center crash analytics

Nuitka release builds usually have no PDB files. An upload that contains only the `.msix` is valid. The symbol member uses `.appxsym`. Partner Center rejects a `.msixsym` member. PDB files are not installed inside the `.msix`. The copy inside the upload stays unsigned even when the loose `.msix` is signed later.

## Signing the self-distribution `.msix`

`signtool` signs `exr_converter-windows-x86_64.msix` only when every Azure Artifact Signing secret below is set. The step runs after the `.msixupload` has been written, and the upload path is not passed to `signtool`. SignPath is still the setup `.exe` only. Do not send either MSIX file through SignPath.

Set these under **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Value |
|--------|--------|
| `AZURE_TENANT_ID` | Directory (tenant) id of the app registration |
| `AZURE_CLIENT_ID` | Application (client) id |
| `AZURE_CLIENT_SECRET` | Client secret for that app registration |
| `AZURE_ARTIFACT_SIGNING_ENDPOINT` | Regional endpoint, such as `https://eus.codesigning.azure.net` |
| `AZURE_ARTIFACT_SIGNING_ACCOUNT` | Artifact Signing account name |
| `AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE` | Certificate profile name |

The app registration needs the Artifact Signing Certificate Profile Signer role. The endpoint must be the region where the account and profile were created. If none of these secrets are set, the loose `.msix` ships unsigned and the job continues. If only some of them are set, the Windows job fails instead of publishing an unsigned package by mistake.

An unsigned MSIX does not install. `Add-AppxPackage` refuses it. A self-hosted install needs a signature from a certificate the target PC trusts. Until those secrets exist, use the setup `.exe` for direct installs, or install from the Store after Partner Center signs the upload.

## Submit in Partner Center

1. Create the app as **MSIX or PWA app** and reserve the name.
2. Copy the identity name, the `CN=...` publisher, and the publisher display name into the repository variables above.
3. Ship a release (merge to `main` so Auto-tag runs, or dispatch Release). Download `exr_converter-windows-x86_64.msixupload` from the GitHub Release.
4. Create the **first** submission yourself and upload that `.msixupload`. Do not sign it first. Do not upload the loose `.msix` or the `.sigstore.json`. Listings, screenshots, age ratings, and pricing are part of this first submission. The Store CLI cannot create that first submission.
5. Store listing images (screenshots, the 300×300 icon) are uploaded in Partner Center. They are separate from the tile logos inside the package.

## Automatic updates after the first submission

On a real release (a `v*` tag push, or a Release dispatch that publishes), the Windows job can submit a later package with the [Microsoft Store Developer CLI](https://learn.microsoft.com/en-us/windows/apps/publish/msstore-dev-cli/overview). It runs only after `exr_converter-windows-x86_64.msixupload` is built and the GitHub Release artifacts have been uploaded. The setup `.exe` and the loose `.msix` are not sent to the Store.

The CLI is `msstore` from `microsoft/microsoft-store-apppublisher` (the setup-msstore-cli action), pinned to CLI **v0.4.3**. The step runs:

```bash
msstore reconfigure --tenantId … --clientId … --clientSecret … --sellerId …
msstore publish exr_converter-windows-x86_64.msixupload --appId …
```

Set these before relying on it. Secrets are under **Settings → Secrets and variables → Actions → Secrets**. `MSSTORE_APP_ID` is a variable, not a secret.

| Name | Kind | Value |
|------|------|--------|
| `MSSTORE_TENANT_ID` | secret | Directory (tenant) id of the Partner Center app registration |
| `MSSTORE_CLIENT_ID` | secret | Application (client) id |
| `MSSTORE_CLIENT_SECRET` | secret | Client secret |
| `MSSTORE_SELLER_ID` | secret | Partner Center seller id |
| `MSSTORE_APP_ID` | variable | Store product id of the app that already has a published submission |

The app registration must be allowed to manage submissions for that seller account. `MSSTORE_APP_ID` is the product id from Partner Center, not `MSIX_IDENTITY_NAME`.

If any of those values is missing, the job prints a notice and continues. If `msstore` fails, times out, or the CLI cannot be installed, the job prints a warning and continues. The GitHub Release, the setup `.exe`, and both MSIX files still ship. Finish that submission in Partner Center, or upload the `.msixupload` from the Release by hand.

`msstore publish` commits the submission. It updates an app that already has a published submission. It does not replace the first manual submission, and a draft you created in the Partner Center UI can make the command fail until that draft is deleted there.

The package is x64, full trust (`runFullTrust`), with entry point `exr_converter.exe` (`Windows.FullTrustApplication`) and device family `Windows.Desktop` (minimum 10.0.19041.0). It does not register file associations or protocol handlers. Open media from inside the app. An execution alias registers `exr_converter.exe` for a new terminal so the CLI works after a Store install.

## Where the app writes

The install folder under `WindowsApps` is read-only. The app reads the bundled OCIO config and LUTs from `resources/ocio` in the package and does not copy them out. Settings (`HKCU\Software\VFXTools`), presets (`%APPDATA%\VFXTools\EXRConverter\presets`), and the TrustMark model cache (`%LOCALAPPDATA%\VFXTools\EXRConverter\Cache\trustmark`) stay in the user profile. The manifest excludes those locations from MSIX write virtualization so they match the setup `.exe`. Converted media is written only where you chose an output path. A Start-menu launch starts in the package folder; the app switches to your user profile before any relative write.
