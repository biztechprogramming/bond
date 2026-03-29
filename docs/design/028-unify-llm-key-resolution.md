# Design Doc 028: Unify LLM Key Resolution

**Status:** Draft  
**Author:** Bond  
**Date:** 2026-03-28  
**Problem:** The `/api/v1/llm/complete` endpoint (used by deploy discovery) fails with 400 `invalid_request_error` because it uses a completely different key resolution + request construction path than the working agent conversation turn flow.

---

## 1. Problem Statement

Bond has **two independent code paths** for calling LLMs:

### Path A — Agent Conversation Turn (WORKS ✅)
```
Gateway → POST /api/v1/conversations/{id}/turn
  → worker.py → _run_agent_turn()
    → ApiKeyResolver(injected_keys, provider_aliases, litellm_prefixes, persistence)
    → resolver.resolve_all(model, utility_model)
      → resolve_api_key() — 4-tier: injected_keys → SpacetimeDB → Vault → env
      → is_oauth_token() check → injects OAUTH_EXTRA_HEADERS
      → normalize_model_for_litellm() — maps provider prefixes (e.g. google/ → gemini/)
    → loop.py → litellm.acompletion(model, messages, **extra_kwargs)
```

**Key details of Path A:**
- `injected_keys` are pre-fetched by the Gateway at container launch time (fresh OAuth tokens)
- `ApiKeyResolver.resolve_all()` returns `(normalized_model, extra_kwargs, utility_kwargs, normalized_utility_model)`
- `extra_kwargs` contains `api_key` + `extra_headers` (if OAuth)
- The OAuth system prompt prefix is injected by the loop via `ensure_oauth_system_prefix()`
- Model normalization handles litellm prefix mapping (e.g. `google/gemini-2.5-flash` → `gemini/gemini-2.5-flash`)

### Path B — Standalone LLM Complete (BROKEN ❌)
```
Gateway → llm-discovery.ts → BackendClient.llmComplete()
  → POST /api/v1/llm/complete
    → llm.py (API endpoint) → chat_completion()
      → get_provider_api_key(provider) — calls Gateway /api/v1/provider-api-keys/:providerId
      → Falls back to _resolve_api_key() — 3-tier: env → settings DB → vault
      → _resolve_model_string() — simple f"{provider}/{model}" concatenation
      → get_oauth_extra_headers(api_key) → sets extra_headers if OAuth
      → ensure_oauth_system_prefix(messages, extra_kwargs=extra_kwargs)
      → litellm.acompletion(model_string, messages, **extra_kwargs)
```

**What's different (and broken) in Path B:**

| Concern | Path A (Working) | Path B (Broken) |
|---------|------------------|------------------|
| **Key source** | `injected_keys` dict (pre-fetched fresh OAuth token from Gateway at container launch) | `get_provider_api_key()` → Gateway `/provider-api-keys/:providerId` at call time |
| **Model normalization** | `ApiKeyResolver.normalize_model_for_litellm()` — uses `litellm_prefixes` from DB | `_resolve_model_string()` — hardcoded `f"{provider}/{model}"` |
| **Provider resolution** | `ApiKeyResolver.resolve_provider()` — uses `provider_aliases` from DB | Reads `settings.llm_provider` directly |
| **OAuth detection** | `ApiKeyResolver.is_oauth_token()` → sets `OAUTH_EXTRA_HEADERS` | `get_oauth_extra_headers()` — same logic but different code path |
| **System prompt** | Injected by loop + `ensure_oauth_system_prefix()` | `ensure_oauth_system_prefix()` only |
| **Config source** | Agent config from DB (`config.get("api_keys")`, `config.get("provider_aliases")`, etc.) | `get_settings()` singleton |
| **Persistence** | Has full persistence client for SpacetimeDB access | None |

### Root Cause of the 400 Error

The 400 `invalid_request_error` with message `"Error"` from Anthropic's OAuth API is most likely caused by one or more of:

1. **Wrong model string** — `_resolve_model_string()` builds `anthropic/claude-sonnet-4-20250514` but the OAuth token may require a different model identifier than what `settings.llm_model` resolves to. `ApiKeyResolver` normalizes via `litellm_prefixes` from the DB; `chat_completion()` does not.

2. **Missing or incorrect headers** — While `get_oauth_extra_headers()` should return the same headers, the code path is different and may not be triggered if the key resolution fails silently.

