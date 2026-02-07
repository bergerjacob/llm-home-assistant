"""
OpenAI Realtime API: unified audio input + intelligent tool calling.
Uses the same states/services context and execute_plan tool as the JSON-mode flow.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import ssl
import subprocess
import tempfile
from typing import Any

from homeassistant.core import HomeAssistant

from .call_openai import (
    build_hass_context,
    fetch_states,
    fetch_services,
    fetch_entity_areas,
)

_LOGGER = logging.getLogger(__name__)

REALTIME_MODEL = "gpt-4o-realtime-preview-2025-06-03"
REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"
REALTIME_RATE = 24000
CHUNK_BYTES = 1024 * 2

TOOLS = [
    {
        "type": "function",
        "name": "execute_plan",
        "description": "Execute control actions on Home Assistant entities.",
        "parameters": {
            "type": "object",
            "properties": {
                "explanation": {
                    "type": "string",
                    "description": "A human-readable summary of what you are doing.",
                },
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {"type": "string", "description": "e.g. light, switch"},
                            "service": {"type": "string", "description": "e.g. turn_on, toggle"},
                            "entity_id": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ],
                                "description": "Entity ID or list of entity IDs",
                            },
                            "data": {
                                "type": "object",
                                "description": "Optional parameters e.g. brightness, color_temp",
                                "additionalProperties": True,
                            },
                        },
                        "required": ["domain", "service", "entity_id"],
                    },
                },
            },
            "required": ["actions", "explanation"],
        },
    }
]


REALTIME_CONTROLLABLE_DOMAINS = {
    "light", "switch", "cover", "climate", "script", "scene", "fan", "media_player",
}

REALTIME_SERVICE_DOMAINS = REALTIME_CONTROLLABLE_DOMAINS | {"homeassistant"}


async def build_realtime_context(hass: HomeAssistant) -> str:
    """Build a trimmed context for the Realtime API (controllable entities only)."""
    all_states = fetch_states(hass)
    all_services = await fetch_services(hass)
    areas = fetch_entity_areas(hass)

    simple_states = []
    for s in all_states:
        eid = s.get("entity_id", "")
        domain = eid.split(".")[0]
        if domain not in REALTIME_CONTROLLABLE_DOMAINS:
            continue
        entry = {
            "entity_id": eid,
            "state": s.get("state"),
            "name": s.get("attributes", {}).get("friendly_name", "Unknown"),
        }
        if eid in areas:
            entry["area"] = areas[eid]
        simple_states.append(entry)

    simple_services = []
    for svc_group in all_services:
        domain = svc_group.get("domain", "")
        if domain in REALTIME_SERVICE_DOMAINS:
            simple_services.append(svc_group)

    context = {"entities": simple_states, "services": simple_services}
    return json.dumps(context, indent=2)


def _instructions_from_context(hass_context_text: str) -> str:
    return (
        "ROLE: You are a Home Assistant controller. "
        "You MUST ALWAYS respond by calling the execute_plan tool. "
        "NEVER respond with plain text. ALWAYS call the tool.\n\n"
        "TOOL SCHEMA:\n"
        "{\n"
        '  "actions": [\n'
        "    {\n"
        '      "domain": "light",\n'
        '      "service": "turn_on",\n'
        '      "entity_id": "light.living_room",\n'
        '      "data": {"brightness": 220}\n'
        "    }\n"
        "  ],\n"
        '  "explanation": "Human-readable summary of what you did"\n'
        "}\n\n"
        "IMPORTANT RULES:\n"
        "- entity_id can be a string or a list of strings\n"
        "- Use ONLY domains, services, and entity_ids from the context below\n"
        "- If the user asks a general question (not a device command), "
        "return actions: [] with an explanation answering the question\n"
        "- If the user asks to control devices, return the matching actions\n\n"
        f"HOME ASSISTANT CONTEXT (states + services):\n{hass_context_text}\n\n"
        "REMEMBER: You MUST call execute_plan for EVERY request. Never reply with plain text."
    )


def _create_ssl_context() -> ssl.SSLContext:
    """Create SSL context (blocking call, must run in executor)."""
    ctx = ssl.create_default_context()
    return ctx


def _audio_file_to_24k_pcm(audio_path: str) -> bytes:
    """Convert audio file to 24kHz mono s16 PCM for Realtime API."""
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
        raw_path = tmp.name
    try:
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-ar", str(REALTIME_RATE), "-ac", "1", "-f", "s16le", raw_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        with open(raw_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(raw_path):
            try:
                os.unlink(raw_path)
            except OSError:
                pass


async def _run_realtime_session(
    hass: HomeAssistant,
    api_key: str,
    audio_pcm: bytes,
    allow_cfg: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run Realtime WebSocket session; return (explanation_or_text, actions_executed)."""
    import websockets
    from ... import call_model as _call_model

    executed_actions: list[dict[str, Any]] = []
    response_text_parts: list[str] = []

    _LOGGER.info("Building trimmed HA context for Realtime session...")
    hass_context_text = await build_realtime_context(hass)
    instructions = _instructions_from_context(hass_context_text)
    _LOGGER.info("Realtime context built (%d chars). Connecting to Realtime API...", len(hass_context_text))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "realtime=v1",
    }

    loop = asyncio.get_running_loop()
    ssl_ctx = await loop.run_in_executor(None, _create_ssl_context)
    _LOGGER.debug("SSL context created in executor (no event-loop blocking)")

    response_complete = asyncio.Event()

    async def receive_events(ws):
        nonlocal response_text_parts, executed_actions
        async for message in ws:
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            _LOGGER.debug("Realtime event: %s", event_type)

            if event_type == "error":
                err = event.get("error", {})
                _LOGGER.error("Realtime API error: %s", err)
                response_complete.set()
                return

            elif event_type == "session.created":
                _LOGGER.info("Realtime session created")

            elif event_type == "session.updated":
                _LOGGER.info("Realtime session updated (tools registered)")

            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = event.get("transcript", "").strip()
                _LOGGER.info("User said: %s", transcript)

            elif event_type == "response.text.delta":
                response_text_parts.append(event.get("delta", ""))

            elif event_type == "response.function_call_arguments.done":
                fn_name = event.get("name")
                args_str = event.get("arguments", "{}")
                _LOGGER.info("Tool call received: %s", fn_name)
                if fn_name == "execute_plan":
                    try:
                        plan = json.loads(args_str)
                        _LOGGER.info("execute_plan payload: %s", json.dumps(plan, indent=2)[:500])
                        actions = plan.get("actions") or []
                        explanation = plan.get("explanation", "")
                        if explanation:
                            response_text_parts.append(explanation)
                        for act in actions:
                            _LOGGER.info("Executing action: %s.%s on %s", act.get("domain"), act.get("service"), act.get("entity_id"))
                            await _call_model._execute_tool_call(hass, act, allow_cfg)
                            executed_actions.append(act)
                        _LOGGER.info("All actions executed")
                    except (json.JSONDecodeError, TypeError) as e:
                        _LOGGER.warning("Realtime execute_plan parse error: %s", e)

            elif event_type == "response.done":
                _LOGGER.info("Realtime response complete")
                response_complete.set()
                return

    _LOGGER.info("Opening WebSocket to %s", REALTIME_URL)
    async with websockets.connect(REALTIME_URL, additional_headers=headers, ssl=ssl_ctx) as ws:
        _LOGGER.info("WebSocket connected")

        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "instructions": instructions,
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": None,
                "tools": TOOLS,
                "tool_choice": "required",
            },
        }
        await ws.send(json.dumps(session_update))
        _LOGGER.info("Session config sent")

        audio_len = len(audio_pcm)
        chunks_sent = 0
        for i in range(0, audio_len, CHUNK_BYTES):
            chunk = audio_pcm[i : i + CHUNK_BYTES]
            if not chunk:
                break
            b64 = base64.b64encode(chunk).decode("utf-8")
            await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": b64}))
            chunks_sent += 1
        _LOGGER.info("Audio sent: %d bytes in %d chunks", audio_len, chunks_sent)

        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        _LOGGER.info("Audio buffer committed")
        await ws.send(json.dumps({"type": "response.create"}))
        _LOGGER.info("Response requested, waiting for Realtime API...")

        recv_task = asyncio.create_task(receive_events(ws))
        try:
            await asyncio.wait_for(response_complete.wait(), timeout=120.0)
            _LOGGER.info("Response complete event received")
        except asyncio.TimeoutError:
            _LOGGER.warning("Realtime session timed out after 120s")
        finally:
            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError:
                pass

    explanation = "".join(response_text_parts).strip() or "Done."
    _LOGGER.info("Realtime session finished. Explanation: %s", explanation[:200])
    return explanation, executed_actions


