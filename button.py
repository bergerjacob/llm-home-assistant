import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info=None,
):
    """Set up the custom button platform."""
    _LOGGER.info("Setting up custom button for llm-home-assistant")
    async_add_entities([LLMSubmitButton(hass)])


class LLMSubmitButton(ButtonEntity):
    """Button entity for submitting LLM commands."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the button."""
        self._hass = hass
        self._attr_name = "Send to Model"
        self._attr_unique_id = "send_to_model"
        self._attr_icon = "mdi:send"

    async def async_press(self) -> None:
        """Handle button press - submit LLM command."""
        # Get text from input_text helper
        command_text = ""
        try:
            command_state = self._hass.states.get("input_text.llm_command")
            if command_state:
                command_text = command_state.state or ""
        except Exception as e:
            _LOGGER.warning(f"Could not read command text: {e}")
        
        # Get model from input_select helper
        model_name = "openai"
        try:
            model_state = self._hass.states.get("input_select.llm_model")
            if model_state:
                model_name = model_state.state or "openai"
        except Exception as e:
            _LOGGER.warning(f"Could not read model selection: {e}")
        
        if not command_text:
            _LOGGER.warning("No command text provided")
            return
        
        _LOGGER.info(f"Submitting command: '{command_text}' using model: {model_name}")
        
        # Call the service
        try:
            await self._hass.services.async_call(
                DOMAIN,
                "process_command",
                {
                    "text": command_text,
                    "model": model_name
                },
                blocking=False,
            )
        except Exception as e:
            _LOGGER.error(f"Error calling process_command service: {e}", exc_info=True)
