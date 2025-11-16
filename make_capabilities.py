#!/usr/bin/env python3
import json

with open("services.json", "r", encoding="utf-8") as f:
    services = json.load(f)

capabilities = {}

for block in services:
    domain = block["domain"]
    for svc_name, svc_data in block["services"].items():
        full_name = f"{domain}.{svc_name}"
        fields = svc_data.get("fields", {}) or {}

        caps = {
            "domain": domain,
            "service": svc_name,
            "fields": {}
        }

        for field_name, meta in fields.items():
            caps["fields"][field_name] = {
                "required": meta.get("required", False),
                "description": meta.get("description", "") or meta.get("name", ""),
                "example": meta.get("example"),
                "selector": meta.get("selector"),
            }

        capabilities[full_name] = caps

# write to a pretty JSON file
with open("services_capabilities.json", "w", encoding="utf-8") as f:
    json.dump(capabilities, f, indent=2)
