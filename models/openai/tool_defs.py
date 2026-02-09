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
                                "type": "string",
                                "description": "Target entity, e.g. 'light.kitchen'.",
                            },
                            "data": {
                                "type": "object",
                                "description": "Optional service data dict.",
                            },
                        },
                        "required": ["domain", "service", "entity_id"],
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
