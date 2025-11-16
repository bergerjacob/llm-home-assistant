#!/usr/bin/env bash
set -euo pipefail

LOCAL_PORT="${1:-11502}"
export OLLAMA_HOST="http://127.0.0.1:${LOCAL_PORT}"

echo "[pi] Checking ${OLLAMA_HOST}…"
if curl -fsS "${OLLAMA_HOST}/api/version" | jq -r '.version' ; then
  echo "[pi] Tunnel looks healthy."
else
  echo "[pi] No response; tunnel or remote server may be down."
  exit 1
fi
