#!/usr/bin/env python3
"""
Standalone real pipeline test.
Actually calls OpenAI API and executes actions via HA service.

Usage: python3 tests/test_real_pipeline.py --prompt "turn on kitchen light"
"""

import sys
import os
import json
import argparse

# Set up paths
_component_dir = "/home/llm-ha/homeassistant/custom_components/llm_home_assistant"
_ha_config_dir = "/home/llm-ha/homeassistant"
sys.path.insert(0, _component_dir)

# Load config
def load_config():
    """Load HA config and API key."""
    import re
    
    config_path = os.path.join(_ha_config_dir, "configuration.yaml")
    
    with open(config_path, 'r') as f:
        content = f.read()
    
    match = re.search(r'llm_home_assistant:\s*\n((?:  .*$\n)*)', content, re.MULTILINE)
    if not match:
        print("Error: llm_home_assistant section not found in configuration.yaml")
        return None, None
    
    llm_section = match.group(1)
    
    # Parse allow_cfg
    allow_cfg = {"domains": [], "services": [], "entities": []}
    
    domains_m = re.search(r'domains:\s*\[([^\]]*)\]', llm_section)
    if domains_m:
        allow_cfg["domains"] = [d.strip().strip('\"').strip("\"") 
                               for d in domains_m.group(1).split(",") if d.strip()]
    
    services_m = re.search(r'services:\s*\n((?:      - .*$\n)*)', llm_section, re.MULTILINE)
    if services_m:
        allow_cfg["services"] = [s.strip().strip("- ").strip('\"').strip("\"") 
                                for s in services_m.group(1).strip().split("\n") if s.strip()]
    
    entities_m = re.search(r'entities:\s*\[([^\]]*)\]', llm_section)
    if entities_m:
        allow_cfg["entities"] = [e.strip().strip('\"').strip("\"") 
                                for e in entities_m.group(1).split(",") if e.strip()]
    
    # Parse API key
    api_key_match = re.search(r'openai_api_key:\s*(!secret |)(.*)$', llm_section, re.MULTILINE)
    openai_api_key = None
    if api_key_match:
        secret_marker = api_key_match.group(1)
        key = api_key_match.group(2).strip()
        if secret_marker:
            # Read from secrets.yaml
            secrets_path = os.path.join(_ha_config_dir, "secrets.yaml")
            if os.path.exists(secrets_path):
                with open(secrets_path, 'r') as f:
                    secrets = f.read()
                secret_match = re.search(rf'openai_api_key:\s*(.+)$', secrets, re.MULTILINE)
                if secret_match:
                    openai_api_key = secret_match.group(1).strip()
        else:
            openai_api_key = key
    
    print(f"Loaded from HA config:")
    print(f"  Domains: {allow_cfg['domains']}")
    print(f"  Services: {len(allow_cfg['services'])} entries")
    print(f"  API Key: {'present' if openai_api_key else 'MISSING'}")
    
    return allow_cfg, openai_api_key

# Call HA service API
def call_ha_service(text, model, openai_api_key, allow_cfg):
    """Call the HA service via HTTP API."""
    import requests
    import time
    
    ha_url = "http://localhost:8123"
    
    # Try to get auth token from file
    token_file = os.path.join(_ha_config_dir, ".storage", "hass_token")
    if os.path.exists(token_file):
        with open(token_file, 'r') as f:
            token = f.read().strip()
    else:
        # Try environment variable
        token = os.environ.get("HA_TOKEN", "")
    
    if not token:
        print("Warning: No HA token found. Service calling may fail.")
        print("Set HA_TOKEN environment variable or create .storage/hass_token")
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Call the chat service
    url = f"{ha_url}/api/services/llm_home_assistant/chat"
    payload = {
        "text": text,
        "model": model if model else "openai"
    }
    
    print(f"\nCalling HA service: {url}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    start = time.time()
    try:
        response = requests.post(url, headers=headers, json=payload)
        elapsed = time.time() - start
        
        print(f"\nResponse ({elapsed:.2f}s):")
        print(f"  Status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(json.dumps(result, indent=2))
        else:
            print(f"  Error: {response.text}")
        
        return response.status_code == 200
    except Exception as e:
        elapsed = time.time() - start
        print(f"  Error after {elapsed:.2f}s: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Real pipeline test")
    parser.add_argument("--prompt", "-p", type=str, required=True, help="User prompt")
    parser.add_argument("--model", type=str, default="openai", help="Model name")
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("  REAL PIPELINE TEST")
    print("=" * 80)
    print(f"Prompt: {args.prompt}")
    print(f"Model: {args.model}")
    
    # Load config
    allow_cfg, openai_api_key = load_config()
    
    if not openai_api_key:
        print("\n❌ Error: No OpenAI API key found!")
        print("Add openai_api_key to configuration.yaml or set OPENAI_API_KEY env var")
        sys.exit(1)
    
    # Call HA service
    success = call_ha_service(args.prompt, args.model, openai_api_key, allow_cfg)
    
    if success:
        print("\n✅ Service call successful!")
    else:
        print("\n❌ Service call failed!")
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
