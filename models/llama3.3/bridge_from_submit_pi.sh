#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-11502}"
SUBMIT_HOST="${SUBMIT_HOST:-submit-b.hpc.engr.oregonstate.edu}"
JUMP_HOST="${JUMP_HOST:-}"   # e.g. JUMP_HOST="suntharv@access.engr.oregonstate.edu"

echo "[pi] opening forward tunnel pi:${PORT} → ${SUBMIT_HOST}:25000"
if [[ -n "$JUMP_HOST" ]]; then
  ssh -tt -f -N -L "${PORT}:127.0.0.1:25000" -J "$JUMP_HOST" "suntharv@${SUBMIT_HOST}"
else
  ssh -tt -f -N -L "${PORT}:127.0.0.1:25000" "suntharv@${SUBMIT_HOST}"
fi

echo "[pi] Test with: export OLLAMA_HOST=http://127.0.0.1:${PORT} && curl -s \$OLLAMA_HOST/api/version"