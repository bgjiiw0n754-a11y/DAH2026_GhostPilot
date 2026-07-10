#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ARDUPILOT_REPO="${ARDUPILOT_REPO:-https://github.com/ArduPilot/ardupilot.git}"
ARDUPILOT_REF="${ARDUPILOT_REF:-5152cde4046b6c0bac5de44fc5d8d0caa925f041}"
ARDUPILOT_DIR="${ARDUPILOT_DIR:-${ROOT_DIR}/.external/ardupilot}"
AP_BIN="${ARDUPILOT_DIR}/build/sitl/bin/arducopter"

echo "[prepare] ArduPilot repo : ${ARDUPILOT_REPO}"
echo "[prepare] ArduPilot ref  : ${ARDUPILOT_REF}"
echo "[prepare] ArduPilot dir  : ${ARDUPILOT_DIR}"

if [[ ! -d "$ARDUPILOT_DIR/.git" ]]; then
  mkdir -p "$(dirname "$ARDUPILOT_DIR")"
  git clone "$ARDUPILOT_REPO" "$ARDUPILOT_DIR"
fi

cd "$ARDUPILOT_DIR"

if [[ "${ARDUPILOT_ALLOW_DIRTY:-0}" != "1" && -n "$(git status --porcelain)" ]]; then
  echo "[prepare] Refusing to build; ArduPilot tree has local changes." >&2
  echo "[prepare] Clean ${ARDUPILOT_DIR}, use a fresh ARDUPILOT_DIR, or set ARDUPILOT_ALLOW_DIRTY=1." >&2
  exit 1
fi

git -c fetch.recurseSubmodules=false fetch --no-tags origin "$ARDUPILOT_REF"
git -c advice.detachedHead=false checkout --detach FETCH_HEAD
git submodule update --init --recursive

if [[ "${INSTALL_ARDUPILOT_PREREQS:-0}" == "1" ]]; then
  Tools/environment_install/install-prereqs-ubuntu.sh -y
  # shellcheck disable=SC1090
  source "${HOME}/.profile" || true
fi

./waf configure --board sitl
./waf copter

if [[ ! -x "$AP_BIN" ]]; then
  echo "[prepare] Build finished but SITL binary was not found: ${AP_BIN}" >&2
  exit 1
fi

echo "[prepare] Built ArduPilot SITL:"
echo "          commit=$(git rev-parse HEAD)"
echo "          binary=${AP_BIN}"
