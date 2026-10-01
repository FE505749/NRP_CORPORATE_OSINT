#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN="$PROJECT_DIR/.venv-macos/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Primero ejecuta setup_mac.sh o setup_mac.command." >&2
  exit 1
fi

cd "$PROJECT_DIR"
exec "$PYTHON_BIN" "$PROJECT_DIR/cli.py" "$@"
