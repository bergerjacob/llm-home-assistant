# LLM Home Assistant

A [Home Assistant](https://www.home-assistant.io/) custom integration that enables natural language control of IoT devices using Large Language Models. Supports both text and voice input.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Home Assistant Compatible](https://img.shields.io/badge/Home%20Assistant-2025.1.4-blue.svg)](https://www.home-assistant.io/)

## Features

- **Natural Language Control**: Control your smart home devices using everyday language
- **Voice Support**: Speak commands directly — audio is processed end-to-end via GPT-4o-Audio
- **Token-Efficient**: Whitelist-based entity filtering reduces context size for faster inference and lower costs
- **Native Entity Support**: Binary sensors and sensor entities are automatically included with their states and attributes
- **Audit Logging**: Full interaction history logged for debugging and accountability

## Architecture

### Text Pipeline

```
User text
  -> llm_home_assistant.chat service
  -> call_model_wrapper()
  -> async_query_openai() -> GPT-4o-Mini (JSON mode)
  -> parse actions via Pydantic (Plan/Action models)
  -> _execute_tool_call() for each action
  -> sensor update + event fire
```

### Audio-Direct Pipeline

```
Microphone
  -> start_recording -> ffmpeg records WAV to _audios/current_request.wav
  -> stop_recording -> stops ffmpeg
    -> process_audio_direct service
      -> reads WAV file from disk
      -> base64 encodes audio
      -> ONE call to gpt-4o-audio-preview (multimodal: audio + HA context)
      -> function calling returns { actions, explanation }
      -> _execute_tool_call() for each action (same as text path)
      -> sensor update + event fire
      -> tts_fallback (gTTS / espeak)
```

The audio-direct pipeline sends speech directly to `gpt-4o-audio-preview` which understands audio and produces structured actions in a single API call — no separate transcription step required.

### Legacy Audio Pipeline (Deprecated)

```
transcribe_audio service
  -> whisper.cpp STT -> text
  -> llm_home_assistant.chat service -> GPT-4o-Mini
  -> actions + TTS
```

This path is preserved for backwards compatibility but is no longer the recommended approach.

## Installation

### Option 1: Manual Installation (Recommended for testing/development)

1. Clone this repository into your Home Assistant's custom components directory:

```bash
cd /path/to/your/homeassistant/config
git clone https://github.com/your-repo/llm-home-assistant.git custom_components/llm_home_assistant
```

2. Create a `.env` file in the project root with your OpenAI API key:

```bash
echo "OPENAI_API_KEY=sk-your-key-here" > .env
```

3. Add the integration configuration to your `configuration.yaml`:

```yaml
llm_home_assistant:
  openai_api_key: !env_var OPENAI_API_KEY
  model: gpt-4o-mini
```

4. Restart Home Assistant

### Option 2: Docker Compose (Full Stack)

```bash
git clone https://github.com/your-repo/llm-home-assistant.git
cd llm-home-assistant
echo "OPENAI_API_KEY=sk-your-key-here" > .env
docker compose up -d
```

Then open Home Assistant at `http://localhost:8123`.

## Configuration

### Basic Configuration

```yaml
llm_home_assistant:
  openai_api_key: !env_var OPENAI_API_KEY
  model: gpt-4o-mini
```

### Whitelist-Based Entity Filtering

To reduce context size and token usage, configure an allowlist in `configuration.yaml`:

```yaml
llm_home_assistant:
  openai_api_key: !env_var OPENAI_API_KEY
  model: gpt-4o-mini
  allow:
    domains:
      - light
      - switch
      - cover
      - binary_sensor
      - sensor
    services:
      - light.turn_on
      - light.turn_off
      - switch.turn_on
      - switch.turn_off
    entities:
      - light.kitchen
      - light.living_room
      - switch.bedroom
      - binary_sensor.door
      - binary_sensor.window
      - sensor.temperature
```

| Option | Description |
|--------|-------------|
| `domains` | Only include entities from these domains. Common domains: `light`, `switch`, `cover`, `binary_sensor`, `sensor`, `climate`, `fan`, `lock`, `media_player`, `vacuum` |
| `services` | Only allow these specific services to be called. If `allow` is configured, services must be explicitly listed (fail-closed). |
| `entities` | Only include these specific entity IDs in the context |

Using whitelist filtering significantly reduces context size, which lowers token usage and latency.

## Registered Services

| Service | Description |
|---------|-------------|
| `llm_home_assistant.chat` | Send text to the LLM. Actions are executed automatically in Home Assistant. |
| `llm_home_assistant.process_command` | Legacy handler for button input. |
| `llm_home_assistant.start_recording` | Start ffmpeg audio recording from microphone. |
| `llm_home_assistant.stop_recording` | Stop recording. Automatically triggers `process_audio_direct`. |
| `llm_home_assistant.process_audio_direct` | Send recorded WAV directly to gpt-4o-audio-preview. |
| `llm_home_assistant.transcribe_audio` | Legacy: whisper.cpp STT then chat service. |
| `llm_home_assistant.tts_fallback` | Text-to-speech using gTTS with espeak fallback. |

## File Structure

### Core Files

| File | Purpose |
|------|---------|
| `__init__.py` | Integration setup. Registers all services, loads platforms, frontend. |
| `call_model.py` | Main orchestration: routes text or audio to the correct OpenAI caller, executes actions in parallel, caches responses, enforces allow_cfg restrictions. |
| `audio_utils.py` | Audio validation (format, size) and base64 encoding. |
| `text_audio_processing.py` | ffmpeg recording, whisper.cpp STT, gTTS/espeak TTS. |
| `device_info.py` | Device state/service formatting + compact context builder with whitelist filtering. |
| `interaction_logger.py` | Interaction logging system for full audit trail. |
| `services.yaml` | Home Assistant service definitions for Developer Tools UI. |

### Model Files

| File | Purpose |
|------|---------|
| `models/openai/call_openai.py` | OpenAI caller using GPT-4o-Mini (JSON mode) or gpt-4o-audio-preview (function calling). Contains `build_compact_context()` with whitelist support. |
| `models/openai/call_openai_audio.py` | Audio-capable OpenAI caller using gpt-4o-audio-preview. |
| `models/openai/tool_defs.py` | OpenAI function calling schema (`PROPOSE_ACTIONS_TOOL`). |
| `models/openai/call_JSON_mode.py` | JSON mode implementation for structured responses. |
| `models/llama3.3/call_llama.py` | Llama 3.3 stub (experimental). |

### Frontend

| File | Purpose |
|------|---------|
| `www/llm-card.js` | Text input card for Lovelace dashboard. |
| `www/llm-recording-card.js` | Recording toggle card with model selection. |

## Testing

### Verify the Integration Loaded

```bash
docker compose logs homeassistant | grep -i "llm_home_assistant\|Error"
```

You should see service registration messages with no import errors.

### Test the Text Pipeline

In Home Assistant **Developer Tools > Services**:

1. Select service: `llm_home_assistant.chat`
2. Service data:
   ```yaml
   text: "What devices are available?"
   ```
3. Click **Call Service**

Check `sensor.llm_model_response` for the LLM's response.

### Test the Audio Pipeline

**Option A: Full Recording Flow**

1. Call `llm_home_assistant.start_recording`
2. Speak a command (e.g., "turn off the kitchen light")
3. Call `llm_home_assistant.stop_recording`

This automatically triggers `process_audio_direct` which calls `gpt-4o-audio-preview`.

**Option B: Manual WAV File**

```bash
# Record audio
arecord -D plughw:3,0 -f S16_LE -r 16000 -c 1 -d 5 _audios/current_request.wav
```

Then call `llm_home_assistant.process_audio_direct` from Developer Tools.

## Troubleshooting

| Symptom | Solution |
|---------|----------|
| Import error on startup | Check `docker compose logs homeassistant | grep Error`. Likely a missing dependency or typo. |
| Service not found | Check startup logs for errors before service registration. |
| Audio model call failed | API key issue or `gpt-4o-audio-preview` not available on your OpenAI plan. |
| Empty response / no actions | Verify the WAV file is valid audio (not empty or corrupt). |
| Wrong ALSA device | Run `arecord -l` to find your microphone device, update in `text_audio_processing.py`. |
| High token usage | Verify `allow` configuration restricts domains/services/entities. |

## License

This project is licensed under the MIT License.

## Contact

- Jacob: bergejac@oregonstate.edu
- Varunesh: suntharv@oregonstate.edu
- Andrew: vuand@oregonstate.edu
- Jhonny: guzmjona@oregonstate.edu
