"""
Model calling module for LLM Home Assistant.
Contains the main orchestration logic for processing user requests,
querying the LLM, and executing the returned actions.

NOTE:
- Original 1-step behavior is preserved.
- We ADD a Step 1 call to gpt-5o-nano to reduce candidate states/services.
- Step 2 uses the ORIGINAL async_query_openai call path and execution logic.
"""
import logging
import os
import json
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.const import ATTR_ENTITY_ID

from .models.openai.call_openai import async_query_openai

_LOGGER = logging.getLogger(__name__)
DOMAIN = "llm_home_assistant"


# =========================
# ORIGINAL call_model.py code
# =========================
def _is_allowed(
    allow: dict[str, Any] | None,
    domain: str,
    service: str,
    entity_id: str | list[str] | None,
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
            # Multiple entities: validate all exist
            for eid in entity_id:
                if hass.states.get(eid) is None:
                    _LOGGER.error("GPT requested unknown entity_id: %s", eid)
                    return
            data.setdefault(ATTR_ENTITY_ID, entity_id)
        else:
            # Single entity: validate exists
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


# =====================================================
# Step 1: cheaper model
# =====================================================

def _safe_dict(obj: Any) -> dict[str, Any]:
    """Handle async_query_openai returning dict or JSON string."""
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _build_ha_summary(hass: HomeAssistant, max_entities_per_domain: int = 12) -> dict[str, Any]:
    """
    Build a compact summary of HA to avoid dumping all states/services.
    Uses hass.states + hass.services only (no extra registries required).
    """
    # Services by domain
    services = hass.services.async_services() or {}
    domains = sorted(services.keys())

    # Example entities per domain (truncated)
    entities_by_domain: dict[str, list[str]] = {}
    for st in hass.states.async_all():
        dom = st.entity_id.split(".", 1)[0]
        bucket = entities_by_domain.setdefault(dom, [])
        if len(bucket) < max_entities_per_domain:
            bucket.append(st.entity_id)

    return {
        "domains_present": sorted(set(list(entities_by_domain.keys()) + domains)),
        "example_entities_by_domain": entities_by_domain,
        "example_services_by_domain": {
            d: sorted(list((services.get(d) or {}).keys()))[:20] for d in domains
        },
    }


def _build_live_entity_details(hass: HomeAssistant, entity_ids: list[str], max_attr_keys: int = 12) -> list[dict[str, Any]]:
    """
    Fetch live state details only for the reduced candidate entities.
    Keep attributes small to control token size.
    """
    out: list[dict[str, Any]] = []
    for eid in entity_ids:
        st = hass.states.get(eid)
        if st is None:
            continue
        attrs = dict(st.attributes or {})
        if len(attrs) > max_attr_keys:
            # trim deterministically
            trimmed = {}
            for k in list(attrs.keys())[:max_attr_keys]:
                trimmed[k] = attrs[k]
            attrs = trimmed
        out.append({"entity_id": eid, "state": st.state, "attributes": attrs})
    return out


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


async def _step1_reduce_candidates(
    hass: HomeAssistant,
    session,
    api_key: str,
    user_text: str,
) -> dict[str, Any]:
    """
    Step 1 (cheaper model): reduce candidate entities/services.
    Uses gpt-5o-nano.
    Output schema (strict):
      {
        "candidate_entities": [...],
        "candidate_services": [...]
      }
    """
    ha_summary = _build_ha_summary(hass)

    system_prompt = (
        "You are a cheap routing model for Home Assistant.\n"
        "Task: select a SMALL subset of relevant entity_ids and domain.services for the user command.\n"
        "Return STRICT JSON only with keys:\n"
        '{ "candidate_entities": ["domain.entity", ...], "candidate_services": ["domain.service", ...] }\n'
        "Rules:\n"
        "- Only select items that plausibly exist given the HA summary.\n"
        "- Keep lists short (<= 20 each).\n"
        "- Prefer precision but include enough to complete the task.\n"
        "- No extra text, JSON only."
    )

    payload = {"user_command": user_text, "ha_summary": ha_summary}

    # IMPORTANT: We do NOT change async_query_openai implementation.
    # We pass model hint via hass.data so the wrapper can pick it up if it supports it,
    # and also include it in the message as a fallback.
    #
    # If your async_query_openai already accepts a model from hass.data, this will work.
    # If not, it will still work as a normal call but may use default model.
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload)},
    ]

    reply = await async_query_openai(
        hass=hass,
        session=session,
        api_key=api_key,
        messages=messages,
        model="gpt-5o-nano",  # if wrapper supports it, great; if not, TypeError below will be handled
    )
    return _safe_dict(reply)


