#!/bin/bash
set -e

# ==============================================================================
# Tiki Karaoke Lounge - Automated Linux AppImage Builder
# Builds a universal, standalone Linux AppImage inside a clean Debian container
# for maximum backward GLIBC compatibility (Ubuntu, Debian, Mint, Fedora, Arch)
# ==============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_TMP="/tmp/tiki_build_$(date +%s)"
OUT_DIR="${REPO_ROOT}/dist"
APPDIR="/tmp/TikiKaraoke.AppDir"
VERSION="${TIKI_VERSION:-1.1.3}"
OUT_APPIMAGE="${OUT_DIR}/TikiKaraoke-Pro-v${VERSION}-x86_64.AppImage"

USE_DOCKER=1
if [ "$1" = "--host" ] || [ "$1" = "--no-docker" ] || [ "${NO_DOCKER}" = "1" ]; then
    USE_DOCKER=0
fi

echo "=== [1/5] Setting up build directories ==="
rm -rf "$APPDIR" 2>/dev/null || true
mkdir -p "$OUT_DIR"
mkdir -p "$BUILD_TMP"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/icons/hicolor/512x512/apps"
mkdir -p "$APPDIR/usr/share/applications"

PYI_COMMON_ARGS=(
    --clean -y --onedir
    --name TikiKaraoke
    --hidden-import uvicorn
    --hidden-import uvicorn.logging
    --hidden-import uvicorn.loops
    --hidden-import uvicorn.loops.auto
    --hidden-import uvicorn.loops.asyncio
    --hidden-import uvicorn.protocols
    --hidden-import uvicorn.protocols.http
    --hidden-import uvicorn.protocols.http.auto
    --hidden-import uvicorn.protocols.http.h11_impl
    --hidden-import uvicorn.protocols.websockets
    --hidden-import uvicorn.protocols.websockets.auto
    --hidden-import uvicorn.protocols.websockets.websockets_impl
    --hidden-import uvicorn.lifespan
    --hidden-import uvicorn.lifespan.on
    --hidden-import uvicorn.lifespan.off
    --hidden-import engineio.async_drivers.asgi
    --collect-all yt_dlp
    --hidden-import qrcode
    --hidden-import jinja2
    --hidden-import pydantic
    --hidden-import pydantic_core
    --hidden-import starlette
    --hidden-import fastapi
    --hidden-import sqlite3
    --hidden-import wave
    --hidden-import tray
    --hidden-import audio_router
    --hidden-import network_helper
    --hidden-import pystray
    --collect-all pystray
    --hidden-import Xlib
    --collect-all Xlib
    --collect-all tkinter
    --collect-all PIL
    --hidden-import PIL._tkinter_finder
    --hidden-import tkinter
    --hidden-import tkinter.messagebox
    --collect-all importer
    --hidden-import importer
    --hidden-import importer.main
    --hidden-import importer.ui.app_window
    --hidden-import importer.ui.cdg_ripper_window
    --hidden-import importer.ui.compuhost_window
    --collect-all routes
    --collect-all services
)

if [ "$USE_DOCKER" = "1" ]; then
    echo "=== [2/5] Compiling standalone binary inside Debian container ==="
    cat << 'EOF' > "${BUILD_TMP}/docker_build.sh"
#!/bin/bash
set -e
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev python3-tk tk-dev libpython3.11 binutils curl file ca-certificates gcc libc6-dev

python3 -m venv /tmp/venv
. /tmp/venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r /src/requirements.txt pyinstaller

