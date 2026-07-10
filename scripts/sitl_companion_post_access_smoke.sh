#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARDUPILOT_DIR="${ARDUPILOT_DIR:-${ROOT_DIR}/.external/ardupilot}"
AP_BIN="${ARDUPILOT_DIR}/build/sitl/bin/arducopter"

cd "$ROOT_DIR"

if [[ ! -x "$AP_BIN" ]]; then
  echo "ArduPilot SITL binary not found. Preparing build: $AP_BIN" >&2
  ARDUPILOT_DIR="$ARDUPILOT_DIR" "${ROOT_DIR}/scripts/prepare_ardupilot_sitl.sh"
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate

REQ_HASH="$(sha256sum requirements.txt | awk '{print $1}')"
REQ_STAMP=".venv/.requirements-${REQ_HASH}"
if [[ ! -f "$REQ_STAMP" ]]; then
  python -m pip install -q -r requirements.txt
  rm -f .venv/.requirements-*
  touch "$REQ_STAMP"
fi

LOG="${TMPDIR:-/tmp}/dah_companion_post_access_sitl.log"
DEFAULTS="${TMPDIR:-/tmp}/dah_companion_mavlink_gps.parm"
PLAN="${TMPDIR:-/tmp}/dah_companion_post_access_plan.json"
PORT="${PORT:-5760}"
rm -f "$LOG" "$DEFAULTS" "$PLAN"

cat >"$DEFAULTS" <<'EOF'
GPS1_TYPE 14
GPS_TYPE 14
EOF

cat >"$PLAN" <<'EOF'
{
  "baseline_lat": 37.5665,
  "baseline_lon": 126.9780,
  "baseline_alt": 20.0,
  "iterations": 24,
  "interval": 0.25,
  "step_m": 1.0,
  "bearing_deg": 45.0,
  "gps_id": 0,
  "fix_type": 3,
  "satellites": 12,
  "hacc": 0.8,
  "vacc": 1.2,
  "sacc": 0.25,
  "warmup_sec": 10.0,
  "consistency_profile": "ekf-smooth",
  "max_accel": 0.6,
  "max_speed": 2.5
}
EOF

"$AP_BIN" \
  --model quad \
  --home=37.5665,126.9780,20,0 \
  --defaults "$DEFAULTS" \
  >"$LOG" 2>&1 &
SITL_PID=$!

cleanup() {
  kill "$SITL_PID" 2>/dev/null || true
  wait "$SITL_PID" 2>/dev/null || true
}
trap cleanup EXIT

python - <<PY
import socket
import sys
import time

port = int("${PORT}")
for _ in range(120):
    sock = socket.socket()
    sock.settimeout(0.2)
    try:
        sock.connect(("127.0.0.1", port))
    except OSError:
        time.sleep(0.25)
    else:
        sock.close()
        sys.exit(0)

print(f"TCP {port} did not open")
sys.exit(1)
PY

python src/companion_gps_bridge.py \
  --target "tcp:127.0.0.1:${PORT}" \
  --plan "$PLAN" \
  --verify

echo
echo "--- companion post-access SITL tail ---"
tail -n 80 "$LOG"
