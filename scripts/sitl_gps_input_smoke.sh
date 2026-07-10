#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate

if ! command -v dronekit-sitl >/dev/null 2>&1; then
  pip install dronekit-sitl
fi

LOG="${TMPDIR:-/tmp}/dah_dronekit_sitl.log"
GPS_ENGINE="${GPS_ENGINE:-gps-input}"
SET_GPS_TYPE="${SET_GPS_TYPE:-1}"
rm -f "$LOG"

dronekit-sitl copter --home=37.5665,126.9780,20,0 >"$LOG" 2>&1 &
SITL_PID=$!

cleanup() {
  kill "$SITL_PID" 2>/dev/null || true
  wait "$SITL_PID" 2>/dev/null || true
}
trap cleanup EXIT

python - <<'PY'
import socket
import sys
import time

for _ in range(80):
    sock = socket.socket()
    sock.settimeout(0.2)
    try:
        sock.connect(("127.0.0.1", 5760))
    except OSError:
        time.sleep(0.25)
    else:
        sock.close()
        sys.exit(0)

print("TCP 5760 did not open")
sys.exit(1)
PY

ARGS=(
  --target tcp:127.0.0.1:5760
  --mode ghost-gps
  --gps-engine "$GPS_ENGINE"
  --verify
  --baseline-lat 37.5665
  --baseline-lon 126.9780
  --baseline-alt 20
  --warmup-sec 5
  --iterations 8
  --interval 0.25
  --step-m 1.0
)

if [[ "$SET_GPS_TYPE" == "1" ]]; then
  ARGS+=(--set-gps-type)
fi

python src/attack_agent.py "${ARGS[@]}"

echo
echo "--- SITL tail ---"
tail -n 40 "$LOG"
