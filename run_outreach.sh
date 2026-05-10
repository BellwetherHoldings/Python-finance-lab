#!/bin/bash
set -euo pipefail

DIR="/home/user/Python-finance-lab"
LOG="$DIR/outreach.log"

echo "=== $(date) ===" >> "$LOG"

cd "$DIR"
source "$DIR/.env"

python3 outreach.py --send       >> "$LOG" 2>&1
python3 outreach.py --follow-ups >> "$LOG" 2>&1

echo "Done." >> "$LOG"
