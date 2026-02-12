"""
Model calling module for LLM Home Assistant.
Contains the main orchestration logic for processing user requests,
querying the LLM, and executing the returned actions.
"""
import asyncio
import hashlib
import logging
import os
import json
import time
from collections import OrderedDict
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.const import ATTR_ENTITY_ID

from .models.openai.call_openai import async_query_openai
from .models.openai.call_openai_audio import async_query_openai_audio
from .audio_utils import validate_audio, encode_audio_base64, normalize_format
from .device_info import _is_state_query, _cfg_hash, _last_cache_hit
from .interaction_logger import new_log_entry, write_log_entry

_LOGGER = logging.getLogger(__name__)
DOMAIN = "llm_home_assistant"

# ---------------------------------------------------------------------------
# Response cache (Task 5) — text path only
# ---------------------------------------------------------------------------
_RESPONSE_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_CACHE_TTL = 60.0
_CACHE_MAX = 50

_CACHEABLE_SERVICES = frozenset({
    "light.turn_on", "light.turn_off",
    "switch.turn_on", "switch.turn_off",
    "cover.open_cover", "cover.close_cover", "cover.set_cover_position",
})


def _cache_key(text: str, model_name: str, cfg_hash: str) -> str:
    """Build a short SHA-256 cache key."""
    raw = text.strip().lower() + (model_name or "") + cfg_hash
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_get(key: str) -> dict[str, Any] | None:
    """Return cached response if fresh, else None."""
    entry = _RESPONSE_CACHE.get(key)
    if entry is None:
        return None
    ts, data = entry
    if time.monotonic() - ts > _CACHE_TTL:
        _RESPONSE_CACHE.pop(key, None)
        return None
    # Move to end (most recently used)
    _RESPONSE_CACHE.move_to_end(key)
    return data


def _cache_put(key: str, data: dict[str, Any]) -> None:
    """Store response in cache, evicting oldest if full."""
    _RESPONSE_CACHE[key] = (time.monotonic(), data)
    _RESPONSE_CACHE.move_to_end(key)
    while len(_RESPONSE_CACHE) > _CACHE_MAX:
        _RESPONSE_CACHE.popitem(last=False)


def _all_cacheable(actions: list[dict[str, Any]]) -> bool:
    """Return True only if every action's service is in _CACHEABLE_SERVICES."""
    for a in actions:
        svc = f"{a.get('domain', '')}.{a.get('service', '')}"
        if svc not in _CACHEABLE_SERVICES:
            return False
    return True


