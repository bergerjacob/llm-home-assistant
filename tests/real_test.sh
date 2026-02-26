#!/bin/bash
# Real pipeline test using HA's service API
# Usage: ./tests/real_test.sh "turn on kitchen light"

PROMPT="$1"

if [ -z "$PROMPT" ]; then
    echo "Usage: ./tests/real_test.sh <prompt>"
    exit 1
fi

HA_URL="${HA_URL:-http://localhost:8123}"
TOKEN_FILE="${TOKEN_FILE:-/home/llm-ha/homeassistant/.storage/hass_token}"
AUTH_HEADER="Authorization: Bearer $(cat "$TOKEN_FILE" 2>/dev/null)"

echo "Testing prompt: $PROMPT"
echo "HA URL: $HA_URL"

# Call the LLM service
curl -s -X POST "$HA_URL/api/services/llm_home_assistant/chat" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"$PROMPT\"}" | python3 -m json.tool

echo ""
echo "=== Testing completed ==="
