# Dynamic Tools

Agent-created tools loaded at runtime by `dynamic_loader.py`.

## Tool Contract

Each dynamic tool file must export:

1. **`SCHEMA`** — a dict with `name`, `description`, and `parameters` (JSON Schema)
2. **`execute()`** — a function (sync or async) that receives keyword arguments matching the schema

### Example

```python
# backend/app/agent/tools/dynamic/get_weather.py

SCHEMA = {
    "name": "get_weather",
    "description": "Get current weather for a location.",
    "parameters": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City or region name"}
        },
        "required": ["location"]
    }
}

def execute(location: str) -> dict:
    import requests
    r = requests.get(f"https://wttr.in/{location}?format=j1", timeout=10)
    r.raise_for_status()
    return r.json()["current_condition"][0]
```

## Rules

- File names become tool names (e.g. `get_weather.py` → tool `get_weather`)
- Files starting with `_` are ignored
- All tools here are PR-reviewed before merge — agents create them via `repo_pr`
- Tools must not import from `backend.app.core` or other sensitive modules
