"""Audio-capable caller supporting both OpenAI gpt-4o-audio-preview and local Qwen2-Audio models."""
from __future__ import annotations

import asyncio
import json
import logging
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
from .tool_defs import PROPOSE_ACTIONS_TOOL, PROPOSE_AUTOMATION_TOOL

_LOGGER = logging.getLogger(__name__)

_CLIENT_LOCK = threading.Lock()
LOCAL_CLIENT: OpenAI | None = None


# ============================================================================
# Configuration
# ============================================================================

AUDIO_MODEL = "gpt-4o-audio-preview"

USE_LOCAL_AUDIO_MODEL = False
LOCAL_AUDIO_BASE_URL = "http://127.0.0.1:8010/v1"
LOCAL_AUDIO_MODEL_NAME = "qwen2-audio-7b-instruct"


# ============================================================================
# System Prompts
# ============================================================================

SYSTEM_PROMPT_TEMPLATE = """\
SYSTEM INSTRUCTION - YOU MUST FOLLOW:
You are an AI voice assistant controlling a Home Assistant home automation system.
Your ONLY job is to understand spoken commands and execute Home Assistant services.

RESPONSE FORMAT:
Return ONLY valid JSON with NO additional text, explanations, or conversational responses.
Format: {{'actions': [{{'domain': 'domain', 'service': 'service', 'entity_id': 'entity_id or [list]', 'data': {{'key': 'value'}}}}, ...], 'explanation': 'brief summary'}}

RULES:
1. ALWAYS return JSON format. NEVER add conversational text before or after JSON.
2. ONLY use services from the provided Home Assistant context.
3. Use specific services: light.turn_on, light.turn_off, switch.turn_on, switch.turn_off, etc.
4. NEVER use generic services like homeassistant.turn_on.
5. For colors: ALWAYS use rgb_color as [R,G,B] values (0-255).
6. Batch multiple entities into one action when possible.
7. MAX 3 actions per request. Prefer 1 if possible.
8. If no actions needed (user asking about state), return empty actions array.
9. Always provide a 10-word explanation of what you did.

HOME ASSISTANT CONTEXT:
{context}

IMPORTANT: You are NOT a chatbot. You are a Home Assistant controller. Execute commands, don't chat."""

AUTOMATION_AUDIO_SYSTEM_PROMPT_TEMPLATE = """\
You are a voice-controlled automation builder for Home Assistant.
The user is speaking a description of an automation they want. Understand their
spoken request and return automation YAML with execution plan.

RESPONSE FORMAT:
Return ONLY valid JSON with NO additional text.
Format: {{'automation_yaml': 'yaml string', 'execution_plan': {{'actions': [...], 'explanation': '...'}}, 'validation_checklist': [...], 'questions': [...]}}

RULES:
1. ALWAYS return JSON format. NEVER add conversational text.
2. automation_yaml must be complete valid Home Assistant YAML.
3. For colors: ALWAYS use rgb_color as [R,G,B] (0-255). NEVER use xy_color or color_temp.
4. execution_plan.actions: list of domain/service/entity_id/data objects.
5. validation_checklist: 2-5 items the user should verify.
6. questions: 0-2 clarifying questions if ambiguous, else empty list.
7. Only use entity_ids and services from provided context.
8. Batch multiple entities into single action when possible.

Context key: e=entity_id, n=name, d=domain, s=state, b=brightness, cm=color_modes, c=supports_color, pos=position, area=room.

HOME ASSISTANT CONTEXT:
{context}"""


# ============================================================================
# OpenAI AUDIO FUNCTIONS (no tool calling)
# ============================================================================

