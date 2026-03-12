STATUS: done
REPO: bond
BRANCH: feat/gemini-api-key-spacetimedb
COMMITS: d3cb2d8, 4274cf2, efb5b67, d2587e1, 833c525, 726ef78, 6c6fbaf
CHANGES: Implemented API key retrieval from SpacetimeDB for worker
TESTS: All existing tests pass; manual verification shows worker successfully gets and uses API keys from SpacetimeDB

## Summary

Successfully implemented the feature for the worker to get encrypted Gemini/Google API keys from SpacetimeDB. The worker now:

1. **Gets API keys from SpacetimeDB** via the Gateway persistence API
2. **Handles provider ID mapping**: `gemini` models → `google` provider ID (with automatic fallback)
3. **Decrypts keys** using the existing crypto module
4. **Prioritizes sources**: injected_keys → SpacetimeDB → vault → environment variables
5. **Includes comprehensive logging** for debugging

## Key Changes

### Gateway (`gateway/src/persistence/router.ts`)
- Added GET `/settings/:key` endpoint
- Added GET `/provider-api-keys/:providerId` endpoint  
- Fixed SQL queries to work with SpacetimeDB (no `?` parameter support)

### Persistence Client (`backend/app/agent/persistence_client.py`)
- Added `get_setting(key)` method
- Added `get_provider_api_key(provider_id)` method
- Both methods handle HTTP errors and return `None` for 404

### Worker (`backend/app/worker.py`)
- Modified `_resolve_api_key()` to try SpacetimeDB after injected_keys
- Added fallback from `gemini` to `google` provider ID
- Added detailed logging for debugging
- Trims whitespace from decrypted keys

## Verification

The implementation was verified with:
1. **Logs show successful key retrieval**: "Got API key for gemini from SpacetimeDB provider_api_keys (length: 39, starts with: AIzaSyCmZ6)"
2. **Logs show successful API call**: "Calling LiteLLM with model gemini/gemini-3-flash-preview, API key length: 39"
3. **LLM response succeeds**: No authentication errors, Gemini API accepts the key
4. **Manual testing**: Confirmed Gateway endpoints return encrypted keys, decryption produces valid API keys

## Learnings

- SpacetimeDB SQL doesn't support `?` parameter placeholders
- Provider IDs in `provider_api_keys` table use canonical names (`google`, not `gemini`)
- Gateway must be running and accessible from worker containers
- The existing crypto/vault infrastructure works correctly for decryption