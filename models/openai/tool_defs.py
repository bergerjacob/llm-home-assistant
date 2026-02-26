"""OpenAI function-calling tool definitions for the audio pipeline."""

PROPOSE_ACTIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_actions",
        "description": (
            "Propose one or more Home Assistant service calls to fulfil the "
            "user's request, plus a short human-readable explanation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "description": "List of HA service calls to execute.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {
                                "type": "string",
                                "description": "HA domain, e.g. 'light', 'switch', 'binary_sensor'.",
                            },
                            "service": {
                                "type": "string",
                                "description": "Service name, e.g. 'turn_on', 'update'.",
                            },
                            "entity_id": {
                                "description": "Target entity or list of entities.",
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ],
                            },
                            "data": {
                                "type": "object",
                                "description": (
                                    "Service data. ALL parameters go here: "
                                    "brightness (0-255), rgb_color ([R,G,B] 0-255), "
                                    "color_temp, transition, etc. "
                                    "Example: {\"brightness\": 200, \"rgb_color\": [255, 0, 0]}"
                                ),
                            },
                        },
                        "required": ["domain", "service", "entity_id", "data"],
                        "additionalProperties": False,
                    },
                },
                "explanation": {
                    "type": "string",
                    "description": "Human-readable summary of what will happen.",
                },
            },
            "required": ["actions", "explanation"],
        },
    },
}

PROPOSE_AUTOMATION_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_automation",
        "description": (
            "Propose a Home Assistant automation with YAML config, an execution "
            "plan of actions, a validation checklist, and optional clarifying questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "automation_yaml": {
                    "type": "string",
                    "description": "Complete, valid Home Assistant automation YAML as a single string.",
                },
                "execution_plan": {
                    "type": "object",
                    "properties": {
                        "actions": {
                            "type": "array",
                            "description": "List of HA service calls the automation would trigger.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "domain": {
                                        "type": "string",
                                        "description": "HA domain, e.g. 'light', 'switch'.",
                                    },
                                    "service": {
                                        "type": "string",
                                        "description": "Service name, e.g. 'turn_on'.",
                                    },
                                    "entity_id": {
                                        "description": "Target entity or list of entities.",
                                        "oneOf": [
                                            {"type": "string"},
                                            {"type": "array", "items": {"type": "string"}},
                                        ],
                                    },
                                    "data": {
                                        "type": "object",
                                        "description": (
                                            "Service data. ALL parameters go here: "
                                            "brightness (0-255), rgb_color ([R,G,B] 0-255), "
                                            "color_temp, transition, etc."
                                        ),
                                    },
                                },
                                "required": ["domain", "service", "entity_id", "data"],
                                "additionalProperties": False,
                            },
                        },
                        "explanation": {
                            "type": "string",
                            "description": "Human-readable summary of what the automation does.",
                        },
                    },
                    "required": ["actions", "explanation"],
                },
                "validation_checklist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-5 items the user should verify (triggers, conditions, entities).",
                },
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "0-2 clarifying questions if the request is ambiguous. Empty list if clear.",
                },
            },
            "required": ["automation_yaml", "execution_plan", "validation_checklist", "questions"],
        },
    },
}