def _openai_blocking_audio_call(
    api_key: str,
    system_prompt: str,
    user_text: str | None,
    audio_b64: str,
    audio_format: str,
) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, Any]]:
    """OpenAI audio call - uses text output directly (no tool calling).
    Runs in executor to avoid blocking HA loop.
    """
    client = _get_client(api_key)
    
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

    t0 = time.monotonic()
    response = client.chat.completions.create(
        model=AUDIO_MODEL,
        messages=messages,
        max_tokens=256,
        temperature=0.1,
    )
    api_call_time = time.monotonic() - t0

    usage_info = _extract_usage(response)
    choice = response.choices[0]
    content = choice.message.content or ""
    
    debug_info: dict[str, Any] = {
        "system_prompt": system_prompt,
        "model_used": AUDIO_MODEL,
        "api_call_time": round(api_call_time, 4),
        "raw_response": content,
    }

    if content:
        json_str = content.strip()
        
        try:
            raw = json.loads(json_str)
            raw = _normalize_actions(raw)
            plan = Plan.model_validate(raw)
            debug_info["parse_success"] = True
            debug_info["pydantic_valid"] = True
            return plan.model_dump(), usage_info, debug_info
        except json.JSONDecodeError:
            try:
                import re
                json_match = re.search(r'\{.*\}', json_str, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    raw = json.loads(json_str)
                    raw = _normalize_actions(raw)
                    plan = Plan.model_validate(raw)
                    debug_info["parse_success"] = True
                    debug_info["pydantic_valid"] = True
                    debug_info["extracted_from_text"] = True
                    return plan.model_dump(), usage_info, debug_info
            except Exception as parse_exc:
                _LOGGER.warning("OpenAI audio JSON parse failed: %s", parse_exc)
        
        debug_info["parse_success"] = False
        debug_info["pydantic_valid"] = False

    return {
        "actions": [],
        "explanation": content or "Audio model returned no actionable response.",
    }, usage_info, debug_info


# ============================================================================
# LOCAL AUDIO FUNCTIONS (Qwen2-Audio - no tool calling)
# ============================================================================

def _get_local_client() -> OpenAI:
    """Return a reusable OpenAI client for local server."""
    global LOCAL_CLIENT
    with _CLIENT_LOCK:
        if LOCAL_CLIENT is None:
            LOCAL_CLIENT = OpenAI(
                api_key="not-needed",
                base_url=LOCAL_AUDIO_BASE_URL,
            )
            _LOGGER.debug("Created local audio client: %s", LOCAL_AUDIO_BASE_URL)
        return LOCAL_CLIENT


def _local_blocking_audio_call(
    system_prompt: str,
    user_text: str | None,
    audio_b64: str,
    audio_format: str,
) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, Any]]:
    """Local Qwen2-Audio call - uses text output directly (no tool calling).
    Runs in executor to avoid blocking HA loop.
    """
    client = _get_local_client()
    
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

    t0 = time.monotonic()
    response = client.chat.completions.create(
        model=LOCAL_AUDIO_MODEL_NAME,
        messages=messages,
        max_tokens=256,
        temperature=0.1,
    )
    api_call_time = time.monotonic() - t0

    usage_info = _extract_usage(response)
    choice = response.choices[0]
    content = choice.message.content or ""
    
    debug_info: dict[str, Any] = {
        "system_prompt": system_prompt,
        "model_used": LOCAL_AUDIO_MODEL_NAME,
        "api_call_time": round(api_call_time, 4),
        "raw_response": content,
    }

    _LOGGER.info("Qwen2-Audio response: %s", content)
    
    if content:
        json_str = content.strip()
        
        try:
            raw = json.loads(json_str)
            raw = _normalize_actions(raw)
            plan = Plan.model_validate(raw)
            debug_info["parse_success"] = True
            debug_info["pydantic_valid"] = True
            return plan.model_dump(), usage_info, debug_info
        except json.JSONDecodeError:
            try:
                import re
                json_match = re.search(r'\{.*\}', json_str, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    raw = json.loads(json_str)
                    raw = _normalize_actions(raw)
                    plan = Plan.model_validate(raw)
                    debug_info["parse_success"] = True
                    debug_info["pydantic_valid"] = True
                    debug_info["extracted_from_text"] = True
                    return plan.model_dump(), usage_info, debug_info
            except Exception as parse_exc:
                _LOGGER.warning("Qwen2-Audio JSON parse failed: %s", parse_exc)
        
        debug_info["parse_success"] = False
        debug_info["pydantic_valid"] = False

    return {
        "actions": [],
        "explanation": content or "Audio model returned no actionable response.",
    }, usage_info, debug_info


# ============================================================================
# AUDIO AUTOMATION - OPENAI (uses tool calling)
# ============================================================================

def _openai_blocking_audio_automation_call(
    api_key: str,
    system_prompt: str,
    user_text: str | None,
    audio_b64: str,
    audio_format: str,
    hass_context_text: str,
) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, Any]]:
    """OpenAI audio automation call - uses tool calling.
    Runs in executor to avoid blocking HA loop.
    """
    client = _get_client(api_key)
    
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
            _LOGGER.warning("OpenAI audio automation parse failed: %s", exc)
            debug_info["pydantic_valid"] = False
            needs_retry = True

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


# ============================================================================
# AUDIO AUTOMATION - LOCAL (no tool calling)
# ============================================================================