# =====================================================
# ORIGINAL ENTRYPOINT with ADDITIVE Step 1 integration
# =====================================================
async def call_model_wrapper(hass: HomeAssistant, text: str, model_name: str):
    """
    Main wrapper to process the user request.
    1. Checks API key.
    2. Calls LLM.
    3. Updates sensor/events.
    4. Executes actions.

    Now: Adds Step 1 (gpt-5o-nano) to reduce states/services before Step 2.
    """
    _LOGGER.info("Processing LLM request: %s (model: %s)", text, model_name)

    # Retrieve configuration from hass.data
    data_store = hass.data.get(DOMAIN, {})
    openai_api_key = data_store.get("openai_api_key")
    allow_cfg = data_store.get("allow_cfg")

    if not openai_api_key:
        _LOGGER.error("No OpenAI API key available; aborting OpenAI request.")
        return

    # Only process OpenAI requests through the OpenAI handler
    if model_name not in ("openai", "gpt-4o", "gpt-4o-mini", "gpt-5-mini"):
        _LOGGER.warning("Model '%s' is not handled by OpenAI handler, skipping", model_name)
        return

    session = async_get_clientsession(hass)

    # -----------------------------
    # ADDED: Step 1
    # -----------------------------
    candidate_entities: list[str] = []
    candidate_services: list[str] = []
    live_entity_details: list[dict[str, Any]] = []

    try:
        try:
            step1_reply = await _step1_reduce_candidates(
                hass=hass,
                session=session,
                api_key=openai_api_key,
                user_text=text,
            )
        except TypeError:
            # If your async_query_openai does not accept `model=`,
            # do the exact same call WITHOUT changing original OpenAI code.
            # (Still the same Step 1 prompt; just can't enforce model at wrapper-level.)
            ha_summary = _build_ha_summary(hass)
            system_prompt = (
                "You are a cheap routing model for Home Assistant.\n"
                "Task: select a SMALL subset of relevant entity_ids and domain.services for the user command.\n"
                "Return STRICT JSON only with keys:\n"
                '{ "candidate_entities": ["domain.entity", ...], "candidate_services": ["domain.service", ...] }\n'
                "Rules:\n"
                "- Only select items that plausibly exist given the HA summary.\n"
                "- Keep lists short (<= 20 each).\n"
                "- Prefer precision but include enough to complete the task.\n"
                "- No extra text, JSON only."
            )
            payload = {"user_command": text, "ha_summary": ha_summary}
            step1_reply = await async_query_openai(
                hass=hass,
                session=session,
                api_key=openai_api_key,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload)},
                ],
            )
            step1_reply = _safe_dict(step1_reply)

        candidate_entities = _dedupe(step1_reply.get("candidate_entities") or [])
        candidate_services = _dedupe(step1_reply.get("candidate_services") or [])

        # Keep only entities that actually exist
        candidate_entities = [eid for eid in candidate_entities if hass.states.get(eid) is not None]

        # Pull live details only for reduced set (latency/token win)
        if candidate_entities:
            live_entity_details = _build_live_entity_details(hass, candidate_entities)

        _LOGGER.debug(
            "Step1 candidates: %d entities, %d services",
            len(candidate_entities),
            len(candidate_services),
        )

    except Exception as exc:
        # IMPORTANT: Never break original functionality.
        # If Step 1 fails, we just continue with original 1-step flow.
        _LOGGER.warning("Step 1 routing failed; continuing with original 1-step. Error: %s", exc)

    # -----------------------------
    # ORIGINAL Step 2 call
    # -----------------------------
    if candidate_entities or candidate_services:
        # Provide reduced candidate context while preserving user message role/content
        # This keeps the original pipeline intact, but makes the prompt smaller/more focused.
        step2_payload = {
            "user_command": text,
            "candidate_entities": candidate_entities,
            "candidate_services": candidate_services,
            "live_entity_details": live_entity_details,
        }
        messages = [
            {
                "role": "user",
                "content": (
                    "Use the candidate Home Assistant entities/services below to answer and produce actions.\n"
                    "Return the normal integration JSON format.\n\n"
                    + json.dumps(step2_payload)
                ),
            }
        ]
    else:
        # Original behavior
        messages = [{"role": "user", "content": text}]

    try:
        reply = await async_query_openai(
            hass=hass,
            session=session,
            api_key=openai_api_key,
            messages=messages,
        )
    except Exception as exc:
        _LOGGER.exception("OpenAI call failed: %s", exc)
        # Update sensor with error
        sensor_entity = data_store.get("sensor_entity")
        if sensor_entity:
            sensor_entity.update_response(f"Error: {exc}")
        return

    # Expected: { "actions": [...], "explanation": "..." }
    reply = _safe_dict(reply)
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
