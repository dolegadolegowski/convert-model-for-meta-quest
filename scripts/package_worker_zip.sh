#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

VERSION="$(cat VERSION | tr -d ' \n\r')"
if [[ -z "$VERSION" ]]; then
  echo "[error] VERSION file is empty."
  exit 1
fi

DIST_DIR="$ROOT_DIR/dist"
mkdir -p "$DIST_DIR"

ARCHIVE_NAME="ConvertModelForMetaQuest-worker-v${VERSION}.zip"
ARCHIVE_PATH="$DIST_DIR/$ARCHIVE_NAME"

rm -f "$ARCHIVE_PATH"

zip -r "$ARCHIVE_PATH" . \
  -x ".git/*" \
  -x ".venv/*" \
  -x "worker_runtime/*" \
  -x "output/*" \
  -x "out/*" \
  -x "reports/*" \
  -x "dist/*" \
  -x ".DS_Store" \
  -x "**/.DS_Store" \
  -x "__pycache__/*" \
  -x "**/__pycache__/*" \
  -x "*.pyc" \
  -x "**/*.pyc" \
  -x ".pytest_cache/*" \
  -x "**/.pytest_cache/*" \
  -x ".codex_write_test"

echo "$ARCHIVE_PATH"
