#!/bin/zsh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "[ConvertModelForMetaQuest] Starting desktop worker..."

if [[ ! -d ".venv" ]]; then
  echo "[setup] Creating virtual environment (.venv)..."
  if ! /usr/bin/python3 -m venv .venv; then
    echo "[error] Could not create .venv"
    echo "Press Enter to close..."
    read
    exit 1
  fi
fi

if [[ ! -f ".venv/bin/activate" ]]; then
  echo "[error] Missing .venv/bin/activate"
  echo "Press Enter to close..."
  read
  exit 1
fi

source ".venv/bin/activate"

if ! python3 -c "import PySide6" >/dev/null 2>&1; then
  echo "[setup] Installing required packages: PySide6 keyring"
  python3 -m pip install --upgrade pip >/dev/null 2>&1 || true
  if ! python3 -m pip install PySide6 keyring; then
    echo "[error] Could not install PySide6/keyring"
    echo "Press Enter to close..."
    read
    exit 1
  fi
fi

if [[ $# -gt 0 ]]; then
  python3 scripts/worker_desktop_app.py "$@"
  EXIT_CODE=$?

  if [[ $EXIT_CODE -ne 0 ]]; then
    echo
    echo "[error] Worker exited with code $EXIT_CODE"
    echo "Press Enter to close..."
    read
  fi

  exit $EXIT_CODE
fi

LOG_DIR="${TMPDIR:-/tmp}"
LOG_FILE="${LOG_DIR%/}/cmq-worker-desktop.log"
nohup python3 scripts/worker_desktop_app.py >"$LOG_FILE" 2>&1 &
WORKER_PID=$!
disown "$WORKER_PID" 2>/dev/null || true
sleep 0.2

if ! kill -0 "$WORKER_PID" 2>/dev/null; then
  echo "[error] Worker failed to start in background."
  echo "Recent log output:"
  tail -n 40 "$LOG_FILE" 2>/dev/null || true
  echo "Press Enter to close..."
  read
  exit 1
fi

echo "[ok] Worker started in background (PID $WORKER_PID). Logs: $LOG_FILE"

if [[ "${TERM_PROGRAM:-}" == "Apple_Terminal" && -z "${CMQ_KEEP_TERMINAL_OPEN:-}" ]]; then
  osascript -e 'tell application "Terminal" to if (count of windows) > 0 then close front window' >/dev/null 2>&1 || true
fi

exit 0