def _local_blocking_audio_automation_call(
    system_prompt: str,
    user_text: str | None,
    audio_b64: str,
    audio_format: str,
    hass_context_text: str,
) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, Any]]:
    """Local Qwen2-Audio automation call - uses text output directly (no tool calling).
    Runs in executor to avoid blocking HA loop.
    """
    client = _get_local_client()
    
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

    t0 = time.monotonic()
    response = client.chat.completions.create(
        model=LOCAL_AUDIO_MODEL_NAME,
        messages=messages,
        max_tokens=512,
        temperature=0.1,
    )
    api_call_time = time.monotonic() - t0

    usage_info = _extract_usage(response)
    choice = response.choices[0]
    content = choice.message.content or ""
    
    debug_info: dict[str, Any] = {
        "system_prompt": system_prompt,
        "model_used": LOCAL_AUDIO_MODEL_NAME,
        "api_call_time": round(api_call_time, 4),
        "raw_response": content,
    }

    _LOGGER.info("Qwen2-Audio automation response: %s", content)
    
    if content:
        json_str = content.strip()
        
        try:
            raw = json.loads(json_str)
            result = AutomationOutput.model_validate(raw).model_dump()
            debug_info["parse_success"] = True
            debug_info["pydantic_valid"] = True
            
            sem_warnings = _validate_automation_semantics(result, hass_context_text)
            if sem_warnings:
                questions = result.get("questions", [])
                checklist = result.get("validation_checklist", [])
                for w in sem_warnings:
                    if len(questions) < 2:
                        questions.append(w)
                    else:
                        checklist.append(w)
                result["questions"] = questions
                result["validation_checklist"] = checklist
            
            return result, usage_info, debug_info
        except json.JSONDecodeError:
            try:
                import re
                json_match = re.search(r'\{.*\}', json_str, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    raw = json.loads(json_str)
                    result = AutomationOutput.model_validate(raw).model_dump()
                    debug_info["parse_success"] = True
                    debug_info["pydantic_valid"] = True
                    debug_info["extracted_from_text"] = True
                    
                    sem_warnings = _validate_automation_semantics(result, hass_context_text)
                    if sem_warnings:
                        questions = result.get("questions", [])
                        checklist = result.get("validation_checklist", [])
                        for w in sem_warnings:
                            if len(questions) < 2:
                                questions.append(w)
                            else:
                                checklist.append(w)
                        result["questions"] = questions
                        result["validation_checklist"] = checklist
                    
                    return result, usage_info, debug_info
            except Exception as parse_exc:
                _LOGGER.warning("Qwen2-Audio automation JSON parse failed: %s", parse_exc)
        
        debug_info["parse_success"] = False
        debug_info.setdefault("pydantic_valid", False)
    return {
        "automation_yaml": "",
        "execution_plan": {"actions": [], "explanation": content or "Audio automation model returned no valid response."},
        "validation_checklist": ["LLM output could not be parsed into valid automation YAML"],
        "questions": ["Could not parse automation from audio. Please try again."],
    }, usage_info, debug_info


# ============================================================================
# ASYNC WRAPPERS - Dispatch based on USE_LOCAL_AUDIO_MODEL
# ============================================================================

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
    """Async entry-point: send audio to audio model."""
    _LOGGER.debug("Preparing audio call (model: %s)", AUDIO_MODEL)

    t_ctx = time.monotonic()
    try:
        from ...device_info import build_compact_context
        hass_context_text = build_compact_context(hass, allow_cfg, force_rebuild=force_rebuild)
        _LOGGER.info("Compact context size: %d chars", len(hass_context_text))
    except Exception as exc:
        _LOGGER.error("Failed to build HA context: %s", exc)
        return {"actions": [], "explanation": f"Failed to build HA context: {exc}"}
    context_build_time = round(time.monotonic() - t_ctx, 4)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.replace("{context}", hass_context_text)

    loop = asyncio.get_running_loop()

    try:
        if USE_LOCAL_AUDIO_MODEL:
            _LOGGER.info("Using local Qwen2-Audio model")
            data, usage_info, debug_info = await loop.run_in_executor(
                None,
                _local_blocking_audio_call,
                system_prompt,
                user_text,
                audio_b64,
                audio_format,
            )
        else:
            _LOGGER.info("Using OpenAI gpt-4o-audio-preview")
            data, usage_info, debug_info = await loop.run_in_executor(
                None,
                _openai_blocking_audio_call,
                api_key,
                system_prompt,
                user_text,
                audio_b64,
                audio_format,
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
                "Audio API token usage - Input: %d, Output: %d, Total: %d%s",
                usage_info["prompt_tokens"],
                usage_info["completion_tokens"],
                usage_info["total_tokens"],
                cache_info,
            )

    except Exception as exc:
        _LOGGER.error("Audio API request failed: %s", exc)
        return {"actions": [], "explanation": f"Audio model call failed: {exc}"}

    _LOGGER.info("OpenAI audio normal response: %s", data)
    return data


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
    """Async entry-point: send audio to audio model for automation building."""
    _LOGGER.info("Audio automation mode (model: %s)", AUDIO_MODEL)

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

    system_prompt = AUTOMATION_AUDIO_SYSTEM_PROMPT_TEMPLATE.replace("{context}", hass_context_text)

    loop = asyncio.get_running_loop()

    try:
        if USE_LOCAL_AUDIO_MODEL:
            _LOGGER.info("Using local Qwen2-Audio model for automation")
            data, usage_info, debug_info = await loop.run_in_executor(
                None,
                _local_blocking_audio_automation_call,
                system_prompt,
                user_text,
                audio_b64,
                audio_format,
                hass_context_text,
            )
        else:
            _LOGGER.info("Using OpenAI gpt-4o-audio-preview for automation")
            data, usage_info, debug_info = await loop.run_in_executor(
                None,
                _openai_blocking_audio_automation_call,
                api_key,
                system_prompt,
                user_text,
                audio_b64,
                audio_format,
                hass_context_text,
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

    except Exception as exc:
        _LOGGER.error("Audio automation API request failed: %s", exc)
        return {
            "automation_yaml": "",
            "execution_plan": {"actions": [], "explanation": f"Audio automation call failed: {exc}"},
            "validation_checklist": [],
            "questions": [],
        }

    _LOGGER.info("OpenAI audio automation response: %s", data)
    return data
