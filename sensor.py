import logging
from homeassistant.components.sensor import SensorEntity
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
    """Set up the custom sensor platform."""
    _LOGGER.info("Setting up custom sensor for llm-home-assistant")
    sensor_entity = LLMResponseSensor(hass)
    async_add_entities([sensor_entity])
    
    # Store reference to sensor entity so service can update it
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["sensor_entity"] = sensor_entity
    _LOGGER.info("Sensor entity registered in hass.data")


class LLMResponseSensor(SensorEntity):
    """Sensor that displays the model's response."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the sensor."""
        self._hass = hass
        self._attr_name = "LLM Model Response"
        self._attr_unique_id = "llm_ha_model_response"
        self._attr_icon = "mdi:brain"
        self._attr_native_value = "No response yet"
        self._attr_extra_state_attributes = {}

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._attr_native_value

    def update_response(self, response_text: str):
        """Update the sensor with the model response or prompt."""
        # For very long text, truncate the state value but keep full text in attributes
        # Home Assistant state values have a practical limit
        max_state_length = 255
        
        if len(response_text) > max_state_length:
            # Truncate for state, but keep full text in attributes
            self._attr_native_value = response_text[:max_state_length] + "..."
            self._attr_extra_state_attributes = {
                "full_text": response_text,
                "text_length": len(response_text),
                "truncated": True
            }
        else:
            self._attr_native_value = response_text
            self._attr_extra_state_attributes = {
                "text_length": len(response_text),
                "truncated": False
            }
        
        # Schedule state update (works from any context)
        self.async_schedule_update_ha_state()
        _LOGGER.info(f"Updated sensor with response (length: {len(response_text)})")

