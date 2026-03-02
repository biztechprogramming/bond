# Coding Task: Add importance decay and batch update to MemoryRepository

## Context

The memory system in `backend/app/features/memory/repository.py` stores memories with an `importance` score (0.0–1.0) but this score never changes after creation. Memories that haven't been accessed in a long time should have their importance decay, and frequently accessed memories should get a boost.

## Requirements

Read these files first to understand the codebase:
- `backend/app/features/memory/repository.py` — the repository you'll modify
- `backend/app/features/memory/models.py` — the data models
- `backend/tests/test_memory_repository.py` — existing tests (for style reference)

Then make these changes to `backend/app/features/memory/repository.py`:

### 1. Add an `decay_importance` method

Add a method `async def decay_importance(self, days_threshold: int = 30, decay_factor: float = 0.95) -> int` that:
- Finds all non-deleted memories where `last_accessed_at` is older than `days_threshold` days ago (or `last_accessed_at` is NULL and `created_at` is older than the threshold)
- Multiplies their `importance` by `decay_factor`
- Enforces a minimum importance of `0.05` (never decay below this)
- Updates `updated_at` to the current timestamp
- Returns the number of memories that were decayed

### 2. Add a `boost_importance` method

Add a method `async def boost_importance(self, memory_id: str, boost: float = 0.1, max_importance: float = 1.0) -> Memory` that:
- Increases the memory's `importance` by `boost`
- Caps at `max_importance`
- Updates `updated_at` to the current timestamp
- Raises `ValueError` if memory not found
- Returns the updated memory

### 3. Add a `batch_update_type` method

Add a method `async def batch_update_type(self, memory_ids: list[str], new_type: str, changed_by: str, reason: str) -> int` that:
- Updates the `type` field for all given memory IDs (only non-deleted ones)
- Creates a version entry for each updated memory
- Returns the count of memories actually updated (skipping not-found and already-deleted)

### 4. Modify the existing `update` method

Change the existing `update` method so it also updates `updated_at` to the current timestamp. Currently it only updates `content` but not the `updated_at` field.

## Constraints

- Only modify `backend/app/features/memory/repository.py` — do NOT change models, tests, or other files
- Follow the existing code style (use `text()` for SQL, ULID for IDs, ISO timestamps)
- All methods must be async and use `self._session`
- Import nothing new — everything you need is already imported
