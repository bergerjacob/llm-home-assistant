# LLM Home Assistant

A Home Assistant custom integration that lets users control IoT devices via natural language using LLMs. Supports both text and voice input.

## Architecture

### Text Pipeline

```
User text
  -> llm_home_assistant.chat service
  -> call_model_wrapper()
  -> async_query_openai() -> gpt-5-mini (JSON mode)
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

The audio-direct pipeline eliminates the whisper.cpp transcription step. Instead of converting speech to text and then sending text to a separate LLM call, the audio is sent directly to `gpt-4o-audio-preview` which understands speech and produces structured actions in a single API round-trip.

### Legacy Audio Pipeline (Still Available)

The original whisper.cpp pipeline is preserved as a fallback:

```
transcribe_audio service
  -> whisper.cpp STT -> text
  -> llm_home_assistant.chat service -> gpt-5-mini
  -> actions + TTS
```

This can be called manually via `llm_home_assistant.transcribe_audio` but is no longer triggered automatically by `stop_recording`.

## Registered Services

| Service | Description |
|---|---|
| `llm_home_assistant.chat` | Send text to the LLM. Actions are executed in HA. |
| `llm_home_assistant.process_command` | Legacy button handler. Reads text from UI input. |
| `llm_home_assistant.start_recording` | Start ffmpeg audio recording from microphone. |
| `llm_home_assistant.stop_recording` | Stop recording. Automatically triggers `process_audio_direct`. |
| `llm_home_assistant.process_audio_direct` | Send recorded WAV directly to gpt-4o-audio-preview. |
| `llm_home_assistant.transcribe_audio` | Legacy: whisper.cpp STT then chat service. |
| `llm_home_assistant.tts_fallback` | Text-to-speech using gTTS with espeak fallback. |

## File Structure

### Core Files

| File | Purpose |
|---|---|
| `__init__.py` | Integration setup. Registers all services, loads platforms, frontend. |
| `call_model.py` | Main orchestration: routes text or audio to the correct OpenAI caller, executes actions. |
| `audio_utils.py` | Audio validation (format, size) and base64 encoding. |
| `text_audio_processing.py` | ffmpeg recording, whisper.cpp STT, gTTS/espeak TTS. |
| `device_info.py` | Device state/service formatting (imported but not actively called). |
| `services.yaml` | HA service definitions for Developer Tools UI. |

### Model Files

| File | Purpose |
|---|---|
| `models/openai/call_openai.py` | Text-only OpenAI caller. Uses `gpt-5-mini` with JSON mode. Contains `build_hass_context()`, Pydantic models (`Action`, `Plan`), entity/service exclusion filters, cache stats. |
| `models/openai/call_openai_audio.py` | Audio OpenAI caller. Uses `gpt-4o-audio-preview` with function calling. Reuses context builder and Pydantic models from `call_openai.py`. |
| `models/openai/tool_defs.py` | OpenAI function calling schema (`PROPOSE_ACTIONS_TOOL`). Defines the `propose_actions` function with actions array + explanation. |
| `models/llama3.3/call_llama.py` | Llama 3.3 stub (not actively used). |

### Frontend

| File | Purpose |
|---|---|
| `www/llm-card.js` | Text input card for Lovelace dashboard. |
| `www/llm-recording-card.js` | Recording toggle card with model selection. |

## How the Audio-Direct Pipeline Works

1. **`stop_recording`** (`__init__.py`) stops the ffmpeg process, then calls `process_audio_direct`.

2. **`process_audio_direct`** (`__init__.py`) reads `_audios/current_request.wav` as raw bytes from disk.

3. **`call_model_wrapper`** (`call_model.py`) receives `audio_data` and `audio_format` kwargs. Since `audio_data` is present, it:
   - Validates the audio via `audio_utils.validate_audio()` (checks non-empty, supported format, under 15 MiB)
   - Base64 encodes via `audio_utils.encode_audio_base64()`
   - Calls `async_query_openai_audio()` instead of the text-only `async_query_openai()`

4. **`async_query_openai_audio`** (`call_openai_audio.py`) builds the HA context using the same `build_hass_context()` from `call_openai.py` (includes entity/service exclusion filtering and area enrichment), then delegates to `_blocking_audio_gpt_call()` in a thread executor.

5. **`_blocking_audio_gpt_call`** constructs a multimodal message:
   - System prompt with HA context (entities, services, areas)
   - User message containing `input_audio` content (base64 WAV)
   - Calls `gpt-4o-audio-preview` with `modalities=["text"]`, `tools=[PROPOSE_ACTIONS_TOOL]`, and forced `tool_choice` on `propose_actions`
   - Parses the function call arguments through the existing `Plan` Pydantic model

6. Back in `call_model_wrapper`, the response is processed identically to the text path: sensor update, event fire, `_execute_tool_call()` loop.

7. **`process_audio_direct`** then writes the response to `_texts/response_text.txt` and calls `tts_fallback` for spoken output.

## Setup

### Prerequisites

- Docker and Docker Compose
- OpenAI API key with access to `gpt-5-mini` and `gpt-4o-audio-preview`
- For audio: a microphone connected to the Raspberry Pi

### Configuration

1. Create a `.env` file in the project root:
   ```
   OPENAI_API_KEY=sk-your-key-here
   ```

2. The `docker-compose.yml` mounts the project into HA:
   ```yaml
   services:
     homeassistant:
       image: ghcr.io/home-assistant/home-assistant:2025.1.4
       volumes:
         - ./ha_config:/config
         - .:/config/custom_components/llm_home_assistant
       environment:
         - OPENAI_API_KEY=${OPENAI_API_KEY}
       ports:
         - "8123:8123"
   ```

3. Start the container:
   ```bash
   docker compose up -d
   ```

4. Open Home Assistant at `http://<pi-ip>:8123`

