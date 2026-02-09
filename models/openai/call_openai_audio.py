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
    build_hass_context,
    _save_cache_stats,
)
from .tool_defs import PROPOSE_ACTIONS_TOOL

_LOGGER = logging.getLogger(__name__)

AUDIO_MODEL = "gpt-4o-audio-preview"

SYSTEM_PROMPT_TEMPLATE = """\
You are a voice-controlled Home Assistant.
The user is speaking a command. Understand their spoken request and call the
`propose_actions` tool with the appropriate Home Assistant service calls.

Rules:
- Only use entity_ids and services that appear in the context below.
- If the user asks about state, return empty actions and explain current state.
- For ambiguous names, pick the closest match from the entity list.
- Always provide an explanation.

HOME ASSISTANT CONTEXT:
{context}"""


def _blocking_audio_gpt_call(
    api_key: str,
    system_prompt: str,
    user_text: str | None,
    audio_b64: str,
    audio_format: str,
) -> tuple[dict[str, Any], dict[str, int] | None]:
    """Synchronous helper — runs in executor to avoid blocking the HA loop."""
    client = OpenAI(api_key=api_key)

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

    response = client.chat.completions.create(
        model=AUDIO_MODEL,
        messages=messages,
        modalities=["text"],
        tools=[PROPOSE_ACTIONS_TOOL],
        tool_choice={"type": "function", "function": {"name": "propose_actions"}},
    )

    # --- Extract usage info ---
    usage_info: dict[str, int] | None = None
    try:
        if response.usage:
            usage_info = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            if hasattr(response.usage, "prompt_tokens_details"):
                details = response.usage.prompt_tokens_details
                if details and hasattr(details, "cached_tokens"):
                    usage_info["cached_tokens"] = details.cached_tokens
                    usage_info["cache_hit_rate"] = (
                        details.cached_tokens / response.usage.prompt_tokens * 100
                        if response.usage.prompt_tokens > 0
                        else 0
                    )
            _save_cache_stats(usage_info)
    except Exception as exc:
        _LOGGER.error("Failed to extract token usage: %s", exc)

    # --- Parse function-call response ---
    choice = response.choices[0]
    tool_calls = choice.message.tool_calls

    if tool_calls:
        args_str = tool_calls[0].function.arguments
        try:
            plan = Plan.model_validate_json(args_str)
            return plan.model_dump(), usage_info
        except Exception as exc:
            _LOGGER.warning(
                "Pydantic validation failed, trying raw JSON: %s", exc
            )
            try:
                raw = json.loads(args_str)
                return {
                    "actions": raw.get("actions", []),
                    "explanation": raw.get("explanation", ""),
                }, usage_info
            except json.JSONDecodeError as je:
                _LOGGER.error("JSON decode failed for tool_call args: %s", je)

    # Fallback: try to parse text content
    content = choice.message.content or ""
    if content:
        _LOGGER.warning("No tool_call returned; attempting text fallback parse")
        try:
            plan = Plan.model_validate_json(content)
            return plan.model_dump(), usage_info
        except Exception:
            pass

    return {
        "actions": [],
        "explanation": content or "Audio model returned no actionable response.",
    }, usage_info


async def async_query_openai_audio(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    *,
    api_key: str,
    audio_b64: str,
    audio_format: str,
    user_text: str | None = None,
) -> dict[str, Any]:
    """Async entry-point: send audio directly to gpt-4o-audio-preview."""
    _LOGGER.debug("Preparing audio call (model: %s)", AUDIO_MODEL)

    try:
        hass_context_text = await build_hass_context(hass)
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
