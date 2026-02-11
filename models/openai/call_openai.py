#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import os
import re
from typing import Any, Dict, Union

from openai import OpenAI
import aiohttp
from pydantic import BaseModel, Field

from homeassistant.core import HomeAssistant
from homeassistant.helpers.service import async_get_all_descriptions

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Model Configuration
# -----------------------------------------------------------------------------
OPENAI_MODEL = "gpt-5-mini"

# -----------------------------------------------------------------------------
# JSON-mode schema
# -----------------------------------------------------------------------------

class Action(BaseModel):
    domain: str
    service: str
    entity_id: Union[str, list[str]]
    data: Dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    actions: list[Action]
    explanation: str

# -----------------------------------------------------------------------------
# Hardcoded Exclusions (Masking)
# -----------------------------------------------------------------------------

EXCLUDED_SERVICE_DOMAINS = {
    "logger", "system_log", "recorder", "backup", "ffmpeg",
    "cloud", "frontend", "config", "hassio", "update", "zha",
    "persistent_notification", "llm_home_assistant", "device_tracker",
    "person", "zone", "conversation"
}

EXCLUDED_SERVICES = {
    "homeassistant.save_persistent_states",
    "homeassistant.stop",
    "homeassistant.restart",
    "homeassistant.check_config",
    "homeassistant.update_entity",
    "homeassistant.reload_core_config",
    "homeassistant.set_location",
    "homeassistant.reload_custom_templates",
    "homeassistant.reload_config_entry",
    "homeassistant.reload_all",
    "scene.reload",
}

EXCLUDED_STATE_DOMAINS = {"zone", "update", "sun", "event"}

EXCLUDED_ENTITY_PATTERNS = [
    r".*\.llm_.*",
    r".*\.backup_.*",
    r".*_identify(_[0-9]+)?$",
    r".*_firmware(_[0-9]+)?$",
    r".*_transition_time(_[0-9]+)?$",
    r".*_on_level(_[0-9]+)?$",
    r".*_start_up_.*",
    r".*_behavior(_[0-9]+)?$",
    r".*_current_level(_[0-9]+)?$",
    r".*_color_temperature(_[0-9]+)?$",
    r".*_delay_time(_[0-9]+)?$",
]

# -----------------------------------------------------------------------------
# Home Assistant Context Builders
# -----------------------------------------------------------------------------

def fetch_states(hass: HomeAssistant) -> list[Dict[str, Any]]:
    """Fetch all entity states from Home Assistant, applying masks."""
    states = []
    for state in hass.states.async_all():
        entity_id = state.entity_id
        domain = entity_id.split(".")[0]

        # 1. Domain exclusion
        if domain in EXCLUDED_STATE_DOMAINS:
            continue

        # 2. Pattern exclusion
        if any(re.match(pattern, entity_id) for pattern in EXCLUDED_ENTITY_PATTERNS):
            continue

        # 3. Specific internal states
        if entity_id == "sun.sun":
            continue

        states.append(dict(state.as_dict()))
    return states


async def fetch_services(hass: HomeAssistant) -> list[Dict[str, Any]]:
    """Fetch all available services from Home Assistant, applying masks."""
    descriptions = await async_get_all_descriptions(hass)
    result = []
    for domain, services in descriptions.items():
        if domain in EXCLUDED_SERVICE_DOMAINS:
            continue

        filtered_services = {}
        for svc_name, svc_data in services.items():
            full_svc = f"{domain}.{svc_name}"
            
            # 1. Specific service exclusion
            if full_svc in EXCLUDED_SERVICES:
                continue
            
            # 2. Pattern: exclude all reload services
            if svc_name == "reload":
                continue

            filtered_services[svc_name] = svc_data

        if filtered_services:
            result.append({
                "domain": domain,
                "services": filtered_services
            })
    return result


