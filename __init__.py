import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery

# The domain of your component.
DOMAIN = "llm-home-assistant"

# Set up a logger for your component
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the component from configuration.yaml."""

    _LOGGER.warning("LLM-HA component is setting up!")
    
    # --- Load the button platform ---
    hass.async_create_task(
        discovery.async_load_platform(
            hass, "button", DOMAIN, {}, config
        )
    )
    
    _LOGGER.info("Button platform loading initiated.")

    # Return True to indicate success
    return True
