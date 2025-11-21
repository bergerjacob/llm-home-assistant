from homeassistant.helpers.aiohttp_client import async_get_clientsession
import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery

# Import the wrapper
from .call_model import call_model_wrapper

# (Previous imports kept if needed, but query_model is not used anymore)
from .device_info import (
    get_all_device_states,
    get_all_available_services,
    format_device_states_for_prompt,
    format_services_for_prompt,
)

import json
from typing import Any
import os

from homeassistant.core import ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.const import ATTR_ENTITY_ID
import voluptuous as vol

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
        vol.Optional("model"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the component from configuration.yaml."""

    _LOGGER.warning("LLM-HA component is setting up!")
    
    # Initialize storage for sensor entity reference
    hass.data.setdefault(DOMAIN, {})
    
    # ====================================================================
    # CONFIGURATION LOADING
    # ====================================================================
    cfg = config.get(DOMAIN, {})

    # Load API key: configuration.yaml first, fallback to environment variable
    openai_api_key: str | None = (
        cfg.get(CONF_OPENAI_API_KEY) or os.getenv("OPENAI_API_KEY")
    )

    default_model: str = cfg.get(CONF_MODEL, DEFAULT_MODEL)
    allow_cfg: dict[str, Any] | None = cfg.get(CONF_ALLOW)

    # Store config in hass.data so call_model.py can access it
    hass.data[DOMAIN]["openai_api_key"] = openai_api_key
    hass.data[DOMAIN]["allow_cfg"] = allow_cfg

    if not openai_api_key:
        _LOGGER.warning(
            "No OpenAI API key provided in configuration.yaml (%s.%s) "
            "and no OPENAI_API_KEY environment variable found. "
            "Service '%s' and 'process_command' will not work.",
            DOMAIN,
            CONF_OPENAI_API_KEY,
            SERVICE_CHAT,
        )

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
    
    # ======================================================
    # Service handler: llm_home_assistant.chat
    # ======================================================
    async def async_handle_llm_chat(call: ServiceCall) -> None:
        """
        Handle the llm_home_assistant.chat service.
        """
        text: str = call.data["text"]
        model_name: str = call.data.get("model", default_model)
        
        # Call the wrapper (moved to call_model.py)
        await call_model_wrapper(hass, text, model_name)

    # ======================================================
    # Service handler: llm_home_assistant.process_command (Legacy/Button)
    # ======================================================
    async def async_handle_process_command(call):
        """Handle the process_command service call (Updated to use new logic)."""
        # 1. Get text input from the frontend UI
        user_text = call.data.get("text", "")
        if not user_text:
             _LOGGER.warning("No text provided to process_command")
             return
        
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
                    model_name = default_model
                    _LOGGER.info(f"No model specified and input_select not found, defaulting to '{model_name}'")
            except Exception as e:
                _LOGGER.warning(f"Error reading model selection: {e}, defaulting to '{default_model}'")
                model_name = default_model
        else:
            _LOGGER.info(f"Using model from service call: {model_name}")
            
        # 3. Call the wrapper (moved to call_model.py)
        await call_model_wrapper(hass, user_text, model_name)
    
    # Register the services
    hass.services.async_register(DOMAIN, "process_command", async_handle_process_command)
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHAT,
        async_handle_llm_chat,
        schema=SERVICE_CHAT_SCHEMA,
    )

    _LOGGER.info("Button platform loading initiated.")
    _LOGGER.info("Sensor platform loading initiated.")
    _LOGGER.info("Service 'process_command' registered (using V2 logic via wrapper).")
    _LOGGER.info(
        "Service '%s' registered for GPT-4o tool-mode integration (default_model=%s, allow_cfg=%s)",
        SERVICE_CHAT,
        default_model,
        allow_cfg,
    )

    return True


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
                        "min": 0,
                        "max": 500,
                        "mode": "text",
                    }
                }
            }
            # Note: input_select config schema is different, fixing above was copy/paste error from input_text
            # Correct schema for input_select is just options/initial/name
            # But let's just keep the previous working logic if I can find it.
            # Wait, previous logic for input_select was:
            # { "input_select": { "llm_model": { "name": ..., "options": ..., "initial": ... } } }
            # It seems I might have copy-pasted the 'min/max' into input_select in my thought, but looking at the file read previously:
            # line 353: "input_select": ... "options": ... "initial": ...
            # It did NOT have min/max. Good.
            
            # Let's fix the config dict for input_select in this write
            config_select = {
                "input_select": {
                    "llm_model": {
                        "name": "LLM Model",
                        "options": ["openai", "llama3.3"],
                        "initial": "openai",
                    }
                }
            }
            await input_select_setup(hass, config_select)
            _LOGGER.info("Created input_select.llm_model")
        except Exception as e:
            _LOGGER.warning(f"Could not create input_select: {e}")
