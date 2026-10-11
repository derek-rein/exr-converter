---
title: Releasing
weight: 30
description: Tagging, Auto-tag, and the Release workflow
---

The full release and deployment process (protected `main`, Makefile, GitHub
Actions, `gh` CLI, checklist, troubleshooting) lives in:

**[AGENTS.md — Releasing and deployment](../AGENTS.md#releasing-and-deployment)**

User-facing history (required on every release):

**[CHANGELOG.md](../CHANGELOG.md)**

**Short path:** merge a release PR that bumps `pyproject.toml` **and** rolls
`CHANGELOG.md` (`## [X.Y.Z]` + compare link) → **Auto-tag release** pushes
`vX.Y.Z` if missing → dispatches **Release** with
`gh workflow run Release --ref main -f tag=vX.Y.Z` → lint/tests/gate → Nuitka
→ Cosign → GitHub Release.

You do **not** need a manual tag push after merge (that was what skipped 0.5.0
until fixed). After merge, do **not** also push a local `vX.Y.Z` unless Auto-tag
failed — dual triggers race and waste runner minutes.

**Hard gates (automated):** Auto-tag and Release refuse to ship if
`CHANGELOG.md` lacks `## [X.Y.Z]` / the bottom compare link, or if the tag does
not match `pyproject.toml` version (except explicit emergency
`source_ref=` rebuilds).

**Emergency rebuild of an existing tag** (packaging / native-bridge hotfix on
`main`, same version, do not move the published tag):

```bash
gh workflow run Release --ref main -f tag=v0.10.0 -f source_ref=main
```

That checks out `main` for the build while still publishing GitHub Release
`v0.10.0`. A plain re-run of the failed tag job will **not** pick up commits
that landed after the tag.

**Provenance:** each asset gets a Cosign `.sigstore.json` bundle **and** a
GitHub Attestations record (`gh attestation verify`). Release notes include the
CHANGELOG section for that version plus verify commands. Optional SignPath
Authenticode for Windows is off until repository vars/secrets are set (see
AGENTS.md).

**macOS notarization:** when the Developer ID secrets are set, the build job
signs each DMG with **Developer ID Application: Medeu Global LLC (83546T4BT8)**
(team `83546T4BT8`, G2 cert, expires 2031-09-17), notarizes it, and staples it
before Cosign. The p12 secret is an OpenSSL 3 `pkcs12 -export -legacy` export.
Without those secrets the DMG stays ad-hoc and notarization is skipped.
The notary upload returns a submission id immediately. The job then checks
that same Apple submission for up to five hours instead of uploading the DMG
again.
Non-code files Nuitka drops under `Contents/MacOS` are moved to
`Contents/Resources` (with symlinks left behind) before that signature, because
a Developer ID seal rejects them as unsigned nested code. Secret names and the
entitlements are in
[AGENTS.md](../AGENTS.md#apple-developer-id-macos-notarization).

To test signing without publishing a GitHub Release:

```bash
gh workflow run Release --ref <branch> -f macos_only=true -f publish=false
```

The run uploads `exr_converter-macos-arm64-signed` and
`exr_converter-macos-x86_64-signed`. On a downloaded DMG:

```bash
spctl -a -vvv -t install exr_converter-macos-arm64.dmg
codesign --verify --deep --strict --verbose=2 exr_converter-macos-arm64.dmg
```

**Docs site:** Markdown under `docs/` is built with Hugo (`site/`) and published
by the **Docs** workflow on push to `main` (path filters under `docs/`, `site/`).
That is independent of the versioned app **Release** workflow. Preview locally
with `make docs-serve`. Public URL:
[derek-rein.github.io/exr-converter](https://derek-rein.github.io/exr-converter/).

User-facing ProRes / VideoToolbox guide: [prores.md](./prores.md).
Related design notes (not release machinery):
[plan-12bit-prores-oxideav.md](./plan-12bit-prores-oxideav.md).
