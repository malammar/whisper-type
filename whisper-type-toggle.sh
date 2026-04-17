#!/usr/bin/env bash
# whisper-type-toggle.sh — send SIGUSR1 to the running whisper-type process
PID_FILE="${XDG_RUNTIME_DIR:-/tmp}/whisper-type.pid"
if [[ -f "$PID_FILE" ]]; then
    kill -USR1 "$(cat "$PID_FILE")"
else
    echo "whisper-type is not running (no PID file at $PID_FILE)" >&2
fi
