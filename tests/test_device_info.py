"""Unit tests for device_info.py — _cfg_hash + per-hass cache isolation.

run_tests.py stubs all HA/voluptuous imports before this runs.
"""
import json
import os
import sys
import time

# Add the PARENT of the repo root so we can import the package by directory name
_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_parent = os.path.dirname(_repo)
_pkg = os.path.basename(_repo)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

# Import via package
exec(f"import {_pkg}.device_info as _di")
_di = locals()["_di"]
_cfg_hash = _di._cfg_hash


# ===================================================================
# LOW-2: _cfg_hash resilience
# ===================================================================

class TestCfgHash:
    """_cfg_hash must never raise, even on non-JSON-serializable input."""

    def test_none_returns_empty(self):
        assert _cfg_hash(None) == ""

    def test_empty_dict_returns_empty(self):
        assert _cfg_hash({}) == ""

    def test_normal_config(self):
        cfg = {"domains": ["light"], "services": ["light.turn_on"]}
        result = _cfg_hash(cfg)
        assert json.loads(result) == cfg

    def test_set_in_config_no_crash(self):
        """Sets are not JSON-serializable; default=str must handle them."""
        cfg = {"domains": {"light", "switch"}}  # set, not list
        result = _cfg_hash(cfg)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_custom_object_no_crash(self):
        class Weird:
            pass
        cfg = {"thing": Weird()}
        result = _cfg_hash(cfg)
        assert isinstance(result, str)

    def test_deterministic(self):
        cfg = {"b": 2, "a": 1}
        assert _cfg_hash(cfg) == _cfg_hash(cfg)
        assert _cfg_hash({"a": 1, "b": 2}) == _cfg_hash({"b": 2, "a": 1})


# ===================================================================
# LOW-1: Per-hass cache isolation
# ===================================================================

class TestPerHassCacheIsolation:
    """Different hass instances must not share cached context."""

    def setup_method(self):
        _di._compact_caches.clear()

    def test_different_ids_different_cache_slots(self):
        hass_a = object()
        hass_b = object()
        assert id(hass_a) != id(hass_b)

        _di._compact_caches[id(hass_a)] = {
            "data": '{"entities":[],"services":{}}',
            "ts": time.monotonic(),
            "cfg_hash": "",
        }

        assert _di._compact_caches.get(id(hass_b)) is None

    def test_same_id_hits_cache(self):
        hass = object()
        now = time.monotonic()
        _di._compact_caches[id(hass)] = {
            "data": '{"test": true}',
            "ts": now,
            "cfg_hash": "",
        }

        cache = _di._compact_caches.get(id(hass))
        assert cache is not None
        assert cache["data"] == '{"test": true}'
