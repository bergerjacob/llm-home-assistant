#!/usr/bin/env bash
set -euo pipefail

LOCAL_PORT="${1:-11502}"
echo "[pi] stopping tunnel(s) on port ${LOCAL_PORT}…"
lsof -ti :"${LOCAL_PORT}" | xargs -r kill
echo "[pi] done."