mkdir -p /out /tmp/pyi_work /tmp/pyi_spec
pyinstaller --clean -y --onedir \
    --name TikiKaraoke \
    --add-data "/src/static:static" \
    --add-data "/src/templates:templates" \
    --distpath /out \
    --workpath /tmp/pyi_work \
    --specpath /tmp/pyi_spec \
    --hidden-import uvicorn \
    --hidden-import uvicorn.logging \
    --hidden-import uvicorn.loops \
    --hidden-import uvicorn.loops.auto \
    --hidden-import uvicorn.loops.asyncio \
    --hidden-import uvicorn.protocols \
    --hidden-import uvicorn.protocols.http \
    --hidden-import uvicorn.protocols.http.auto \
    --hidden-import uvicorn.protocols.http.h11_impl \
    --hidden-import uvicorn.protocols.websockets \
    --hidden-import uvicorn.protocols.websockets.auto \
    --hidden-import uvicorn.protocols.websockets.websockets_impl \
    --hidden-import uvicorn.lifespan \
    --hidden-import uvicorn.lifespan.on \
    --hidden-import uvicorn.lifespan.off \
    --hidden-import engineio.async_drivers.asgi \
    --collect-all yt_dlp \
    --hidden-import qrcode \
    --hidden-import jinja2 \
    --hidden-import pydantic \
    --hidden-import pydantic_core \
    --hidden-import starlette \
    --hidden-import fastapi \
    --hidden-import sqlite3 \
    --hidden-import wave \
    --hidden-import tray \
    --hidden-import audio_router \
    --hidden-import network_helper \
    --hidden-import pystray \
    --collect-all pystray \
    --hidden-import Xlib \
    --collect-all Xlib \
    --collect-all tkinter \
    --collect-all PIL \
    --hidden-import PIL._tkinter_finder \
    --hidden-import tkinter \
    --hidden-import tkinter.messagebox \
    --collect-all importer \
    --hidden-import importer \
    --hidden-import importer.main \
    --hidden-import importer.ui.app_window \
    --hidden-import importer.ui.cdg_ripper_window \
    --hidden-import importer.ui.compuhost_window \
    --collect-all routes \
    --collect-all services \
    /src/main.py
EOF

    chmod +x "${BUILD_TMP}/docker_build.sh"
    docker run --rm \
        -v "${REPO_ROOT}:/src:ro" \
        -v "${BUILD_TMP}:/out" \
        -v "${BUILD_TMP}/docker_build.sh:/build.sh:ro" \
        debian:bookworm-slim /bin/bash /build.sh
else
    echo "=== [2/5] Compiling standalone binary directly on host (non-Docker) ==="
    mkdir -p /tmp/pyi_work /tmp/pyi_spec
    "${REPO_ROOT}/venv/bin/pyinstaller" "${PYI_COMMON_ARGS[@]}" \
        --add-data "${REPO_ROOT}/static:static" \
        --add-data "${REPO_ROOT}/templates:templates" \
        --distpath "${BUILD_TMP}" \
        --workpath /tmp/pyi_work \
        --specpath /tmp/pyi_spec \
        "${REPO_ROOT}/main.py"
fi

