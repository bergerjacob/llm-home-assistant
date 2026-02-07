from homeassistant.components.select import SelectEntity

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([LLMModelSelect(hass)])


class LLMModelSelect(SelectEntity):
    def __init__(self, hass):
        self.hass = hass
        self._attr_name = "LLM Model"
        self._attr_unique_id = "llm_model_select"
        self._attr_options = [
            "OpenAI (GPT-4o)",
            "Llama 3.3",
        ]
        self._attr_current_option = "OpenAI (GPT-4o)"

    async def async_select_option(self, option: str):
        self._attr_current_option = option
        self.async_write_ha_state()

        model_map = {
            "OpenAI (GPT-4o)": "openai",
            "Llama 3.3": "llama3.3",
        }

        self.hass.data.setdefault("llm_home_assistant", {})
        self.hass.data["llm_home_assistant"]["model"] = model_map[option]
