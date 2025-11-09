import logging
from homeassistant.core import HomeAssistant

# The domain of your component.
DOMAIN = "llm-home-assistant"

# Set up a logger for your component
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Hello World component from configuration.yaml."""
    
    # This is the "print statement" you are looking for!
    _LOGGER.info("Hello World component is setting up!")

    # Store a simple flag or data. This makes it "loaded".
    hass.data[DOMAIN] = {"message": "Hello!"}
    
    # We have removed the service registration that was causing the error.

    # Return True to indicate success
    return True
