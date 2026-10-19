#!/usr/bin/env bash
set -e

# Tiki Karaoke Automated Linux Installer
# Sets up Python virtual environment, dependencies, and desktop launcher

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

echo "=========================================================="
echo "🌴 🌺      TIKI KARAOKE LOUNGE LINUX INSTALLER     🍹 🌴"
echo "=========================================================="
echo ""

# 1. Check Python 3 & Version (>= 3.10)
echo "🔍 Checking Python installation..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Python 3 is not installed!"
    echo "   Please install Python 3 (3.10 or newer):"
    echo "     Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    echo "     Fedora:        sudo dnf install -y python3 python3-pip"
    echo "     Arch/Manjaro:  sudo pacman -S python python-pip"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys; print("ok" if sys.version_info >= (3, 10) else "bad")')

if [ "$PY_OK" != "ok" ]; then
    echo "❌ Python 3.10+ is required (found Python $PY_VERSION)."
    echo "   Please upgrade your Python version before proceeding."
    exit 1
fi
echo "  ✅ Python $PY_VERSION detected."

# 2. Check FFmpeg (Warning only)
echo "🔍 Checking FFmpeg (required for YouTube audio/video processing)..."
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    echo "  ✅ FFmpeg & FFprobe found."
else
    echo "  ⚠️  FFmpeg is not installed or not in PATH."
    echo "     YouTube downloads will not work until FFmpeg is installed."
    echo "     Install now with:"
    echo "       Ubuntu/Debian/Mint:  sudo apt install -y ffmpeg"
    echo "       Fedora:              sudo dnf install -y ffmpeg"
    echo "       Arch/Manjaro:        sudo pacman -S ffmpeg"
    echo ""
fi

# 3. Virtual Environment Setup
echo "📦 Setting up isolated Python virtual environment in ./venv..."
if [ ! -d "$DIR/venv" ]; then
    python3 -m venv "$DIR/venv"
    echo "  ✅ Virtual environment created."
else
    echo "  ℹ️  Existing virtual environment found in ./venv."
fi

# 4. Install / Update Python dependencies
echo "📥 Installing required Python dependencies..."
"$DIR/venv/bin/pip" install --upgrade pip --quiet
"$DIR/venv/bin/pip" install -r "$DIR/requirements.txt" --quiet
echo "  ✅ Python dependencies successfully installed."

# 5. Make helper scripts executable
chmod +x "$DIR/start.sh" "$DIR/setup.sh" "$DIR/main.py" 2>/dev/null || true

# 6. Generate XDG Desktop Application Launcher
echo "🖥️  Creating desktop application launcher..."
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"

DESKTOP_FILE="$DESKTOP_DIR/tiki-karaoke.desktop"
ICON_PATH="$DIR/static/tiki-icon.svg"

cat <<EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=Tiki Karaoke
GenericName=Karaoke Party System
Comment=Self-hosted modern karaoke party system with mobile queueing
Exec="$DIR/start.sh"
Path=$DIR
Icon=$ICON_PATH
Terminal=true
Categories=AudioVideo;Audio;Player;
Keywords=karaoke;music;party;youtube;singer;
StartupNotify=true
EOF

chmod +x "$DESKTOP_FILE"

# Refresh desktop database if available
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi
echo "  ✅ Desktop launcher installed to: $DESKTOP_FILE"

# Check Linux Firewall (UFW / Firewalld) for LAN device access
echo ""
echo "🛡️ Checking Linux local network firewall for mobile/LAN access..."
if command -v ufw >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
        sudo ufw allow 8000/tcp comment "Tiki Karaoke" >/dev/null 2>&1 || true
        echo "  ✅ UFW firewall rule added for port 8000/tcp."
    else
        echo "  ℹ️  If mobile devices on your Wi-Fi cannot connect, open port 8000 in UFW:"
        echo -e "     \033[1;33msudo ufw allow 8000/tcp comment 'Tiki Karaoke'\033[0m"
    fi
fi

echo ""
echo "=========================================================="
echo "🎉 INSTALLATION COMPLETE! Your Karaoke Lounge is ready."
echo "=========================================================="
echo ""
echo "To start Tiki Karaoke, simply run:"
echo -e "   \033[1;33m./start.sh\033[0m"
echo ""
echo "Or launch 'Tiki Karaoke' from your Linux Application Menu!"
echo ""
