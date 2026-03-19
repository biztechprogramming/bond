"""SolidTime time entries — list and create time entries."""
from __future__ import annotations

SCHEMA = {
    "name": "solidtime_time_entries",
    "description": "List or create SolidTime time entries. Use action='list' to view entries (with optional date filters), or action='create' to log a new entry.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create"],
                "description": "Action to perform"
            },
            "start": {
                "type": "string",
                "description": "ISO 8601 datetime for start (required for create, optional filter for list)"
            },
            "end": {
                "type": "string",
                "description": "ISO 8601 datetime for end (optional)"
            },
            "project_id": {
                "type": "string",
                "description": "Project UUID (optional)"
            },
            "task_id": {
                "type": "string",
                "description": "Task UUID (optional)"
            },
            "description": {
                "type": "string",
                "description": "Description of the time entry (for create)"
            },
            "billable": {
                "type": "boolean",
                "description": "Whether the entry is billable (default: false)"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tag UUIDs to attach"
            }
        },
        "required": ["action"]
    }
}


def execute(action: str, start: str = None, end: str = None, project_id: str = None,
            task_id: str = None, description: str = None, billable: bool = False,
            tags: list = None) -> dict:
    from ._solidtime_config import load_solidtime_config, solidtime_request
    config = load_solidtime_config()

    if action == "list":
        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return solidtime_request("GET", "/time-entries", config, params=params)

    elif action == "create":
        if not start:
            return {"error": "start is required for creating a time entry"}
        body = {
            "member_id": config["memberId"],
            "start": start,
            "billable": billable,
        }
        if end:
            body["end"] = end
        if project_id:
            body["project_id"] = project_id
        if task_id:
            body["task_id"] = task_id
        if description:
            body["description"] = description
        if tags:
            body["tags"] = tags
        return solidtime_request("POST", "/time-entries", config, json=body)

    return {"error": f"Unknown action: {action}"}
