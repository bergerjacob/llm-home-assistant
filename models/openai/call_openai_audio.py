"""Audio-capable OpenAI caller using gpt-4o-audio-preview with function calling."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any

from openai import OpenAI
import aiohttp

from homeassistant.core import HomeAssistant

from .call_openai import (
    Action,
    Plan,
    AutomationOutput,
    _normalize_actions,
    _validate_automation_semantics,
    _save_cache_stats,
    _get_client,
    _extract_usage,
    NEEDS_CONTEXT,
)
from openai import OpenAI
from .tool_defs import PROPOSE_ACTIONS_TOOL, PROPOSE_AUTOMATION_TOOL

_LOGGER = logging.getLogger(__name__)

_client_lock = threading.Lock()
_local_client: OpenAI | None = None


def _get_local_client() -> OpenAI:
    """Return a reusable OpenAI client for local server."""
    global _local_client
    with _client_lock:
        if _local_client is None:
            _local_client = OpenAI(
                api_key="not-needed",
                base_url=LOCAL_AUDIO_BASE_URL,
            )
            _LOGGER.debug("Created local audio client: %s", LOCAL_AUDIO_BASE_URL)
        return _local_client

AUDIO_MODEL = "gpt-4o-audio-preview"

USE_LOCAL_AUDIO_MODEL = True

LOCAL_AUDIO_BASE_URL = "http://127.0.0.1:8010/v1"
LOCAL_AUDIO_MODEL = "/tmp/bergejac/models/models--Qwen--Qwen2-Audio-7B-Instruct/snapshots/0a095220c30b7b31434169c3086508ef3ea5bf0a/"

SYSTEM_PROMPT_TEMPLATE = """\
You are a voice-controlled Home Assistant.
The user is speaking a command. Understand their spoken request and call the
`propose_actions` tool with the appropriate Home Assistant service calls.

Rules:
- IMPORTANT: Use specific domain services like `light.turn_on`, NOT `homeassistant.turn_on`.
- For ALL color/temperature changes, ALWAYS use `rgb_color` as [R,G,B] (0-255). NEVER use xy_color, hs_color, or color_temp.
  Examples: warm white=[255,180,100], cool white=[200,220,255], sky blue=[135,206,235], red=[255,0,0].
- Batch multiple targets into one action with an entity_id list when they share the same service and data.
- entity_id can be a single string or a list of strings.
- Max 3 actions per request. Prefer 1. Keep explanation under 15 words.
- Only use entity_ids and services that appear in the context below.
- If the user asks about state, return empty actions and explain current state.
- For ambiguous names, pick the closest match from the entity list.
- Always provide an explanation.

Context key: e=entity_id, n=name, d=domain, s=state, b=brightness, cm=color_modes, c=supports_color, pos=position, area=room, dc=device_class, unit=unit_of_measurement, val=value, rem=remaining, bat=battery_level, vol=volume_level, mut=muted, title=media_title, spd=speed, spd_opts=speed_options, hum=humidity, opts=options, min/max/step=range, fin=finishes_at, st=status.

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
    if USE_LOCAL_AUDIO_MODEL:
        client = _get_local_client()
        active_model = LOCAL_AUDIO_MODEL
    else:
        client = _get_client(api_key)
        active_model = AUDIO_MODEL
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

    _LOGGER.debug("Calling %s with audio (%s format)", active_model, audio_format)

    t0 = time.monotonic()
    response = client.chat.completions.create(
        model=active_model,
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
        "model_used": active_model,
        "api_call_time": round(api_call_time, 4),
    }

    if tool_calls:
        args_str = tool_calls[0].function.arguments
        debug_info["raw_response"] = args_str
        try:
            raw = json.loads(args_str)
            raw = _normalize_actions(raw)
            plan = Plan.model_validate(raw)
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
                raw = _normalize_actions(raw)
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
            raw = json.loads(content)
            raw = _normalize_actions(raw)
            plan = Plan.model_validate(raw)
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


# ---------------------------------------------------------------------------
# Audio Automation Builder
# ---------------------------------------------------------------------------

AUTOMATION_AUDIO_SYSTEM_PROMPT_TEMPLATE = """\
You are a voice-controlled automation builder for Home Assistant.
The user is speaking a description of an automation they want. Understand their
spoken request and call the `propose_automation` tool with a complete automation.

Rules:
- IMPORTANT: Use specific domain services like `light.turn_on`, NOT `homeassistant.turn_on`.
- For ALL color/temperature changes, ALWAYS use `rgb_color` as [R,G,B] (0-255). NEVER use xy_color, hs_color, or color_temp.
  Examples: warm white=[255,180,100], cool white=[200,220,255], sky blue=[135,206,235], red=[255,0,0].
- automation_yaml: a complete, valid Home Assistant automation YAML string.
- execution_plan: the actions the automation would trigger, using domain/service/entity_id/data.
- validation_checklist: 2-5 items the user should verify (triggers, conditions, entities).
- questions: 0-2 clarifying questions if the request is ambiguous. Empty list if clear.
- Only use entity_ids and services from the context below.
- Batch multiple targets into one action with an entity_id list when they share the same service and data.
- Always provide an explanation in execution_plan.

Context key: e=entity_id, n=name, d=domain, s=state, b=brightness, cm=color_modes, c=supports_color, pos=position, area=room.

HOME ASSISTANT CONTEXT:
{context}"""


