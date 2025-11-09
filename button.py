import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

# Import the DOMAIN from our __init__.py file
from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

# This is your light's ID
TARGET_ENTITY_ID = "light.third_reality_inc_3rcb01057z"

async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info=None,
):
    """Set up the custom button platform."""
    _LOGGER.info("Setting up custom button for llm-home-assistant")
    async_add_entities([MyCustomButton(hass)])


class MyCustomButton(ButtonEntity):
    """Defines the custom button entity."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the button."""
        self._hass = hass
        
        # This is the name that will show up in Home Assistant
        self._attr_name = "My Custom Logic Button"
        
        # This MUST be unique across your entire Home Assistant
        self._attr_unique_id = "llm_ha_custom_logic_button_01"
        
        # You can give it a custom icon
        self._attr_icon = "mdi:cogs"


    async def async_press(self) -> None:
        """
        This is the code that runs when the button is pressed.
        """
        
        _LOGGER.warning(
            "Custom Button Pressed! Running our custom logic..."
        )

        _LOGGER.info(f"Toggling entity: {TARGET_ENTITY_ID}")
        
        await self._hass.services.async_call(
            "light",
            "toggle",
            {
                "entity_id": TARGET_ENTITY_ID
            },
            blocking=True,
        )
        
        _LOGGER.warning("Custom logic complete.")
