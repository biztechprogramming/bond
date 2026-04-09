# Design Doc 106: MCP Command Parsing & Test Reliability

## Status: Draft
## Author: Bond
## Date: 2026-04-09

---

## Problem

When a user configures an MCP server with a full command string like `node /mnt/c/dev/automation/solidtime/mcp-solidtime/dist/index.js -s user` in the command field and clicks "Test Connection", they get:

```
[Errno 2] No such file or directory
```

This happens because users naturally paste the entire command line into the `command` field, but the backend expects `command` to be just the executable (e.g., `node`) with everything else in the `args` array. Python's `subprocess` then tries to find an executable literally named `"node /mnt/c/dev/automation/solidtime/mcp-solidtime/dist/index.js -s user"` — which doesn't exist.

### Root Causes

**Bug 1: No smart command/args splitting.** The frontend form has separate fields for `command` and `args[]`, but neither the frontend nor the backend attempts to split a full command string when args is empty. `StdioServerParameters(command="node /path/to/script.js -s user", args=[])` fails because `command` must be a single executable.

**Bug 2: Unhelpful error message.** The `[Errno 2] No such file or directory` error gives no indication that the problem is command parsing. Users assume the path is wrong when the real issue is that the entire string is being treated as the executable name.

### Affected Code Paths

Both paths construct `StdioServerParameters` with the raw, unsplit command:

1. **`MCPConnection.__init__`** (`backend/app/mcp/manager.py`) — creates `StdioServerParameters(command=config.command, args=config.args, ...)`
2. **`test_mcp_server()`** (`backend/app/api/v1/mcp.py`, line ~189) — same pattern for the test endpoint

---

## Architecture Context

The MCP tool flow for agents in containers is:

```
Agent (container) → broker/mcp/tools (Gateway :18789) → Backend :18790/api/v1/mcp/proxy/call → MCPManager → stdio_client(command)
```

Key points:
- **The Backend runs on the host**, not in a container. It spawns MCP server processes as child processes. Host paths like `/mnt/c/dev/...` are valid.
- **Agents never spawn MCP processes directly** — they go through the broker proxy (`gateway/src/broker/router.ts` lines 190-284), which routes to the backend's API.
- **The broker proxy is correct and needs no changes.** It routes `GET /broker/mcp/tools` → Backend `/api/v1/mcp/proxy/tools` and `POST /broker/mcp` → Backend `/api/v1/mcp/proxy/call`, with policy filtering applied.
- **Future consideration:** If the Backend were ever containerized, host paths would break. This is out of scope but worth noting.

---

## Solution: Smart Command Parsing

### 1. Backend: `parse_command()` function

Add to `backend/app/mcp/manager.py`:

```python
import shlex

def parse_command(command: str, args: List[str]) -> tuple[str, List[str]]:
    """Split a full command string into (executable, args) if args is empty.

    If args is already populated, the user explicitly set them — leave as-is.
    If args is empty and command contains spaces, use shlex.split() to parse.
    """
    if args:
        return command, args

    if not command or ' ' not in command:
        return command, args

    parts = shlex.split(command)
    return parts[0], parts[1:]
```

Apply in two places:

**`MCPConnection.__init__()`:**
```python
def __init__(self, config: MCPServerConfig):
    resolved_cmd, resolved_args = parse_command(config.command, config.args)
    self.server_params = StdioServerParameters(
        command=resolved_cmd,
        args=resolved_args,
        env={**os.environ, **(config.env or {})}
    )
```

**`test_mcp_server()`** in `backend/app/api/v1/mcp.py`:
```python
resolved_cmd, resolved_args = parse_command(config.command, config.args or [])
server_params = StdioServerParameters(
    command=resolved_cmd,
    args=resolved_args,
    env={**os.environ, **(config.env or {})}
)
```

### 2. Backend: Enhanced Test Response

Add resolved command/args to `MCPServerTestResponse` so the user can see what was actually executed:

```python
class MCPServerTestResponse(BaseModel):
    success: bool
    status: str
    tools: list
    connect_time_ms: int
    error: Optional[str]
    resolved_command: str       # NEW
    resolved_args: List[str]    # NEW
```

Improve error classification:

```python
except FileNotFoundError:
    error = f"Executable not found: '{resolved_cmd}'. Check that it is installed and in PATH."
except PermissionError:
    error = f"Permission denied: '{resolved_cmd}'. Check file permissions."
except asyncio.TimeoutError:
    error = "Connection timed out after 10 seconds. The server started but didn't respond."
except Exception as e:
    error = str(e)
```

### 3. Frontend: Auto-split UX

