"""SolidTime timer — start, stop, and check active timer."""
from __future__ import annotations

SCHEMA = {
    "name": "solidtime_timer",
    "description": "Manage SolidTime timer. Actions: 'active' (get running timer), 'start' (start a new timer), 'stop' (stop the running timer).",
    "keywords": ["timer", "start timer", "stop timer", "clock in", "clock out", "running timer", "active timer", "solidtime", "solid time"],
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["active", "start", "stop"],
                "description": "Action to perform"
            },
            "project_id": {
                "type": "string",
                "description": "Project UUID (for start)"
            },
            "task_id": {
                "type": "string",
                "description": "Task UUID (for start)"
            },
            "description": {
                "type": "string",
                "description": "Description (for start)"
            },
            "billable": {
                "type": "boolean",
                "description": "Whether billable (for start, default: false)"
            }
        },
        "required": ["action"]
    }
}


def execute(action: str, project_id: str = None, task_id: str = None,
            description: str = None, billable: bool = False) -> dict:
    import requests
    from ._solidtime_config import load_solidtime_config, solidtime_request
    config = load_solidtime_config()

    if action == "active":
        # Active timer endpoint is user-scoped, not org-scoped
        url = f"{config['url']}/api/v1/users/me/time-entries/active"
        headers = {"Authorization": config["apiToken"], "Accept": "application/json"}
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code in (204, 404):
            return {"active": False, "message": "No timer running"}
        resp.raise_for_status()
        return {"active": True, "entry": resp.json().get("data")}

    elif action == "start":
        from datetime import datetime, timezone
        body = {
            "member_id": config["memberId"],
            "start": datetime.now(timezone.utc).isoformat(),
            "billable": billable,
        }
        if project_id:
            body["project_id"] = project_id
        if task_id:
            body["task_id"] = task_id
        if description:
            body["description"] = description
        return solidtime_request("POST", "/time-entries", config, json=body)

    elif action == "stop":
        # Get active timer first
        url = f"{config['url']}/api/v1/users/me/time-entries/active"
        headers = {"Authorization": config["apiToken"], "Accept": "application/json"}
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code in (204, 404):
            return {"error": "No active timer to stop"}
        resp.raise_for_status()
        entry = resp.json().get("data", {})
        entry_id = entry.get("id")
        if not entry_id:
            return {"error": "Could not find active timer ID"}

        # Update with end time
        from datetime import datetime, timezone
        update_body = {
            "member_id": config["memberId"],
            "start": entry.get("start"),
            "end": datetime.now(timezone.utc).isoformat(),
            "billable": entry.get("billable", False),
        }
        if entry.get("project_id"):
            update_body["project_id"] = entry["project_id"]
        if entry.get("task_id"):
            update_body["task_id"] = entry["task_id"]
        if entry.get("description"):
            update_body["description"] = entry["description"]
        return solidtime_request("PUT", f"/time-entries/{entry_id}", config, json=update_body)

    return {"error": f"Unknown action: {action}"}
