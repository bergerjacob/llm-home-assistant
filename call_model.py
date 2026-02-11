"""
Model calling module for LLM Home Assistant.
Contains the main orchestration logic for processing user requests,
querying the LLM, and executing the returned actions.
"""
import logging
import os
import json
import time
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.const import ATTR_ENTITY_ID

from .models.openai.call_openai import async_query_openai
from .models.openai.call_openai_audio import async_query_openai_audio
from .audio_utils import validate_audio, encode_audio_base64, normalize_format

_LOGGER = logging.getLogger(__name__)
DOMAIN = "llm_home_assistant"


def merge_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Merge actions that share the same domain, service, and data (ignoring entity_id).
    Multiple entity_ids are collapsed into a single action with a list of entity_ids.
    """
    if not actions:
        return actions

    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for action in actions:
        domain = action.get("domain", "")
        service = action.get("service", "")
        data = action.get("data") or {}
        entity_id = action.get("entity_id")

        # Build a key from everything except entity_id (strip from data too,
        # in case a malformed/fallback response put entity_id inside data).
        data_for_key = {k: v for k, v in data.items() if k != "entity_id"}
        key = f"{domain}.{service}:{json.dumps(data_for_key, sort_keys=True)}"

        if key not in groups:
            groups[key] = {
                "domain": domain,
                "service": service,
                "data": data,
                "_entity_ids": [],
            }
            order.append(key)

        # Collect entity_ids (can be str or list)
        if entity_id:
            if isinstance(entity_id, list):
                for eid in entity_id:
                    if eid not in groups[key]["_entity_ids"]:
                        groups[key]["_entity_ids"].append(eid)
            else:
                if entity_id not in groups[key]["_entity_ids"]:
                    groups[key]["_entity_ids"].append(entity_id)

    merged: list[dict[str, Any]] = []
    for key in order:
        g = groups[key]
        eids = g.pop("_entity_ids")
        if len(eids) == 1:
            g["entity_id"] = eids[0]
        elif len(eids) > 1:
            g["entity_id"] = eids
        merged.append(g)

    return merged

def _is_allowed(
    allow: dict[str, Any] | None,
    domain: str,
    service: str,
    entity_id: str | list[str] | None,
) -> bool:
    """
    Fail-closed allowlist check for safety.

    allow can contain:
      - domains: list of allowed domains (e.g. ["light", "switch"])
      - services: list of allowed "<domain>.<service>" strings
      - entities: list of allowed entity_ids

    If allow is None or empty dict, everything is allowed (no restrictions).
    If allow is provided, services MUST be explicitly listed; missing or empty
    services list denies all service calls (fail-closed).
    """
    if not allow:
        return True

    domains = allow.get("domains")
    services = allow.get("services")
    entities = allow.get("entities")

    if domains and domain not in domains:
        return False

    # Fail-closed: if allow_cfg is in use, services must be explicitly listed.
    if not services:
        _LOGGER.warning(
            "allow_cfg is active but 'services' is missing or empty — "
            "denying %s.%s (add services to allow_cfg to fix)",
            domain, service,
        )
        return False
    if f"{domain}.{service}" not in services:
        return False

    if entities and entity_id:
        # Handle both single entity (str) and multiple entities (list)
        entity_list = entity_id if isinstance(entity_id, list) else [entity_id]
        for eid in entity_list:
            if eid not in entities:
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

    # Handle entity_id (can be string or list of strings)
    if entity_id:
        if isinstance(entity_id, list):
            valid = [eid for eid in entity_id if hass.states.get(eid) is not None]
            invalid = set(entity_id) - set(valid)
            if invalid:
                _LOGGER.warning("Dropping unknown entity_ids: %s", invalid)
            if not valid:
                _LOGGER.error("No valid entity_ids remain for %s.%s", domain, service)
                return
            entity_id = valid if len(valid) > 1 else valid[0]
            data.setdefault(ATTR_ENTITY_ID, entity_id)
        else:
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


async def call_model_wrapper(
    hass: HomeAssistant,
    text: str,
    model_name: str,
    *,
    audio_data: bytes | None = None,
    audio_format: str | None = None,
):
    """
    Main wrapper to process the user request.
    1. Checks API key.
    2. Calls LLM (text path or audio-direct path).
    3. Updates sensor/events.
    4. Executes actions.
    """
    _LOGGER.info("Processing LLM request: %s (model: %s, audio=%s)", text, model_name, audio_data is not None)

    # Retrieve configuration from hass.data
    data_store = hass.data.get(DOMAIN, {})
    openai_api_key = data_store.get("openai_api_key")
    allow_cfg = data_store.get("allow_cfg")

    if not openai_api_key:
        _LOGGER.error("No OpenAI API key available; aborting OpenAI request.")
        return

    # Only process OpenAI requests through the OpenAI handler
    if model_name not in ("openai", "gpt-4o", "gpt-4o-mini", "gpt-5-mini", "gpt-4o-audio-preview"):
        _LOGGER.warning("Model '%s' is not handled by OpenAI handler, skipping", model_name)
        return

    session = async_get_clientsession(hass)
    t_start = time.monotonic()

    try:
        if audio_data is not None:
            # --- Audio-direct path ---
            fmt = normalize_format(audio_format or "wav")
            validate_audio(audio_data, fmt)
            audio_b64 = encode_audio_base64(audio_data)
            _LOGGER.info("Audio-direct path: %d bytes, format=%s", len(audio_data), fmt)

            reply = await async_query_openai_audio(
                hass=hass,
                session=session,
                api_key=openai_api_key,
                audio_b64=audio_b64,
                audio_format=fmt,
                user_text=text if text else None,
                allow_cfg=allow_cfg,
            )
        else:
            # --- Existing text path ---
            messages = [{"role": "user", "content": text}]
            reply = await async_query_openai(
                hass=hass,
                session=session,
                api_key=openai_api_key,
                messages=messages,
                allow_cfg=allow_cfg,
            )
    except Exception as exc:
        _LOGGER.exception("OpenAI call failed: %s", exc)
        # Update sensor with error
        sensor_entity = data_store.get("sensor_entity")
        if sensor_entity:
            sensor_entity.update_response(f"Error: {exc}")
        return

    # Expected: { "actions": [...], "explanation": "..." }
    raw_actions: list[dict[str, Any]] = reply.get("actions") or []
    actions = merge_actions(raw_actions)
    explanation: str = reply.get("explanation", "")

    _LOGGER.info(
        "Observability: actions_before_merge=%d, actions_after_merge=%d, elapsed=%.2fs",
        len(raw_actions), len(actions), time.monotonic() - t_start,
    )

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
