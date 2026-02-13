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
