#!/usr/bin/env python3
"""Ad-hoc re-sign a macOS .app without ``codesign --deep`` on nested frameworks.

Blackmagic RAW SDK 6.0 ships ``BlackmagicRawAPI.framework`` with an internal
layout that is not a clean Apple versioned framework. After a normal
``copytree`` (which dereferences ``Versions/Current`` and top-level
``Resources``), ``codesign --deep`` reports::

    bundle format is ambiguous (could be app or framework)

even when the framework lives under ``Contents/Frameworks/`` (PR #54).
Typical extra top-level directories: real ``Resources/``, real
``Versions/Current/``, ``Contents/``, ``Libraries/``, ``PlugIns/``, or a
nested ``.bundle`` / helper ``.app``.

This helper:

1. Restores versioned-framework symlinks (never deletes GPU decoder libs).
2. Signs Mach-O files deepest-first, then ``.framework/Versions/<ver>``, then
   the ``.framework`` bundle, then the outer ``.app`` — **never** ``--deep``.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from packaging_util import safe_print

# Names we never drop when relocating / normalizing a framework tree.
_RUNTIME_KEEP_PARTS = (
    "blackmagicraw",
    "decoder",
    "metal",
    "cuda",
    "opencl",
    "instructionset",
)
_SKIP_SIGN_SUFFIXES = {
    ".plist",
    ".pri",
    ".prl",
    ".qmltypes",
    ".qml",
    ".js",
    ".png",
    ".ico",
    ".icns",
    ".txt",
    ".md",
    ".h",
    ".hpp",
    ".xml",
    ".strings",
    ".nib",
    ".xib",
}
_SKIP_SIGN_NAMES = {"info.plist", "pkginfo", "version.plist", "codeResources"}
_SIGN = ("codesign", "--force", "--sign", "-", "--timestamp=none")
_VERIFY = ("codesign", "--verify", "--strict", "--verbose=2")


def _try_symlink(link: Path, target: str | Path) -> bool:
    """Create *link* → *target*. Returns False when the OS refuses (Windows)."""
    try:
        link.symlink_to(target)
        return True
    except OSError:
        return False


def _is_runtime_keep(name: str) -> bool:
    lower = name.lower()
    if any(part in lower for part in _RUNTIME_KEEP_PARTS):
        return True
    return lower.endswith((".dylib", ".so", ".bundle"))


def _relative_file_names(root: Path) -> set[str]:
    names: set[str] = set()
    if not root.is_dir():
        return names
    for p in root.rglob("*"):
        if p.is_file() or p.is_symlink():
            names.add(str(p.relative_to(root)))
    return names


def _merge_tree(src: Path, dest: Path) -> None:
    """Copy files from *src* into *dest*, keeping existing dest files."""
    if not src.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_symlink() or item.is_file():
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target, follow_symlinks=False)
        elif item.is_dir():
            _merge_tree(item, target)


def framework_ambiguity_reasons(fw: Path) -> list[str]:
    """Why ``codesign --deep`` would treat *fw* as both an app and a framework."""
    reasons: list[str] = []
    if not fw.is_dir():
        return reasons
    contents = fw / "Contents"
    versions = fw / "Versions"
    has_contents = contents.is_dir() and not contents.is_symlink()
    has_versions = versions.is_dir()
    if has_contents and has_versions:
        reasons.append("both Contents/ and Versions/ at framework root")
    if has_contents and (contents / "Info.plist").is_file() and has_versions:
        reasons.append("Contents/Info.plist plus Versions/ (app-like + framework-like)")
    current = versions / "Current"
    if current.is_dir() and not current.is_symlink() and has_versions:
        reasons.append("Versions/Current is a real directory (flattened symlink)")
    resources = fw / "Resources"
    if resources.is_dir() and not resources.is_symlink() and has_versions:
        reasons.append(
            "top-level Resources/ is a real directory (should be symlink to "
            "Versions/Current/Resources)"
        )
    if (fw / "Info.plist").is_file() and has_versions:
        reasons.append("Info.plist at framework root")
    if (fw / "MacOS").is_dir() and has_versions:
        reasons.append("top-level MacOS/ plus Versions/")
    for app in fw.rglob("*.app"):
        if app.is_dir():
            reasons.append(f"nested app bundle {app.relative_to(fw)}")
    return reasons


def describe_framework_tree(fw: Path, *, limit: int = 80) -> list[str]:
    """Human-readable layout lines (symlink targets included)."""
    lines: list[str] = []
    if not fw.exists():
        return [f"missing {fw}"]
    for p in sorted(fw.rglob("*")):
        rel = p.relative_to(fw)
        if p.is_symlink():
            lines.append(f"link {rel} -> {p.readlink()}")
        elif p.is_dir():
            lines.append(f"dir  {rel}/")
        else:
            lines.append(f"file {rel}")
        if len(lines) >= limit:
            lines.append("…")
            break
    return lines


def _version_dir(fw: Path) -> Path | None:
    versions = fw / "Versions"
    if not versions.is_dir():
        return None
    named = [
        p for p in versions.iterdir() if p.is_dir() and not p.is_symlink() and p.name != "Current"
    ]
    for cand in named:
        if cand.name == "A":
            return cand
    if named:
        return named[0]
    current = versions / "Current"
    if current.is_dir() and not current.is_symlink():
        return current
    return None


def normalize_versioned_framework(fw: Path) -> list[str]:
    """Restore Apple versioned-framework symlinks. Keep GPU decoder binaries.

    Returns a list of actions taken (empty if the tree was already clean or
    not a versioned framework).
    """
    actions: list[str] = []
    if not fw.is_dir() or not fw.name.endswith(".framework"):
        return actions
    versions = fw / "Versions"
    if not versions.is_dir():
        return actions

    current = versions / "Current"
    version = _version_dir(fw)
    if version is None:
        return actions

    if version.name == "Current":
        dest = versions / "A"
        if dest.exists():
            _merge_tree(version, dest)
            shutil.rmtree(version)
        else:
            version.rename(dest)
        version = dest
        actions.append("renamed Versions/Current -> Versions/A")

    if current.exists() and current.resolve() != version.resolve():
        if current.is_symlink():
            current.unlink()
        elif current.is_dir():
            _merge_tree(current, version)
            shutil.rmtree(current)
            actions.append(f"merged flattened Versions/Current into {version.name}")
        else:
            current.unlink()
    if current.exists() and current.is_symlink():
        if current.readlink() != Path(version.name):
            current.unlink()
    if not current.exists() and _try_symlink(current, version.name):
        actions.append(f"linked Versions/Current -> {version.name}")

    stem = fw.name.removesuffix(".framework")
    for name in (stem, "Resources", "Headers"):
        target = version / name
        top = fw / name
        if not target.exists():
            if top.is_symlink():
                top.unlink()
            continue
        rel = Path("Versions") / "Current" / name
        if top.is_symlink():
            if top.readlink() != rel:
                top.unlink()
                if _try_symlink(top, rel):
                    actions.append(f"relinked {name} -> {rel}")
            continue
        if top.exists():
            if top.is_dir():
                if _relative_file_names(top) != _relative_file_names(target):
                    _merge_tree(top, target)
                    actions.append(f"merged top-level {name}/ into Versions/{version.name}/{name}")
                shutil.rmtree(top)
            else:
                if not target.exists():
                    shutil.copy2(top, target)
                top.unlink()
        if _try_symlink(top, rel):
            actions.append(f"linked {name} -> {rel}")

    contents = fw / "Contents"
    if contents.is_dir() and not contents.is_symlink():
        macos_dir = contents / "MacOS"
        if macos_dir.is_dir():
            _merge_tree(macos_dir, version)
        res = contents / "Resources"
        if res.is_dir():
            _merge_tree(res, version / "Resources")
        for extra in ("Libraries", "Frameworks", "PlugIns", "Plugins", "Helpers"):
            src = contents / extra
            if src.is_dir():
                _merge_tree(src, version / extra)
        # Last-resort: keep leftover runtime files instead of deleting them.
        leftovers = [p for p in contents.rglob("*") if p.is_file() and _is_runtime_keep(p.name)]
        if leftovers:
            keep = version / "Libraries"
            keep.mkdir(parents=True, exist_ok=True)
            for p in leftovers:
                dest = keep / p.name
                if not dest.exists():
                    shutil.copy2(p, dest)
                    actions.append(f"kept runtime {p.name} under Versions/{version.name}/Libraries")
        shutil.rmtree(contents)
        actions.append("removed app-like Contents/ after merging unique files")

    return actions


def normalize_frameworks_under(root: Path) -> list[str]:
    actions: list[str] = []
    if not root.is_dir():
        return actions
    frameworks = [p for p in root.rglob("*.framework") if p.is_dir()]
    frameworks.sort(key=lambda p: len(str(p)), reverse=True)
    for fw in frameworks:
        for act in normalize_versioned_framework(fw):
            actions.append(f"{fw.name}: {act}")
    return actions


def frameworks_under_macos(app: Path) -> list[Path]:
    macos = app / "Contents" / "MacOS"
    if not macos.is_dir():
        return []
    return [p for p in macos.rglob("*.framework") if p.is_dir() or p.is_symlink()]


def iter_nested_frameworks(app: Path) -> list[Path]:
    found = [p for p in app.rglob("*.framework") if p.is_dir()]
    found.sort(key=lambda p: len(p.parts), reverse=True)
    return found


def _should_sign_file(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    name = path.name
    if name.startswith("._") or name.startswith("."):
        return False
    if name.lower() in _SKIP_SIGN_NAMES:
        return False
    suffix = path.suffix.lower()
    if suffix in _SKIP_SIGN_SUFFIXES:
        return False
    if suffix in {".dylib", ".so", ".bundle"}:
        return True
    parent = path.parent.name
    if parent in {"MacOS", "Helpers"} or parent.endswith(".framework"):
        return True
    if path.parent.parent.name == "Versions":
        return True
    # Qt framework binaries under PySide6/Qt/lib have no suffix.
    if suffix == "" and "Qt" in path.parts:
        return True
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def iter_macho_files(root: Path) -> list[Path]:
    """Candidate Mach-O / dylib files, deepest first (symlinks skipped)."""
    files = [p for p in root.rglob("*") if _should_sign_file(p)]
    files.sort(key=lambda p: (len(p.parts), str(p)), reverse=True)
    return files


def iter_nested_bundles(root: Path) -> list[Path]:
    """``.bundle`` and helper ``.app`` dirs inside *root*, deepest first."""
    found: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_dir() or p.is_symlink():
            continue
        if p.name.endswith(".bundle") or (p.name.endswith(".app") and ".framework" in p.parts):
            found.append(p)
    found.sort(key=lambda p: len(p.parts), reverse=True)
    return found


def plan_adhoc_resign(app: Path) -> list[list[str]]:
    """Return codesign argv lists. Never includes ``--deep``."""
    leaked = frameworks_under_macos(app)
    if leaked:
        preview = "\n".join(str(p) for p in leaked[:20])
        raise SystemExit(
            f"ERROR: .framework under Contents/MacOS (codesign --deep will fail):\n{preview}"
        )

    cmds: list[list[str]] = []
    seen: set[str] = set()

    def _add(target: Path) -> None:
        key = str(target)
        if key in seen:
            return
        seen.add(key)
        cmds.append([*_SIGN, key])

    for macho in iter_macho_files(app):
        _add(macho)
    for bundle in iter_nested_bundles(app):
        _add(bundle)
    for fw in iter_nested_frameworks(app):
        versions = fw / "Versions"
        if versions.is_dir():
            for ver in sorted(versions.iterdir(), key=lambda p: p.name):
                if ver.name == "Current" and ver.is_symlink():
                    continue
                if ver.is_dir():
                    _add(ver)
        _add(fw)
    exe = app / "Contents" / "MacOS" / "exr_converter"
    if exe.is_file():
        _add(exe)
    _add(app)
    return cmds


def plan_adhoc_verify(app: Path) -> list[list[str]]:
    """Shallow verify (no ``--deep``) of nested frameworks, then the app."""
    cmds: list[list[str]] = []
    for fw in reversed(iter_nested_frameworks(app)):
        versions = fw / "Versions"
        current = versions / "Current"
        if current.exists():
            cmds.append([*_VERIFY, str(current)])
        else:
            cmds.append([*_VERIFY, str(fw)])
    cmds.append([*_VERIFY, str(app)])
    return cmds


def _run_cmds(cmds: list[list[str]], *, dry_run: bool) -> None:
    for cmd in cmds:
        safe_print(" ".join(cmd))
        if dry_run:
            continue
        subprocess.check_call(cmd)


def resign_app(app: Path, *, dry_run: bool = False) -> list[list[str]]:
    """Normalize nested frameworks, then ad-hoc sign inside-out without --deep."""
    app = app.resolve()
    if not app.exists():
        raise SystemExit(f"app bundle not found: {app}")
    leaked = frameworks_under_macos(app)
    if leaked:
        preview = "\n".join(str(p) for p in leaked[:20])
        raise SystemExit(
            f"ERROR: .framework under Contents/MacOS (codesign --deep will fail):\n{preview}"
        )

    for fw in iter_nested_frameworks(app):
        reasons = framework_ambiguity_reasons(fw)
        if reasons:
            safe_print(f"Ambiguous framework before normalize: {fw}", file=sys.stderr)
            for reason in reasons:
                safe_print(f"  - {reason}", file=sys.stderr)
        for act in normalize_versioned_framework(fw):
            safe_print(f"normalize {fw.name}: {act}", file=sys.stderr)
        after = framework_ambiguity_reasons(fw)
        if after:
            safe_print(
                f"Still ambiguous after normalize (signing without --deep): {fw}",
                file=sys.stderr,
            )
            for reason in after:
                safe_print(f"  - {reason}", file=sys.stderr)
        for line in describe_framework_tree(fw):
            safe_print(f"  {line}", file=sys.stderr)

    cmds = plan_adhoc_resign(app)
    darwin = sys.platform == "darwin"
    if not dry_run and not darwin:
        raise SystemExit("codesign is only available on macOS (pass --dry-run)")
    _run_cmds(cmds, dry_run=dry_run or not darwin)
    if not dry_run and darwin:
        _run_cmds(plan_adhoc_verify(app), dry_run=False)
    return cmds


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app", type=Path, help="Path to the .app bundle")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print codesign argv only (used by unit tests / Linux CI)",
    )
    args = ap.parse_args()
    resign_app(args.app, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