# ---------------------------------------------------------------------------
# Parallel action execution (Task 4)
# ---------------------------------------------------------------------------
def _build_action_groups(actions: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """
    Group actions by entity overlap for parallel execution.
    Actions with disjoint entity sets go in the same group (parallel).
    Actions with overlapping entities go in different groups (sequential).
    Actions with no entity_id get their own group.
    """
    groups: list[tuple[set[str], list[dict[str, Any]]]] = []

    for action in actions:
        eid = action.get("entity_id")
        if not eid:
            # No entity_id → own group
            groups.append((set(), [action]))
            continue

        entity_set = set(eid) if isinstance(eid, list) else {eid}

        # Find a group with disjoint entities
        placed = False
        for group_entities, group_actions in groups:
            if group_entities and not group_entities & entity_set:
                group_entities.update(entity_set)
                group_actions.append(action)
                placed = True
                break

        if not placed:
            groups.append((entity_set, [action]))

    return [g[1] for g in groups]


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


async def _execute_tool_call(hass: HomeAssistant, action: dict[str, Any], allow_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """
    Execute a single JSON action item returned by GPT-4o.
    Returns a result dict for the interaction log.
    """
    _LOGGER.debug("Received GPT action: %s", action)

    domain = action.get("domain")
    service = action.get("service")
    entity_id = action.get("entity_id", None)
    data = action.get("data") or {}
    t0 = time.monotonic()

    result: dict[str, Any] = {
        "domain": domain,
        "service": service,
        "entity_id": entity_id,
        "data": {k: v for k, v in data.items() if k != ATTR_ENTITY_ID},
        "allowed": False,
        "valid_entities": [],
        "dropped_entities": [],
        "success": False,
        "error": None,
    }

    # Validate required fields
    if not domain or not service:
        _LOGGER.error("GPT action missing domain or service: %s", action)
        result["error"] = "missing domain or service"
        result["execution_time"] = round(time.monotonic() - t0, 4)
        return result

    # Handle entity_id (can be string or list of strings)
    dropped: list[str] = []
    if entity_id:
        if isinstance(entity_id, list):
            valid = [eid for eid in entity_id if hass.states.get(eid) is not None]
            invalid = set(entity_id) - set(valid)
            dropped = list(invalid)
            if invalid:
                _LOGGER.warning("Dropping unknown entity_ids: %s", invalid)
            if not valid:
                _LOGGER.error("No valid entity_ids remain for %s.%s", domain, service)
                result["dropped_entities"] = dropped
                result["error"] = "no valid entity_ids"
                result["execution_time"] = round(time.monotonic() - t0, 4)
                return result
            entity_id = valid if len(valid) > 1 else valid[0]
            data.setdefault(ATTR_ENTITY_ID, entity_id)
            result["valid_entities"] = valid
            result["dropped_entities"] = dropped
        else:
            if hass.states.get(entity_id) is None:
                _LOGGER.error("GPT requested unknown entity_id: %s", entity_id)
                result["dropped_entities"] = [entity_id]
                result["error"] = f"unknown entity_id: {entity_id}"
                result["execution_time"] = round(time.monotonic() - t0, 4)
                return result
            data.setdefault(ATTR_ENTITY_ID, entity_id)
            result["valid_entities"] = [entity_id]
    result["entity_id"] = entity_id

    # Enforce allowlist restrictions
    if not _is_allowed(allow_cfg, domain, service, entity_id):
        _LOGGER.warning(
            "GPT action blocked by allowlist: %s.%s (%s)",
            domain,
            service,
            data,
        )
        result["error"] = "blocked by allowlist"
        result["execution_time"] = round(time.monotonic() - t0, 4)
        return result

    result["allowed"] = True
    _LOGGER.info("Executing GPT action %s.%s with %s", domain, service, data)

    try:
        await hass.services.async_call(
            domain,
            service,
            data,
            blocking=True
        )
        result["success"] = True
    except Exception as exc:
        _LOGGER.error(
            "Service call %s.%s failed with data %s: %s",
            domain,
            service,
            data,
            exc,
        )
        result["error"] = str(exc)

    result["execution_time"] = round(time.monotonic() - t0, 4)
    return result


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
    2. Calls LLM (text path or audio-direct path), with response cache for text.
    3. Updates sensor/events.
    4. Executes actions (parallel where possible).
    5. Writes interaction log entry.
    """
    _LOGGER.info("Processing LLM request: %s (model: %s, audio=%s)", text, model_name, audio_data is not None)

    log = new_log_entry()
    log["request"] = {
        "type": "audio" if audio_data is not None else "text",
        "user_prompt": text,
        "model_requested": model_name,
        "audio_format": audio_format if audio_data is not None else None,
    }

    # Retrieve configuration from hass.data
    data_store = hass.data.get(DOMAIN, {})
    openai_api_key = data_store.get("openai_api_key")
    allow_cfg = data_store.get("allow_cfg")

    if not openai_api_key:
        _LOGGER.error("No OpenAI API key available; aborting OpenAI request.")
        log["error"] = "no API key"
        await hass.async_add_executor_job(write_log_entry, log)
        return

    # Only process OpenAI requests through the OpenAI handler
    if model_name not in ("openai", "gpt-4o", "gpt-4o-mini", "gpt-5-mini", "gpt-4o-audio-preview"):
        _LOGGER.warning("Model '%s' is not handled by OpenAI handler, skipping", model_name)
        log["error"] = f"unsupported model: {model_name}"
        await hass.async_add_executor_job(write_log_entry, log)
        return

    # "openai" is a handler type, not a real model name — pass None so
    # the OpenAI callers use their hardcoded default (OPENAI_MODEL).
    if model_name == "openai":
        model_name = None

    session = async_get_clientsession(hass)
    t_start = time.monotonic()

    # Detect state queries → force context rebuild for fresh data
    force_rebuild = _is_state_query(text) if text else False
    state_query_detected = force_rebuild

    response_cache_hit = False

    try:
        if audio_data is not None:
            # --- Audio-direct path (no response cache) ---
            fmt = normalize_format(audio_format or "wav")
            validate_audio(audio_data, fmt)
            audio_b64 = encode_audio_base64(audio_data)
            _LOGGER.info("Audio-direct path: %d bytes, format=%s", len(audio_data), fmt)

            # For audio, detect state query from user_text if present
            audio_force = _is_state_query(text) if text else False

            reply = await async_query_openai_audio(
                hass=hass,
                session=session,
                api_key=openai_api_key,
                audio_b64=audio_b64,
                audio_format=fmt,
                user_text=text if text else None,
                allow_cfg=allow_cfg,
                model_name=model_name,
                force_rebuild=audio_force,
            )
        else:
            # --- Text path with response cache ---
            cfg_h = _cfg_hash(allow_cfg)
            c_key = _cache_key(text, model_name, cfg_h)

            cached = _cache_get(c_key)
            if cached is not None:
                _LOGGER.info("Response cache HIT key=%s", c_key)
                reply = cached
                response_cache_hit = True
            else:
                _LOGGER.info("Response cache MISS key=%s", c_key)
                messages = [{"role": "user", "content": text}]
                reply = await async_query_openai(
                    hass=hass,
                    session=session,
                    api_key=openai_api_key,
                    messages=messages,
                    allow_cfg=allow_cfg,
                    model_name=model_name,
                    force_rebuild=force_rebuild,
                )

                # Cache only if actions exist and all are cacheable
                raw_acts = reply.get("actions") or []
                if raw_acts and _all_cacheable(raw_acts):
                    _cache_put(c_key, reply)
                    _LOGGER.info("Response cache STORE key=%s", c_key)
                else:
                    reason = "no_actions" if not raw_acts else "uncacheable_service"
                    _LOGGER.info("Response cache SKIP reason=%s", reason)

    except Exception as exc:
        _LOGGER.exception("OpenAI call failed: %s", exc)
        # Update sensor with error
        sensor_entity = data_store.get("sensor_entity")
        if sensor_entity:
            sensor_entity.update_response(f"Error: {exc}")
        log["error"] = str(exc)
        log["timing"]["total_elapsed"] = round(time.monotonic() - t_start, 4)
        await hass.async_add_executor_job(write_log_entry, log)
        return

    # --- Extract debug info attached by the OpenAI callers ---
    debug_info = reply.pop("_debug_info", {})
    hass_key = id(hass)
    context_cache_hit = _last_cache_hit.get(hass_key, False)

    log["context"] = {
        "allowlist_config": allow_cfg,
        "force_rebuild": force_rebuild,
        "state_query_detected": state_query_detected,
        "context_cache_hit": context_cache_hit,
        "context_size_chars": debug_info.get("context_size_chars"),
        "context_build_time": debug_info.get("context_build_time"),
        "compact_context_packet": debug_info.get("compact_context_packet"),
    }

    log["llm_call"] = {
        "system_prompt": debug_info.get("system_prompt"),
        "model_used": debug_info.get("model_used"),
        "response_cache_hit": response_cache_hit,
        "raw_response": debug_info.get("raw_response"),
        "parse_success": debug_info.get("parse_success"),
        "pydantic_valid": debug_info.get("pydantic_valid"),
        "api_call_time": debug_info.get("api_call_time"),
        "token_usage": debug_info.get("token_usage"),
    }

    # Expected: { "actions": [...], "explanation": "..." }
    raw_actions: list[dict[str, Any]] = reply.get("actions") or []
    actions = merge_actions(raw_actions)
    explanation: str = reply.get("explanation", "")

    action_groups = _build_action_groups(actions)

    log["actions"] = {
        "raw_actions": raw_actions,
        "merged_actions": actions,
        "raw_count": len(raw_actions),
        "merged_count": len(actions),
        "explanation": explanation,
        "group_count": len(action_groups),
    }

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

    # --- Parallel action execution (Task 4) ---
    group_sizes = [len(g) for g in action_groups]
    _LOGGER.info("Executing %d action groups; group sizes: %s", len(action_groups), group_sizes)

    all_exec_results: list[dict[str, Any]] = []
    t_exec_start = time.monotonic()

    for group in action_groups:
        if len(group) == 1:
            r = await _execute_tool_call(hass, group[0], allow_cfg)
            all_exec_results.append(r)
        else:
            results = await asyncio.gather(
                *(_execute_tool_call(hass, a, allow_cfg) for a in group)
            )
            all_exec_results.extend(results)

    action_execution_time = round(time.monotonic() - t_exec_start, 4)

    log["execution"] = all_exec_results
    log["timing"] = {
        "total_elapsed": round(time.monotonic() - t_start, 4),
        "context_build_time": debug_info.get("context_build_time"),
        "llm_api_call_time": debug_info.get("api_call_time"),
        "action_execution_time": action_execution_time,
    }

    await hass.async_add_executor_job(write_log_entry, log)
