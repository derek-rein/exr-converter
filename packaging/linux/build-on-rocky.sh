#!/bin/bash
# Build EXR Converter Linux artifacts inside Rocky Linux 8 (glibc 2.28).
#
# Run as root in a rockylinux:8 container or chroot, with this repo as the
# working tree (the Release workflow bind-mounts it). Outputs, flattened
# for the release artifact:
#   linux-dist/exr_converter-linux-x86_64.AppImage
#   linux-dist/exr_converter-linux-x86_64.tar.gz
#   linux-dist/exr_converter-linux-x86_64.rpm
#
# PySide6 comes from uv.lock like every other platform. pyproject.toml pins
# 6.9.2 everywhere because 6.10+ Linux wheels are manylinux_2_34 (glibc 2.34,
# RHEL 9+) and 6.9.2 is manylinux_2_28, so the app runs on RHEL / Rocky 8.
# Nuitka flags match the macOS/Windows "Build with Nuitka" step in
# .github/workflows/release.yml.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

fix_owner() {
  if [ -n "${HOST_UID:-}" ]; then
    chown -R "${HOST_UID}:${HOST_GID:-$HOST_UID}" "$ROOT" || true
    # Cache mounts are host directories. The runner must be able to read them.
    for cache in /root/.cache/uv /root/.cache/Nuitka; do
      if [ -d "$cache" ]; then
        chown -R "${HOST_UID}:${HOST_GID:-$HOST_UID}" "$cache" || true
      fi
    done
  fi
}
trap fix_owner EXIT

echo "---- Rocky Linux base ----"
cat /etc/redhat-release
# ldd writes several lines. Piping to head exits early and, with pipefail,
# that SIGPIPE aborts the whole build.
ldd_ver=$(ldd --version 2>&1 || true)
printf '%s\n' "${ldd_ver%%$'\n'*}"

dnf -y install dnf-plugins-core
dnf config-manager --set-enabled powertools
# Rocky 8's patchelf 0.12 (EPEL) rewrites LOAD segments so OpenBLAS will not
# map. Nuitka and fix_bundle_ocio use whatever `patchelf` is first on PATH.
# Nuitka rejects patchelf 0.18.0 as a known-bad release. 0.19.2 is static.
PATCHELF_VER="0.19.2"
PATCHELF_SHA256="2abdc34fbe949c995a1baa4ec310896a085588a45a99cd0d726851bdfac2a4bb"
curl -fSL -o /tmp/patchelf-0.19.2-x86_64.tar.gz \
  "https://github.com/NixOS/patchelf/releases/download/${PATCHELF_VER}/patchelf-${PATCHELF_VER}-x86_64.tar.gz"
echo "${PATCHELF_SHA256}  /tmp/patchelf-0.19.2-x86_64.tar.gz" | sha256sum -c -
rm -rf /tmp/patchelf-extract
mkdir -p /tmp/patchelf-extract
tar -C /tmp/patchelf-extract -xzf /tmp/patchelf-0.19.2-x86_64.tar.gz
install -m 755 /tmp/patchelf-extract/bin/patchelf /usr/local/bin/patchelf
export PATH="/usr/local/bin:${PATH}"
patchelf --version

dnf -y install \
  gcc gcc-c++ make git curl ca-certificates \
  mesa-libEGL mesa-libGL libglvnd-opengl \
  libxkbcommon libxkbcommon-x11 \
  libX11 libXext libXrender libXi fontconfig freetype dbus-libs \
  glib2 libSM libICE libxcb libuuid-devel binutils tar gzip findutils \
  which xz pkgconf rpm zstd fuse-libs file \
  gcc-toolset-12-libatomic-devel
