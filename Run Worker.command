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

python3 scripts/worker_desktop_app.py "$@"
EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
  echo
  echo "[error] Worker exited with code $EXIT_CODE"
  echo "Press Enter to close..."
  read
fi

exit $EXIT_CODE
