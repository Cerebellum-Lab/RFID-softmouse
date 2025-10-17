#!/usr/bin/env bash
# Simple helper to invoke standalone RFID scanner (Linux/macOS)
# Usage:  ./scan.sh [--port /dev/ttyUSB0] [--baud 9600] [--list]
# Or after chmod +x scan.sh placed on PATH, just: scan.sh --list

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python >/dev/null 2>&1; then
  echo "Python not found in PATH. Activate your environment first." >&2
  exit 1
fi

exec python "$SCRIPT_DIR/scan.py" "$@"
