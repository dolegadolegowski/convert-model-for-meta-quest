#!/bin/zsh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
LOCAL_ENV_FILE=".cmq_worker.env"

print_and_wait_then_exit() {
  local message="$1"
  echo "$message"
  echo "Press Enter to close..."
  read
  exit 1
}

python_version_ok() {
  local pybin="$1"
  "$pybin" - <<'PY'
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
}

detect_supported_python() {
  local requested="${CMQ_PYTHON:-}"
  if [[ -n "$requested" ]]; then
    if command -v "$requested" >/dev/null 2>&1 && python_version_ok "$requested"; then
      command -v "$requested"
      return 0
    fi
    return 1
  fi

  local candidates=(
    "/opt/homebrew/bin/python3.13"
    "/opt/homebrew/bin/python3.12"
    "/opt/homebrew/bin/python3.11"
    "/opt/homebrew/bin/python3.10"
    "/usr/local/bin/python3.13"
    "/usr/local/bin/python3.12"
    "/usr/local/bin/python3.11"
    "/usr/local/bin/python3.10"
    "python3.13"
    "python3.12"
    "python3.11"
    "python3.10"
    "python3"
  )

  local candidate resolved
  for candidate in "${candidates[@]}"; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    resolved="$(command -v "$candidate")"
    if python_version_ok "$resolved"; then
      echo "$resolved"
      return 0
    fi
  done
  return 1
}

echo "[ConvertModelForMetaQuest] Starting desktop worker..."

if [[ -f "$LOCAL_ENV_FILE" ]]; then
  set -a
  source "$LOCAL_ENV_FILE"
  set +a
  echo "[setup] Loaded local env from $LOCAL_ENV_FILE"
fi

if [[ -z "${CMQ_CONNECTION_CODE_SECRET:-}" && -z "${WORKER_CONNECTION_CODE_SHARED_SECRET:-}" ]]; then
  echo "[info] Connection Code secret not set. Set CMQ_CONNECTION_CODE_SECRET (or legacy WORKER_CONNECTION_CODE_SHARED_SECRET) to use Connection Code tab."
fi

HOST_PYTHON="$(detect_supported_python)"
if [[ -z "$HOST_PYTHON" ]]; then
  print_and_wait_then_exit "[error] Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ not found. Install and retry. Suggested: brew install python@3.11"
fi

echo "[setup] Using Python: $HOST_PYTHON"

RECREATE_VENV=0
if [[ -d ".venv" ]]; then
  if [[ ! -x ".venv/bin/python3" ]]; then
    RECREATE_VENV=1
  elif ! python_version_ok ".venv/bin/python3"; then
    echo "[setup] Existing .venv uses unsupported Python (<${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}); recreating."
    RECREATE_VENV=1
  fi
fi

if [[ "$RECREATE_VENV" -eq 1 ]]; then
  rm -rf ".venv"
fi

if [[ ! -d ".venv" ]]; then
  echo "[setup] Creating virtual environment (.venv)..."
  if ! "$HOST_PYTHON" -m venv .venv; then
    print_and_wait_then_exit "[error] Could not create .venv"
  fi
fi

if [[ ! -f ".venv/bin/activate" ]]; then
  print_and_wait_then_exit "[error] Missing .venv/bin/activate"
fi

source ".venv/bin/activate"
VENV_PYTHON=".venv/bin/python3"

if ! "$VENV_PYTHON" -c "import PySide6" >/dev/null 2>&1; then
  echo "[setup] Installing required packages: PySide6 keyring"
  "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null 2>&1 || true
  # --no-compile avoids known py_compile crashes in some macOS/Python combinations.
  if ! "$VENV_PYTHON" -m pip install --no-compile PySide6 keyring; then
    print_and_wait_then_exit "[error] Could not install PySide6/keyring. Ensure Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ and internet access are available."
  fi
fi

if [[ $# -gt 0 ]]; then
  "$VENV_PYTHON" scripts/worker_desktop_app.py "$@"
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
nohup "$VENV_PYTHON" scripts/worker_desktop_app.py >"$LOG_FILE" 2>&1 &
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
  (
    sleep 0.7
    osascript >/dev/null 2>&1 <<'APPLESCRIPT'
tell application "Terminal"
  try
    if (count of windows) > 0 then close front window
  end try
end tell
APPLESCRIPT
  ) &
fi

exit 0