3. **Stale/wrong token** — `get_provider_api_key()` fetches from the Gateway's `/provider-api-keys/:providerId` endpoint which may return a different (or differently-formatted) token than what `injected_keys` provides.

4. **Missing system prompt prefix** — If `ensure_oauth_system_prefix()` doesn't detect the OAuth context (because `extra_kwargs` isn't populated correctly), the required `"You are Claude Code..."` prefix won't be prepended, causing Anthropic's OAuth API to reject the request.

---

## 2. Proposed Solution: Reuse `ApiKeyResolver` in `chat_completion()`

### Principle
**One code path for LLM calls.** `chat_completion()` should use `ApiKeyResolver` — the same class that works in the agent loop — instead of its own bespoke key resolution.

### 2.1 Create a Standalone `ApiKeyResolver` Factory

The challenge is that `ApiKeyResolver` currently requires:
- `injected_keys: dict[str, str]` — from agent container config
- `provider_aliases: dict[str, str]` — from DB
- `litellm_prefixes: dict[str, str]` — from DB  
- `persistence: Any` — a persistence client for SpacetimeDB

For standalone (non-agent) use, we need a factory that constructs one without container context.

**New function in `api_key_resolver.py`:**

```python
async def create_standalone_resolver() -> ApiKeyResolver:
    """Create an ApiKeyResolver for non-agent contexts (e.g. /llm/complete).
    
    Fetches provider config from the Gateway and settings DB,
    mirroring what worker.py does at container launch.
    """
    from backend.app.core.oauth import get_provider_api_key
    from backend.app.config import get_settings
    
    settings = get_settings()
    provider = settings.llm_provider  # e.g. "anthropic"
    
    # Fetch fresh key from Gateway (same as container launch)
    injected_keys: dict[str, str] = {}
    gateway_result = await get_provider_api_key(provider)
    if gateway_result:
        api_key, key_type = gateway_result
        injected_keys[provider] = api_key
    
    # Load provider_aliases and litellm_prefixes from settings/DB
    # These are stored in the settings table or can be loaded from providers.yaml
    provider_aliases = await _load_provider_aliases()
    litellm_prefixes = await _load_litellm_prefixes()
    
    return ApiKeyResolver(
        injected_keys=injected_keys,
        provider_aliases=provider_aliases,
        litellm_prefixes=litellm_prefixes,
        persistence=None,  # No SpacetimeDB in standalone mode
    )
```

### 2.2 Rewrite `chat_completion()` to Use `ApiKeyResolver`

**Replace the current `chat_completion()` in `backend/app/agent/llm.py`:**

```python
async def chat_completion(
    messages: list[dict[str, str]],
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    stream: bool = False,
) -> str | AsyncIterator[str]:
    """Call an LLM via LiteLLM using the unified ApiKeyResolver."""
    from backend.app.agent.api_key_resolver import create_standalone_resolver
    from backend.app.core.oauth import ensure_oauth_system_prefix
    
    settings = get_settings()
    provider = provider or settings.llm_provider
    model = model or settings.llm_model
    
    # Use the SAME resolver the agent loop uses
    resolver = await create_standalone_resolver()
    
    # Build the full model string (e.g. "anthropic/claude-sonnet-4-20250514")
    raw_model = f"{provider}/{model}" if "/" not in model else model
    normalized_model = resolver.normalize_model_for_litellm(raw_model)
    
    # Resolve key + headers
    api_key = await resolver.resolve_api_key(normalized_model)
    extra_kwargs: dict = {}
    if api_key:
        extra_kwargs["api_key"] = api_key
        if resolver.is_oauth_token(api_key):
            extra_kwargs["extra_headers"] = dict(resolver.OAUTH_EXTRA_HEADERS)
    
    # Inject OAuth system prompt prefix (CRITICAL for Claude Max)
    ensure_oauth_system_prefix(messages, extra_kwargs=extra_kwargs)
    
    logger.info("LLM call: model=%s, oauth=%s, messages=%d",
                normalized_model, "extra_headers" in extra_kwargs, len(messages))
    
    if stream:
        response = await litellm.acompletion(
            model=normalized_model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            stream=True, **extra_kwargs,
        )
        return _stream_response(response)
    else:
        response = await litellm.acompletion(
            model=normalized_model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            **extra_kwargs,
        )
        return response.choices[0].message.content
```

