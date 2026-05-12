#!/usr/bin/env bash
# start.sh — Starts dashboard + scheduler for yt-pub-lives
set -euo pipefail

PORT="${1:-8090}"
DIR="$(cd "$(dirname "$0")" && pwd)"

export PATH="/usr/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/google-cloud-sdk/bin:$PATH"

# Kill previous instances
fuser -k "$PORT/tcp" 2>/dev/null || true
pkill -f "python3 $DIR/scheduler.py" 2>/dev/null || true
sleep 1

# Start scheduler in background
echo "==> Scheduler started (log: /tmp/yt-scheduler.log)"
python3 "$DIR/scheduler.py" > /tmp/yt-scheduler.log 2>&1 &
SCHED_PID=$!

# Start dashboard (foreground)
echo "==> Dashboard: http://localhost:$PORT"
echo "==> To stop: Ctrl+C"
echo ""

cleanup() {
  echo ""
  echo "==> Stopping scheduler (PID $SCHED_PID)..."
  kill $SCHED_PID 2>/dev/null || true
  echo "==> Stopped."
}
trap cleanup EXIT

cd "$DIR/dashboard" && python3 server.py "$PORT"
