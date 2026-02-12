"""Audio-capable OpenAI caller using gpt-4o-audio-preview with function calling."""
from __future__ import annotations

import asyncio
import json
import logging
import os
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
_AUDIO_TOKEN_CAP = 280
_AUDIO_RETRY_CAP = 500
_FIX_JSON_MSG = "Fix JSON: return ONLY valid JSON for the required schema. No prose."

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
) -> tuple[dict[str, Any], dict[str, int] | None]:
    """Synchronous helper — runs in executor to avoid blocking the HA loop."""
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

    # --- First attempt with standard token cap ---
    data, usage_info, retry_reason = _try_audio_call(
        client, messages, _AUDIO_TOKEN_CAP,
    )
    if data is not None:
        return data, usage_info

    # --- Retry once with higher cap + fix-JSON system message ---
    _LOGGER.warning("Audio token cap retry triggered (cap=%d) reason=%s", _AUDIO_RETRY_CAP, retry_reason)
    retry_messages = list(messages) + [
        {"role": "system", "content": _FIX_JSON_MSG},
    ]
    data, usage_info, _ = _try_audio_call(
        client, retry_messages, _AUDIO_RETRY_CAP,
    )
    if data is not None:
        return data, usage_info

    return {
        "actions": [],
        "explanation": f"Audio failed after retry: {retry_reason}",
    }, usage_info


def _try_audio_call(
    client: OpenAI,
    messages: list[dict[str, Any]],
    max_tokens: int,
) -> tuple[dict[str, Any] | None, dict[str, int] | None, str]:
    """
    Single attempt at an audio OpenAI call.
    Returns (data, usage_info, retry_reason). data is None if retry needed.
    """
    response = client.chat.completions.create(
        model=AUDIO_MODEL,
        messages=messages,
        modalities=["text"],
        tools=[PROPOSE_ACTIONS_TOOL],
        tool_choice={"type": "function", "function": {"name": "propose_actions"}},
        max_completion_tokens=max_tokens,
    )

    usage_info = _extract_usage(response)

    # Check for truncation
    choice = response.choices[0]
    if choice.finish_reason == "length":
        return None, usage_info, "truncated"

    tool_calls = choice.message.tool_calls

    if tool_calls:
        args_str = tool_calls[0].function.arguments
        try:
            plan = Plan.model_validate_json(args_str)
            return plan.model_dump(), usage_info, ""
        except Exception as exc:
            _LOGGER.warning(
                "Pydantic validation failed, trying raw JSON: %s", exc
            )
            try:
                raw = json.loads(args_str)
                return {
                    "actions": raw.get("actions", []),
                    "explanation": raw.get("explanation", ""),
                }, usage_info, ""
            except json.JSONDecodeError as je:
                _LOGGER.error("JSON decode failed for tool_call args: %s", je)
                return None, usage_info, f"parse_error: {je}"

    # Fallback: try to parse text content
    content = choice.message.content or ""
    if content:
        _LOGGER.warning("No tool_call returned; attempting text fallback parse")
        try:
            plan = Plan.model_validate_json(content)
            return plan.model_dump(), usage_info, ""
        except Exception:
            pass

    return {
        "actions": [],
        "explanation": content or "Audio model returned no actionable response.",
    }, usage_info, ""


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
    try:
        from ...device_info import build_compact_context
        hass_context_text = build_compact_context(hass, allow_cfg, force_rebuild=force_rebuild)
        _LOGGER.info("Compact context size: %d chars", len(hass_context_text))
    except Exception as exc:
        _LOGGER.error("Failed to build HA context: %s", exc)
        return {"actions": [], "explanation": f"Failed to build HA context: {exc}"}

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(context=hass_context_text)

    loop = asyncio.get_running_loop()

    try:
        data, usage_info = await loop.run_in_executor(
            None,
            _blocking_audio_gpt_call,
            api_key,
            system_prompt,
            user_text,
            audio_b64,
            audio_format,
            model_name,
        )

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
