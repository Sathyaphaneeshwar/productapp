#!/usr/bin/env bash
set -euo pipefail

# Starts backend (Flask) and frontend (Vite) together.
# Logs are written to ../logs.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/logs"

mkdir -p "$LOG_DIR"

# Resolve backend Python (supports both Unix- and Windows-style venv layouts).
BACKEND_PYTHON=""
if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  BACKEND_PYTHON="$BACKEND_DIR/.venv/bin/python"
elif [[ -x "$BACKEND_DIR/.venv/Scripts/python.exe" ]]; then
  BACKEND_PYTHON="$BACKEND_DIR/.venv/Scripts/python.exe"
else
  BACKEND_PYTHON="python"
fi

BACKEND_LOG="$LOG_DIR/backend_dev.log"
FRONTEND_LOG="$LOG_DIR/frontend_dev.log"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

start_backend() {
  echo "Starting backend with $BACKEND_PYTHON (logs: $BACKEND_LOG)"
  (
    cd "$BACKEND_DIR"
    exec "$BACKEND_PYTHON" app.py
  ) >"$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!
}

start_frontend() {
  echo "Starting frontend on port $FRONTEND_PORT (logs: $FRONTEND_LOG)"
  (
    cd "$FRONTEND_DIR"
    if [[ ! -d node_modules ]]; then
      echo "Installing frontend dependencies..."
      npm install
    fi
    exec npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT"
  ) >"$FRONTEND_LOG" 2>&1 &
  FRONTEND_PID=$!
}

cleanup() {
  echo "Stopping services..."
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" >/dev/null 2>&1 || true
}

trap cleanup EXIT

start_backend
start_frontend

echo "Both services started."
echo "Backend log:   $BACKEND_LOG"
echo "Frontend log:  $FRONTEND_LOG"
echo "Press Ctrl+C to stop."

wait
