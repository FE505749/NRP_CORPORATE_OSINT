#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

PYTHON_BIN=""
for CANDIDATE in python3.12 python3.13 python3.11 python3; do
  if command -v "$CANDIDATE" >/dev/null 2>&1 && "$CANDIDATE" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)' >/dev/null 2>&1; then
    PYTHON_BIN="$CANDIDATE"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "No se encontro Python 3.11, 3.12 o 3.13." >&2
  exit 1
fi

"$PYTHON_BIN" "$PROJECT_DIR/scripts/setup_env.py"
echo "Listo. Ejecuta ./run_mac.command"
