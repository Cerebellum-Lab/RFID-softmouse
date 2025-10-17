#!/usr/bin/env bash
# Simple helper to invoke standalone RFID scanner (Linux/macOS)
# Usage:  ./scan.sh [--port /dev/ttyUSB0] [--baud 9600] [--list]
# Or after chmod +x scan.sh placed on PATH, just: scan.sh --list

set -euo pipefail
# Resolve real path even when invoked via a symlink (so we can find scan.py)
if command -v readlink >/dev/null 2>&1; then
  REAL_PATH="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
else
  # Portable fallback using Python realpath
  REAL_PATH="$(python - <<'PY'
import os,sys
print(os.path.realpath(sys.argv[1]))
PY
"${BASH_SOURCE[0]}")"
fi
SCRIPT_DIR="$(cd "$(dirname "${REAL_PATH}")" && pwd)"

if ! command -v python >/dev/null 2>&1; then
  echo "Python not found in PATH. Activate your environment first." >&2
  exit 1
fi

# If user did not specify --port, attempt to read rfid_port from systemdata.yaml
ARGS=("$@")
PORT_ARG_PRESENT=false
for a in "${ARGS[@]}"; do
  case "$a" in
    --port|--port=*) PORT_ARG_PRESENT=true; break;;
  esac
done

if [ "$PORT_ARG_PRESENT" = false ]; then
  SYS_YAML="$SCRIPT_DIR/systemdata.yaml"
  if [ -f "$SCRIPT_DIR/acquisition/systemdata.yaml" ]; then
    SYS_YAML="$SCRIPT_DIR/acquisition/systemdata.yaml"
  fi
  if [ -f "$SYS_YAML" ]; then
    # Extract rfid_port value (simple grep/sed; robust enough for current flat structure)
    SAVED_PORT="$(grep -E '^rfid_port:' "$SYS_YAML" | sed -E 's/rfid_port:\s*"?([^"#]+)"?.*/\1/' | tr -d '\r' | xargs)" || true
    if [ -n "$SAVED_PORT" ]; then
      # Prepend --port argument
      ARGS=("--port" "$SAVED_PORT" "${ARGS[@]}")
      echo "[scan] Defaulting to saved RFID port: $SAVED_PORT" >&2
    fi
  fi
fi

exec python "$SCRIPT_DIR/scan.py" "${ARGS[@]}"
