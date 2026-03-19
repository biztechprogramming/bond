"""SolidTime projects — list and create projects."""
from __future__ import annotations

SCHEMA = {
    "name": "solidtime_projects",
    "description": "List or create SolidTime projects. Use action='list' to see all projects, or action='create' to make a new one.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create"],
                "description": "Action to perform"
            },
            "name": {
                "type": "string",
                "description": "Project name (required for create)"
            },
            "color": {
                "type": "string",
                "description": "Hex color e.g. '#3498db' (required for create)"
            },
            "is_billable": {
                "type": "boolean",
                "description": "Whether the project is billable (default: false)"
            },
            "client_id": {
                "type": "string",
                "description": "Client UUID (optional)"
            }
        },
        "required": ["action"]
    }
}


def execute(action: str, name: str = None, color: str = "#3498db",
            is_billable: bool = False, client_id: str = None) -> dict:
    from ._solidtime_config import load_solidtime_config, solidtime_request
    config = load_solidtime_config()

    if action == "list":
        return solidtime_request("GET", "/projects", config)

    elif action == "create":
        if not name:
            return {"error": "name is required for creating a project"}
        body = {"name": name, "color": color, "is_billable": is_billable}
        if client_id:
            body["client_id"] = client_id
        return solidtime_request("POST", "/projects", config, json=body)

    return {"error": f"Unknown action: {action}"}