async def build_hass_context(hass: HomeAssistant) -> str:
    """
    Build a simple JSON snapshot of states + services for the model.
    """
    from ...device_info import fetch_entity_areas

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
# Cache Statistics Tracking
# --------------------------------------------------------------------
def _save_cache_stats(usage_info: dict[str, Any]) -> None:
    """
    Save cache hit rate statistics to a file for monitoring.
    """
    try:
        stats_path = os.path.join(os.path.dirname(__file__), "cache_stats.json")
        
        # Load existing stats if file exists
        existing_stats = []
        if os.path.exists(stats_path):
            try:
                with open(stats_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                # Handle both formats: list or {"history": [...]}
                if isinstance(raw, list):
                    existing_stats = raw
                elif isinstance(raw, dict):
                    existing_stats = raw.get("history", [])
            except Exception as e:
                _LOGGER.warning("Failed to load existing cache stats: %s", e)
        
        # Add timestamp to current stats
        current_stat = {
            "timestamp": datetime.now().isoformat(),
            "prompt_tokens": usage_info.get("prompt_tokens", 0),
            "cached_tokens": usage_info.get("cached_tokens", 0),
            "completion_tokens": usage_info.get("completion_tokens", 0),
            "total_tokens": usage_info.get("total_tokens", 0),
            "cache_hit_rate": usage_info.get("cache_hit_rate", 0.0),
        }
        
        existing_stats.append(current_stat)
        
        # Keep only last 100 entries
        if len(existing_stats) > 100:
            existing_stats = existing_stats[-100:]
        
        # Calculate overall statistics
        total_prompt_tokens = sum(s["prompt_tokens"] for s in existing_stats)
        total_cached_tokens = sum(s["cached_tokens"] for s in existing_stats)
        overall_cache_hit_rate = (
            total_cached_tokens / total_prompt_tokens * 100
            if total_prompt_tokens > 0 else 0
        )
        
        # Save to file with summary
        output = {
            "summary": {
                "total_calls": len(existing_stats),
                "total_prompt_tokens": total_prompt_tokens,
                "total_cached_tokens": total_cached_tokens,
                "overall_cache_hit_rate": round(overall_cache_hit_rate, 2),
                "last_cache_hit_rate": round(current_stat["cache_hit_rate"], 2),
            },
            "history": existing_stats,
        }
        
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        
        _LOGGER.info(
            "Cache stats - Current: %.1f%% (%d/%d tokens), Overall: %.1f%% (%d/%d tokens)",
            current_stat["cache_hit_rate"],
            current_stat["cached_tokens"],
            current_stat["prompt_tokens"],
            overall_cache_hit_rate,
            total_cached_tokens,
            total_prompt_tokens,
        )
        
    except Exception as e:
        _LOGGER.warning("Failed to save cache stats: %s", e)


# --------------------------------------------------------------------
# INTERNAL: blocking call to OpenAI (run in executor)
# --------------------------------------------------------------------
def _blocking_gpt_call(
    api_key: str | None,
    messages: list[dict[str, Any]],
    hass_context_text: str,
) -> tuple[dict[str, Any], dict[str, int] | None]:
    """
    Synchronous helper that actually calls the OpenAI Chat Completions API.
    This is run in a background thread using run_in_executor.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "No OpenAI API key provided. "
            "Set OPENAI_API_KEY env var or pass api_key into async_query_openai."
        )

    client = OpenAI(api_key=key)

    # Dynamic system prompt
    system_message_content = (
        "You control Home Assistant.\n"
        "Respond ONLY with valid JSON.\n\n"
        "The JSON MUST have this exact shape:\n"
        "{\n"
        '  "actions": [\n'
        "    {\n"
        '      "domain": "light",\n'
        '      "service": "turn_on",\n'
        '      "entity_id": ["light.room1", "light.room2"],\n'
        '      "data": {"brightness": 220}\n'
        "    }\n"
        "  ],\n"
        '  "explanation": "Short summary"\n'
        "}\n\n"
        "RULES:\n"
        "- IMPORTANT: Use specific domain services like `light.turn_on`, NOT `homeassistant.turn_on`.\n"
        "- Batch multiple targets into one action with an entity_id list when they share the same service and data.\n"
        "- entity_id can be a single string or a list of strings.\n"
        "- Max 3 actions per request. Prefer 1. Keep explanation under 15 words.\n"
        "- Use only entity_ids and services from the context below.\n\n"
        "CONTEXT KEY: e=entity_id, n=name, d=domain, s=state, b=brightness, "
        "cm=color_modes, c=supports_color, pos=position, area=room.\n\n"
        f"HOME ASSISTANT CONTEXT:\n{hass_context_text}"
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
        model=OPENAI_MODEL,
        messages=final_messages,
        response_format={"type": "json_object"},
    )

    # Extract token usage from OpenAI API response
    usage_info = None
    try:
        if response.usage:
            usage_info = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            
            # Extract cache information if available
            if hasattr(response.usage, 'prompt_tokens_details'):
                details = response.usage.prompt_tokens_details
                if details and hasattr(details, 'cached_tokens'):
                    usage_info["cached_tokens"] = details.cached_tokens
                    usage_info["cache_hit_rate"] = (
                        details.cached_tokens / response.usage.prompt_tokens * 100
                        if response.usage.prompt_tokens > 0 else 0
                    )
            
            # Save cache statistics to file
            _save_cache_stats(usage_info)
    except Exception as e:
        _LOGGER.error("Failed to extract token usage: %s", e)

    content = response.choices[0].message.content

    try:
        # Validate against our Plan schema
        plan = Plan.model_validate_json(content)
        return plan.model_dump(), usage_info
    except Exception as e:
        _LOGGER.error("Failed to parse/validate JSON from model: %s; raw content: %s", e, content)
        # Safe fallback
        return {
            "actions": [],
            "explanation": f"Failed to parse JSON from model: {e}",
        }, usage_info


# --------------------------------------------------------------------
# MAIN FUNCTION: async_query_openai
# --------------------------------------------------------------------
async def async_query_openai(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    *,
    api_key: str,
    messages: list[dict[str, Any]],
    allow_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Call OpenAI using the configured model and return a structured
    JSON object that Home Assistant can interpret.
    """
    _LOGGER.debug("Preparing OpenAI JSON-mode call (model: %s)", OPENAI_MODEL)

    # Build compact context on the event loop (uses async_all / async_render)
    try:
        from ...device_info import build_compact_context
        hass_context_text = build_compact_context(hass, allow_cfg)
        _LOGGER.info("Compact context size: %d chars", len(hass_context_text))
    except Exception as e:
        _LOGGER.error("Failed to build HA context: %s", e)
        return {
            "actions": [],
            "explanation": f"Failed to build HA context: {e}"
        }

    loop = asyncio.get_running_loop()

    try:
        data: dict[str, Any]
        usage_info: dict[str, int] | None
        data, usage_info = await loop.run_in_executor(
            None,
            _blocking_gpt_call,
            api_key,
            messages,
            hass_context_text,
        )
        
        # Log token usage in the async context where logging is more visible
        if usage_info:
            cache_info = ""
            if "cached_tokens" in usage_info:
                cache_info = f", Cached: {usage_info['cached_tokens']} tokens ({usage_info['cache_hit_rate']:.1f}%)"
            
            _LOGGER.info(
                "OpenAI API token usage - Input: %d tokens, Output: %d tokens, Total: %d tokens%s",
                usage_info["prompt_tokens"],
                usage_info["completion_tokens"],
                usage_info["total_tokens"],
                cache_info,
            )
        else:
            _LOGGER.warning("OpenAI API response missing usage information")
            
    except Exception as e:
        _LOGGER.error("OpenAI API request failed: %s", e)
        return {
            "actions": [],
            "explanation": f"Model call failed: {e}",
        }

    _LOGGER.debug("OpenAI parsed response (actions): %s", data)
    return data
