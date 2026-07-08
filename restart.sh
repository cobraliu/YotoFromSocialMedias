#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PID_FILE="./app.pid"
LOG_FILE="./app.log"
PORT=8081

# Prefer conda/venv python if available, fall back to system python3
PYTHON="${HOME}/miniconda3/bin/python3"
[ -x "$PYTHON" ] || PYTHON="${HOME}/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="$(which python3)"

# Stop existing process
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Stopping PID $OLD_PID ..."
    kill "$OLD_PID"
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

# Also kill anything holding the port (in case PID file is stale)
STALE=$(lsof -ti tcp:$PORT 2>/dev/null || true)
if [ -n "$STALE" ]; then
  echo "Killing stale process on port $PORT ..."
  kill $STALE 2>/dev/null || true
  sleep 1
fi

# Start
echo "Starting YotoFromSocialMedias on port $PORT ..."
echo "Using Python: $PYTHON"
nohup "$PYTHON" yotofromsocialmedias.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

sleep 1
if kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
  echo "Started (PID $(cat $PID_FILE))  →  http://localhost:$PORT"
else
  echo "ERROR: process exited immediately. Check $LOG_FILE"
  exit 1
fi
