from homeassistant.helpers.aiohttp_client import async_get_clientsession
import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery

# Import the model query function
from .call_model import query_model

from .device_info import (
    get_all_device_states,
    get_all_available_services,
    format_device_states_for_prompt,
    format_services_for_prompt,
)

# ====================================================================
# NEW: Imports for GPT-4o tool-mode integration 
# ====================================================================
import json
from typing import Any
import os  # <-- just import here

from homeassistant.core import ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.const import ATTR_ENTITY_ID
import voluptuous as vol

# UPDATED: import the async JSON-planner function
from .models.openai.call_gpt4o_v2 import async_query_gpt4o_with_tools
# ====================================================================

# The domain of your component.
DOMAIN = "llm_home_assistant"

# Set up a logger for your component
_LOGGER = logging.getLogger(__name__)

# ====================================================================
# NEW: Tool-mode configuration keys and helpers 
# ====================================================================
CONF_OPENAI_API_KEY = "openai_api_key"
CONF_MODEL = "model"
CONF_ALLOW = "allow"

DEFAULT_MODEL = "gpt-4o"

SERVICE_CHAT = "chat"
SERVICE_CHAT_SCHEMA = vol.Schema(
    {
        vol.Required("text"): cv.string,
        vol.Optional("context"): dict,
    }
)


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
# ====================================================================


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the component from configuration.yaml."""

    _LOGGER.warning("LLM-HA component is setting up!")
    
    # Initialize storage for sensor entity reference
    hass.data.setdefault(DOMAIN, {})
    
    # --- Load the button platform ---
    hass.async_create_task(
        discovery.async_load_platform(
            hass, "button", DOMAIN, {}, config
        )
    )
    
    # --- Load the sensor platform ---
    hass.async_create_task(
        discovery.async_load_platform(
            hass, "sensor", DOMAIN, {}, config
        )
    )
    
    # Create input_text and input_select helpers automatically
    await _create_helpers(hass)
    
    async def async_handle_process_command(call):
        """Handle the process_command service call."""
        # 1. Get text input from the frontend UI
        user_text = call.data.get("text", "Default text if empty")
        
        # 2. Get model selection (from service data or input_select helper)
        model_name = call.data.get("model", None)
        if not model_name:
            # Try to read from input_select helper
            try:
                model_state = hass.states.get("input_select.llm_model")
                if model_state:
                    model_name = model_state.state
                    _LOGGER.info(f"Using model from input_select: {model_name}")
                else:
                    model_name = "openai"  # Default to openai
                    _LOGGER.info("No model specified and input_select not found, defaulting to 'openai'")
            except Exception as e:
                _LOGGER.warning(f"Error reading model selection: {e}, defaulting to 'openai'")
                model_name = "openai"
        else:
            _LOGGER.info(f"Using model from service call: {model_name}")
        
        _LOGGER.info(f"Received command: {user_text} (using model: {model_name})")

	# --- DEBUG: Log all device states and services ---
        _LOGGER.debug("Gathering device states and services for debug log...")
        try:
            # 1. Get raw data
            device_states = await get_all_device_states(hass)
            services_info = await get_all_available_services(hass)

            # 2. Log the RAW data as pretty-printed JSON
            _LOGGER.debug("--- START RAW DEVICE STATES (JSON) ---")
            _LOGGER.debug(json.dumps(device_states, indent=2))
            _LOGGER.debug("--- END RAW DEVICE STATES (JSON) ---")

            _LOGGER.debug("--- START RAW AVAILABLE SERVICES (JSON) ---")
            _LOGGER.debug(json.dumps(services_info, indent=2))
            _LOGGER.debug("--- END RAW AVAILABLE SERVICES (JSON) ---")

            # 3. We no longer need the format... or states_log variables here.
            #    The format_... functions will be used later by your 
            #    build_comprehensive_prompt function (if you call it).

        except Exception as e:
            _LOGGER.warning(f"Error gathering device/service info for debug log: {e}", exc_info=True)
        # --- END DEBUG LOG ---

        
        # 3. Call the model query function (run in executor to avoid blocking)
        try:
            # Use async_add_executor_job to run blocking I/O in a thread pool
            model_response = await hass.async_add_executor_job(query_model, user_text, model_name)
            
            # 4. Handle the model response
            _LOGGER.info(f"Model Output: {model_response}")
            
            # 5. Update the sensor entity if it exists
            sensor_entity = hass.data[DOMAIN].get("sensor_entity")
            if sensor_entity:
                # update_response schedules the async state update internally
                sensor_entity.update_response(model_response)
            else:
                _LOGGER.warning("Sensor entity not found, cannot update display")
            
            # 6. Fire an event that other components can listen for
            hass.bus.async_fire("llm_response_ready", {"payload": model_response})
            
        except Exception as e:
            _LOGGER.error(f"Error calling model: {e}", exc_info=True)
    
    # Register the service 'process_command'
    hass.services.async_register(DOMAIN, "process_command", async_handle_process_command)
    
    _LOGGER.info("Button platform loading initiated.")
    _LOGGER.info("Sensor platform loading initiated.")
    _LOGGER.info("Service 'process_command' registered.")

    # ====================================================================
    # NEW: Tool-mode chat service using GPT-4o function calling
    # ====================================================================
    cfg = config.get(DOMAIN, {})

    # Load API key: configuration.yaml first, fallback to environment variable
    openai_api_key: str | None = (
        cfg.get(CONF_OPENAI_API_KEY) or os.getenv("OPENAI_API_KEY")
    )

    model: str = cfg.get(CONF_MODEL, DEFAULT_MODEL)
    allow_cfg: dict[str, Any] | None = cfg.get(CONF_ALLOW)

    if not openai_api_key:
        _LOGGER.warning(
            "No OpenAI API key provided in configuration.yaml (%s.%s) "
            "and no OPENAI_API_KEY environment variable found. "
            "Service '%s' will not work.",
            DOMAIN,
            CONF_OPENAI_API_KEY,
            SERVICE_CHAT,
        )

    session = async_get_clientsession(hass)

    # ======================================================
    # FIXED: Updated, correct _execute_tool_call()
    # ======================================================
    async def _execute_tool_call(action: dict[str, Any]) -> None:
        """
        Execute a single JSON action item returned by GPT-4o.
        Expected format:
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.living_room",
                "data": {...}
            }
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

    # ======================================================
    # Service handler
    # ======================================================
    async def async_handle_llm_chat(call: ServiceCall) -> None:
        """
        Handle the llm_home_assistant.chat service.

        Sends user text to the GPT-4o JSON planner.
        GPT output should be:
            {
              "actions": [...],
              "explanation": "..."
            }
        """
        text: str = call.data["text"]
        _LOGGER.info("LLM tool-mode chat request: %s", text)

        if not openai_api_key:
            _LOGGER.error("No OpenAI API key available; aborting GPT-4o request.")
            return

        messages = [{"role": "user", "content": text}]

        try:
            reply = await async_query_gpt4o_with_tools(
                session=session,
                api_key=openai_api_key,
                model=model,
                messages=messages,
            )
        except Exception as exc:
            _LOGGER.exception("OpenAI tool-mode call failed: %s", exc)
            return

        # Expected: { "actions": [...], "explanation": "..." }
        actions: list[dict[str, Any]] = reply.get("actions") or []
        explanation: str = reply.get("explanation", "")

        if explanation:
            _LOGGER.info("Assistant explanation: %s", explanation)

        for action in actions:
            await _execute_tool_call(action)

    # ======================================================
    # Register the chat service
    # ======================================================
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHAT,
        async_handle_llm_chat,
        schema=SERVICE_CHAT_SCHEMA,
    )

    _LOGGER.info(
        "Service '%s' registered for GPT-4o tool-mode integration (model=%s, allow_cfg=%s)",
        SERVICE_CHAT,
        model,
        allow_cfg,
    )

    return True
# =================================================================================






async def _create_helpers(hass: HomeAssistant):
    """Create input_text and input_select helpers automatically."""
    from homeassistant.components.input_text import async_setup as input_text_setup
    from homeassistant.components.input_select import async_setup as input_select_setup
    
    # Check if already exist, if not create them
    if hass.states.get("input_text.llm_command") is None:
        try:
            config = {
                "input_text": {
                    "llm_command": {
                        "name": "LLM Command",
                        "initial": "",
                        "min": 0,
                        "max": 500,
                        "mode": "text",
                    }
                }
            }
            await input_text_setup(hass, config)
            _LOGGER.info("Created input_text.llm_command")
        except Exception as e:
            _LOGGER.warning(f"Could not create input_text: {e}")
    
    if hass.states.get("input_select.llm_model") is None:
        try:
            config = {
                "input_select": {
                    "llm_model": {
                        "name": "LLM Model",
                        "options": ["openai", "llama3.3"],
                        "initial": "openai",
                    }
                }
            }
            await input_select_setup(hass, config)
            _LOGGER.info("Created input_select.llm_model")
        except Exception as e:
            _LOGGER.warning(f"Could not create input_select: {e}")
