#!/usr/bin/env bash
# install.sh — set up whisper-type on a Linux/X11 desktop
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV="$SCRIPT_DIR/.venv"
MAIN="$SCRIPT_DIR/whisper-type.py"
TOGGLE="$SCRIPT_DIR/whisper-type-toggle.sh"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/whisper-type.service"
XBINDKEYS_RC="$HOME/.xbindkeysrc"

echo "==> whisper-type installer"
echo "    Script dir: $SCRIPT_DIR"

# ── 1. Check hard dependencies ────────────────────────────────────────────────
MISSING=()
for cmd in xdotool xbindkeys notify-send; do
    command -v "$cmd" &>/dev/null || MISSING+=("$cmd")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo ""
    echo "ERROR: missing required packages: ${MISSING[*]}"
    echo "       Run: sudo apt install ${MISSING[*]}"
    exit 1
fi

if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.9+ first."
    exit 1
fi

# ── 2. Virtualenv + pip deps ──────────────────────────────────────────────────
echo ""
echo "==> Creating virtualenv at $VENV"
rm -rf "$VENV"
# --system-site-packages gives pystray access to gi/PyGObject (AppIndicator)
"$PYTHON" -m venv --system-site-packages "$VENV"
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

# ── 4. Make scripts executable ────────────────────────────────────────────────
chmod +x "$MAIN" "$TOGGLE"

# ── 5. xbindkeys hotkey ───────────────────────────────────────────────────────
# Read the configured hotkey binding from config and convert to xbindkeys format
BINDING=$(grep -m1 '^binding' "$CONFIG_FILE" | sed 's/.*=\s*"\(.*\)"/\1/')
BINDING="${BINDING:-Alt+F9}"

# Convert "Alt+F9" → "alt + F9" for xbindkeys
XBIND_KEY=$(echo "$BINDING" \
    | sed 's/+/ + /g' \
    | sed 's/\bAlt\b/alt/g' \
    | sed 's/\bCtrl\b/control/g' \
    | sed 's/\bShift\b/shift/g' \
    | sed 's/\bSuper\b/mod4/g' \
    | tr -s ' ')

XBIND_ENTRY="\"$TOGGLE\"\n  $XBIND_KEY"

echo ""
if [[ ! -f "$XBINDKEYS_RC" ]]; then
    echo "==> Creating $XBINDKEYS_RC"
    printf '%b\n' "$XBIND_ENTRY" > "$XBINDKEYS_RC"
elif grep -qF "$TOGGLE" "$XBINDKEYS_RC"; then
    echo "==> xbindkeys entry already present — skipping."
else
    echo "==> Adding hotkey to $XBINDKEYS_RC"
    printf '\n%b\n' "$XBIND_ENTRY" >> "$XBINDKEYS_RC"
fi
echo "    Hotkey: $BINDING"

# Reload xbindkeys if it's running
if pgrep -x xbindkeys &>/dev/null; then
    pkill xbindkeys
    xbindkeys
    echo "    xbindkeys reloaded."
else
    echo "    xbindkeys not running — it will pick up the config on next start."
fi

# ── 6. Remove legacy autostart entry if present ───────────────────────────────
LEGACY_DESKTOP="$HOME/.config/autostart/whisper-type.desktop"
if [[ -f "$LEGACY_DESKTOP" ]]; then
    rm "$LEGACY_DESKTOP"
    echo ""
    echo "==> Removed legacy autostart entry."
fi

# ── 7. systemd user service ───────────────────────────────────────────────────
echo ""
echo "==> Installing systemd user service"
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Whisper Type — hotkey voice-to-text
After=graphical-session.target sound.target
PartOf=graphical-session.target

[Service]
ExecStart=$VENV/bin/python3 $MAIN
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable whisper-type.service

# Restart if already running, otherwise start fresh
if systemctl --user is-active --quiet whisper-type.service; then
    systemctl --user restart whisper-type.service
    echo "    Service restarted."
else
    systemctl --user start whisper-type.service
    echo "    Service started."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "==> Done!"
echo ""
echo "    Edit your server address:"
echo "      \$EDITOR $CONFIG_FILE"
echo ""
echo "    Useful commands:"
echo "      systemctl --user status whisper-type    # check status"
echo "      systemctl --user restart whisper-type   # restart"
echo "      journalctl --user -u whisper-type -f    # follow logs"
