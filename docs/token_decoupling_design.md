# Design Doc: Decoupling Agent from SPACETIMEDB_TOKEN

## Objective
Remove the requirement for the agent to have `SPACETIMEDB_TOKEN` in its environment while maintaining the ability to interact with SpacetimeDB where necessary.

## Background
Currently, some tool calls or environment checks fail if `SPACETIMEDB_TOKEN` is not set. This creates a hard dependency on a specific secret that the agent should ideally not need to manage or possess.

## Proposed Solutions

### 1. Proxy/Wrapper Service
Instead of the agent calling SpacetimeDB directly or tools requiring the token, all Spacetime-related operations should go through a proxy service or a dedicated "Spacetime Tool" that handles authentication on the host side.
- **Benefit:** Agent never sees the token.
- **Implementation:** Create a specialized tool that uses the host's credentials.

### 2. Token Injection via Sandbox Mount
If the token is required for CLI tools (like `stdb`), it should be mounted into the sandbox at a standard location (e.g., `~/.spacetime/config`) by the infrastructure, rather than being passed as an environment variable to the agent process.
- **Benefit:** CLI tools work out-of-the-box without agent intervention.

### 3. Graceful Degradation in Tools
Modify tools (like `work_plan` or `search_memory` if they are the culprits) to detect the absence of the token and:
- Fall back to a local SQLite/File-based storage.
- Provide a clear, non-blocking warning instead of a hard 500 error.

## Implementation Plan
1. **Identify Failure Points:** Audit the `work_plan` and `search_memory` tool implementations to see why they require the token.
2. **Local Fallback:** Implement a local filesystem fallback for the `work_plan` tool when the SpacetimeDB backend is unavailable.
3. **Environment Sanitization:** Ensure the agent's system prompt or environment doesn't explicitly look for the token.

## Success Criteria
- Agent can use `work_plan` and `search_memory` without `SPACETIMEDB_TOKEN` being set in its environment.
- No 500 errors from tool calls due to missing credentials.