### 2.3 Load Provider Config from DB/YAML

**New helper functions** (in `api_key_resolver.py` or a shared config module):

```python
async def _load_provider_aliases() -> dict[str, str]:
    """Load provider aliases from providers.yaml."""
    import yaml
    config_path = Path(__file__).parent / "providers.yaml"
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return {p["id"]: p.get("canonical", p["id"]) 
                for p in data.get("providers", [])}
    return {}

async def _load_litellm_prefixes() -> dict[str, str]:
    """Load litellm prefix mappings from providers.yaml."""
    import yaml
    config_path = Path(__file__).parent / "providers.yaml"
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return {p["id"]: p.get("litellm_prefix", p["id"]) 
                for p in data.get("providers", [])}
    return {}
```

---

## 3. What Gets Deleted

Once `chat_completion()` uses `ApiKeyResolver`, the following become dead code:

| Code | File | Action |
|------|------|--------|
| `_resolve_api_key()` | `backend/app/agent/llm.py` | **Delete** — replaced by `ApiKeyResolver.resolve_api_key()` |
| `_get_api_key_from_settings()` | `backend/app/agent/llm.py` | **Delete** — replaced by `ApiKeyResolver` SpacetimeDB path |
| `_resolve_model_string()` | `backend/app/agent/llm.py` | **Delete** — replaced by `ApiKeyResolver.normalize_model_for_litellm()` |
| `get_provider_api_key()` call in `chat_completion()` | `backend/app/agent/llm.py` | **Delete** — `create_standalone_resolver()` handles this |

**`get_provider_api_key()`** in `oauth.py` is NOT deleted — it's still used by `create_standalone_resolver()` to fetch the fresh token from the Gateway.

---

## 4. Files Changed

| File | Change |
|------|--------|
| `backend/app/agent/api_key_resolver.py` | Add `create_standalone_resolver()`, `_load_provider_aliases()`, `_load_litellm_prefixes()` |
| `backend/app/agent/llm.py` | Rewrite `chat_completion()` to use `ApiKeyResolver`; delete `_resolve_api_key()`, `_get_api_key_from_settings()`, `_resolve_model_string()` |
| `backend/app/api/v1/llm.py` | No change needed — it already calls `chat_completion()` |
| `gateway/src/deployments/llm-discovery.ts` | No change needed — it already calls `BackendClient.llmComplete()` |
| `gateway/src/backend/client.ts` | No change needed |

---

## 5. Testing Plan

1. **Unit test `create_standalone_resolver()`** — mock `get_provider_api_key()` to return an OAuth token, verify the resolver is constructed with correct `injected_keys` and the model normalization works.

2. **Unit test `chat_completion()` with OAuth** — mock `litellm.acompletion`, verify:
   - Model string is normalized (not just `f"{provider}/{model}"`)
   - `extra_headers` contains OAuth headers when token is `sk-ant-oat-*`
   - System prompt starts with `"You are Claude Code, Anthropic's official CLI for Claude."`
   - `api_key` is passed through

3. **Integration test** — trigger deploy discovery from the UI, verify:
   - No 400 error
   - LLM returns valid JSON analysis
   - Backend logs show `oauth_headers=True` and correct model string

4. **Regression test** — verify agent conversation turns still work (they don't use `chat_completion()` at all, so this is a safety check only).

---

## 6. Migration / Rollout

This is a **pure refactor** of the backend's `chat_completion()` function. No API changes, no schema changes, no frontend changes.

1. Implement changes on `feature/appdeploy-skill` branch
2. Test deploy discovery end-to-end
3. Merge

---

## 7. Future Considerations

- **Caching the resolver** — `create_standalone_resolver()` fetches a fresh token on every call. For high-frequency use, consider caching the resolver instance with a TTL matching the OAuth token lifetime.
- **Consolidating `get_provider_api_key()` into `ApiKeyResolver`** — The Gateway fetch logic in `oauth.py` could be moved into `ApiKeyResolver` as another resolution tier, further reducing code paths.
- **Removing `_resolve_api_key()` from the agent loop** — `loop.py` line 262 has a `_resolve_api_key()` call that appears to be dead code since `ApiKeyResolver` already resolved the key. Verify and remove.
