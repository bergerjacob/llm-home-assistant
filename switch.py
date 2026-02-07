from homeassistant.components.switch import SwitchEntity

class AudioRecordingSwitch(SwitchEntity):
    def __init__(self, hass):
        self.hass = hass
        self._attr_name = "LLM Recording Request"
        self._attr_unique_id = "llm_recording_switch"
        self._is_on = False

    @property
    def is_on(self):
        from .text_audio_processing import is_recording
        return is_recording()

    async def async_turn_on(self, **kwargs):
        await self.hass.services.async_call(
            "llm_home_assistant",
            "start_recording",
            {},
            blocking=True
        )

    async def async_turn_off(self, **kwargs):
        await self.hass.services.async_call(
            "llm_home_assistant",
            "stop_recording",
            {},
            blocking=True
        )
