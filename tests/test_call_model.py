"""Unit tests for call_model.py — merge_actions + _is_allowed.

run_tests.py stubs all HA/openai/pydantic imports before this runs.
"""
import os
import sys

# Add the PARENT of the repo root so we can import the package by directory name
_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_parent = os.path.dirname(_repo)
_pkg = os.path.basename(_repo)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

# Import via package so relative imports inside call_model.py resolve
exec(f"from {_pkg}.call_model import merge_actions, _is_allowed")
merge_actions = locals()["merge_actions"]
_is_allowed = locals()["_is_allowed"]


# ===================================================================
# MED-1: _is_allowed — fail-closed services enforcement
# ===================================================================

class TestIsAllowedServiceEnforcement:
    """When allow_cfg is present, missing/empty services must deny."""

    def test_allow_none_permits_everything(self):
        assert _is_allowed(None, "light", "turn_on", "light.a") is True

    def test_allow_empty_dict_permits_everything(self):
        assert _is_allowed({}, "light", "turn_on", "light.a") is True

    def test_services_present_and_matching(self):
        allow = {"services": ["light.turn_on", "light.turn_off"]}
        assert _is_allowed(allow, "light", "turn_on", "light.a") is True

    def test_services_present_but_not_matching(self):
        allow = {"services": ["light.turn_off"]}
        assert _is_allowed(allow, "light", "turn_on", "light.a") is False

    def test_services_missing_denies(self):
        """MED-1 core: domains present but no services key => deny."""
        allow = {"domains": ["light"]}
        assert _is_allowed(allow, "light", "turn_on", "light.a") is False

    def test_services_empty_list_denies(self):
        """MED-1 core: services is [] => deny."""
        allow = {"domains": ["light"], "services": []}
        assert _is_allowed(allow, "light", "turn_on", "light.a") is False

    def test_services_none_denies(self):
        """MED-1 core: services is explicitly None => deny."""
        allow = {"domains": ["light"], "services": None}
        assert _is_allowed(allow, "light", "turn_on", "light.a") is False

    def test_domain_mismatch_still_blocked(self):
        allow = {"domains": ["switch"], "services": ["switch.turn_on"]}
        assert _is_allowed(allow, "light", "turn_on", "light.a") is False

    def test_entity_list_all_allowed(self):
        allow = {
            "services": ["light.turn_on"],
            "entities": ["light.a", "light.b"],
        }
        assert _is_allowed(allow, "light", "turn_on", ["light.a", "light.b"]) is True

    def test_entity_list_one_disallowed(self):
        allow = {
            "services": ["light.turn_on"],
            "entities": ["light.a"],
        }
        assert _is_allowed(allow, "light", "turn_on", ["light.a", "light.b"]) is False

    def test_no_entity_id_still_checks_service(self):
        allow = {"services": ["light.turn_on"]}
        assert _is_allowed(allow, "light", "turn_on", None) is True
        assert _is_allowed(allow, "light", "turn_off", None) is False


# ===================================================================
# MED-2: merge_actions — entity_id in data dict should not block merge
# ===================================================================

class TestMergeActions:
    """merge_actions grouping and entity_id handling."""

    def test_basic_merge(self):
        actions = [
            {"domain": "light", "service": "turn_off", "entity_id": "light.a", "data": {}},
            {"domain": "light", "service": "turn_off", "entity_id": "light.b", "data": {}},
        ]
        merged = merge_actions(actions)
        assert len(merged) == 1
        assert set(merged[0]["entity_id"]) == {"light.a", "light.b"}

    def test_merge_with_entity_id_in_data(self):
        """MED-2 core: entity_id inside data dict must not pollute key."""
        actions = [
            {
                "domain": "light",
                "service": "turn_off",
                "entity_id": "light.a",
                "data": {"entity_id": "light.a"},
            },
            {
                "domain": "light",
                "service": "turn_off",
                "entity_id": "light.b",
                "data": {"entity_id": "light.b"},
            },
        ]
        merged = merge_actions(actions)
        assert len(merged) == 1  # would be 2 without the fix
        eids = merged[0]["entity_id"]
        assert set(eids if isinstance(eids, list) else [eids]) == {"light.a", "light.b"}

    def test_different_data_no_merge(self):
        actions = [
            {"domain": "light", "service": "turn_on", "entity_id": "light.a", "data": {"brightness": 100}},
            {"domain": "light", "service": "turn_on", "entity_id": "light.b", "data": {"brightness": 200}},
        ]
        merged = merge_actions(actions)
        assert len(merged) == 2

    def test_mixed_list_and_string(self):
        actions = [
            {"domain": "light", "service": "turn_on", "entity_id": ["light.a", "light.b"], "data": {}},
            {"domain": "light", "service": "turn_on", "entity_id": "light.c", "data": {}},
        ]
        merged = merge_actions(actions)
        assert len(merged) == 1
        assert merged[0]["entity_id"] == ["light.a", "light.b", "light.c"]

    def test_empty_actions(self):
        assert merge_actions([]) == []

    def test_single_entity_stays_string(self):
        actions = [
            {"domain": "light", "service": "turn_on", "entity_id": "light.a", "data": {}},
        ]
        merged = merge_actions(actions)
        assert merged[0]["entity_id"] == "light.a"

    def test_deduplication(self):
        actions = [
            {"domain": "light", "service": "turn_off", "entity_id": "light.a", "data": {}},
            {"domain": "light", "service": "turn_off", "entity_id": "light.a", "data": {}},
        ]
        merged = merge_actions(actions)
        assert len(merged) == 1
        assert merged[0]["entity_id"] == "light.a"
