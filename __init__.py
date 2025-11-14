import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery

# Import the model query function
from .call_model import query_model

# The domain of your component.
DOMAIN = "llm-home-assistant"

# Set up a logger for your component
_LOGGER = logging.getLogger(__name__)


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

    # Return True to indicate success
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
                    }
                }
            }
            await input_select_setup(hass, config)
            _LOGGER.info("Created input_select.llm_model")
        except Exception as e:
            _LOGGER.warning(f"Could not create input_select: {e}")
