#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys 
from typing import Any, Dict

import requests
import yaml
from openai import OpenAI
from pydantic import BaseModel, Field

_client: OpenAI | None = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client

# -----------------------------------------------------------------------------
# Load secrets.yaml
# -----------------------------------------------------------------------------
SECRETS_PATH = "/home/llm-ha/homeassistant/config/secrets.yaml"


def load_secrets() -> dict:
    try:
        with open(SECRETS_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"WARNING: Failed to load secrets.yaml: {e}", file=sys.stderr)
        return {}


_SECRETS = load_secrets()

HASS_BASE_URL = os.environ.get(
    "HASS_BASE_URL",
    _SECRETS.get("hass_base_url", "http://127.0.0.1:8123"),
).rstrip("/")

HASS_TOKEN = os.environ.get(
    "HASS_TOKEN",
    _SECRETS.get("hass_token"),
)

if HASS_TOKEN is None:
    print(
        "WARNING: HASS_TOKEN is not set in env or secrets.yaml.\n"
        "Set HASS_TOKEN or add 'hass_token' to secrets.yaml.",
        file=sys.stderr,
    )


def _ha_headers() -> Dict[str, str]:
    if not HASS_TOKEN:
        raise RuntimeError("HASS_TOKEN is not set")
    return {
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json",
    }

# -----------------------------------------------------------------------------
# Home Assistant config
# -----------------------------------------------------------------------------
def fetch_states() -> list[Dict[str, Any]]:
    """Fetch all entity states from Home Assistant HTTP API."""
    url = f"{HASS_BASE_URL}/api/states"
    resp = requests.get(url, headers=_ha_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_services() -> list[Dict[str, Any]]:
    """Fetch all available services from Home Assistant HTTP API."""
    url = f"{HASS_BASE_URL}/api/services"
    resp = requests.get(url, headers=_ha_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_entity_areas() -> Dict[str, str]:
    """Fetch entity area/room names via a Home Assistant template."""
    url = f"{HASS_BASE_URL}/api/template"
    # Iterate all states, resolve area_name, and build a JSON map: { "entity_id": "Area Name" }
    # We avoid dict.update() to prevent SecurityError. We build the JSON string manually.
    template = """
{% set ns = namespace(items=[]) %}
{% for s in states %}
  {% set a = area_name(s.entity_id) %}
  {% if a %}
    {% set ns.items = ns.items + [[s.entity_id, a]] %}
  {% endif %}
{% endfor %}
{
{% for item in ns.items %}
  {{ item[0] | to_json }}: {{ item[1] | to_json }}{% if not loop.last %},{% endif %}
{% endfor %}
}
"""

    try:
        resp = requests.post(url, headers=_ha_headers(), json={"template": template}, timeout=10)
        if resp.status_code != 200:
            print(f"WARNING: Template API error {resp.status_code}: {resp.text}", file=sys.stderr)
        resp.raise_for_status()
        # The API returns the rendered string (JSON), so we parse it
        data = json.loads(resp.text)
        print(f"DEBUG: Found {len(data)} entities with areas.", file=sys.stderr)
        return data
    except Exception as e:
        print(f"WARNING: Failed to fetch areas: {e}", file=sys.stderr)
        return {}


def build_hass_context() -> str:
    """
    Build a simple JSON snapshot of states + services for the model.
    This is *not* a schema, just context for GPT to reason over.
    """
    states = fetch_states()
    services = fetch_services()
    areas = fetch_entity_areas()

    # Enrich states with 'area' field if available
    for s in states:
        eid = s.get("entity_id")
        if eid and eid in areas:
            s["area"] = areas[eid]

    context = {
        "states": states,
        "services": services,
    }
    # Pretty-printed JSON string to embed in the system prompt
    return json.dumps(context, indent=2)

# -----------------------------------------------------------------------------
# JSON-mode schema
# -----------------------------------------------------------------------------

class Action(BaseModel):
    domain: str
    service: str
    entity_id: str
    data: Dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    actions: list[Action]
    explanation: str

# -----------------------------------------------------------------------------
# Planning function (JSON mode)
# -----------------------------------------------------------------------------
def plan_call_service(user_message: str) -> Plan:
    """
    Call GPT-5-mini in JSON mode to plan one or more Home Assistant service calls.

    Expected JSON:
    {
      "actions": [
        {
          "domain": "light",
          "service": "turn_on",
          "entity_id": "light.living_room",
          "data": { ... }
        }
      ],
      "explanation": "..."
    }
    """
    if not HASS_TOKEN:
        raise RuntimeError("HASS_TOKEN is not configured")

    client = get_client()
    hass_context_text = build_hass_context()

    completion = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You control Home Assistant.\n"
                    "Respond ONLY with valid JSON.\n\n"
                    "The JSON MUST have this exact shape:\n"
                    "{\n"
                    '  \"actions\": [\n'
                    "    {\n"
                    '      \"domain\": \"light\",\n'
                    '      \"service\": \"turn_on\",\n'
                    '      \"entity_id\": \"light.living_room\",\n'
                    '      \"data\": {\"brightness\": 220}\n'
                    "    },\n"
                    "    ... one object per requested action ...\n"
                    "  ],\n"
                    '  \"explanation\": \"Human-readable summary of what you did\"\n'
                    "}\n\n"
                    "Use only domains / services / entity_ids that exist in the "
                    "Home Assistant context below.\n\n"
                    f"HOME ASSISTANT CONTEXT (states + services):\n{hass_context_text}"
                ),
            },
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},  # JSON mode
    )

    content = completion.choices[0].message.content
    # Validate against our Plan schema
    return Plan.model_validate_json(content)

# -----------------------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------------------


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            'Usage: ./call_JSON_mode.py "your natural language request here"',
            file=sys.stderr,
        )
        sys.exit(1)

    if HASS_TOKEN is None:
        print(
            "ERROR: HASS_TOKEN is not set in env or secrets.yaml.\n"
            "Add 'hass_token' to secrets.yaml or export HASS_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(1)

    user_prompt = " ".join(sys.argv[1:])

    try:
        plan = plan_call_service(user_prompt)
    except Exception as e:
        print(f"ERROR planning service call: {e}", file=sys.stderr)
        sys.exit(1)

    # Print JSON so your HA plugin or shell tools can consume it
    print(json.dumps(plan.model_dump(), indent=2))