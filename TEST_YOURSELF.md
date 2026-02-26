# How to Test the Real Pipeline

## Prerequisites
- Home Assistant must be running
- You need an HA Admin token

## Quick Start - Run Real Tests with Service API

```bash
# Option 1: Use curl directly (if you have a token)
curl -X POST "http://localhost:8123/api/services/llm_home_assistant/chat" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"text": "turn on kitchen light"}'

# Option 2: Use our helper script (set HA_TOKEN first)
export HA_TOKEN="YOUR_TOKEN_HERE"
./tests/real_test.sh "turn on kitchen light"

# Option 3: Use test_real_pipeline.py (set HA_TOKEN)
export HA_TOKEN="YOUR_TOKEN_HERE"
python3 tests/test_real_pipeline.py --prompt "turn on kitchen light"
```

## How to Get an HA Admin Token

### Method 1: Developer Tools (Easiest)
1. Go to HA UI → Settings → People
2. Find your user → Click "Create Long-Lived Access Token"
3. Copy the token (starts with `eyJ...`)

### Method 2: via API (if logged in)
```
POST /api/auth/token
Content-Type: application/json

{
  "client_id": "https://my.home-assistant.io/refresh-token",
  "grant_type": "refresh_token",
  "refresh_token": "YOUR_REFRESH_TOKEN"
}
```

## What These Tests Do

### test_whitelist_debug.py (Filtering Only)
- ✅ Loads `allow_cfg` from HA configuration.yaml
- ✅ Tests the exact same filtering logic as real HA
- ✅ Shows exactly what entities/services the LLM sees
- ❌ Does NOT call OpenAI API
- ❌ Does NOT execute real actions

### test_real_pipeline.py / real_test.sh (Full Pipeline)
- ✅ Loads `allow_cfg` from HA configuration.yaml
- ✅ Calls your actual HA instance via API
- ✅ Runs the full `call_model_wrapper()` flow
- ✅ Calls OpenAI API
- ✅ Executes real actions on your devices

## Iteration Workflow

1. **Filtering Testing** (fast):
   ```bash
   python3 tests/test_whitelist_debug.py --prompt "turn on lights" --load-allowcfg
   python3 tests/test_whitelist_debug.py --prompt "turn on lights" --load-allowcfg --cfg domains:light,switch
   ```

2. **Full Pipeline Testing** (requires token):
   ```bash
   export HA_TOKEN="your_token_here"
   python3 tests/test_real_pipeline.py --prompt "turn on kitchen light"
   ```

3. **Compare** - The filtering should be identical between both methods!

## The Flow (Identical in Both Methods)

```
user prompt
    ↓
call_model_wrapper(hass, text, model)
    ↓  
Read allow_cfg from hass.data[DOMAIN]["allow_cfg"]
    ↓
call build_compact_context(hass, allow_cfg)
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

Both test methods use this exact same flow!