# GCC 8 on RHEL 8 ships libatomic.so and only a 32-bit libatomic.a.
# The uv CPython build sets MODLIBS to -l:libatomic.a, which Nuitka
# passes to the linker. The toolset archive has no versioned glibc symbols.
ATOMIC_A=/opt/rh/gcc-toolset-12/root/usr/lib/gcc/x86_64-redhat-linux/12/libatomic.a
GCC_LIBDIR="$(dirname "$(gcc -print-file-name=libatomic.so)")"
ln -sfn "$ATOMIC_A" "${GCC_LIBDIR}/libatomic.a"
FOUND_ATOMIC="$(gcc -print-file-name=libatomic.a)"
case "$FOUND_ATOMIC" in
  /*) echo "libatomic.a -> ${FOUND_ATOMIC}" ;;
  *) echo "ERROR: linker did not find libatomic.a (${FOUND_ATOMIC})" >&2; exit 1 ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/0.12.3/install.sh | sh
fi
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"

if ! command -v rustc >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain stable
fi
# shellcheck disable=SC1091
. "${HOME}/.cargo/env"

uv python install 3.13
# The cache mount and the workspace are different filesystems in CI.
export UV_LINK_MODE=copy
uv sync --frozen --group bundle --group dev

uv run python - <<'PY'
import PySide6
from PySide6.QtCore import qVersion
import PyOpenColorIO as ocio
import onnxruntime

print(f"PySide6 {PySide6.__version__} Qt {qVersion()}")
print(f"OpenColorIO {ocio.GetVersion()}")
print(f"onnxruntime {onnxruntime.__version__}")
# A newer lock would pull a manylinux_2_34 wheel that uv refuses here,
# but fail loudly if Qt drifts from the RHEL 8 pin anyway.
if tuple(int(x) for x in PySide6.__version__.split(".")[:3]) != (6, 9, 2):
    raise SystemExit(f"expected PySide6 6.9.2 from uv.lock, got {PySide6.__version__}")
nums = tuple(int(x) for x in ocio.GetVersion().split(".")[:3])
if nums < (2, 5, 0):
    raise SystemExit(f"OpenColorIO {ocio.GetVersion()} is older than 2.5")
PY

uv run python scripts/ensure_ocio.py
uv run python scripts/build_oxideav_prores.py

if [ -n "${R3D_SDK_READ_TOKEN:-}" ]; then
  SDK_ROOT="$(uv run python scripts/fetch_r3d_sdk.py --out "${RUNNER_TEMP:-/tmp}/r3d-sdk")"
  export R3D_SDK_ROOT="$SDK_ROOT"
  export R3D_BRIDGE_REF="${R3D_BRIDGE_REF:-bridge-v1}"
  uv run python scripts/build_r3d_bridge.py -v
else
  echo "R3D_SDK_READ_TOKEN not set — skipping R3D bridge"
fi

if [ -n "${BRAW_SDK_READ_TOKEN:-}" ]; then
  SDK_ROOT="$(uv run python scripts/fetch_braw_sdk.py --out "${RUNNER_TEMP:-/tmp}/braw-sdk")"
  export BRAW_SDK_ROOT="$SDK_ROOT"
  export BRAW_BRIDGE_REF="${BRAW_BRIDGE_REF:-bridge-v1}"
  uv run python scripts/build_braw_bridge.py -v
else
  echo "BRAW_SDK_READ_TOKEN not set — skipping BRAW bridge"
fi

if [ -n "${CAMERA_RAW_READ_TOKEN:-}" ]; then
  dnf -y install cmake ocl-icd
  if ! command -v git-lfs >/dev/null 2>&1; then
    curl -fsSL -o /tmp/git-lfs.tar.gz \
      https://github.com/git-lfs/git-lfs/releases/download/v3.6.1/git-lfs-linux-amd64-v3.6.1.tar.gz
    rm -rf /tmp/git-lfs-extract
    mkdir -p /tmp/git-lfs-extract
    tar -C /tmp/git-lfs-extract -xzf /tmp/git-lfs.tar.gz
    install -m 755 /tmp/git-lfs-extract/*/git-lfs /usr/local/bin/git-lfs
  fi
  uv run python scripts/camera_raw_plugin.py prepare
else
  echo "camera RAW plugin token not set; skipping"
fi

uv run python scripts/build_cinemadng.py

uv run pyside6-rcc resources.qrc -o src/rc_resources.py
uv run python scripts/prepare_macos_onnxruntime.py

RAW_FLAGS=()
while IFS= read -r flag || [ -n "$flag" ]; do
  flag="${flag%$'\r'}"
  [ -n "$flag" ] || continue
  RAW_FLAGS+=("$flag")
done < <(uv run python scripts/camera_raw_plugin.py nuitka-flags)

