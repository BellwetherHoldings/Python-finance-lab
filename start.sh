#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/scheduler.pid"
LOG_FILE="$DIR/outreach.log"

if [ ! -f "$DIR/.env" ]; then
    echo "ERROR: $DIR/.env not found. Copy .env.example and fill in your values."
    exit 1
fi

source "$DIR/.env"

[ -f "$DIR/venv/bin/activate" ]  && source "$DIR/venv/bin/activate"
[ -f "$DIR/.venv/bin/activate" ] && source "$DIR/.venv/bin/activate"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Scheduler is already running (PID $OLD_PID). Use stop.sh to stop it."
        exit 1
    else
        echo "Stale PID file found (PID $OLD_PID not running). Removing."
        rm -f "$PID_FILE"
    fi
fi

cd "$DIR"
nohup python3 scheduler.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Scheduler started (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
echo "Stop with: $DIR/stop.sh"
