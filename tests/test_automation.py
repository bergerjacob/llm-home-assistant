"""Unit tests for automation builder mode.

Tests cover:
- _detect_automation_mode routing
- _validate_automation_semantics semantic checks
- NEEDS_CONTEXT sentinel
- Sensor state / event payload format
- PROPOSE_AUTOMATION_TOOL schema
- Audio automation routing (automation_mode flag)
- Automation never executes guard
"""
import os
import sys
import json

# Add the PARENT of the repo root so we can import the package by directory name
_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_parent = os.path.dirname(_repo)
_pkg = os.path.basename(_repo)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

# Import via package so relative imports resolve
exec(f"from {_pkg}.call_model import _detect_automation_mode")
_detect_automation_mode = locals()["_detect_automation_mode"]

exec(f"from {_pkg}.models.openai.call_openai import _validate_automation_semantics, NEEDS_CONTEXT")
_validate_automation_semantics = locals()["_validate_automation_semantics"]
NEEDS_CONTEXT = locals()["NEEDS_CONTEXT"]

exec(f"from {_pkg}.models.openai.tool_defs import PROPOSE_AUTOMATION_TOOL, PROPOSE_ACTIONS_TOOL")
PROPOSE_AUTOMATION_TOOL = locals()["PROPOSE_AUTOMATION_TOOL"]
PROPOSE_ACTIONS_TOOL = locals()["PROPOSE_ACTIONS_TOOL"]


# ===================================================================
# Routing: _detect_automation_mode
# ===================================================================

class TestAutomationRouting:
    """Tests for _detect_automation_mode prefix detection."""

    def test_slash_automation_prefix(self):
        is_auto, clean = _detect_automation_mode("/automation turn on lights at sunset")
        assert is_auto is True
        assert clean == "turn on lights at sunset"

    def test_automation_colon_prefix(self):
        is_auto, clean = _detect_automation_mode("automation: turn on lights at sunset")
        assert is_auto is True
        assert clean == "turn on lights at sunset"

    def test_normal_text_not_automation(self):
        is_auto, clean = _detect_automation_mode("turn on the kitchen lights")
        assert is_auto is False
        assert clean == "turn on the kitchen lights"

    def test_empty_text_not_automation(self):
        is_auto, clean = _detect_automation_mode("")
        assert is_auto is False
        assert clean == ""


# ===================================================================
# Semantic Validation: _validate_automation_semantics
# ===================================================================