# Flags kept in sync with `make bundle` and the Release workflow Nuitka step.
# NUITKA_JOBS caps C compile/LTO parallelism on small machines (default: all cores).
uv run python -m nuitka \
  --standalone \
  ${NUITKA_JOBS:+--jobs="${NUITKA_JOBS}"} \
  --output-dir=dist \
  --output-filename=exr_converter \
  --assume-yes-for-downloads \
  --python-flag=-OO \
  --lto=yes \
  --enable-plugin=pyside6 \
  --nofollow-import-to=tkinter \
  --nofollow-import-to=unittest \
  --nofollow-import-to=pydoc \
  --nofollow-import-to=PIL \
  --nofollow-import-to='PySide6.QtWebEngine*' \
  --noinclude-qt-translations \
  --noinclude-qt-plugins=printsupport,mediaservice,iconengines \
  --noinclude-dlls='*Qt6WebEngine*' \
  --noinclude-dlls='*Qt6Svg*' \
  --noinclude-dlls='*Qt6Pdf*' \
  --noinclude-dlls='*Qt6Positioning*' \
  --noinclude-dlls='*Qt6PrintSupport*' \
  --include-package-data=av \
  --include-package=OpenImageIO \
  --include-package-data=OpenImageIO \
  --include-package=PyOpenColorIO \
  --include-package-data=PyOpenColorIO \
  --include-package=fileseq \
  --include-package=OpenGL \
  --include-package=onnxruntime \
  --include-package-data=onnxruntime \
  --include-data-dir=resources/ocio=resources/ocio \
  --include-data-files=pyproject.toml=pyproject.toml \
  --include-module=ssl \
  --include-module=exr_prores \
  ${RAW_FLAGS[@]+"${RAW_FLAGS[@]}"} \
  --linux-icon=resources/icons/icon.png \
  --quiet \
  main.py

uv run python scripts/fix_bundle_ocio.py --strict dist/main.dist
uv run python scripts/install_cinemadng_into_bundle.py dist/main.dist
uv run python scripts/install_r3d_into_bundle.py dist/main.dist
uv run python scripts/install_braw_into_bundle.py dist/main.dist
uv run python scripts/camera_raw_plugin.py install dist/main.dist

STRIP_EX=()
if [ -f build/camera-raw-strip-paths.txt ]; then
  while IFS= read -r pat || [ -n "$pat" ]; do
    [ -n "$pat" ] || continue
    STRIP_EX+=(! -path "$pat")
  done < build/camera-raw-strip-paths.txt
fi
find dist/main.dist -type f \( -name '*.so' -o -name '*.so.*' -o -name 'exr_converter' \) \
  ! -path '*/r3d/*' ! -path '*/braw/*' \
  ${STRIP_EX[@]+"${STRIP_EX[@]}"} \
  -exec strip --strip-unneeded {} +
# patchelf (and strip of those patchelf'd files) mis-aligns NumPy OpenBLAS
# and PyAV's hashed libxcb. Put the wheel copies back afterwards. They
# already use RPATH $ORIGIN and sit next to the binary. Do not strip them
# again: strip re-breaks those two NumPy libraries.
uv run python scripts/repair_linux_bundle_elf.py \
  dist/main.dist \
  .venv/lib/python3.13/site-packages
du -sh dist/main.dist

echo "---- dynamic linker ----"
if ldd dist/main.dist/exr_converter | grep -F 'not found'; then
  echo "ERROR: exr_converter has unresolved libraries" >&2
  exit 1
fi

# Rocky 8 GCC 8 libstdc++ tops out at GLIBCXX_3.4.25 / CXXABI_1.3.11.
uv run python scripts/check_glibc_floor.py dist/main.dist \
  --max-glibc 2.28 \
  --max-glibcxx 3.4.25 \
  --max-cxxabi 1.3.11

export QT_QPA_PLATFORM=offscreen
dist/main.dist/exr_converter --smoke-test
export EXR_CONVERTER_BIN="$ROOT/dist/main.dist/exr_converter"
uv run pytest tests/test_integration_conversions.py -v -m integration

mkdir -p linux-dist
NFPM_VER="2.41.3"
NFPM_SHA256="22aa6d3bc2ec239d62d3d190bcb036a47f2b24e0c3c6edfccebb6a55fbb2078e"
NFPM_TGZ="/tmp/nfpm_${NFPM_VER}_Linux_x86_64.tar.gz"
curl -fSL -o "$NFPM_TGZ" \
  "https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VER}/nfpm_${NFPM_VER}_Linux_x86_64.tar.gz"
echo "${NFPM_SHA256}  ${NFPM_TGZ}" | sha256sum -c -
tar -C /tmp -xzf "$NFPM_TGZ" nfpm
uv run python scripts/package_linux.py \
  --dist dist/main.dist \
  --out-dir linux-dist \
  --nfpm /tmp/nfpm

