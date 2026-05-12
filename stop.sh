#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/scheduler.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found — scheduler may not be running."
    exit 0
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Process $PID is not running. Removing stale PID file."
    rm -f "$PID_FILE"
    exit 0
fi

echo "Sending SIGTERM to scheduler (PID $PID)..."
kill -TERM "$PID"

for i in $(seq 1 10); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Scheduler stopped cleanly."
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

echo "Process did not stop in 10s — sending SIGKILL."
kill -KILL "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "Scheduler killed."