**In `McpTab.tsx`**, add auto-split behavior on the command field's `onBlur`:

```typescript
function handleCommandBlur() {
  if (editing.command.includes(' ') && editing.args.length === 0) {
    const parts = editing.command.split(/\s+/);
    setEditing({
      ...editing,
      command: parts[0],
      args: parts.slice(1)
    });
  }
}
```

This fires when the user tabs/clicks away from the command field. If the command contains spaces and args is empty, it splits automatically. The user sees the split happen and can adjust before saving.

**Show resolved command/args in test results:**

```
✅ Connected in 340ms — 3 tools discovered
   Resolved: node /mnt/c/dev/.../dist/index.js -s user
   • create_time_entry
   • list_projects
   • list_tasks
```

On error:
```
❌ Executable not found: 'node /mnt/c/dev/.../dist/index.js -s user'
   Resolved command: "node /mnt/c/dev/.../dist/index.js -s user" (no splitting applied)
   Hint: The command field should contain only the executable (e.g., "node").
         Use the Args fields for additional arguments.
```

---

## Test Plan

### Unit Tests (`backend/tests/test_mcp_command_parsing.py`)

```python
import pytest
from app.mcp.manager import parse_command

class TestParseCommand:
    def test_simple_command_no_args(self):
        """Single executable, no args — unchanged."""
        assert parse_command("node", []) == ("node", [])

    def test_full_command_string_split(self):
        """Full command string with empty args — split."""
        assert parse_command("node /path/to/script.js -s user", []) == (
            "node", ["/path/to/script.js", "-s", "user"]
        )

    def test_args_already_set_no_resplit(self):
        """Args explicitly provided — don't re-parse command."""
        assert parse_command("node", ["/path/to/script.js", "-s", "user"]) == (
            "node", ["/path/to/script.js", "-s", "user"]
        )

    def test_absolute_path_no_args(self):
        """Absolute path executable, no args — unchanged."""
        assert parse_command("/usr/local/bin/node", []) == ("/usr/local/bin/node", [])

    def test_quoted_path_with_spaces(self):
        """Quoted path containing spaces — shlex handles it."""
        assert parse_command('node "/path with spaces/script.js"', []) == (
            "node", ["/path with spaces/script.js"]
        )

    def test_npx_complex_args(self):
        """Complex npx command — split correctly."""
        assert parse_command("npx -y @modelcontextprotocol/server-filesystem /tmp", []) == (
            "npx", ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )

    def test_empty_command(self):
        """Empty command — returned as-is."""
        assert parse_command("", []) == ("", [])

    def test_uvx_command(self):
        """uvx command with flags — split correctly."""
        assert parse_command("uvx mcp-server-git --repository /path", []) == (
            "uvx", ["mcp-server-git", "--repository", "/path"]
        )
```

### Integration Tests

- Mock `stdio_client`, verify `parse_command` is applied before `StdioServerParameters` is created
- Verify `resolved_command` and `resolved_args` are returned in `MCPServerTestResponse`
- Test error classification: pass a nonexistent executable → verify `FileNotFoundError` message
- Test with args already populated → verify no re-parsing occurs

### Frontend Test Considerations (describe only)

- Auto-split on blur: type `node /path/script.js` into command, blur → command becomes `node`, args becomes `["/path/script.js"]`
- Auto-split does NOT fire when args already has entries
- Test connection results display the resolved command and args
- Error hint appears when the unsplit command fails

---

## Implementation Tasks

1. Add `parse_command()` to `backend/app/mcp/manager.py`
2. Apply `parse_command()` in `MCPConnection.__init__()` before creating `StdioServerParameters`
3. Apply `parse_command()` in `test_mcp_server()` endpoint before creating `StdioServerParameters`
4. Add `resolved_command` and `resolved_args` fields to `MCPServerTestResponse`
5. Improve error classification in `test_mcp_server()` (FileNotFoundError, PermissionError, TimeoutError)
6. Update frontend `McpTab.tsx`: add `onBlur` auto-split on the command field
7. Update frontend test results display to show resolved command/args
8. Write unit tests (`backend/tests/test_mcp_command_parsing.py`)
9. Write integration tests for the test endpoint with parse_command

---

## References

- [105-mcp-live-status-and-connection-testing.md](105-mcp-live-status-and-connection-testing.md) — MCP Live Status & Connection Testing
- [054-host-side-mcp-proxy.md](054-host-side-mcp-proxy.md) — Host-Side MCP Proxy Architecture
- [053-solidtime-mcp-integration.md](053-solidtime-mcp-integration.md) — SolidTime MCP Integration