VERSION="$(uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
APPDIR="exr_converter-linux-x86_64.AppDir"
rm -rf "$APPDIR"
mkdir -p "${APPDIR}/usr/bin" "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
cp -a dist/main.dist/. "${APPDIR}/usr/bin/"
chmod +x "${APPDIR}/usr/bin/exr_converter"
cp resources/icons/icon.png "${APPDIR}/usr/share/icons/hicolor/256x256/apps/exr_converter.png"
cp resources/icons/icon.png "${APPDIR}/exr_converter.png"
printf '[Desktop Entry]\nName=EXR Converter\nExec=exr_converter\nIcon=exr_converter\nType=Application\nCategories=Graphics;\n' \
  > "${APPDIR}/exr_converter.desktop"
printf '#!/bin/bash\nSELF="$(readlink -f "$0")"\nHERE="$(dirname "$SELF")"\nexec "$HERE/usr/bin/exr_converter" "$@"\n' \
  > "${APPDIR}/AppRun"
chmod +x "${APPDIR}/AppRun"

APPIMAGETOOL_VER="1.9.1"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
curl -fSL -o /tmp/appimagetool \
  "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VER}/appimagetool-x86_64.AppImage"
echo "${APPIMAGETOOL_SHA256}  /tmp/appimagetool" | sha256sum -c -
chmod +x /tmp/appimagetool
# No FUSE inside the container. The runtime unpacks next to the tool.
export APPIMAGE_EXTRACT_AND_RUN=1
ARCH=x86_64 VERSION="${VERSION}" /tmp/appimagetool \
  --no-appstream --comp zstd \
  "${APPDIR}" "linux-dist/exr_converter-linux-x86_64.AppImage"
chmod +x linux-dist/exr_converter-linux-x86_64.AppImage
rm -rf "$APPDIR"

echo "---- read-only tarball launch ----"
CHECK_ROOT="$(mktemp -d)"
tar -xzf linux-dist/exr_converter-linux-x86_64.tar.gz -C "$CHECK_ROOT"
TREE="$(echo "$CHECK_ROOT"/exr-converter-*)"
chmod -R a-w "$TREE"
export XDG_CONFIG_HOME="$(mktemp -d)"
export XDG_CACHE_HOME="$(mktemp -d)"
export XDG_DATA_HOME="$(mktemp -d)"
export XDG_RUNTIME_DIR="$(mktemp -d)"
export HOME="$(mktemp -d)"
BEFORE="$(find "$TREE" -printf '%p %s\n' | sort)"
LINK_DIR="$(mktemp -d)"
ln -s "$TREE/exr-converter" "$LINK_DIR/exr-converter"
( cd /tmp && "$LINK_DIR/exr-converter" --version )
( cd /tmp && QT_QPA_PLATFORM=offscreen "$LINK_DIR/exr-converter" --smoke-test )
AFTER="$(find "$TREE" -printf '%p %s\n' | sort)"
if [ "$BEFORE" != "$AFTER" ]; then
  echo "ERROR: read-only prefix changed during launch" >&2
  diff -u <(printf '%s\n' "$BEFORE") <(printf '%s\n' "$AFTER") || true
  exit 1
fi
echo "OK: prefix unchanged (settings under $XDG_CONFIG_HOME)"

echo "---- RPM metadata and install ----"
rpm -qp --queryformat 'vendor=%{VENDOR} license=%{LICENSE} arch=%{ARCH} name=%{NAME}\n' \
  linux-dist/exr_converter-linux-x86_64.rpm
rpm -qpl linux-dist/exr_converter-linux-x86_64.rpm | grep -E \
  '/opt/exr-converter/exr-converter$|/usr/bin/exr-converter$|/usr/share/applications/exr-converter.desktop$|/usr/share/icons/hicolor/256x256/apps/exr-converter.png$'
rpm -Uvh linux-dist/exr_converter-linux-x86_64.rpm
test -L /usr/bin/exr-converter
/usr/bin/exr-converter --version

mkdir -p release-linux
cp -a \
  linux-dist/exr_converter-linux-x86_64.AppImage \
  linux-dist/exr_converter-linux-x86_64.tar.gz \
  linux-dist/exr_converter-linux-x86_64.rpm \
  release-linux/
ls -lh release-linux linux-dist
echo "Rocky Linux 8 build finished."