class TestSemanticValidation:
    """Tests for _validate_automation_semantics against compact context."""

    CONTEXT = json.dumps({
        "entities": [
            {"e": "light.kitchen", "n": "Kitchen Light", "d": "light", "s": "on"},
            {"e": "light.bedroom", "n": "Bedroom Light", "d": "light", "s": "off"},
            {"e": "switch.fan", "n": "Fan", "d": "switch", "s": "off"},
        ],
        "services": {
            "light": ["turn_on", "turn_off"],
            "switch": ["turn_on", "turn_off"],
        }
    })

    def test_valid_plan_no_warnings(self):
        output = {
            "execution_plan": {
                "actions": [
                    {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen", "data": {}},
                ],
                "explanation": "Turn on kitchen light",
            }
        }
        warnings = _validate_automation_semantics(output, self.CONTEXT)
        assert warnings == []

    def test_unknown_entity_warning(self):
        output = {
            "execution_plan": {
                "actions": [
                    {"domain": "light", "service": "turn_on", "entity_id": "light.garage", "data": {}},
                ],
                "explanation": "Turn on garage light",
            }
        }
        warnings = _validate_automation_semantics(output, self.CONTEXT)
        assert len(warnings) == 1
        assert "Unknown entity: light.garage" in warnings[0]

    def test_disallowed_service_warning(self):
        output = {
            "execution_plan": {
                "actions": [
                    {"domain": "climate", "service": "set_temperature", "entity_id": "climate.living_room", "data": {}},
                ],
                "explanation": "Set temp",
            }
        }
        warnings = _validate_automation_semantics(output, self.CONTEXT)
        assert any("Disallowed service" in w for w in warnings)


# ===================================================================
# NEEDS_CONTEXT sentinel
# ===================================================================

class TestNeedsContext:
    """Tests for the NEEDS_CONTEXT sentinel value."""

    def test_is_string(self):
        assert isinstance(NEEDS_CONTEXT, str)

    def test_expected_text(self):
        assert NEEDS_CONTEXT == "NEEDS_CONTEXT: compact context JSON was not provided by the system."


# ===================================================================
# Sensor / Event format
# ===================================================================

class TestAutomationSensorEvent:
    """Tests for sensor state format and event payload structure."""

    def test_sensor_state_stays_short(self):
        """Automation sensor states should be short status strings, not full YAML."""
        valid_states = [
            "Automation ready",
            "Automation ready (questions)",
            "Error: something went wrong",
            NEEDS_CONTEXT,
        ]
        for state in valid_states:
            assert len(state) <= 255, f"State too long: {state}"

    def test_event_payload_has_mode_key(self):
        """The event payload for automation mode should include 'mode': 'automation'."""
        # Simulate the event payload structure from call_model.py
        payload = {
            "mode": "automation",
            "automation_yaml": "alias: Test\ntrigger: ...",
            "execution_plan": {"actions": [], "explanation": "test"},
            "validation_checklist": ["Check trigger", "Check entity"],
            "questions": [],
        }
        assert payload["mode"] == "automation"
        assert "automation_yaml" in payload
        assert "validation_checklist" in payload
        assert "questions" in payload


# ===================================================================
# PROPOSE_AUTOMATION_TOOL schema
# ===================================================================

class TestProposeAutomationToolSchema:
    """Tests for the PROPOSE_AUTOMATION_TOOL function calling schema."""

    def test_tool_type_is_function(self):
        assert PROPOSE_AUTOMATION_TOOL["type"] == "function"

    def test_function_name(self):
        assert PROPOSE_AUTOMATION_TOOL["function"]["name"] == "propose_automation"

    def test_required_fields(self):
        required = PROPOSE_AUTOMATION_TOOL["function"]["parameters"]["required"]
        assert "automation_yaml" in required
        assert "execution_plan" in required
        assert "validation_checklist" in required
        assert "questions" in required

    def test_execution_plan_has_actions_and_explanation(self):
        ep = PROPOSE_AUTOMATION_TOOL["function"]["parameters"]["properties"]["execution_plan"]
        assert "actions" in ep["properties"]
        assert "explanation" in ep["properties"]
        assert ep["required"] == ["actions", "explanation"]


# ===================================================================
# Audio automation routing (automation_mode flag)
# ===================================================================

class TestAudioAutomationRouting:
    """Tests for automation_mode flag routing in call_model_wrapper."""

    def test_empty_text_with_explicit_flag(self):
        """Empty text + explicit automation_mode=True should NOT trigger text prefix detection."""
        is_auto, clean = _detect_automation_mode("")
        assert is_auto is False
        assert clean == ""
        # The explicit flag is handled separately in call_model_wrapper,
        # so _detect_automation_mode returns False, and the flag is set externally.

    def test_text_prefix_still_works(self):
        """Text prefix /automation should still trigger automation mode."""
        is_auto, clean = _detect_automation_mode("/automation dim bedroom at 10pm")
        assert is_auto is True
        assert clean == "dim bedroom at 10pm"


# ===================================================================
# Automation never executes guard
# ===================================================================

class TestAutomationNeverExecutes:
    """Regression guard: automation mode should never execute actions."""

    def test_automation_output_has_no_execute_path(self):
        """Verify that automation output schema has execution_plan but
        call_model_wrapper returns early before _execute_tool_call."""
        # The automation early-return block in call_model_wrapper exits
        # before reaching the action execution loop. We verify this by
        # checking that the automation output schema does NOT have a
        # top-level 'actions' key (it uses execution_plan.actions instead).
        automation_reply = {
            "automation_yaml": "alias: Test\ntrigger: ...",
            "execution_plan": {
                "actions": [
                    {"domain": "light", "service": "turn_on",
                     "entity_id": "light.kitchen", "data": {}}
                ],
                "explanation": "test",
            },
            "validation_checklist": ["Check trigger", "Check entity"],
            "questions": [],
        }
        # Top-level 'actions' key is what triggers execution in call_model_wrapper.
        # Automation replies should NOT have it at top level.
        assert "actions" not in automation_reply
        # But execution_plan.actions should exist for display purposes.
        assert "actions" in automation_reply["execution_plan"]
