"""Unit tests for latency reduction features.

Covers:
- Client singleton (Task 2)
- Model passthrough (Task 3)
- Parallel action grouping (Task 4)
- Response cache (Task 5)
- State-query detection + context cache TTL (Task 6)

run_tests.py stubs all HA/openai/pydantic imports before this runs.
"""
import os
import sys
import time
import json

# Add the PARENT of the repo root so we can import the package by directory name
_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_parent = os.path.dirname(_repo)
_pkg = os.path.basename(_repo)
if _parent not in sys.path:
    sys.path.insert(0, _parent)


# ===================================================================
# Task 2: Client singleton
# ===================================================================

exec(f"from {_pkg}.models.openai.call_openai import _get_client, _client_lock")
_get_client = locals()["_get_client"]
_client_lock = locals()["_client_lock"]

# We need to mock OpenAI for the singleton test
import types

_call_count = 0
_last_key = None


def _mock_openai_cls(api_key=None):
    global _call_count, _last_key
    _call_count += 1
    _last_key = api_key
    obj = types.SimpleNamespace()
    obj.api_key = api_key
    return obj


class TestClientSingleton:
    """_get_client should reuse client when key is unchanged."""

    def setup_method(self):
        global _call_count
        _call_count = 0
        # Patch OpenAI class used by _get_client
        import importlib
        mod = sys.modules[f"{_pkg}.models.openai.call_openai"]
        self._orig_openai = getattr(mod, "OpenAI", None)
        mod.OpenAI = _mock_openai_cls
        # Reset singleton state
        mod._client = None
        mod._client_key = None

    def teardown_method(self):
        mod = sys.modules[f"{_pkg}.models.openai.call_openai"]
        if self._orig_openai is not None:
            mod.OpenAI = self._orig_openai
        mod._client = None
        mod._client_key = None

    def test_same_key_reuses_client(self):
        global _call_count
        c1 = _get_client("key-abc")
        c2 = _get_client("key-abc")
        assert c1 is c2
        assert _call_count == 1  # Only one OpenAI() call

    def test_different_key_creates_new_client(self):
        global _call_count
        c1 = _get_client("key-abc")
        c2 = _get_client("key-xyz")
        assert c1 is not c2
        assert _call_count == 2

    def test_lock_exists(self):
        import threading
        assert isinstance(_client_lock, type(threading.Lock()))


# ===================================================================
# Task 3: Model passthrough — verified via function signatures
# ===================================================================

exec(f"from {_pkg}.models.openai.call_openai import _blocking_gpt_call, async_query_openai")
_blocking_gpt_call = locals()["_blocking_gpt_call"]
async_query_openai = locals()["async_query_openai"]

exec(f"from {_pkg}.models.openai.call_openai_audio import _blocking_audio_gpt_call, async_query_openai_audio")
_blocking_audio_gpt_call = locals()["_blocking_audio_gpt_call"]
async_query_openai_audio = locals()["async_query_openai_audio"]

import inspect


class TestModelPassthrough:
    """Model name param exists in call signatures."""

    def test_blocking_gpt_call_has_model_name(self):
        sig = inspect.signature(_blocking_gpt_call)
        assert "model_name" in sig.parameters

    def test_async_query_openai_has_model_name(self):
        sig = inspect.signature(async_query_openai)
        assert "model_name" in sig.parameters

    def test_blocking_audio_gpt_call_has_model_name(self):
        sig = inspect.signature(_blocking_audio_gpt_call)
        assert "model_name" in sig.parameters

    def test_async_query_openai_audio_has_model_name(self):
        sig = inspect.signature(async_query_openai_audio)
        assert "model_name" in sig.parameters


# ===================================================================
# Task 4: Parallel action grouping
# ===================================================================

exec(f"from {_pkg}.call_model import _build_action_groups")
_build_action_groups = locals()["_build_action_groups"]


class TestBuildActionGroups:
    """_build_action_groups must partition actions by entity overlap."""

    def test_disjoint_entities_same_group(self):
        actions = [
            {"domain": "light", "service": "turn_on", "entity_id": "light.a"},
            {"domain": "light", "service": "turn_on", "entity_id": "light.b"},
        ]
        groups = _build_action_groups(actions)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_overlapping_entities_different_groups(self):
        actions = [
            {"domain": "light", "service": "turn_on", "entity_id": "light.a"},
            {"domain": "light", "service": "turn_off", "entity_id": "light.a"},
        ]
        groups = _build_action_groups(actions)
        assert len(groups) == 2

    def test_no_entity_own_group(self):
        actions = [
            {"domain": "scene", "service": "turn_on"},
            {"domain": "light", "service": "turn_on", "entity_id": "light.a"},
        ]
        groups = _build_action_groups(actions)
        # scene has no entity_id → own group; light → own group
        assert len(groups) == 2

    def test_empty_actions(self):
        assert _build_action_groups([]) == []

    def test_list_entity_id(self):
        actions = [
            {"domain": "light", "service": "turn_on", "entity_id": ["light.a", "light.b"]},
            {"domain": "switch", "service": "turn_on", "entity_id": "switch.c"},
        ]
        groups = _build_action_groups(actions)
        # Disjoint sets → same group
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_partial_overlap_separate(self):
        actions = [
            {"domain": "light", "service": "turn_on", "entity_id": ["light.a", "light.b"]},
            {"domain": "light", "service": "turn_off", "entity_id": "light.b"},
        ]
        groups = _build_action_groups(actions)
        assert len(groups) == 2


