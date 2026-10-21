"""Linux studio tarball, RPM metadata, and the Rocky 8 release job."""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_glibc_floor import (  # noqa: E402
    exceeds,
    parse_version_needs,
    version_tuple,
)
from package_linux import (  # noqa: E402
    LAUNCHER_NAME,
    OPT_PREFIX,
    RPM_DEPENDS,
    VENDOR,
    desktop_text,
    make_tarball,
    package_distribution,
    project_version,
    rez_package_py,
    stage_tree,
)
from repair_linux_bundle_elf import (  # noqa: E402
    load_segments_aligned,
    restore_misaligned,
)

_READELF = """
Version needs section '.gnu.version_r' contains 2 entries:
  0x0010:   Name: GLIBC_2.28  Flags: none  Version: 8
  0x0020:   Name: GLIBC_2.17  Flags: none  Version: 7
  0x0030:   Name: GLIBCXX_3.4.21  Flags: none  Version: 4
  0x0040:   Name: CXXABI_1.3.11  Flags: none  Version: 5
Version definition section '.gnu.version_d' contains 1 entries:
  0x0050:   Name: GLIBC_2.34  Flags: none  Version: 9
"""


def test_version_needs_ignore_definitions() -> None:
    found = parse_version_needs(_READELF)
    assert "GLIBC_2.28" in found
    assert "GLIBC_2.34" not in found
    assert exceeds("GLIBC_2.34", (2, 28))
    assert not exceeds("GLIBC_2.28", (2, 28))
    assert version_tuple("CXXABI_1.3.11") == (1, 3, 11)
    assert exceeds("CXXABI_1.3.13", (1, 3, 11))


