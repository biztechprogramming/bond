"""SolidTime summary — weekly tracked hours and tag/client lists."""
from __future__ import annotations

SCHEMA = {
    "name": "solidtime_summary",
    "description": "Get SolidTime summary data. Actions: 'weekly' (weekly tracked hours chart), 'clients' (list clients), 'tags' (list tags).",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["weekly", "clients", "tags"],
                "description": "Action to perform"
            }
        },
        "required": ["action"]
    }
}


def execute(action: str) -> dict:
    from ._solidtime_config import load_solidtime_config, solidtime_request
    config = load_solidtime_config()

    if action == "weekly":
        return solidtime_request("GET", "/charts/total-weekly-time", config)

    elif action == "clients":
        return solidtime_request("GET", "/clients", config)

    elif action == "tags":
        return solidtime_request("GET", "/tags", config)

    return {"error": f"Unknown action: {action}"}