## Testing

### Verify the integration loaded

```bash
docker compose logs homeassistant | grep -i "process_audio_direct\|Error"
```

You should see `Service 'process_audio_direct' registered` with no import errors.

### Test the text pipeline

In HA **Developer Tools > Services**:
- Service: `llm_home_assistant.chat`
- Service data:
  ```yaml
  text: "What devices are available?"
  ```
- Click **Call Service**

Check `sensor.llm_model_response` for the response.

### Test the audio-direct pipeline

**Option A: Full recording flow (mic required)**

1. Call `llm_home_assistant.start_recording`
2. Speak a command (e.g. "turn off the kitchen light")
3. Call `llm_home_assistant.stop_recording`
4. Watch the logs:
   ```bash
   docker compose logs -f homeassistant | grep -i "audio\|PROCESS_AUDIO\|explanation\|action"
   ```

**Option B: Drop a WAV file manually**

Record on the Pi:
```bash
arecord -D plughw:3,0 -f S16_LE -r 16000 -c 1 -d 5 _audios/current_request.wav
```

Then call `llm_home_assistant.process_audio_direct` from Developer Tools with no data.

### Test the legacy whisper pipeline (backwards compatibility)

Call `llm_home_assistant.transcribe_audio` with:
```yaml
filename: "current_request.wav"
```

This goes through whisper.cpp STT, confirming the old path still works.

### Expected log output for a successful audio-direct call

```
=== PROCESS_AUDIO_DIRECT SERVICE ===
Read audio file: .../current_request.wav (N bytes)
Audio-direct path: N bytes, format=wav
Audio API token usage - Input: X, Output: Y, Total: Z
Assistant explanation: I turned off the kitchen light.
Executing GPT action light.turn_off with {'entity_id': 'light.kitchen'}
Called TTS fallback after audio-direct processing
```

### Troubleshooting

| Symptom | Check |
|---|---|
| Import error on startup | Check `docker compose logs homeassistant \| grep Error`. Likely a missing dependency or typo. |
| `process_audio_direct` service not found | Service didn't register. Check startup logs for errors before that line. |
| `Audio model call failed` | API key issue or `gpt-4o-audio-preview` not available on your OpenAI plan. |
| Empty response / no actions | Verify the WAV file is valid audio (not empty or corrupt). |
| Wrong ALSA device | Run `arecord -l` on the Pi to find your mic, update the device in `text_audio_processing.py`. |

## Contacts

- Jacob: bergejac@oregonstate.edu
- Varunesh: suntharv@oregonstate.edu
- Andrew: vuand@oregonstate.edu
- Jhonny: guzmjona@oregonstate.edu