def _fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "main.dist"
    dist.mkdir()
    binary = dist / "exr_converter"
    binary.write_text(
        '#!/bin/sh\necho "cwd=$(pwd)"\necho "args=$*"\necho "bin=$0"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    (dist / "resources").mkdir()
    (dist / "resources" / "ocio").mkdir()
    (dist / "resources" / "ocio" / "marker.txt").write_text("ocio", encoding="utf-8")
    return dist


def test_staged_tree_launcher_and_rez(tmp_path: Path) -> None:
    folder = stage_tree(_fake_dist(tmp_path), tmp_path / "stage", "9.9.9")
    assert folder.name == "exr-converter-9.9.9"
    assert (folder / "exr-converter").stat().st_mode & stat.S_IXUSR
    assert "readlink -f" in (folder / "exr-converter").read_text(encoding="utf-8")
    assert "XDG_CONFIG_HOME" in (folder / "README").read_text(encoding="utf-8")
    assert 'version = "9.9.9"' in (folder / "package.py").read_text(encoding="utf-8")
    assert "{root}" in rez_package_py("9.9.9")
    assert (folder / "resources" / "ocio" / "marker.txt").read_text(encoding="utf-8") == "ocio"
    desktop = (folder / "exr-converter.desktop").read_text(encoding="utf-8")
    assert "Exec=exr-converter\n" in desktop
    rpm_desktop = desktop_text(exec_line=f"{OPT_PREFIX}/exr-converter")
    assert f"Exec={OPT_PREFIX}/exr-converter\n" in rpm_desktop
    assert VENDOR == "Medeu Global LLC"


def test_launcher_is_relocatable_and_read_only(tmp_path: Path) -> None:
    folder = stage_tree(_fake_dist(tmp_path), tmp_path / "stage", "9.9.9")
    for path in folder.rglob("*"):
        mode = path.stat().st_mode
        path.chmod(mode & ~0o222)
    link_dir = tmp_path / "bin dir"
    link_dir.mkdir()
    link = link_dir / "exr-converter"
    link.symlink_to(folder / "exr-converter")
    work = tmp_path / "elsewhere"
    work.mkdir()
    proc = subprocess.run(
        [str(link), "--help"],
        cwd=work,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"cwd={work}" in proc.stdout
    assert "args=--help" in proc.stdout
    assert not (folder / "sidefile").exists()


def test_tarball_round_trip(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path)
    stage = stage_tree(dist, tmp_path / "stage", project_version())
    archive = tmp_path / "exr_converter-linux-x86_64.tar.gz"
    make_tarball(stage, archive)
    out = tmp_path / "extracted"
    out.mkdir()
    with tarfile.open(archive, "r:gz") as handle:
        handle.extractall(out, filter="data")
    folder = out / f"exr-converter-{project_version()}"
    assert (folder / LAUNCHER_NAME).is_file()
    assert (folder / "LICENSE").is_file()
    assert (folder / "package.py").is_file()


@pytest.mark.skipif(
    shutil.which("nfpm") is None or shutil.which("rpm") is None,
    reason="nfpm and rpm",
)
def test_rpm_installs_opt_prefix(tmp_path: Path) -> None:
    nfpm = Path(shutil.which("nfpm") or "")
    tarball, rpm = package_distribution(
        _fake_dist(tmp_path),
        tmp_path / "out",
        version="9.9.9",
        nfpm=nfpm,
    )
    assert tarball.is_file()
    info = subprocess.check_output(
        [
            "rpm",
            "-qp",
            "--queryformat",
            "%{NAME}|%{VERSION}|%{ARCH}|%{VENDOR}|%{LICENSE}\n",
            str(rpm),
        ],
        text=True,
    )
    assert info.strip() == "exr-converter|9.9.9|x86_64|Medeu Global LLC|MIT"
    listing = subprocess.check_output(["rpm", "-qpl", str(rpm)], text=True)
    assert "/opt/exr-converter/exr-converter\n" in listing or listing.endswith(
        "/opt/exr-converter/exr-converter\n"
    )
    assert "/usr/bin/exr-converter\n" in listing
    assert "/usr/share/applications/exr-converter.desktop\n" in listing
    assert "/usr/share/icons/hicolor/256x256/apps/exr-converter.png\n" in listing
    requires = subprocess.check_output(["rpm", "-qpR", str(rpm)], text=True)
    for dep in RPM_DEPENDS:
        assert dep in requires


def test_release_workflow_linux_job_is_rocky_only() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    script = (ROOT / "packaging" / "linux" / "build-on-rocky.sh").read_text(encoding="utf-8")
    lowered = release.lower()
    assert "snap" not in lowered
    assert "flatpak" not in lowered
    assert "rockylinux:8" in release
    assert "linux_only" in release
    assert "exr_converter-linux-x86_64.tar.gz" in release
    assert "exr_converter-linux-x86_64.rpm" in release
    assert "artifacts/*.tar.gz" in release
    assert "artifacts/*.rpm" in release
    assert '{"os":"ubuntu-24.04"' not in release
    assert "PySide6-Essentials==6.9.2" in script
    assert "shiboken6==6.9.2" in script
    assert "--no-install-package pyside6-essentials" in script
    assert "UV_NO_SYNC=1" in script
    assert "gcc-toolset-12-libatomic-devel" in script
    assert "libatomic.a" in script
    assert "patchelf-0.19.2-x86_64.tar.gz" in script
    assert "scripts/repair_linux_bundle_elf.py" in script
    assert "scripts/fix_bundle_ocio.py --strict" in script
    assert "scripts/install_r3d_into_bundle.py" in script
    assert "scripts/check_glibc_floor.py" in script
    assert "--max-glibc 2.28" in script
    assert 'NFPM_VER="2.41.3"' in script
    assert "nfpm_${NFPM_VER}_Linux_x86_64.tar.gz" in script
    # Shared Nuitka strip flags stay aligned with the macOS/Windows step.
    for flag in (
        "--lto=yes",
        "--enable-plugin=pyside6",
        "--include-module=exr_prores",
        "--include-package=PyOpenColorIO",
        "--include-data-dir=resources/ocio=resources/ocio",
        "--noinclude-dlls='*Qt6WebEngine*'",
    ):
        assert flag in script
        assert flag in release


def test_load_segments_aligned_on_host_ls() -> None:
    if not Path("/bin/ls").is_file() or shutil.which("readelf") is None:
        pytest.skip("readelf or /bin/ls")
    assert load_segments_aligned(Path("/bin/ls"))


def test_restore_replaces_misaligned_openblas(tmp_path: Path) -> None:
    broken = Path("/tmp/exr-src/dist/main.dist/libscipy_openblas64_-f48b354e.so")
    healthy = Path(
        "/tmp/exr-src/.venv/lib/python3.13/site-packages/numpy.libs/"
        "libscipy_openblas64_-f48b354e.so"
    )
    if not broken.is_file() or not healthy.is_file():
        pytest.skip("Rocky dist not built in this environment")
    if load_segments_aligned(broken):
        pytest.skip("OpenBLAS in the dist is already aligned")
    copied = tmp_path / broken.name
    shutil.copy2(broken, copied)
    restored = restore_misaligned(tmp_path, [healthy.parent])
    assert restored == [copied]
    assert load_segments_aligned(copied)


def test_patchelf_strict_flag_is_wired() -> None:
    source = (ROOT / "scripts" / "fix_bundle_ocio.py").read_text(encoding="utf-8")
    assert '"--strict"' in source
    assert "patchelf" in source
    assert "$ORIGIN" in source


@pytest.mark.skipif(sys.platform == "win32", reason="ELF readelf")
@pytest.mark.skipif(shutil.which("readelf") is None, reason="readelf")
def test_host_glibc_scan_smoke(tmp_path: Path) -> None:
    """The scanner reports a floor for a real ELF (this host's /bin/ls or python)."""
    candidate = Path("/bin/ls")
    if not candidate.is_file():
        pytest.skip("no /bin/ls")
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "check_glibc_floor.py"),
            str(candidate),
            "--max-glibc",
            "2.99",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "GLIBC:" in proc.stdout
    assert "OK: glibc floor" in proc.stdout
