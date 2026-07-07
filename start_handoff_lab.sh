#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
HOST="${HANDOFF_LAB_HOST:-127.0.0.1}"
PORT="${HANDOFF_LAB_PORT:-51514}"

cd "$ROOT"
echo "[Handoff Lab] Starting service at http://$HOST:$PORT/qa-viewer"
python3 server.py
