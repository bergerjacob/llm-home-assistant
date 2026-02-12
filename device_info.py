"""
Module to gather device states and available services from Home Assistant.
Similar to what Paul from The Home Assistant library does.
"""
import json
import logging
import re
import time
from typing import Dict, List, Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import template
import voluptuous as vol
from voluptuous.schema_builder import Marker

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State-query detection (Task 6)
# ---------------------------------------------------------------------------
_STATE_QUERY_RE = re.compile(
    r"\b(what is|what are|what's|status|is the|are the|check|tell me|how is|how are|current|state of)\b",
    re.IGNORECASE,
)


def _is_state_query(text: str) -> bool:
    """Return True if the user text looks like a state/status query."""
    return bool(_STATE_QUERY_RE.search(text))


# ---------------------------------------------------------------------------
# TTL cache for compact context (30-second), keyed per hass instance
# ---------------------------------------------------------------------------
_CONTEXT_TTL = 30.0
_compact_caches: dict[int, dict[str, Any]] = {}

# Exclusion patterns (mirrored from call_openai.py to avoid circular import)
_EXCLUDED_STATE_DOMAINS = {"zone", "update", "sun", "event"}
_EXCLUDED_ENTITY_PATTERNS = [
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
_COMPILED_PATTERNS = [re.compile(p) for p in _EXCLUDED_ENTITY_PATTERNS]


def fetch_entity_areas(hass: HomeAssistant) -> dict[str, str]:
    """Fetch entity area/room names via a Home Assistant template.

    Must be called from the event loop (uses async_render).
    """
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


def _cfg_hash(allow_cfg: dict | None) -> str:
    """Quick hash of allow_cfg for cache invalidation.

    Uses default=str so non-JSON-serializable values (sets, custom objects)
    don't crash the hash — they just get a str() representation.
    """
    if not allow_cfg:
        return ""
    return json.dumps(allow_cfg, sort_keys=True, default=str)


def _entity_to_compact(entity_id: str, state, attrs: dict, area: str | None) -> dict:
    """Build a compact dict for a single entity."""
    domain = entity_id.split(".")[0]
    c: dict[str, Any] = {
        "e": entity_id,
        "n": attrs.get("friendly_name", entity_id),
        "d": domain,
        "s": state,
    }

    if domain == "light":
        c["b"] = attrs.get("brightness")
        cm = attrs.get("supported_color_modes", [])
        if cm:
            c["cm"] = cm
        if "color" in (cm or []) or "hs" in (cm or []) or "rgb" in (cm or []) or "xy" in (cm or []):
            c["c"] = 1
    elif domain == "cover":
        c["pos"] = attrs.get("current_position")
    elif domain == "climate":
        c["mode"] = attrs.get("hvac_mode")
        c["cur_t"] = attrs.get("current_temperature")
        c["tgt_t"] = attrs.get("temperature")
    elif domain == "binary_sensor":
        dc = attrs.get("device_class")
        if dc:
            c["dc"] = dc

    if area:
        c["area"] = area

    return c


def build_compact_context(
    hass: HomeAssistant,
    allow_cfg: dict | None,
    force_rebuild: bool = False,
) -> str:
    """
    Build a compact JSON context of entities + allowed services.
    Uses a 30-second TTL cache keyed per hass instance.
    Pass force_rebuild=True to bypass cache (e.g. for state queries).
    MUST be called from the event loop.
    """
    hass_key = id(hass)
    cache = _compact_caches.get(hass_key, {"data": "", "ts": 0.0, "cfg_hash": ""})

    cfg_h = _cfg_hash(allow_cfg)
    now = time.monotonic()
    if (
        not force_rebuild
        and cache["data"]
        and (now - cache["ts"] < _CONTEXT_TTL)
        and cache["cfg_hash"] == cfg_h
    ):
        _LOGGER.debug("Compact context cache hit (age=%.2fs)", now - cache["ts"])
        return cache["data"]

    if force_rebuild:
        _LOGGER.debug("Compact context force rebuild requested")

    allow_cfg = allow_cfg or {}
    allowed_domains = set(allow_cfg.get("domains") or [])
    allowed_entities = set(allow_cfg.get("entities") or [])
    allowed_services_list = allow_cfg.get("services") or []

    areas = fetch_entity_areas(hass)

    entities: list[dict] = []
    for s in hass.states.async_all():
        eid = s.entity_id
        domain = eid.split(".")[0]

        # Domain filter from allowlist
        if allowed_domains and domain not in allowed_domains:
            continue
        # Entity filter from allowlist
        if allowed_entities and eid not in allowed_entities:
            continue
        # System domain exclusions
        if domain in _EXCLUDED_STATE_DOMAINS:
            continue
        # Pattern exclusions
        if any(p.match(eid) for p in _COMPILED_PATTERNS):
            continue
        # Skip sun.sun
        if eid == "sun.sun":
            continue

        attrs = dict(s.attributes) if s.attributes else {}
        entities.append(_entity_to_compact(eid, s.state, attrs, areas.get(eid)))

    # Build allowed services map: {"light": ["turn_on", "turn_off"], ...}
    svc_map: dict[str, list[str]] = {}
    for full_svc in allowed_services_list:
        parts = full_svc.split(".", 1)
        if len(parts) == 2:
            svc_map.setdefault(parts[0], []).append(parts[1])

    context = {"entities": entities, "services": svc_map}
    result = json.dumps(context, separators=(",", ":"))

    _compact_caches[hass_key] = {"data": result, "ts": now, "cfg_hash": cfg_h}
    _LOGGER.info("Built compact context: %d entities, %d chars", len(entities), len(result))
    return result


async def get_all_device_states(hass: HomeAssistant) -> List[Dict[str, Any]]:
    """
    Get all current device states from Home Assistant.
    Returns a list of dictionaries with entity_id, state, and relevant attributes.

    Similar to Paul from The Home Assistant library - pulls all devices connected
    to the network and their reasonable states.
    """
    device_states = []

    try:
        # Get all states from Home Assistant
        all_states = hass.states.async_all()

        for state in all_states:
            entity_id = state.entity_id
            state_value = state.state
            attributes = dict(state.attributes) if state.attributes else {}

            # Filter out internal/system entities that aren't useful
            # Skip entities like sun.sun, sensor.date, etc. that are system-level
            skip_domains = ["sun", "sensor.date", "sensor.time"]
            if any(entity_id.startswith(f"{domain}.") for domain in skip_domains):
                continue

            # Build a clean state representation
            device_info: Dict[str, Any] = {
                "entity_id": entity_id,
                "state": state_value,
                "friendly_name": attributes.get("friendly_name", entity_id),
            }

            # Add relevant attributes based on domain
            domain = entity_id.split(".")[0]

            if domain == "light":
                device_info["brightness"] = attributes.get("brightness")
                device_info["color_mode"] = attributes.get("color_mode")
                device_info["rgb_color"] = attributes.get("rgb_color")
                device_info["supported_color_modes"] = attributes.get(
                    "supported_color_modes", []
                )
                device_info["supported_features"] = attributes.get(
                    "supported_features", 0
                )
                device_info["effect_list"] = attributes.get("effect_list")
            elif domain == "switch":
                device_info["device_class"] = attributes.get("device_class")
            elif domain == "sensor":
                device_info["unit_of_measurement"] = attributes.get(
                    "unit_of_measurement"
                )
                device_info["device_class"] = attributes.get("device_class")
            elif domain == "climate":
                device_info["temperature"] = attributes.get("temperature")
                device_info["target_temp_high"] = attributes.get("target_temp_high")
                device_info["target_temp_low"] = attributes.get("target_temp_low")
                device_info["current_temperature"] = attributes.get(
                    "current_temperature"
                )
                device_info["hvac_modes"] = attributes.get("hvac_modes", [])
                device_info["hvac_mode"] = attributes.get("hvac_mode")
            elif domain == "cover":
                device_info["current_position"] = attributes.get("current_position")
                device_info["supported_features"] = attributes.get(
                    "supported_features", 0
                )
            elif domain == "fan":
                device_info["speed"] = attributes.get("speed")
                device_info["speed_list"] = attributes.get("speed_list", [])
            elif domain == "media_player":
                device_info["media_title"] = attributes.get("media_title")
                device_info["media_artist"] = attributes.get("media_artist")
                device_info["volume_level"] = attributes.get("volume_level")
                device_info["is_volume_muted"] = attributes.get("is_volume_muted")
                device_info["supported_features"] = attributes.get(
                    "supported_features", 0
                )
            elif domain == "lock":
                device_info["code_format"] = attributes.get("code_format")
            elif domain == "alarm_control_panel":
                device_info["code_format"] = attributes.get("code_format")
                device_info["changed_by"] = attributes.get("changed_by")

            # Add any other relevant common attributes
            if "device_class" in attributes:
                device_info["device_class"] = attributes["device_class"]
            if "icon" in attributes:
                device_info["icon"] = attributes["icon"]

            device_states.append(device_info)

        _LOGGER.info(f"Gathered {len(device_states)} device states")
        return device_states

    except Exception as e:
        _LOGGER.error(f"Error gathering device states: {e}", exc_info=True)
        return []


async def get_all_available_services(hass: HomeAssistant) -> Dict[str, Dict[str, Any]]:
    """
    Get all available services and their schemas from Home Assistant.
    Returns a dictionary mapping service names to their schemas and parameters.

    This provides the API format - headers, flags, and parameters that REST commands
    would use to call each service.
    """
    services_info: Dict[str, Dict[str, Any]] = {}
    try:
        # This call is correct. It gets all registered services.
        all_services = hass.services.async_services()

        for domain, domain_services in all_services.items():
            for service_name, service_object in domain_services.items():
                service_key = f"{domain}.{service_name}"
                service_data: Dict[str, Any] = {
                    "domain": domain,
                    "service": service_name,
                    "full_name": service_key,
                    "fields": {},
                }

                # Get the schema from the service object
                schema = getattr(service_object, "schema", None)
                if not schema or not hasattr(schema, "schema"):
                    services_info[service_key] = service_data
                    continue  # Skip if no schema

                # The .schema.schema is the dict of fields
                field_schemas = schema.schema
                if not isinstance(field_schemas, dict):
                    services_info[service_key] = service_data
                    continue  # Skip if schema is not a dict

                for field_key, field_validator in field_schemas.items():
                    try:
                        # The field_key can be a string or a Marker object
                        # str(field_key) safely gets the name (e.g., "entity_id")
                        field_name = str(field_key)
                        field_info: Dict[str, Any] = {}

                        # Use getattr to safely access attributes.
                        # This handles all cases (vol.Required, vol.Optional, or plain string keys)
                        field_info["required"] = getattr(field_key, "required", False)
                        field_info["description"] = getattr(
                            field_key, "description", ""
                        )

                        # Safely get the default value
                        default_val = getattr(field_key, "default", vol.UNDEFINED)
                        if default_val != vol.UNDEFINED:
                            # Call default if it's a function, otherwise use the value
                            field_info["default"] = (
                                default_val() if callable(default_val) else default_val
                            )

                        # Get a string representation of the validator (e.g., "str", "positive_int")
                        field_info["type"] = str(field_validator)

                        service_data["fields"][field_name] = field_info

                    except Exception as e:
                        # Log if a specific field fails, but don't stop the whole process
                        _LOGGER.warning(
                            f"Failed to parse schema for field '{str(field_key)}' "
                            f"in service '{service_key}': {e}"
                        )

                services_info[service_key] = service_data

        _LOGGER.info(f"Gathered {len(services_info)} available services")
        return services_info

    except Exception as e:
        # This is the main exception handler
        _LOGGER.error(f"Error gathering available services: {e}", exc_info=True)
        return {}


def format_device_states_for_prompt(device_states: List[Dict[str, Any]]) -> str:
    """
    Format device states into a readable string for the prompt.
    """
    if not device_states:
        return "No devices found."

    lines: List[str] = ["=== CURRENT DEVICE STATES ===", ""]

    for device in device_states:
        lines.append(f"Entity: {device['entity_id']}")
        lines.append(f"  Friendly Name: {device.get('friendly_name', 'N/A')}")
        lines.append(f"  State: {device['state']}")

        # Add domain-specific information
        for key, value in device.items():
            if key not in ["entity_id", "state", "friendly_name"] and value is not None:
                lines.append(f"  {key}: {value}")

        lines.append("")

    return "\n".join(lines)


def format_services_for_prompt(services_info: Dict[str, Dict[str, Any]]) -> str:
    """
    Format available services into a readable string for the prompt.
    Shows what can be called on each device/domain.
    """
    if not services_info:
        return "No services found."

    lines: List[str] = ["=== AVAILABLE SERVICES AND ACTIONS ===", ""]

    # Group by domain for better organization
    by_domain: Dict[str, List[Dict[str, Any]]] = {}
    for service_key, service_data in services_info.items():
        domain = service_data["domain"]
        if domain not in by_domain:
            by_domain[domain] = []
        by_domain[domain].append(service_data)

    # Sort domains alphabetically
    for domain in sorted(by_domain.keys()):
        lines.append(f"--- {domain.upper()} Domain ---")
        services = by_domain[domain]

        for service_data in sorted(services, key=lambda x: x["service"]):
            service_name = service_data["service"]
            full_name = service_data["full_name"]
            lines.append(f"  Service: {full_name}")

            # Add field information if available
            fields = service_data.get("fields")
            if isinstance(fields, dict) and fields:
                lines.append("    Parameters:")
                for field_name, field_info in fields.items():
                    field_line = f"      - {field_name}"
                    if isinstance(field_info, dict):
                        if field_info.get("required"):
                            field_line += " (required)"
                        if field_info.get("description"):
                            field_line += f": {field_info['description']}"
                        if "default" in field_info:
                            field_line += f" [default: {field_info['default']}]"
                    lines.append(field_line)
            elif fields == "Schema not available":
                lines.append("    Parameters: Schema not available")

            lines.append("")

        lines.append("")

    return "\n".join(lines)


async def build_comprehensive_prompt(hass: HomeAssistant, user_input: str) -> str:
    """
    Build a comprehensive prompt that includes:
    1. User input
    2. All current device states
    3. All available services and actions

    This is the full prompt that would be sent to the model.
    """
    _LOGGER.info("Building comprehensive prompt with device states and services...")

    # Gather device states and services
    device_states = await get_all_device_states(hass)
    services_info = await get_all_available_services(hass)

    # Format each section
    device_states_section = format_device_states_for_prompt(device_states)
    services_section = format_services_for_prompt(services_info)

    # Build the complete prompt
    prompt_parts: List[str] = [
        "=== USER COMMAND ===",
        user_input,
        "",
        device_states_section,
        "",
        services_section,
        "",
        "=== INSTRUCTIONS ===",
        "Based on the user command above, the current device states, and available services,",
        "determine what actions to take. Output valid JSON only.",
    ]

    full_prompt = "\n".join(prompt_parts)

    _LOGGER.info(
        f"Built prompt with {len(device_states)} devices and {len(services_info)} services"
    )
    _LOGGER.debug(f"Prompt length: {len(full_prompt)} characters")

    return full_prompt