echo "=== [3/5] Assembling AppDir ==="
cp -r "${BUILD_TMP}/TikiKaraoke"/* "$APPDIR/usr/bin/"

mkdir -p "${REPO_ROOT}/bin"
if [ ! -f "${REPO_ROOT}/bin/cloudflared" ]; then
    echo "Downloading cloudflared (linux-amd64)..."
    curl -sL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" -o "${REPO_ROOT}/bin/cloudflared"
    chmod +x "${REPO_ROOT}/bin/cloudflared"
fi
if [ ! -f "${REPO_ROOT}/bin/bore" ]; then
    echo "Downloading bore (v0.5.2 x86_64-unknown-linux-musl)..."
    curl -sL "https://github.com/ekzhang/bore/releases/download/v0.5.2/bore-v0.5.2-x86_64-unknown-linux-musl.tar.gz" | tar -xz -C "${REPO_ROOT}/bin" bore
    chmod +x "${REPO_ROOT}/bin/bore"
fi

if [ -d "${REPO_ROOT}/bin" ]; then
    mkdir -p "$APPDIR/usr/bin/bin"
    cp -r "${REPO_ROOT}/bin"/* "$APPDIR/usr/bin/bin/" || true
    chmod +x "$APPDIR/usr/bin/bin/"* 2>/dev/null || true

    if [ -d "$APPDIR/usr/bin/_internal" ]; then
        mkdir -p "$APPDIR/usr/bin/_internal/bin"
        cp -r "${REPO_ROOT}/bin"/* "$APPDIR/usr/bin/_internal/bin/" || true
        chmod +x "$APPDIR/usr/bin/_internal/bin/"* 2>/dev/null || true
    fi
fi

# Standalone entry points inside AppImage
cat << 'EOF' > "$APPDIR/usr/bin/TikiKaraokeImporter"
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
exec "${HERE}/TikiKaraoke" --importer "$@"
EOF
chmod +x "$APPDIR/usr/bin/TikiKaraokeImporter"

cat << 'EOF' > "$APPDIR/usr/bin/TikiKaraokeRipper"
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
exec "${HERE}/TikiKaraoke" --cdg-ripper "$@"
EOF
chmod +x "$APPDIR/usr/bin/TikiKaraokeRipper"

if [ -d "$APPDIR/usr/bin/_internal" ]; then
    cp "$APPDIR/usr/bin/TikiKaraokeImporter" "$APPDIR/usr/bin/_internal/TikiKaraokeImporter"
    cp "$APPDIR/usr/bin/TikiKaraokeRipper" "$APPDIR/usr/bin/_internal/TikiKaraokeRipper"
fi

cp "${REPO_ROOT}/tray.py" "$APPDIR/usr/bin/tray.py"
cp "${REPO_ROOT}/tray.py" "$APPDIR/usr/bin/_internal/tray.py" 2>/dev/null || true
cp "${REPO_ROOT}/static/tiki-icon.png" "$APPDIR/tiki-icon.png"
cp "${REPO_ROOT}/static/tiki-icon.png" "$APPDIR/usr/bin/tiki-icon.png"
mkdir -p "$APPDIR/usr/bin/static"
cp "${REPO_ROOT}/static/tiki-icon.png" "$APPDIR/usr/bin/static/tiki-icon.png"
cp "${REPO_ROOT}/static/tiki-icon.png" "$APPDIR/usr/share/icons/hicolor/512x512/apps/tiki-icon.png"

cat << 'EOF' > "$APPDIR/tiki-karaoke.desktop"
[Desktop Entry]
Name=Tiki Karaoke Lounge
GenericName=Karaoke & Party System
Comment=Turnkey Dual-Screen Karaoke & Party Entertainment System
Exec=AppRun %U
Icon=tiki-icon
Type=Application
Categories=AudioVideo;Audio;Player;
Terminal=false
StartupWMClass=TikiKaraoke
EOF

cp "$APPDIR/tiki-karaoke.desktop" "$APPDIR/usr/share/applications/tiki-karaoke.desktop"

cat << EOF > "$APPDIR/AppRun"
#!/bin/bash
SELF=\$(readlink -f "\$0")
HERE=\${SELF%/*}
export APPDIR="\${HERE}"
export TIKI_VERSION="\${TIKI_VERSION:-${VERSION}}"

# Preserve host LD_LIBRARY_PATH for spawned external tools (browsers, xdg-open, /bin/sh)
export LD_LIBRARY_PATH_ORIG="\${LD_LIBRARY_PATH}"
unset LD_LIBRARY_PATH

export PATH="\${HERE}/usr/bin:\${HERE}/usr/bin/bin:\${HERE}/usr/bin/_internal/bin:\${PATH}"

cd "\${HERE}/usr/bin"
ARG0=\$(basename "\$0")
if [[ "\$ARG0" == *"Importer"* || "\$ARG0" == *"importer"* ]]; then
    exec "\${HERE}/usr/bin/TikiKaraoke" --importer "\$@"
elif [[ "\$ARG0" == *"Ripper"* || "\$ARG0" == *"ripper"* ]]; then
    exec "\${HERE}/usr/bin/TikiKaraoke" --cdg-ripper "\$@"
fi
exec "\${HERE}/usr/bin/TikiKaraoke" "\$@"
EOF

chmod +x "$APPDIR/AppRun"
chmod +x "$APPDIR/usr/bin/TikiKaraoke"
ln -sf AppRun "$APPDIR/TikiKaraoke"
ln -sf AppRun "$APPDIR/TikiKaraokeImporter"
ln -sf AppRun "$APPDIR/TikiKaraokeRipper"

echo "=== [4/5] Packaging AppImage ==="
if [ ! -f "/tmp/appimagetool" ]; then
    curl -sL https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage -o /tmp/appimagetool
    chmod +x /tmp/appimagetool
fi

export ARCH=x86_64
/tmp/appimagetool --appimage-extract-and-run "$APPDIR" "$OUT_APPIMAGE"
chmod +x "$OUT_APPIMAGE"

echo "=== [5/5] Cleaning up temporary build files ==="
if [ "$USE_DOCKER" = "1" ]; then
    docker run --rm -v "${BUILD_TMP}:/b" debian:bookworm-slim rm -rf /b 2>/dev/null || true
fi
rm -rf "$BUILD_TMP" "$APPDIR" /tmp/pyi_work /tmp/pyi_spec 2>/dev/null || true

echo "================================================================="
echo "✅ AppImage build complete: ${OUT_APPIMAGE}"
ls -lh "$OUT_APPIMAGE"
echo "================================================================="
