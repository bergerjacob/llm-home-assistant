"""Audio-capable OpenAI caller using gpt-4o-audio-preview with function calling."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from openai import OpenAI
import aiohttp

from homeassistant.core import HomeAssistant

from .call_openai import (
    Action,
    Plan,
    _save_cache_stats,
    _get_client,
    _extract_usage,
)
from .tool_defs import PROPOSE_ACTIONS_TOOL

_LOGGER = logging.getLogger(__name__)

AUDIO_MODEL = "gpt-4o-audio-preview"

SYSTEM_PROMPT_TEMPLATE = """\
You are a voice-controlled Home Assistant.
The user is speaking a command. Understand their spoken request and call the
`propose_actions` tool with the appropriate Home Assistant service calls.

Rules:
- IMPORTANT: Use specific domain services like `light.turn_on`, NOT `homeassistant.turn_on`.
- Batch multiple targets into one action with an entity_id list when they share the same service and data.
- entity_id can be a single string or a list of strings.
- Max 3 actions per request. Prefer 1. Keep explanation under 15 words.
- Only use entity_ids and services that appear in the context below.
- If the user asks about state, return empty actions and explain current state.
- For ambiguous names, pick the closest match from the entity list.
- Always provide an explanation.

Context key: e=entity_id, n=name, d=domain, s=state, b=brightness, cm=color_modes, c=supports_color, pos=position, area=room.

HOME ASSISTANT CONTEXT:
{context}"""


def _blocking_audio_gpt_call(
    api_key: str,
    system_prompt: str,
    user_text: str | None,
    audio_b64: str,
    audio_format: str,
    model_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, Any]]:
    """Synchronous helper — runs in executor to avoid blocking the HA loop.
    Returns (data, usage_info, debug_info).
    """
    client = _get_client(api_key)

    if model_name and model_name != AUDIO_MODEL:
        _LOGGER.info(
            "Configured model=%s differs from audio model=%s; using %s",
            model_name, AUDIO_MODEL, AUDIO_MODEL,
        )

    # Build multimodal user content
    user_content: list[dict[str, Any]] = []
    if user_text:
        user_content.append({"type": "text", "text": user_text})
    user_content.append({
        "type": "input_audio",
        "input_audio": {"data": audio_b64, "format": audio_format},
    })

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    _LOGGER.debug("Calling %s with audio (%s format)", AUDIO_MODEL, audio_format)

    t0 = time.monotonic()
    response = client.chat.completions.create(
        model=AUDIO_MODEL,
        messages=messages,
        modalities=["text"],
        tools=[PROPOSE_ACTIONS_TOOL],
        tool_choice={"type": "function", "function": {"name": "propose_actions"}},
    )
    api_call_time = time.monotonic() - t0

    usage_info = _extract_usage(response)

    choice = response.choices[0]
    tool_calls = choice.message.tool_calls

    debug_info: dict[str, Any] = {
        "system_prompt": system_prompt,
        "model_used": AUDIO_MODEL,
        "api_call_time": round(api_call_time, 4),
    }

    if tool_calls:
        args_str = tool_calls[0].function.arguments
        debug_info["raw_response"] = args_str
        try:
            plan = Plan.model_validate_json(args_str)
            debug_info["parse_success"] = True
            debug_info["pydantic_valid"] = True
            return plan.model_dump(), usage_info, debug_info
        except Exception as exc:
            _LOGGER.warning(
                "Pydantic validation failed, trying raw JSON: %s", exc
            )
            debug_info["pydantic_valid"] = False
            try:
                raw = json.loads(args_str)
                debug_info["parse_success"] = True
                return {
                    "actions": raw.get("actions", []),
                    "explanation": raw.get("explanation", ""),
                }, usage_info, debug_info
            except json.JSONDecodeError as je:
                _LOGGER.error("JSON decode failed for tool_call args: %s", je)
                debug_info["parse_success"] = False

    # Fallback: try to parse text content
    content = choice.message.content or ""
    debug_info["raw_response"] = debug_info.get("raw_response", content)
    if content:
        _LOGGER.warning("No tool_call returned; attempting text fallback parse")
        try:
            plan = Plan.model_validate_json(content)
            debug_info["parse_success"] = True
            debug_info["pydantic_valid"] = True
            return plan.model_dump(), usage_info, debug_info
        except Exception:
            debug_info["parse_success"] = False
            debug_info["pydantic_valid"] = False

    return {
        "actions": [],
        "explanation": content or "Audio model returned no actionable response.",
    }, usage_info, debug_info


async def async_query_openai_audio(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    *,
    api_key: str,
    audio_b64: str,
    audio_format: str,
    user_text: str | None = None,
    allow_cfg: dict[str, Any] | None = None,
    model_name: str | None = None,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    """Async entry-point: send audio directly to gpt-4o-audio-preview."""
    _LOGGER.debug("Preparing audio call (model: %s)", AUDIO_MODEL)

    # Build compact context on the event loop (uses async_all / async_render)
    t_ctx = time.monotonic()
    try:
        from ...device_info import build_compact_context
        hass_context_text = build_compact_context(hass, allow_cfg, force_rebuild=force_rebuild)
        _LOGGER.info("Compact context size: %d chars", len(hass_context_text))
    except Exception as exc:
        _LOGGER.error("Failed to build HA context: %s", exc)
        return {"actions": [], "explanation": f"Failed to build HA context: {exc}"}
    context_build_time = round(time.monotonic() - t_ctx, 4)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(context=hass_context_text)

    loop = asyncio.get_running_loop()

    try:
        data, usage_info, debug_info = await loop.run_in_executor(
            None,
            _blocking_audio_gpt_call,
            api_key,
            system_prompt,
            user_text,
            audio_b64,
            audio_format,
            model_name,
        )

        # Attach debug info for the interaction logger
        data["_debug_info"] = debug_info
        data["_debug_info"]["context_build_time"] = context_build_time
        data["_debug_info"]["context_size_chars"] = len(hass_context_text)
        data["_debug_info"]["compact_context_packet"] = hass_context_text
        if usage_info:
            data["_debug_info"]["token_usage"] = usage_info

        if usage_info:
            cache_info = ""
            if "cached_tokens" in usage_info:
                cache_info = (
                    f", Cached: {usage_info['cached_tokens']} tokens "
                    f"({usage_info['cache_hit_rate']:.1f}%)"
                )
            _LOGGER.info(
                "Audio API token usage - Input: %d, Output: %d, Total: %d%s",
                usage_info["prompt_tokens"],
                usage_info["completion_tokens"],
                usage_info["total_tokens"],
                cache_info,
            )
        else:
            _LOGGER.warning("Audio API response missing usage information")

    except Exception as exc:
        _LOGGER.error("Audio OpenAI API request failed: %s", exc)
        return {"actions": [], "explanation": f"Audio model call failed: {exc}"}

    _LOGGER.debug("Audio parsed response (actions): %s", data)
    return data