def _blocking_audio_automation_gpt_call(
    api_key: str,
    system_prompt: str,
    user_text: str | None,
    audio_b64: str,
    audio_format: str,
    hass_context_text: str,
    model_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, Any]]:
    """Synchronous helper for audio automation — runs in executor.
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

    _LOGGER.debug("Calling %s with audio for automation (%s format)", AUDIO_MODEL, audio_format)

    t0 = time.monotonic()
    response = client.chat.completions.create(
        model=AUDIO_MODEL,
        messages=messages,
        modalities=["text"],
        tools=[PROPOSE_AUTOMATION_TOOL],
        tool_choice={"type": "function", "function": {"name": "propose_automation"}},
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

    retried = False

    def _parse_tool_args(args_str: str) -> dict[str, Any]:
        raw = json.loads(args_str)
        return AutomationOutput.model_validate(raw).model_dump()

    # Check tool call exists and has correct name
    needs_retry = (
        not tool_calls
        or not tool_calls[0].function
        or tool_calls[0].function.name != "propose_automation"
    )

    if not needs_retry:
        args_str = tool_calls[0].function.arguments
        debug_info["raw_response"] = args_str
        try:
            data = _parse_tool_args(args_str)
            debug_info["parse_success"] = True
            debug_info["pydantic_valid"] = True

            # Semantic validation
            sem_warnings = _validate_automation_semantics(data, hass_context_text)
            if sem_warnings:
                questions = data.get("questions", [])
                checklist = data.get("validation_checklist", [])
                for w in sem_warnings:
                    if len(questions) < 2:
                        questions.append(w)
                    else:
                        checklist.append(w)
                data["questions"] = questions
                data["validation_checklist"] = checklist

            return data, usage_info, debug_info
        except Exception as exc:
            _LOGGER.warning("Audio automation parse failed: %s", exc)
            debug_info["pydantic_valid"] = False
            needs_retry = True

    # Retry once (shared budget)
    if needs_retry and not retried:
        retried = True
        _LOGGER.info("Retrying audio automation call (missing/invalid tool call)")
        messages.append({
            "role": "system",
            "content": (
                "Call propose_automation. Do not respond with text. "
                "validation_checklist must have 2-5 items. questions max 2."
            ),
        })
        try:
            t0_retry = time.monotonic()
            retry_resp = client.chat.completions.create(
                model=AUDIO_MODEL,
                messages=messages,
                modalities=["text"],
                tools=[PROPOSE_AUTOMATION_TOOL],
                tool_choice={"type": "function", "function": {"name": "propose_automation"}},
            )
            debug_info["retry_api_call_time"] = round(time.monotonic() - t0_retry, 4)
            retry_tc = retry_resp.choices[0].message.tool_calls
            if retry_tc and retry_tc[0].function.name == "propose_automation":
                args_str = retry_tc[0].function.arguments
                debug_info["raw_response_retry"] = args_str
                data = _parse_tool_args(args_str)
                debug_info["parse_success"] = True
                debug_info["pydantic_valid"] = True

                sem_warnings = _validate_automation_semantics(data, hass_context_text)
                if sem_warnings:
                    questions = data.get("questions", [])
                    checklist = data.get("validation_checklist", [])
                    for w in sem_warnings:
                        if len(questions) < 2:
                            questions.append(w)
                        else:
                            checklist.append(w)
                    data["questions"] = questions
                    data["validation_checklist"] = checklist

                return data, usage_info, debug_info
        except Exception as retry_exc:
            _LOGGER.error("Audio automation retry failed: %s", retry_exc)
            debug_info["parse_success"] = False
            debug_info["pydantic_valid"] = False

    # Fallback: return schema-valid error output
    debug_info["parse_success"] = False
    debug_info.setdefault("pydantic_valid", False)
    return {
        "automation_yaml": "",
        "execution_plan": {"actions": [], "explanation": "Audio automation model returned no valid response."},
        "validation_checklist": [
            "LLM output could not be parsed into valid automation YAML",
            "Retry the request or simplify the automation description",
        ],
        "questions": ["Could not parse automation from audio. Please try again."],
    }, usage_info, debug_info


async def async_query_openai_audio_automation(
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
    """Async entry-point: send audio to gpt-4o-audio-preview for automation building."""
    _LOGGER.info("Audio automation mode (model: %s)", AUDIO_MODEL)

    # Build compact context
    t_ctx = time.monotonic()
    try:
        from ...device_info import build_compact_context
        hass_context_text = build_compact_context(hass, allow_cfg, force_rebuild=force_rebuild)
        _LOGGER.info("Compact context size: %d chars", len(hass_context_text))
    except Exception as exc:
        _LOGGER.error("Failed to build HA context for audio automation: %s", exc)
        return {"_needs_context": True, "message": NEEDS_CONTEXT}

    if not hass_context_text or not hass_context_text.strip():
        _LOGGER.error("Compact context is empty for audio automation mode")
        return {"_needs_context": True, "message": NEEDS_CONTEXT}

    context_build_time = round(time.monotonic() - t_ctx, 4)

    system_prompt = AUTOMATION_AUDIO_SYSTEM_PROMPT_TEMPLATE.format(context=hass_context_text)

    loop = asyncio.get_running_loop()

    try:
        data, usage_info, debug_info = await loop.run_in_executor(
            None,
            _blocking_audio_automation_gpt_call,
            api_key,
            system_prompt,
            user_text,
            audio_b64,
            audio_format,
            hass_context_text,
            model_name,
        )

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
                "Audio automation API token usage - Input: %d, Output: %d, Total: %d%s",
                usage_info["prompt_tokens"],
                usage_info["completion_tokens"],
                usage_info["total_tokens"],
                cache_info,
            )
        else:
            _LOGGER.warning("Audio automation API response missing usage information")

    except Exception as exc:
        _LOGGER.error("Audio automation OpenAI API request failed: %s", exc)
        return {
            "automation_yaml": "",
            "execution_plan": {"actions": [], "explanation": f"Audio automation call failed: {exc}"},
            "validation_checklist": [],
            "questions": [],
        }

    _LOGGER.debug("Audio automation parsed response: %s", data)
    return data