# ===================================================================
# Task 5: Response cache
# ===================================================================

exec(f"from {_pkg}.call_model import _cache_key, _cache_get, _cache_put, _all_cacheable, _RESPONSE_CACHE, _CACHE_TTL, _CACHE_MAX, _CACHEABLE_SERVICES")
_cache_key_fn = locals()["_cache_key"]
_cache_get = locals()["_cache_get"]
_cache_put = locals()["_cache_put"]
_all_cacheable = locals()["_all_cacheable"]
_RESPONSE_CACHE = locals()["_RESPONSE_CACHE"]
_CACHE_TTL = locals()["_CACHE_TTL"]
_CACHE_MAX = locals()["_CACHE_MAX"]
_CACHEABLE_SERVICES = locals()["_CACHEABLE_SERVICES"]


class TestResponseCache:
    """Response cache: store, hit, miss, eviction."""

    def setup_method(self):
        _RESPONSE_CACHE.clear()

    def test_cache_key_deterministic(self):
        k1 = _cache_key_fn("turn on lights", "gpt-5-mini", "cfg1")
        k2 = _cache_key_fn("turn on lights", "gpt-5-mini", "cfg1")
        assert k1 == k2

    def test_cache_key_differs_on_text(self):
        k1 = _cache_key_fn("turn on lights", "gpt-5-mini", "cfg1")
        k2 = _cache_key_fn("turn off lights", "gpt-5-mini", "cfg1")
        assert k1 != k2

    def test_cache_key_case_insensitive(self):
        k1 = _cache_key_fn("Turn On Lights", "m", "c")
        k2 = _cache_key_fn("turn on lights", "m", "c")
        assert k1 == k2

    def test_put_and_get(self):
        data = {"actions": [{"domain": "light", "service": "turn_on"}], "explanation": "ok"}
        _cache_put("k1", data)
        assert _cache_get("k1") == data

    def test_miss(self):
        assert _cache_get("nonexistent") is None

    def test_ttl_expiry(self):
        data = {"actions": [], "explanation": "test"}
        _cache_put("k2", data)
        # Manually expire
        ts, d = _RESPONSE_CACHE["k2"]
        _RESPONSE_CACHE["k2"] = (time.monotonic() - _CACHE_TTL - 1, d)
        assert _cache_get("k2") is None

    def test_eviction_at_max(self):
        for i in range(_CACHE_MAX + 5):
            _cache_put(f"key-{i}", {"actions": [], "explanation": str(i)})
        assert len(_RESPONSE_CACHE) == _CACHE_MAX

    def test_all_cacheable_true(self):
        actions = [
            {"domain": "light", "service": "turn_on"},
            {"domain": "switch", "service": "turn_off"},
        ]
        assert _all_cacheable(actions) is True

    def test_all_cacheable_false(self):
        actions = [
            {"domain": "light", "service": "turn_on"},
            {"domain": "automation", "service": "trigger"},
        ]
        assert _all_cacheable(actions) is False

    def test_cacheable_services_set(self):
        assert "light.turn_on" in _CACHEABLE_SERVICES
        assert "light.turn_off" in _CACHEABLE_SERVICES
        assert "switch.turn_on" in _CACHEABLE_SERVICES
        assert "cover.open_cover" in _CACHEABLE_SERVICES

    def test_cache_constants(self):
        assert _CACHE_TTL == 60.0
        assert _CACHE_MAX == 50


# ===================================================================
# Task 6: State-query detection + context TTL
# ===================================================================

exec(f"from {_pkg}.device_info import _is_state_query, _CONTEXT_TTL, build_compact_context")
_is_state_query = locals()["_is_state_query"]
_CONTEXT_TTL = locals()["_CONTEXT_TTL"]
build_compact_context_fn = locals()["build_compact_context"]


class TestIsStateQuery:
    """_is_state_query detects status/state questions."""

    def test_what_is(self):
        assert _is_state_query("what is the living room light?") is True

    def test_status(self):
        assert _is_state_query("status of the kitchen") is True

    def test_is_the(self):
        assert _is_state_query("is the front door locked?") is True

    def test_check(self):
        assert _is_state_query("check the thermostat") is True

    def test_current(self):
        assert _is_state_query("current temperature") is True

    def test_command_not_query(self):
        assert _is_state_query("turn on the lights") is False

    def test_set_not_query(self):
        assert _is_state_query("set brightness to 50") is False

    def test_empty_string(self):
        assert _is_state_query("") is False

    def test_case_insensitive(self):
        assert _is_state_query("What Is the light?") is True


class TestContextTTL:
    """Verify context cache TTL is 30 seconds."""

    def test_ttl_value(self):
        assert _CONTEXT_TTL == 30.0


class TestForceRebuildSignature:
    """build_compact_context accepts force_rebuild param."""

    def test_force_rebuild_param(self):
        sig = inspect.signature(build_compact_context_fn)
        assert "force_rebuild" in sig.parameters
        # Default should be False
        assert sig.parameters["force_rebuild"].default is False
