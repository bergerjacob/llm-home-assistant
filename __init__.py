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
    
    # ======================================================
    # Service handler: llm_home_assistant.setup_dashboard (REMOVED)
    # ======================================================
    # Manually setup dashboard service removed to simplify
    
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

    # ======================================================
    # Frontend Resource Registration
    # ======================================================
    # Register static path so the card JS is accessible
    # URL: /llm_home_assistant/llm-card.js
    
    # Robustly find the component directory
    from pathlib import Path
    file_path = Path(__file__).resolve()
    
    # Handle the case where we are running from __pycache__
    if file_path.parent.name == "__pycache__":
        component_dir_path = file_path.parent.parent
    else:
        component_dir_path = file_path.parent
        
    component_dir = str(component_dir_path)
    www_dir = str(component_dir_path / "www")
    
    _LOGGER.info(f"LLM-HA: component_dir={component_dir}")
    _LOGGER.info(f"LLM-HA: www_dir={www_dir}")

    # 1. Register static path first
    try:
        from homeassistant.components.http import StaticPathConfig
        await hass.http.async_register_static_paths([
            StaticPathConfig(
                "/llm_home_assistant",
                www_dir,
                True
            )
        ])
    except ImportError:
         # Fallback for older HA versions
         try:
             hass.http.register_static_path(
                "/llm_home_assistant",
                www_dir,
                True
            )
         except AttributeError:
             _LOGGER.warning("Could not register static path: neither async_register_static_paths nor register_static_path found")
    except Exception as e:
        _LOGGER.warning(f"Error registering static paths: {e}")

    # 2. Register the card as a Lovelace resource
    import time
    _ts = int(time.time())
    card_url = f"/llm_home_assistant/llm-card.js?v={_ts}"
    recording_card_url = f"/llm_home_assistant/llm-recording-card.js?v={_ts}"
    realtime_card_url = f"/llm_home_assistant/llm-realtime-audio-card.js?v={_ts}"

    try:
        from homeassistant.components.frontend import add_extra_module_url
        add_extra_module_url(hass, card_url)
        add_extra_module_url(hass, recording_card_url)
        add_extra_module_url(hass, realtime_card_url)
        _LOGGER.info("Registered frontend cards: llm-card, llm-recording-card, llm-realtime-audio-card")
    except ImportError:
        try:
            from homeassistant.components.frontend import add_extra_js_url
            add_extra_js_url(hass, card_url)
            add_extra_js_url(hass, recording_card_url)
            add_extra_js_url(hass, realtime_card_url)
            _LOGGER.info("Registered frontend cards (legacy): llm-card, llm-recording-card, llm-realtime-audio-card")
        except Exception as e:
            _LOGGER.warning("Could not register extra JS: %s", e)
    except Exception as e:
        _LOGGER.warning("Error registering frontend resource: %s", e)

    try:
        from homeassistant.components.lovelace import add_resource
        from homeassistant.components.lovelace.const import RESOURCE_TYPE_MODULE
        add_resource(hass, realtime_card_url, RESOURCE_TYPE_MODULE)
        _LOGGER.info("Added realtime audio card to Lovelace resources: %s", realtime_card_url)
    except (ImportError, AttributeError, TypeError) as e:
        _LOGGER.debug("Could not add Lovelace resource (add resource manually in Settings > Dashboards > Resources): %s", e)

    # ======================================================
    # Register Sidebar Panel
    # ======================================================
    # NOTE: Sidebar panel registration is deliberately removed
    # per user request. The card should be added manually to
    # the Overview dashboard.

     # ======================================================
    #STT/TTS set up
    # ======================================================
    config_dir_2 = hass.config.config_dir
    debug_path = os.path.join(
    config_dir_2, 
    "custom_components", 
    DOMAIN, 
    "_texts",
    "debuggin_text.txt")

    def _write_file(path: str, content: str, mode: str = "a") -> None:
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)

    def _start_recording_service(call: ServiceCall):
        _LOGGER.info("=== START_RECORDING SERVICE ===")
        from time import time
        try:
            _write_file(debug_path, f"Recording starte : {time()}\n")
        except Exception as e:
            _LOGGER.debug("Debug write failed: %s", e)
        try:
            from .text_audio_processing import start_recording
            result = start_recording()
            _LOGGER.info(f"Recording started: {result}")
            
            
        except Exception as e:
            _LOGGER.error(f"Failed to start recording: {e}")

    hass.services.async_register(
        DOMAIN,
        "start_recording",
        _start_recording_service,
        schema=vol.Schema({})
    )

    # Register stop_recording service
    async def _stop_recording_service(call: ServiceCall):
        _LOGGER.info("=== STOP_RECORDING SERVICE ===")
        pipeline = call.data.get("pipeline", "transcribe")
        try:
            from .text_audio_processing import stop_recording
            result = stop_recording()
            _LOGGER.info(f"Recording stopped: {result}")
            if result.get("status") == "not_recording":
                _LOGGER.warning("Stop called but no recording was in progress; skipping pipeline.")
                return
            from asyncio import sleep
            await sleep(1.0)
            from time import time
            await hass.async_add_executor_job(_write_file, debug_path, f"Recording stopped : {time()}\n")
            if not result.get("success"):
                _LOGGER.warning("Recording stopped but no audio file was created; skipping pipeline.")
                return
            if pipeline != "realtime":
                hass.async_create_task(
                    hass.services.async_call(
                        DOMAIN,
                        "transcribe_audio",
                        {"filename": "current_request.wav"}
                    )
                )
        except Exception as e:
            _LOGGER.error(f"Failed to stop recording: {e}")

    hass.services.async_register(
        DOMAIN,
        "stop_recording",
        _stop_recording_service,
        schema=vol.Schema({vol.Optional("pipeline", default="transcribe"): cv.string})
    )

    async def _transcribe_and_store(call):
        
        if DOMAIN not in hass.data:
            _LOGGER.warning(f"hass.data[{DOMAIN}] not found, initializing now")
            hass.data[DOMAIN] = {"default_model": "openai"}
        

        # Check if recording actually happened
        
        filename = call.data.get("filename")
        if not filename:
            _LOGGER.error("No filename provided for transcribe_audio")
            return
        
        config_dir = hass.config.config_dir
        audio_path = os.path.join(
        config_dir, 
        "custom_components", 
        DOMAIN, 
        "_audios",
        filename)
        
        _LOGGER.info(f"Looking for audio file at: {audio_path}")

        if not os.path.exists(audio_path):
            _LOGGER.error(f"File does not exist: {audio_path}")
            # Check if recording actually happened
            from .text_audio_processing import is_recording
            if is_recording():
                _LOGGER.warning("Recording is still in progress! Wait for it to finish.")
            return
        
        try:
            size = os.path.getsize(audio_path)

            _LOGGER.info(f"Audio file size: {size} bytes")
            await hass.async_add_executor_job(_write_file, debug_path, f"Audio file size: {size} bytes\n")

            from .text_audio_processing import stt_whisper
            from time import time
            await hass.async_add_executor_job(_write_file, debug_path, f"CAlling transcribe : {time()}\n")
            text = await hass.async_add_executor_job(stt_whisper, audio_path)
            await hass.async_add_executor_job(_write_file, debug_path, "***" + text + "+++\n")
            await hass.async_add_executor_job(_write_file, debug_path, f"finish transcribed : {time()}\n")
            
            #save transcription to HA state
            hass.states.async_set(DOMAIN+".last_transcription", text)
            
            _LOGGER.info(f"Transcription successful: {text}")
            
            # Call LLM chat service with transcribed text and model
            model_entity_id = "select.llm_model_select"
            # Get model - FIX: Check if entity exists
            model_state = hass.states.get(model_entity_id)
            selected_model = hass.data[DOMAIN].get("default_model", "openai")  # fallback
            model_entity_id = "select.llm_model_select"
            model_state = hass.states.get(model_entity_id)
            
            if model_state and model_state.state:
                selected_model = model_state.state
                _LOGGER.info(f"Using selected model: {selected_model}")
            else:
                # DEBUG: Check hass.data before accessing
                _LOGGER.info(f"hass.data.get('{DOMAIN}'): {hass.data.get(DOMAIN)}")
            
            from time import time
            await hass.async_add_executor_job(_write_file, debug_path, f"calling calling model with: {text}\ncalling caht model at : {time()}\n")
            await hass.services.async_call(
                DOMAIN,
                "chat",
                {
                    "text": text,
                    "model": selected_model
                },
                blocking=True
            )
            await hass.async_add_executor_job(_write_file, debug_path, f"LLM processing started with model: {selected_model}\ncalling caht ended at : {time()}\n")
            _LOGGER.info("LLM chat service called with transcription")
            # Path to the text file in _texts folder
            text_file = os.path.join(
                hass.config.config_dir,
                "custom_components",
                DOMAIN,
                "_texts",
                "response_text.txt"
            )

            entity_id = "sensor.llm_model_response"
            state_obj = hass.states.get(entity_id)
            state_str = state_obj.state if state_obj else "no sensor data"

            try:
                await hass.async_add_executor_job(_write_file, text_file, state_str, "w")
                _LOGGER.info("Saved sensor state to %s: %s", text_file, state_str)
            except Exception as e:
                _LOGGER.error("Error writing sensor state to file: %s", e)
            
            # Schedule the async TTS fallback service
            await hass.services.async_call(
            DOMAIN,
            "tts_fallback",
            {},
            blocking=False)
            _LOGGER.info("Called async TTS fallback service")
            
        except Exception as e:
            _LOGGER.error(f"Error during transcription: {e}")
            _LOGGER.error(f"Error type: {type(e).__name__}")
            _LOGGER.error(f"Error message: {str(e)}")
            _LOGGER.error("Full traceback:", exc_info=True)
            return

    hass.services.async_register(
        DOMAIN,
        "transcribe_audio",
        _transcribe_and_store,
        schema=vol.Schema({vol.Optional("filename", default="current_request.wav",description="Filename in _audios folder"): cv.string})
        )

    async def _process_realtime_audio_service(call: ServiceCall):
        """Process recorded audio with OpenAI Realtime API and execute tool calls."""
        _LOGGER.info("=== PROCESS_REALTIME_AUDIO SERVICE ===")
        filename = call.data.get("filename", "current_request.wav")
        config_dir = hass.config.config_dir
        audio_path = os.path.join(config_dir, "custom_components", DOMAIN, "_audios", filename)
        if not os.path.isfile(audio_path):
            _LOGGER.error("Realtime audio file not found: %s", audio_path)
            return
        data_store = hass.data.get(DOMAIN, {})
        api_key = data_store.get("openai_api_key")
        if not api_key:
            _LOGGER.error("No OpenAI API key; cannot run Realtime API")
            return
        allow_cfg = data_store.get("allow_cfg")
        try:
            from .models.openai.call_realtime_audio import process_realtime_audio
            result = await process_realtime_audio(hass, api_key, audio_path, allow_cfg)
            _LOGGER.info("Realtime audio result: %s", result.get("explanation", "")[:200])
            response_text_path = os.path.join(config_dir, "custom_components", DOMAIN, "_texts", "response_text.txt")
            await hass.async_add_executor_job(_write_file, response_text_path, result.get("explanation", ""), "w")
            await hass.services.async_call(DOMAIN, "tts_fallback", {}, blocking=False)
        except Exception as e:
            _LOGGER.exception("process_realtime_audio failed: %s", e)

    hass.services.async_register(
        DOMAIN,
        "process_realtime_audio",
        _process_realtime_audio_service,
        schema=vol.Schema({vol.Optional("filename", default="current_request.wav"): cv.string}),
    )

    async def _async_tts_fallback_service(call: ServiceCall):
        """Async service that reads text from file and uses TTS with fallback."""
        _LOGGER.info("=== TTS FALLBACK SERVICE ===")
        
        try:
            # Get text from file
            config_dir = hass.config.config_dir
            text_path = os.path.join(
                config_dir, 
                "custom_components", 
                DOMAIN, 
                "_texts",
                "response_text.txt"
            )
            
            # Read the text file asynchronously
            if not await hass.async_add_executor_job(os.path.exists, text_path):
                _LOGGER.error(f"Text file not found: {text_path}")
                return
                
            def read_file():
                with open(text_path, 'r') as f:
                    return f.read().strip()
            
            text = await hass.async_add_executor_job(read_file)
            
            if not text:
                _LOGGER.warning("Text file is empty")
                text = "There was no reponse from model"
                
            _LOGGER.info(f"Read text from file ({len(text)} chars): {text[:100]}...")
            
            # Import TTS functions
            from .text_audio_processing import tts_google, tts_espeak
            
            # Try Google TTS first
            try:
                _LOGGER.info("Attempting Google TTS...")
                
                # If tts_google is a blocking function, run it in executor
                result = await hass.async_add_executor_job(tts_google, text)
                # If tts_google is async: result = await tts_google(text)
                
                _LOGGER.info(f"Google TTS successful: {result}")
                
            except Exception as google_error:
                _LOGGER.warning(f"Google TTS failed: {google_error}")
                _LOGGER.info("Falling back to eSpeak TTS...")
                
                # Fallback to eSpeak
                try:
                    # If tts_espeak is a blocking function
                    result = await hass.async_add_executor_job(tts_espeak, text)
                    # If tts_espeak is async: result = await tts_espeak(text)
                    
                    _LOGGER.info(f"eSpeak TTS successful: {result}")
                    
                except Exception as espeak_error:
                    _LOGGER.error(f"Both TTS methods failed. eSpeak error: {espeak_error}")
                    raise
            
        except Exception as e:
            _LOGGER.error(f"TTS fallback service failed: {e}")
    
    # Register the async fallback service
    hass.services.async_register(
        DOMAIN, 
        "tts_fallback",
        _async_tts_fallback_service,
        schema=vol.Schema({})
    )
    
    _LOGGER.info("LLM Home Assistant integration setup complete")

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


async def async_setup_entry(hass, config):
    from .switch import AudioRecordingSwitch
    from .select import LLMModelSelect

    # Initialize hass.data[DOMAIN]
    hass.data.setdefault(DOMAIN, {})
    
    # Create and register the select entity
    select = LLMModelSelect(hass)
    await select.async_added_to_hass()
    
    # entity is available immediately
    hass.states.async_set(
    "select.llm_model_select",
    "openai",
    {
        "friendly_name": "LLM Model",
        "options": ["openai", "llama3.3"],
        "icon": "mdi:robot"
    }
    )
    
    # Store in hass.data
    hass.data[DOMAIN]["select"] = select

    switch = AudioRecordingSwitch(hass)
    await switch.async_added_to_hass()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["switch"] = switch
    
    # Register the select
    
    select = LLMModelSelect(hass)
    await select.async_added_to_hass()
    hass.data[DOMAIN]["select"] = select
    return True

async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    return True