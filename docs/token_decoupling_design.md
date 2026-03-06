# Design Doc: Decoupling Agent from SPACETIMEDB_TOKEN

## Status
**Approved** — Implementing Option 3 (Graceful Degradation) as the primary approach.

## Objective
Remove the requirement for the agent to have `SPACETIMEDB_TOKEN` in its environment while maintaining the ability to interact with SpacetimeDB where necessary. The agent should function correctly regardless of whether SpacetimeDB credentials are available.

## Background
Currently, some tool calls (notably `work_plan` and `search_memory`) fail with HTTP 500 errors if `SPACETIMEDB_TOKEN` is not set. This creates a hard dependency on a specific secret that the agent should ideally not need to manage or possess. When the token is absent, the agent loses access to planning and memory tools entirely, which degrades the overall experience.

## Proposed Solutions

### Option 1: Proxy/Wrapper Service
Instead of the agent calling SpacetimeDB directly or tools requiring the token, all Spacetime-related operations should go through a proxy service or a dedicated "Spacetime Tool" that handles authentication on the host side.
- **Benefit:** Agent never sees the token.
- **Drawback:** Requires additional infrastructure (proxy service), adds network latency, and introduces a new point of failure.
- **Implementation:** Create a specialized tool that uses the host's credentials.

### Option 2: Token Injection via Sandbox Mount
If the token is required for CLI tools (like `stdb`), it should be mounted into the sandbox at a standard location (e.g., `~/.spacetime/config`) by the infrastructure, rather than being passed as an environment variable to the agent process.
- **Benefit:** CLI tools work out-of-the-box without agent intervention.
- **Drawback:** Still requires the token to exist somewhere; just moves the configuration burden to the infrastructure layer. Doesn't solve the problem when SpacetimeDB is genuinely unavailable.

### Option 3: Graceful Degradation with Local Fallback ✅ (Selected)
Modify the SpacetimeDB client layer to detect the absence of the token (or connection failure) and:
- Fall back to a local SQLite-based storage for `work_plan` and `search_memory` operations.
- Provide a clear, non-blocking warning (logged once) instead of a hard 500 error.
- Automatically use SpacetimeDB when the token is available, SQLite when it's not.

## Decision Rationale
**Option 3 was selected** because:
1. **No infrastructure changes required** — works with the existing setup.
2. **Resilient** — the agent functions even when SpacetimeDB is completely unavailable (network issues, maintenance, etc.).
3. **Backwards compatible** — when `SPACETIMEDB_TOKEN` is set, behavior is unchanged.
4. **Incremental** — Options 1 and 2 can be layered on top later if needed.

## Architecture

### Component Design
```
┌─────────────────────────────────────────┐
│           Tool Layer                    │
│   (work_plan, search_memory, etc.)      │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│       spacetime_client.py               │
│  ┌─────────────────────────────────┐    │
│  │  get_client() → StorageBackend  │    │
│  └──────────┬──────────────────────┘    │
│             │                           │
│    ┌────────▼────────┐                  │
│    │ SPACETIMEDB_TOKEN│                 │
│    │    present?      │                 │
│    └───┬─────────┬───┘                  │
│      Yes         No                     │
│    ┌───▼───┐  ┌──▼──────────┐           │
│    │SpaceDB│  │SQLiteFallback│          │
│    │Backend│  │Backend       │          │
│    └───────┘  └─────────────┘           │
└─────────────────────────────────────────┘
```

### Key Files
- `src/spacetime_client.py` — Main client module with backend abstraction
- `src/backends/base.py` — Abstract `StorageBackend` interface
- `src/backends/spacetimedb_backend.py` — SpacetimeDB implementation
- `src/backends/sqlite_backend.py` — SQLite fallback implementation
- `tests/test_spacetime_client.py` — Unit tests

### Interface
```python
class StorageBackend(ABC):
    """Abstract interface for storage backends."""
    
    @abstractmethod
    def store(self, namespace: str, key: str, value: dict) -> None: ...
    
    @abstractmethod
    def retrieve(self, namespace: str, key: str) -> Optional[dict]: ...
    
    @abstractmethod
    def list_keys(self, namespace: str) -> List[str]: ...
    
    @abstractmethod
    def delete(self, namespace: str, key: str) -> None: ...
    
    @abstractmethod
    def is_available(self) -> bool: ...
```

## Implementation Plan
1. **Create the `StorageBackend` abstract base class** defining the interface that both backends must implement.
2. **Implement `SQLiteBackend`** as the local fallback — stores data in `~/.bond/fallback.db` using a simple key-value schema with namespaces.
3. **Implement `SpacetimeDBBackend`** as the primary backend — wraps existing SpacetimeDB calls, requires `SPACETIMEDB_TOKEN`.
4. **Create `get_client()` factory function** in `spacetime_client.py` that returns the appropriate backend based on environment.
5. **Add logging** — warn once when falling back to SQLite so operators are aware.
6. **Write tests** covering: fallback selection, SQLite CRUD operations, backend switching, and error handling.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Data written to SQLite is lost when container is destroyed | Medium | Document that SQLite fallback is ephemeral; recommend volume mount for persistence |
| SQLite and SpacetimeDB data diverge | Low | Fallback is designed for degraded operation, not sync; clear warning in logs |
| SQLite concurrent access issues | Low | Use WAL mode and proper connection handling |
| Fallback masks real configuration issues | Medium | Log a clear warning on first fallback; include in health check output |

## Testing Strategy
- **Unit tests:** Test each backend independently (SQLite CRUD, SpacetimeDB mock calls).
- **Integration test:** Verify `get_client()` returns correct backend based on env vars.
- **Negative tests:** Ensure no exceptions are raised when `SPACETIMEDB_TOKEN` is absent.
- **All tests must pass with AND without `SPACETIMEDB_TOKEN` set.**

## Rollback Plan
Since this is additive (new module, no changes to existing code paths), rollback is simply removing the new files. The existing direct SpacetimeDB calls continue to work when the token is present.

## Success Criteria
- [ ] Agent can use `work_plan` and `search_memory` without `SPACETIMEDB_TOKEN` being set in its environment.
- [ ] No 500 errors from tool calls due to missing credentials.
- [ ] When `SPACETIMEDB_TOKEN` IS set, behavior is unchanged (SpacetimeDB backend is used).
- [ ] A clear warning is logged when falling back to SQLite.
- [ ] All unit tests pass in both token-present and token-absent configurations.
