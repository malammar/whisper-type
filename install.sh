#!/usr/bin/env bash
# install.sh — set up whisper-type on a Linux/X11 desktop
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV="$SCRIPT_DIR/.venv"
MAIN="$SCRIPT_DIR/whisper-type.py"

echo "==> whisper-type installer"
echo "    Script dir: $SCRIPT_DIR"

# ── 1. Check hard dependencies ────────────────────────────────────────────────
if ! command -v xdotool &>/dev/null; then
    echo ""
    echo "ERROR: xdotool is required but not installed."
    echo "       Run: sudo apt install xdotool"
    exit 1
fi

if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.9+ first."
    exit 1
fi

# ── 2. Virtualenv + pip deps ──────────────────────────────────────────────────
echo ""
echo "==> Creating virtualenv at $VENV"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q wyoming sounddevice numpy pystray pillow python-xlib

PY_MINOR=$("$VENV/bin/python3" -c "import sys; print(sys.version_info.minor)")
if [[ "$PY_MINOR" -lt 11 ]]; then
    "$VENV/bin/pip" install -q tomli
fi
echo "    Dependencies installed."

# ── 3. Default config ─────────────────────────────────────────────────────────
CONFIG_DIR="$HOME/.config/whisper-type"
CONFIG_FILE="$CONFIG_DIR/config.toml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    mkdir -p "$CONFIG_DIR"
    cp "$SCRIPT_DIR/config.toml" "$CONFIG_FILE"
    echo ""
    echo "==> Config written to $CONFIG_FILE"
    echo "    Edit [server] host/port to point at your Wyoming faster-whisper instance."
else
    echo ""
    echo "==> Config already exists at $CONFIG_FILE — skipping."
fi

# ── 4. Make script executable ─────────────────────────────────────────────────
chmod +x "$MAIN"

# ── 5. Autostart .desktop entry ───────────────────────────────────────────────
echo ""
echo "==> Installing autostart entry"
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/whisper-type.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=whisper-type
Comment=Hotkey voice-to-text via Wyoming faster-whisper
Exec=setsid $VENV/bin/python3 $MAIN
X-GNOME-Autostart-enabled=true
NoDisplay=true
DESKTOP
echo "    Will start automatically on next login."

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "==> Done!"
echo ""
echo "    Edit your server address:"
echo "      \$EDITOR $CONFIG_FILE"
echo ""
echo "    Start now:"
echo "      setsid $VENV/bin/python3 $MAIN &"
echo ""
echo "    The hotkey (default Alt+F9) is grabbed directly — no DE config needed."
echo "    Change it any time from the tray icon menu."
