# Development Testing Guide

This guide helps developers test the LLM Home Assistant integration against a live Home Assistant instance.

## Prerequisites

- Home Assistant instance running and accessible
- A Home Assistant Long-Lived Access Token
- OpenAI API key configured

## Obtaining a Home Assistant Access Token

### Method 1: Home Assistant UI (Recommended)

1. Go to **Settings → People**
2. Find your user profile
3. Click **Create Long-Lived Access Token**
4. Enter a name (e.g., "LLM Home Assistant Testing")
5. Copy the generated token (starts with `eyJ...`)

### Method 2: API Call

```bash
curl -X POST "https://your-ha-instance:8123/api/auth/token" \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "https://my.home-assistant.io/refresh-token",
    "grant_type": "refresh_token",
    "refresh_token": "YOUR_REFRESH_TOKEN"
  }'
```

## Quick Testing with curl

```bash
export HA_TOKEN="your_token_here"
curl -X POST "http://localhost:8123/api/services/llm_home_assistant/chat" \
  -H "Authorization: Bearer ${HA_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"text": "turn on kitchen light"}'
```

## Testing with Helper Scripts

### Using real_test.sh

```bash
export HA_TOKEN="your_token_here"
./tests/real_test.sh "turn on kitchen light"
```

### Using test_real_pipeline.py

```bash
export HA_TOKEN="your_token_here"
python3 tests/test_real_pipeline.py --prompt "turn on kitchen light"
```

## Test Scripts Overview

### test_whitelist_debug.py

Tests the whitelist filtering logic without calling the OpenAI API.

```bash
python3 tests/test_whitelist_debug.py --prompt "turn on lights" --load-allowcfg
python3 tests/test_whitelist_debug.py --prompt "turn on lights" --load-allowcfg --cfg domains:light,switch
```

**What it tests:**
- Loads `allow_cfg` from Home Assistant configuration.yaml
- Shows exactly what entities/services the LLM would see
- Verifies filtering logic

**What it does NOT do:**
- Call the OpenAI API
- Execute real actions on devices

### test_real_pipeline.py / real_test.sh

Full end-to-end pipeline test.

**What it tests:**
- Loads `allow_cfg` from configuration
- Calls the actual Home Assistant instance via API
- Executes the full `call_model_wrapper()` flow
- Calls the OpenAI API
- Executes real actions on devices

## Processing Flow

Both test methods use the same core flow:

```
user prompt
    ↓
call_model_wrapper(hass, text, model)
    ↓
Read allow_cfg from hass.data[DOMAIN]["allow_cfg"]
    ↓
build_compact_context(hass, allow_cfg)
    ↓
Apply filtering: domains, services, entities
    ↓
Build compact JSON context
    ↓
Call OpenAI API with context
    ↓
Parse LLM response
    ↓
Execute actions via hass.services.async_call()
    ↓
Update sensor state
    ↓
Log interaction
```

## Iterating on Changes

### 1. Test Filtering (Fast)

Does not require API calls or tokens:

```bash
python3 tests/test_whitelist_debug.py --prompt "turn on lights" --load-allowcfg
```

### 2. Test Full Pipeline

Requires token, but validates end-to-end behavior:

```bash
export HA_TOKEN="your_token_here"
python3 tests/test_real_pipeline.py --prompt "turn on kitchen light"
```

### 3. Verify Consistency

The filtering behavior should be identical between both test methods.
