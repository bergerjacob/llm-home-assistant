#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from openai import OpenAI   # OpenAI official SDK
import aiohttp              # kept for HA compatibility in the function signature

_LOGGER = logging.getLogger(__name__)

# --------------------------------------------------------------------
# SYSTEM PROMPT: tell the model to output ONLY JSON actions for HA
# --------------------------------------------------------------------
SYSTEM_PROMPT = """
"You are a Home Assistant agent. Only use device entity_ids that appear in the provided context. "
"Never invent entity names. "
"Valid lights include: light.third_reality_inc_3rcb01057z. "
"If the user mentions a room or object, map it to the closest valid entity."


You receive:
- A list of devices and entities in the home.
- The current states of those entities.
- The user's natural language request.

Your job:
1. Understand the user's intent (e.g., turn on lights, set brightness, change colors,
   set scenes, change temperature, etc.).
2. Output ONLY a single JSON object using this schema:

{
  "actions": [
    {
      "domain": "string",          // e.g. "light", "switch", "scene"
      "service": "string",         // e.g. "turn_on", "turn_off"
      "entity_id": "string",       // must be a valid entity_id from the provided list
      "data": {                    // optional service data
        // key-value pairs, like brightness, rgb_color, color_temp, effect, etc.
      }
    }
  ],
  "explanation": "short human readable explanation of what you did"
}

Rules:
- ALWAYS return valid JSON that matches the schema.
- Do NOT include comments in the JSON.
- If you are not sure which entity to use, choose the closest match from the list.
- Never make up entities that are not in the list.
- If the user refers to sports teams or moods (e.g. "Oregon Ducks colors",
  "Blazers colors", "cozy mode"), choose reasonable rgb_color values.
"""


# --------------------------------------------------------------------
# INTERNAL: blocking call to OpenAI (run in executor)
# --------------------------------------------------------------------
def _blocking_gpt_call(
    api_key: str | None,
    model: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Synchronous helper that actually calls the OpenAI Chat Completions API.
    This is run in a background thread using run_in_executor.
    """

    # Prefer the explicit api_key argument; fall back to environment
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "No OpenAI API key provided. "
            "Set OPENAI_API_KEY env var or pass api_key into async_query_gpt4o_with_tools."
        )

    client = OpenAI(api_key=key)

    # Prepend our system prompt, but keep the rest of the messages intact
    final_messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *messages,
    ]

    response = client.chat.completions.create(
        model=model,
        messages=final_messages,
        temperature=0.2,
        # JSON mode: forces the model to emit a single JSON object
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        _LOGGER.error("Failed to parse JSON from model: %s; raw content: %s", e, content)
        # Safe fallback: return a 'do nothing' plan so HA doesn't crash
        return {
            "actions": [],
            "explanation": f"Failed to parse JSON from model: {e}",
        }

    # Sanity checks
    if "actions" not in data or not isinstance(data.get("actions"), list):
        _LOGGER.warning(
            "Model response missing 'actions' list. Response: %s",
            data,
        )
        data.setdefault("actions", [])

    if "explanation" not in data:
        data["explanation"] = ""

    return data


# --------------------------------------------------------------------
# MAIN FUNCTION: async_query_gpt4o_with_tools
# --------------------------------------------------------------------
async def async_query_gpt4o_with_tools(
    session: aiohttp.ClientSession,  # not used, but kept for compatibility with HA
    *,
    api_key: str,                    # now actually used if provided
    model: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Call GPT-4o (or similar) using OpenAI’s Python SDK and return a structured
    JSON object that Home Assistant can interpret.

    Expected return format:
    {
      "actions": [
        {
          "domain": "light",
          "service": "turn_on",
          "entity_id": "light.living_room",
          "data": {
            "brightness": 220,
            "rgb_color": [0, 122, 51]
          }
        }
      ],
      "explanation": "Turned on the living room lights to Oregon Ducks green."
    }

    Notes:
    - The actual API call is executed in a thread via run_in_executor so we do NOT
      block Home Assistant's event loop.
    - 'session' is unused but kept in the signature for backwards compatibility.
    """

    _LOGGER.debug("Preparing GPT-4o JSON-mode call (actions planner)")

    loop = asyncio.get_running_loop()

    try:
        data: dict[str, Any] = await loop.run_in_executor(
            None,
            _blocking_gpt_call,
            api_key,
            model,
            messages,
        )
    except Exception as e:
        _LOGGER.error("GPT-4o JSON-mode API request failed: %s", e)
        # Return safe fallback
        return {
            "actions": [],
            "explanation": f"Model call failed: {e}",
        }

    _LOGGER.debug("GPT-4o parsed response (actions): %s", data)
    return data
