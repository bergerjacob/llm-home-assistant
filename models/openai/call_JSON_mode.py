#!/usr/bin/env python3
from __future__ import annotations

import json
import sys 
from typing import Any, Dict

from openai import OpenAI
from pydantic import BaseModel, Field

_client: OpenAI | None = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client

MOCK_HASS_CONTEXT = {
    "states": {
        "light.living_room": {
            "state": "off",
            "attributes": {
                "friendly_name": "Living Room Light",
                "supported_color_modes": ["brightness", "color_temp", "rgb"],
                "brightness": 0,
            },
        },
        "light.kitchen": {
            "state": "on",
            "attributes": {
                "friendly_name": "Kitchen Light",
                "supported_color_modes": ["brightness"],
                "brightness": 200,
            },
        },
        "climate.living_room": {
            "state": "heat",
            "attributes": {
                "friendly_name": "Living Room Thermostat",
                "current_temperature": 22,
                "temperature": 22,
            },
        },
        "switch.coffee_maker": {
            "state": "off",
            "attributes": {
                "friendly_name": "Coffee Maker",
            },
        },
        "hello_action.hello": {
            "state": "World",
            "attributes": {
                "friendly_name": "Hello Action State",
            },
        },
    }
}

class Properties(BaseModel):
    domain: str
    service: str
    entity_id: str
    data: Dict[str, Any] = Field(default_factory=dict)

class Parameters(BaseModel):
    tool: str
    parameters: list[Properties]

def plan_call_service(user_message: str) -> Parameters:
    client = get_client()
    hass_state = json.dumps(MOCK_HASS_CONTEXT)

    response = client.responses.create(
        model="gpt-4o",
        input=[
            {
                "role": "system",
                "content": (
                    "You control Home Assistant.\n"
                    "Use the hass state below and return a single JSON object with:\n"
                    '{"tool": "call_service", "parameters": ['
                    '  {"domain": ..., "service": ..., "entity_id": ..., "data": {...}},'
                    '  {"domain": ..., "service": ..., "entity_id": ..., "data": {...}},'
                    "  ...\n"
                    "]}\n\n"
                    "Each item in 'parameters' is ONE service call. "
                    "Include one item for each action the user requests.\n\n"
                    f"hass_state:\n{hass_state}"
                ),
            },
            {"role": "user", "content": user_message},
        ],
        text={"format": {"type": "json_object"}},  # JSON mode
    )

    return Parameters.model_validate(json.loads(response.output_text))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: ./call_JSON_mode.py \"your natural language request here\"",
            file=sys.stderr,
        )
        sys.exit(1)

    # Join all arguments into one prompt string
    user_prompt = " ".join(sys.argv[1:])

    params = plan_call_service(user_prompt)
    # Print JSON so your HA plugin can read it
    print(json.dumps(params.model_dump(), indent=2))