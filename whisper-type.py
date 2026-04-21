#!/usr/bin/env python3
"""
whisper-type — press a hotkey, talk, press again (or release), get text typed.

Key detection strategy:
  Press  — GNOME custom shortcut fires whisper-type-toggle.sh → SIGUSR1
  Release— XQueryKeymap polling (passive read of key state, no interception)

Tray menu:
  ● Toggle mode  /  ○ Hold to talk   — switch recording mode
  Hotkey: Alt+F9 / Change hotkey…   — updates GNOME shortcut + config
  Quit

Config: ~/.config/whisper-type/config.toml  (created on first run)
"""

import asyncio
import os
import re
import signal
import subprocess
import sys
import threading
import time

import numpy as np
import sounddevice as sd
import pystray
from PIL import Image, ImageDraw
from Xlib import XK, display as Xdisplay
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStop
from wyoming.client import AsyncTcpClient

# ── Config ─────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = """\
[server]
host = "127.0.0.1"
port = 10300

[audio]
sample_rate   = 16000
channels      = 1
chunk_samples = 4096

[typing]
delay_ms       = 8
pre_type_sleep = 0.15

[hotkey]
binding  = "Alt+F9"
mode     = "toggle"   # "toggle" or "hold"
debounce = 0.4

[beep]
start_hz = 880
stop_hz  = 440
duration = 0.12
volume   = 0.35
"""

CONFIG_PATH     = os.path.expanduser("~/.config/whisper-type/config.toml")
TOGGLE_SCRIPT   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper-type-toggle.sh")
BEEP_SAMPLERATE = 44100
PID_FILE        = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "whisper-type.pid")
HOLD_POLL_HZ    = 30    # times per second to check key state in hold mode


def _load_toml(path: str) -> dict:
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"[config] {e} — using defaults", flush=True)
        return {}


def _save_toml_key(path: str, section: str, key: str, value):
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = DEFAULT_CONFIG.splitlines(keepends=True)

    in_section = False
    replaced   = False
    result     = []
    val_str    = f'"{value}"' if isinstance(value, str) else str(value)

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == f"[{section}]"
        if in_section and stripped.startswith(f"{key}") and "=" in stripped:
            result.append(f"{key} = {val_str}\n")
            replaced = True
        else:
            result.append(line)

    if not replaced:
        result.append(f"\n[{section}]\n{key} = {val_str}\n")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.writelines(result)


def _ensure_config():
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            f.write(DEFAULT_CONFIG)
        print(f"[config] Created {CONFIG_PATH}", flush=True)


def _cfg(cfg: dict, *keys, default):
    v = cfg
    for k in keys:
        if not isinstance(v, dict) or k not in v:
            return default
        v = v[k]
    return v


_ensure_config()
_cfg_data = _load_toml(CONFIG_PATH)

WYOMING_HOST    = _cfg(_cfg_data, "server",  "host",           default="127.0.0.1")
WYOMING_PORT    = _cfg(_cfg_data, "server",  "port",           default=10300)
SAMPLE_RATE     = _cfg(_cfg_data, "audio",   "sample_rate",    default=16_000)
CHANNELS        = _cfg(_cfg_data, "audio",   "channels",       default=1)
CHUNK_SAMPLES   = _cfg(_cfg_data, "audio",   "chunk_samples",  default=4096)
TYPE_DELAY_MS   = _cfg(_cfg_data, "typing",  "delay_ms",       default=8)
PRE_TYPE_SLEEP  = _cfg(_cfg_data, "typing",  "pre_type_sleep", default=0.15)
HOTKEY_DEBOUNCE = _cfg(_cfg_data, "hotkey",  "debounce",       default=0.4)
BEEP_START_HZ   = _cfg(_cfg_data, "beep",    "start_hz",       default=880)
BEEP_STOP_HZ    = _cfg(_cfg_data, "beep",    "stop_hz",        default=440)
BEEP_DURATION   = _cfg(_cfg_data, "beep",    "duration",       default=0.12)
BEEP_VOLUME     = _cfg(_cfg_data, "beep",    "volume",         default=0.35)

_binding: str = _cfg(_cfg_data, "hotkey", "binding", default="Alt+F9")
_mode: str    = _cfg(_cfg_data, "hotkey", "mode",    default="toggle")

# ── States ─────────────────────────────────────────────────────────────────────

IDLE       = "idle"
RECORDING  = "recording"
PROCESSING = "processing"

_state          = IDLE
_chunks: list   = []
_lock           = threading.Lock()
_next_toggle_at = 0.0
_tray: pystray.Icon | None = None


# ── Tray icon ──────────────────────────────────────────────────────────────────

_COLOURS  = {IDLE: "#5a5a5a", RECORDING: "#e03030", PROCESSING: "#e09000"}
_TOOLTIPS = {
    IDLE:       "Whisper Type - idle",
    RECORDING:  "Whisper Type - recording...",
    PROCESSING: "Whisper Type - transcribing...",
}



def _make_icon(state: str) -> Image.Image:
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    mx, my = size // 2, size // 2
    draw.ellipse([4, 4, size - 4, size - 4], fill=_COLOURS[state])
    bw, bh = 14, 20
    draw.rounded_rectangle(
        [mx - bw//2, my - bh//2 - 4, mx + bw//2, my + bh//2 - 4],
        radius=7, fill="white",
    )
    aw = 18
    draw.arc([mx - aw//2, my - 4, mx + aw//2, my + 16], 0, 180, fill="white", width=3)
    draw.line([mx, my + 16, mx, my + 22],         fill="white", width=3)
    draw.line([mx - 6, my + 22, mx + 6, my + 22], fill="white", width=3)
    if state == PROCESSING:
        for angle in [0, 120, 240]:
            rad = angle * 3.14159 / 180
            dx, dy = int(24 * np.cos(rad)), int(24 * np.sin(rad))
            r = 4
            draw.ellipse([mx+dx-r, my+dy-r, mx+dx+r, my+dy+r], fill=(255, 255, 255, 180))
    return img


def _set_state(state: str):
    global _state
    _state = state
    if _tray:
        _tray.icon  = _make_icon(state)
        _tray.title = _TOOLTIPS[state]


def _rebuild_menu():
    if _tray:
        _tray.menu = _make_menu()


def _make_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem("Whisper Type", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Toggle mode",
            lambda icon, item: _set_mode("toggle"),
            checked=lambda item: _mode == "toggle",
            radio=True,
        ),
        pystray.MenuItem(
            "Hold to talk",
            lambda icon, item: _set_mode("hold"),
            checked=lambda item: _mode == "hold",
            radio=True,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"Hotkey: {_binding}", None, enabled=False),
        pystray.MenuItem("Change hotkey…", lambda icon, item: threading.Thread(
            target=_do_rebind, daemon=True).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Quit",
            lambda icon, item: (icon.stop(), os.kill(os.getpid(), signal.SIGTERM)),
        ),
    )


# ── Mode switching ─────────────────────────────────────────────────────────────

def _set_mode(mode: str):
    global _mode
    _mode = mode
    _save_toml_key(CONFIG_PATH, "hotkey", "mode", mode)
    _log(f"Mode → {mode}")
    _rebuild_menu()
    if _state == RECORDING:
        _stop_recording()


# ── Hotkey rebind (via zenity dialog — no key interception needed) ─────────────

def _do_rebind():
    """Ask the user to type a new binding string via a GUI dialog."""
    try:
        result = subprocess.run(
            ["zenity", "--entry",
             "--title=Whisper Type — Change Hotkey",
             "--text=Enter new hotkey combo (e.g.  Alt+F10  or  Ctrl+F8):",
             f"--entry-text={_binding}"],
            capture_output=True, text=True,
        )
        new_binding = result.stdout.strip()
        if result.returncode == 0 and new_binding:
            _apply_new_binding(new_binding)
    except FileNotFoundError:
        # zenity not available — fall back to notification with instructions
        _notify(f"Edit {CONFIG_PATH} and change [hotkey] binding, then restart.")
        _log(f"[rebind] zenity not found — edit {CONFIG_PATH} manually")


def _apply_new_binding(new_binding: str):
    global _binding
    _binding = new_binding
    _save_toml_key(CONFIG_PATH, "hotkey", "binding", new_binding)
    _log(f"Hotkey → {new_binding}")
    _notify(f"Hotkey set to {new_binding}  (restart to apply GNOME shortcut)")
    _rebuild_menu()
    _update_gnome_shortcut(new_binding)


def _update_gnome_shortcut(binding: str):
    """Update the GNOME custom shortcut command to use the new binding."""
    # Convert "Alt+F9" → "<Alt>F9" (GNOME format)
    gnome_binding = ""
    parts = [p.strip() for p in binding.split("+")]
    mod_map = {"alt": "<Alt>", "ctrl": "<Control>", "shift": "<Shift>", "super": "<Super>"}
    for part in parts[:-1]:
        gnome_binding += mod_map.get(part.lower(), f"<{part}>")
    gnome_binding += parts[-1]

    BASE = "org.gnome.settings-daemon.plugins.media-keys"
    # Find our existing shortcut slot
    try:
        slots = subprocess.check_output(
            ["gsettings", "get", BASE, "custom-keybindings"], text=True
        ).strip()
        for slot_path in re.findall(r"'([^']+)'", slots):
            name = subprocess.check_output(
                ["gsettings", "get", f"{BASE}.custom-keybinding:{slot_path}", "name"],
                text=True,
            ).strip().strip("'")
            if name == "Whisper Type Toggle":
                subprocess.run([
                    "gsettings", "set",
                    f"{BASE}.custom-keybinding:{slot_path}", "binding", gnome_binding,
                ])
                _log(f"[gnome] shortcut updated to {gnome_binding}")
                return
    except Exception as e:
        _log(f"[gnome] could not update shortcut: {e}")


def _notify(msg: str):
    try:
        subprocess.Popen(
            ["notify-send", "-a", "whisper-type", "-t", "4000", "Whisper Type", msg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


# ── XQueryKeymap — passive key-state poll for hold mode ───────────────────────

def _binding_keycodes(binding: str) -> list[int]:
    """
    Return all keycodes that must be held for the binding to be considered active.
    e.g. "Alt+F9" → [64, 75]  (LAlt + F9; RAlt is also checked separately)
    """
    MOD_KC = {
        "alt":   [64, 108],   # LAlt, RAlt
        "ctrl":  [37, 105],
        "shift": [50, 62],
        "super": [133, 134],
    }
    parts = [p.strip() for p in binding.split("+")]
    key   = parts[-1]
    mods  = [p.lower() for p in parts[:-1]]

    d      = Xdisplay.Display()
    keysym = XK.string_to_keysym(key) or XK.string_to_keysym(key.upper())
    key_kc = d.keysym_to_keycode(keysym)
    d.close()

    # For each modifier, we need AT LEAST ONE of its keycodes pressed
    # Store as list-of-lists; later we AND across groups
    groups: list[list[int]] = []
    for mod in mods:
        groups.append(MOD_KC.get(mod, []))
    groups.append([key_kc])
    return groups   # type: ignore[return-value]


def _keymap_held(keymap: list[int], kc: int) -> bool:
    return bool(keymap[kc >> 3] & (1 << (kc & 7)))


def _binding_active(d: Xdisplay.Display, groups) -> bool:
    """Return True if all modifier groups AND the main key are currently pressed."""
    keymap = d.query_keymap()
    for group in groups:
        if not any(_keymap_held(keymap, kc) for kc in group):
            return False
    return True


def _hold_watcher():
    """
    Poll XQueryKeymap until the main key of the binding is released.
    We only check the non-modifier key (e.g. F9) because GNOME releases
    modifier keys internally before running the shortcut command.
    """
    parts  = [p.strip() for p in _binding.split("+")]
    key    = parts[-1]
    d      = Xdisplay.Display()
    keysym = XK.string_to_keysym(key) or XK.string_to_keysym(key.upper())
    key_kc = d.keysym_to_keycode(keysym)
    interval = 1.0 / HOLD_POLL_HZ
    try:
        time.sleep(0.1)   # let the keydown settle in XQueryKeymap
        while _state == RECORDING:
            keymap = d.query_keymap()
            if not _keymap_held(keymap, key_kc):
                _stop_recording()
                break
            time.sleep(interval)
    finally:
        d.close()


# ── Beep ───────────────────────────────────────────────────────────────────────

def _beep(freq: float):
    t    = np.linspace(0, BEEP_DURATION, int(BEEP_SAMPLERATE * BEEP_DURATION), False)
    wave = (np.sin(2 * np.pi * freq * t) * BEEP_VOLUME).astype(np.float32)
    fade = np.linspace(1.0, 0.0, len(wave) // 4)
    wave[-len(fade):] *= fade
    try:
        with sd.OutputStream(samplerate=BEEP_SAMPLERATE, channels=1, dtype="float32") as out:
            out.write(wave.reshape(-1, 1))
    except Exception as e:
        print(f"[beep] {e}", flush=True)


# ── Wyoming transcription ──────────────────────────────────────────────────────

async def _transcribe(pcm_float32: np.ndarray) -> str:
    pcm_int16 = (pcm_float32 * 32767).clip(-32768, 32767).astype(np.int16)
    raw_bytes = pcm_int16.tobytes()
    async with AsyncTcpClient(WYOMING_HOST, WYOMING_PORT) as client:
        await client.write_event(Transcribe().event())
        step = CHUNK_SAMPLES * 2
        for offset in range(0, len(raw_bytes), step):
            await client.write_event(
                AudioChunk(rate=SAMPLE_RATE, width=2, channels=CHANNELS,
                           audio=raw_bytes[offset : offset + step]).event()
            )
        await client.write_event(AudioStop().event())
        while True:
            event = await asyncio.wait_for(client.read_event(), timeout=30)
            if event is None:
                return ""
            if Transcript.is_type(event.type):
                return (Transcript.from_event(event).text or "").strip()


# ── Recording control ──────────────────────────────────────────────────────────

def _start_recording():
    global _chunks
    if _state != IDLE:
        return
    with _lock:
        _chunks = []
    _set_state(RECORDING)
    threading.Thread(target=_beep, args=(BEEP_START_HZ,), daemon=True).start()
    _log("🎙  Recording…")
    if _mode == "hold":
        threading.Thread(target=_hold_watcher, daemon=True).start()


def _stop_recording():
    global _chunks
    if _state != RECORDING:
        return
    threading.Thread(target=_beep, args=(BEEP_STOP_HZ,), daemon=True).start()
    with _lock:
        chunks  = list(_chunks)
        _chunks = []
    threading.Thread(target=_process, args=(chunks,), daemon=True).start()


def _process(chunks: list):
    if not chunks:
        _log("⚠  Nothing recorded.")
        _set_state(IDLE)
        return
    _set_state(PROCESSING)
    _log("⏹  Transcribing…")
    audio = np.concatenate(chunks, axis=0).flatten()
    try:
        text = asyncio.run(_transcribe(audio))
    except Exception as exc:
        _log(f"✗  Transcription failed: {exc}")
        _set_state(IDLE)
        return
    if not text:
        _log("⚠  Empty transcript.")
        _set_state(IDLE)
        return
    _log(f"📝  {text!r}")
    time.sleep(PRE_TYPE_SLEEP)
    _type(text)
    _set_state(IDLE)


def _type(text: str):
    try:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", f"--delay={TYPE_DELAY_MS}", "--", text],
            check=True,
        )
    except FileNotFoundError:
        _log("✗  xdotool not found — sudo apt install xdotool")
    except subprocess.CalledProcessError as exc:
        _log(f"✗  xdotool error: {exc}")


# ── Audio callback ─────────────────────────────────────────────────────────────

def _audio_cb(indata, frames, time_info, status):
    if status:
        print(f"[audio] {status}", file=sys.stderr)
    if _state == RECORDING:
        with _lock:
            _chunks.append(indata.copy())


# ── SIGUSR1 handler — fired by whisper-type-toggle.sh via GNOME shortcut ───────

def _on_signal_toggle(signum=None, frame=None):
    global _next_toggle_at
    now = time.monotonic()
    if now < _next_toggle_at:
        return
    _next_toggle_at = now + HOTKEY_DEBOUNCE

    if _mode == "toggle":
        if _state == IDLE:
            _start_recording()
        elif _state == RECORDING:
            _stop_recording()
        # ignore while PROCESSING

    elif _mode == "hold":
        # In hold mode the GNOME shortcut fires on press only;
        # release is detected by _hold_watcher via XQueryKeymap
        _start_recording()


# ── Misc ───────────────────────────────────────────────────────────────────────

def _log(msg: str):
    print(msg, flush=True)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    global _tray

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGUSR1, _on_signal_toggle)

    _log(f"whisper-type  {WYOMING_HOST}:{WYOMING_PORT}  PID {os.getpid()}")
    _log(f"Hotkey: {_binding}  Mode: {_mode}")

    _tray = pystray.Icon("whisper-type", _make_icon(IDLE), _TOOLTIPS[IDLE], _make_menu())
    threading.Thread(target=_tray.run, daemon=True).start()

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS,
            dtype="float32", callback=_audio_cb,
        ):
            while True:
                signal.pause()
    finally:
        try:
            os.unlink(PID_FILE)
        except FileNotFoundError:
            pass
        if _tray:
            _tray.stop()


if __name__ == "__main__":
    main()
