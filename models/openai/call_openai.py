#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict

from openai import OpenAI
import aiohttp
from pydantic import BaseModel, Field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import template
from homeassistant.helpers.service import async_get_all_descriptions

_LOGGER = logging.getLogger(__name__)

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
# Home Assistant Context Builders
# -----------------------------------------------------------------------------

def fetch_states(hass: HomeAssistant) -> list[Dict[str, Any]]:
    """Fetch all entity states from Home Assistant."""
    # Ensure we return mutable dictionaries, as state.as_dict() might return ReadOnlyDict
    return [dict(state.as_dict()) for state in hass.states.async_all()]


async def fetch_services(hass: HomeAssistant) -> list[Dict[str, Any]]:
    """Fetch all available services from Home Assistant."""
    # async_get_all_descriptions returns { domain: { service: description } }
    descriptions = await async_get_all_descriptions(hass)
    result = []
    for domain, services in descriptions.items():
        result.append({
            "domain": domain,
            "services": services
        })
    return result


def fetch_entity_areas(hass: HomeAssistant) -> Dict[str, str]:
    """Fetch entity area/room names via a Home Assistant template."""
    template_str = """
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
        tmpl = template.Template(template_str, hass)
        rendered = tmpl.async_render(parse_result=False)
        return json.loads(rendered)
    except Exception as e:
        _LOGGER.warning("Failed to fetch areas via template: %s", e)
        return {}


async def build_hass_context(hass: HomeAssistant) -> str:
    """
    Build a simple JSON snapshot of states + services for the model.
    """
    states = fetch_states(hass)
    services = await fetch_services(hass)
    areas = fetch_entity_areas(hass)

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


# --------------------------------------------------------------------
# INTERNAL: blocking call to OpenAI (run in executor)
# --------------------------------------------------------------------
def _blocking_gpt_call(
    api_key: str | None,
    model: str,
    messages: list[dict[str, Any]],
    hass_context_text: str,
) -> dict[str, Any]:
    """
    Synchronous helper that actually calls the OpenAI Chat Completions API.
    This is run in a background thread using run_in_executor.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "No OpenAI API key provided. "
            "Set OPENAI_API_KEY env var or pass api_key into async_query_gpt4o_with_tools."
        )

    client = OpenAI(api_key=key)

    # Dynamic system prompt
    system_message_content = (
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
    )

    # Debug: Log system prompt length
    _LOGGER.debug("System prompt length: %d characters", len(system_message_content))

    # Optional: Save last prompt for inspection
    try:
        debug_path = os.path.join(os.path.dirname(__file__), "last_prompt.txt")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(system_message_content)
    except Exception as e:
        _LOGGER.warning("Failed to write last_prompt.txt: %s", e)

    final_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_message_content},
        *messages,
    ]

    response = client.chat.completions.create(
        model=model,
        messages=final_messages,
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content

    try:
        # Validate against our Plan schema
        plan = Plan.model_validate_json(content)
        return plan.model_dump()
    except Exception as e:
        _LOGGER.error("Failed to parse/validate JSON from model: %s; raw content: %s", e, content)
        # Safe fallback
        return {
            "actions": [],
            "explanation": f"Failed to parse JSON from model: {e}",
        }


# --------------------------------------------------------------------
# MAIN FUNCTION: async_query_gpt4o_with_tools
# --------------------------------------------------------------------
async def async_query_gpt4o_with_tools(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Call GPT-4o (or similar) using OpenAI’s Python SDK and return a structured
    JSON object that Home Assistant can interpret.
    """
    _LOGGER.debug("Preparing GPT-4o JSON-mode call (actions planner)")

    # Build the context dynamically (async)
    try:
        hass_context_text = await build_hass_context(hass)
    except Exception as e:
        _LOGGER.error("Failed to build HA context: %s", e)
        return {
            "actions": [],
            "explanation": f"Failed to build HA context: {e}"
        }

    loop = asyncio.get_running_loop()

    try:
        data: dict[str, Any] = await loop.run_in_executor(
            None,
            _blocking_gpt_call,
            api_key,
            model,
            messages,
            hass_context_text,
        )
    except Exception as e:
        _LOGGER.error("GPT-4o JSON-mode API request failed: %s", e)
        return {
            "actions": [],
            "explanation": f"Model call failed: {e}",
        }

    _LOGGER.debug("GPT-4o parsed response (actions): %s", data)
    return data
