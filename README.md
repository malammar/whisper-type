# whisper-type

Press a hotkey, talk, press again (or release) — your words are typed at the cursor.

Uses a [Wyoming faster-whisper](https://github.com/rhasspy/wyoming-faster-whisper)
server for speech-to-text and `xdotool` to type the result into whatever window has focus.

```
Alt+F9  →  🔴 red tray icon  + high beep  →  recording
Alt+F9  →  🟡 amber tray icon + low beep  →  transcribing
done    →  ⚫ grey tray icon              →  text typed at cursor
```

---

## Features

- **Toggle mode** — press to start, press again to stop (default)
- **Hold to talk** — hold the key while speaking, release to transcribe
- **Rebind hotkey** — change it live from the tray menu, saved to config
- **Tray icon** — shows idle / recording / transcribing state
- **Audio beeps** — distinct tones for start and stop
- Works on **any X11 desktop** — hotkey is grabbed directly, no DE config needed

---

## Requirements

| Requirement | Notes |
|---|---|
| Linux with **X11** | Wayland not yet supported (`xdotool` limitation) |
| **xdotool** | `sudo apt install xdotool` |
| **Python 3.9+** | 3.11+ recommended (avoids extra `tomli` dep) |
| **Wyoming faster-whisper** server | Running somewhere on your network |

---

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/whisper-type
cd whisper-type
bash install.sh
```

`install.sh` will:
1. Create a `.venv` and install Python dependencies
2. Write a default config to `~/.config/whisper-type/config.toml`
3. Register a `~/.config/autostart` entry so it starts on login

Then **edit your config** to point at your Wyoming server:

```bash
$EDITOR ~/.config/whisper-type/config.toml
```

```toml
[server]
host = "192.168.1.100"   # ← your Wyoming faster-whisper host or IP
port = 10300
```

Start it now (without logging out):

```bash
setsid .venv/bin/python3 whisper-type.py &
```

---

## Configuration

Config is loaded from the first location found:

1. `~/.config/whisper-type/config.toml` ← created automatically on first run
2. `config.toml` in the same directory as `whisper-type.py`
3. Built-in defaults

Full reference:

```toml
[server]
host = "127.0.0.1"   # Wyoming faster-whisper hostname or IP
port = 10300

[audio]
sample_rate   = 16000   # Hz — must match server expectation
channels      = 1
chunk_samples = 4096    # PCM frames per Wyoming AudioChunk message

[typing]
delay_ms       = 8      # ms between keystrokes (raise if keys are dropped)
pre_type_sleep = 0.15   # seconds before typing so window focus returns

[hotkey]
binding  = "Alt+F9"   # hotkey combo — also changeable from the tray menu
mode     = "toggle"   # "toggle" (press/press) or "hold" (hold down to record)
debounce = 0.4        # seconds to ignore re-triggers after a toggle

[beep]
start_hz = 880    # pitch when recording starts
stop_hz  = 440    # pitch when recording stops
duration = 0.12
volume   = 0.35   # 0.0–1.0
```

### Changing the hotkey

Either edit `config.toml` and restart, or use the tray icon:

1. Right-click the tray icon → **Change hotkey…**
2. A notification appears: *"Press your new hotkey combo…"*
3. Press the desired combo — it's applied immediately and saved to config

Supported modifiers: `Alt`, `Ctrl`, `Shift`, `Super`

---

## Tray menu

```
Whisper Type           (label)
───────────────────
● Toggle mode
○ Hold to talk
───────────────────
Hotkey: Alt+F9         (current binding, read-only)
Change hotkey…
───────────────────
Quit
```

---

## How it works

```
whisper-type.py starts
  └─▶ Registers a GNOME custom shortcut → whisper-type-toggle.sh
        └─ On keypress: shell script sends SIGUSR1 to whisper-type.py
              ├─ Toggle mode:  SIGUSR1 #1 → start recording
              │                SIGUSR1 #2 → stop  recording
              └─ Hold mode:    SIGUSR1 → start recording
                               XQueryKeymap polling → detects key release → stop

On stop:
  sounddevice PCM chunks → concatenated → sent to Wyoming server via TCP
  Wyoming: Transcribe → AudioChunk… → AudioStop → Transcript
  xdotool types the transcript into the focused window
```

The hotkey is registered as a **GNOME custom shortcut** (via `gsettings`) that
runs `whisper-type-toggle.sh`, which sends `SIGUSR1` to the daemon process.
In **hold mode**, key-release is detected by passively polling `XQueryKeymap`
(no key interception) so the trigger is still GNOME-side.

---

## Wyoming faster-whisper server

If you don't have one running yet:

```bash
# Docker
docker run -it -p 10300:10300 rhasspy/wyoming-faster-whisper \
  --model small-int8 --language en

# pip
pip install wyoming-faster-whisper
wyoming-faster-whisper --uri tcp://0.0.0.0:10300 --model small-int8
```

---

## Troubleshooting

**No tray icon**
Install the AppIndicator library:
```bash
sudo apt install gir1.2-ayatanaappindicator3-0.1
# or
sudo apt install gir1.2-appindicator3-0.1
```

**Hotkey doesn't work after changing it**
The new binding is applied immediately. If it stops working, check that no
other app has already grabbed the same combo (e.g. your DE's own shortcuts).

**Text is typed in the wrong window**
Increase `pre_type_sleep` (e.g. `0.3`).

**Keys are dropped or garbled**
Increase `delay_ms` (e.g. `20`). Some apps need a slower typing speed.

**Transcription is slow**
Model quality/size is set server-side. Use `tiny-int8` for speed, or run
the server on a machine with a GPU.