async def process_realtime_audio(
    hass: HomeAssistant,
    api_key: str,
    audio_path: str,
    allow_cfg: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Process an audio file with the Realtime API and execute any tool calls.
    Returns {"explanation": str, "actions": list} and updates sensor / fires event.
    """
    _LOGGER.info("=== PROCESS REALTIME AUDIO START === file: %s", audio_path)

    if not os.path.isfile(audio_path):
        _LOGGER.error("Audio file not found: %s", audio_path)
        return {"explanation": f"Audio file not found: {audio_path}", "actions": []}

    file_size = os.path.getsize(audio_path)
    _LOGGER.info("Audio file size: %d bytes", file_size)

    try:
        loop = asyncio.get_running_loop()
        _LOGGER.info("Converting audio to 24kHz PCM via ffmpeg...")
        audio_pcm = await loop.run_in_executor(
            None, _audio_file_to_24k_pcm, audio_path
        )
        _LOGGER.info("Audio converted: %d bytes PCM", len(audio_pcm))
    except subprocess.CalledProcessError as e:
        _LOGGER.error("ffmpeg conversion failed: %s", e)
        return {"explanation": f"Audio conversion failed: {e}", "actions": []}
    except Exception as e:
        _LOGGER.exception("Audio read failed: %s", e)
        return {"explanation": str(e), "actions": []}

    if not audio_pcm:
        _LOGGER.error("No audio data after conversion")
        return {"explanation": "No audio data", "actions": []}

    try:
        explanation, executed_actions = await _run_realtime_session(
            hass, api_key, audio_pcm, allow_cfg
        )
    except Exception as e:
        _LOGGER.exception("Realtime session failed: %s", e)
        return {"explanation": f"Realtime API error: {e}", "actions": []}

    _LOGGER.info("=== PROCESS REALTIME AUDIO DONE === actions=%d", len(executed_actions))

    data_store = hass.data.get("llm_home_assistant", {})
    sensor_entity = data_store.get("sensor_entity")
    if sensor_entity:
        sensor_entity.update_response(explanation)
        _LOGGER.info("Sensor updated with explanation")
    hass.bus.async_fire("llm_response_ready", {"payload": explanation})

    return {"explanation": explanation, "actions": executed_actions}
