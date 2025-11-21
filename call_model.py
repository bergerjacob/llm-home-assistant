"""
Model calling module for LLM Home Assistant.
Contains the main orchestration logic for processing user requests,
querying the LLM, and executing the returned actions.
"""
import logging
import os
import json
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.const import ATTR_ENTITY_ID

from .models.openai.call_openai import async_query_gpt4o_with_tools

_LOGGER = logging.getLogger(__name__)
DOMAIN = "llm_home_assistant"

def _is_allowed(
    allow: dict[str, Any] | None,
    domain: str,
    service: str,
    entity_id: str | None,
) -> bool:
    """
    Simple allowlist check for safety.

    allow can contain:
      - domains: list of allowed domains (e.g. ["light", "switch"])
      - services: list of allowed "<domain>.<service>" strings
      - entities: list of allowed entity_ids

    If allow is None or empty, everything is allowed.
    """
    if not allow:
        return True

    domains = allow.get("domains")
    services = allow.get("services")
    entities = allow.get("entities")

    if domains and domain not in domains:
        return False
    if services and f"{domain}.{service}" not in services:
        return False
    if entities and entity_id and entity_id not in entities:
        return False

    return True


async def _execute_tool_call(hass: HomeAssistant, action: dict[str, Any], allow_cfg: dict[str, Any] | None) -> None:
    """
    Execute a single JSON action item returned by GPT-4o.
    """
    _LOGGER.debug("Received GPT action: %s", action)

    domain = action.get("domain")
    service = action.get("service")
    entity_id = action.get("entity_id", None)
    data = action.get("data") or {}

    # Validate required fields
    if not domain or not service:
        _LOGGER.error("GPT action missing domain or service: %s", action)
        return

    # Validate entity exists (if supplied)
    if entity_id:
        if hass.states.get(entity_id) is None:
            _LOGGER.error("GPT requested unknown entity_id: %s", entity_id)
            return
        data.setdefault(ATTR_ENTITY_ID, entity_id)

    # Enforce allowlist restrictions
    if not _is_allowed(allow_cfg, domain, service, entity_id):
        _LOGGER.warning(
            "GPT action blocked by allowlist: %s.%s (%s)",
            domain,
            service,
            data,
        )
        return

    _LOGGER.info("Executing GPT action %s.%s with %s", domain, service, data)

    try:
        await hass.services.async_call(
            domain,
            service,
            data,
            blocking=True
        )
    except Exception as exc:
        _LOGGER.error(
            "Service call %s.%s failed with data %s: %s",
            domain,
            service,
            data,
            exc,
        )


async def call_model_wrapper(hass: HomeAssistant, text: str, model_name: str):
    """
    Main wrapper to process the user request.
    1. Checks API key.
    2. Calls LLM.
    3. Updates sensor/events.
    4. Executes actions.
    """
    _LOGGER.info("Processing LLM request: %s (model: %s)", text, model_name)

    # Retrieve configuration from hass.data
    data_store = hass.data.get(DOMAIN, {})
    openai_api_key = data_store.get("openai_api_key")
    allow_cfg = data_store.get("allow_cfg")
    
    if not openai_api_key:
        _LOGGER.error("No OpenAI API key available; aborting GPT request.")
        return

    # Map 'openai' to 'gpt-4o-mini' to fix 404 error if old value is selected
    actual_model = model_name
    if model_name == "openai" or model_name == "gpt-4o":
        actual_model = "gpt-4o-mini"

    messages = [{"role": "user", "content": text}]
    session = async_get_clientsession(hass)

    try:
        reply = await async_query_gpt4o_with_tools(
            hass=hass,
            session=session,
            api_key=openai_api_key,
            model=actual_model,
            messages=messages,
        )
    except Exception as exc:
        _LOGGER.exception("OpenAI tool-mode call failed: %s", exc)
        # Update sensor with error
        sensor_entity = data_store.get("sensor_entity")
        if sensor_entity:
            sensor_entity.update_response(f"Error: {exc}")
        return

    # Expected: { "actions": [...], "explanation": "..." }
    actions: list[dict[str, Any]] = reply.get("actions") or []
    explanation: str = reply.get("explanation", "")

    if explanation:
        _LOGGER.info("Assistant explanation: %s", explanation)
        
        # Update sensor
        sensor_entity = data_store.get("sensor_entity")
        if sensor_entity:
            sensor_entity.update_response(explanation)
        else:
            _LOGGER.warning("Sensor entity not found, cannot update display")
        
        # Fire event
        hass.bus.async_fire("llm_response_ready", {"payload": explanation})

    for action in actions:
        await _execute_tool_call(hass, action, allow_cfg)
