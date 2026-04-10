# 108 — Coding Agent Provider & Model Settings

**Status:** Proposed
**Date:** 2026-04-10
**Author:** (design)
**Related:** [037-coding-agent-skill](037-coding-agent-skill.md), [059-coding-agent-credential-unification](059-coding-agent-credential-unification.md)

---

## 1. Problem Statement

Bond's coding agent tool (`backend/app/agent/tools/coding_agent.py`) spawns sub-agents (Claude Code, Codex, Pi) but provides **no user-facing control** over:

- **Which model** the sub-agent uses. Codex currently runs with its CLI default, which may not match the user's intent or subscription. Claude Code similarly defaults to whatever model the CLI chooses. There is no `--model` flag passed.
- **Which coding agent provider** is the global default. The tool schema exposes `agent_type` as a per-invocation enum (`claude | codex | pi`), but there is no settings-level default — the orchestrating LLM picks `claude` by default (schema default), with no admin override.
- **Which providers are available.** `AGENT_COMMANDS` is a hardcoded dict; adding a new provider requires a code change and redeployment.

The LLM tab in Settings shows the *orchestrator* provider/model (from `bond.json`) as read-only, and has coding-agent checkboxes for log/stream — but nothing for coding-agent provider or model selection.

### User-Facing Goals

1. Admin can set the **default coding agent provider** (e.g., "use Codex globally").
2. Admin can set the **model** for each coding agent provider (e.g., Codex should use `o3` not `o4-mini`).
3. The settings UI surfaces available providers based on what is installed and has credentials — not a hardcoded list.
4. Future coding agent providers can be registered without frontend changes.

---

## 2. Current-State Analysis

### 2.1 Tool Invocation Path

1. **Tool schema** (`backend/app/agent/tools/definitions.py`): `coding_agent` accepts `agent_type` enum `[claude, codex, pi]` with default `claude`.
2. **Handler** (`coding_agent.py:handle_coding_agent`): looks up `AGENT_COMMANDS[agent_type]` → spawns `binary + args` as subprocess.
3. **No model flag** is appended to the command. Claude Code CLI supports `--model`, Codex supports `--model`, Pi supports `-m`. None are used.
4. **Context injection** (`backend/app/worker.py`): queries `settings WHERE key LIKE 'coding_agent.%'` and passes as `coding_agent_settings` dict. Currently only `log_to_file` and `stream_output` are consumed.

### 2.2 Settings Infrastructure

| Layer | Mechanism | Coding-Agent Coverage |
|-------|-----------|----------------------|
| `bond.json` / env vars | `get_settings()` in `config.py` | Orchestrator LLM only (`llm_provider`, `llm_model`) |
| SpacetimeDB `settings` table | Generic key-value via `set_setting` reducer | `coding_agent.log_to_file`, `coding_agent.stream_output` |
| SpacetimeDB `provider_api_keys` table | Encrypted keys by `(provider_id, key_type)` | `llm.api_key.anthropic`, `llm.api_key.openai` exist but are not wired to coding-agent env injection |
| `REQUIRED_ENV` dict in `coding_agent.py` | Checks `os.environ` for required API key | Hardcoded: claude→`ANTHROPIC_API_KEY`, codex→`OPENAI_API_KEY` |

### 2.3 Frontend Settings Page

`frontend/src/app/settings/page.tsx`:
- **LLM tab**: read-only provider/model from bond.json, turn timeout slider, coding-agent log/stream checkboxes.
- **API Keys tab**: input fields for `llm.api_key.{anthropic,openai,google}`, `embedding.api_key.*`, `image.api_key.*`.
- No coding-agent provider selector or model override anywhere.

### 2.4 Credential Flow

- Claude Code: `ANTHROPIC_API_KEY` env var **or** OAuth credentials at `~/.claude/.credentials.json` (see doc 059).
- Codex: `OPENAI_API_KEY` env var.
- Pi: `ANTHROPIC_API_KEY` env var.
- `handle_coding_agent` explicitly removes `ANTHROPIC_API_KEY` from Claude's env to force OAuth (lines 254-260), but falls back to env var if no credentials file exists.

---

## 3. Proposed Design

### 3.1 Settings Data Model

Use the existing SpacetimeDB `settings` table (generic key-value) for all new keys. This is consistent with how `coding_agent.log_to_file` and `coding_agent.stream_output` already work and avoids schema migrations.

**New settings keys:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `coding_agent.default_provider` | string | `"claude"` | Global default coding agent provider |
| `coding_agent.model.claude` | string | `""` (empty = CLI default) | Model override for Claude Code (`--model` flag) |
| `coding_agent.model.codex` | string | `""` | Model override for Codex (`--model` flag) |
| `coding_agent.model.pi` | string | `""` | Model override for Pi (`-m` flag) |
| `coding_agent.model.<future>` | string | `""` | Model override for any registered provider |

Empty string means "use the CLI's built-in default." This is intentional — we should not guess what models each CLI supports; the user sets an override only when needed.

**Why not a dedicated table?** The settings table already stores coding-agent config, the schema is flexible for arbitrary providers, and the frontend/backend patterns for reading/writing generic settings are established. A dedicated `coding_agent_providers` table would add migration cost with no clear benefit at this scale.

### 3.2 Provider Registry (Backend)

Replace the hardcoded `AGENT_COMMANDS` dict with a registry that merges built-in providers with any future registered ones.

```python
# coding_agent.py

@dataclass
class CodingAgentProvider:
    id: str                    # e.g. "claude", "codex"
    binary: str                # CLI binary name
    base_args: list[str]       # default args (e.g. ["--print"])
    model_flag: str | None     # flag name for model override, e.g. "--model", "-m"
    needs_pty: bool
    required_env_key: str      # e.g. "ANTHROPIC_API_KEY"
    env_key_provider_id: str   # maps to provider_api_keys table provider_id

BUILTIN_PROVIDERS: dict[str, CodingAgentProvider] = {
    "claude": CodingAgentProvider(
        id="claude", binary="claude",
        base_args=["--dangerously-skip-permissions", "--print"],
        model_flag="--model", needs_pty=True,
        required_env_key="ANTHROPIC_API_KEY",
        env_key_provider_id="anthropic",
    ),
    "codex": CodingAgentProvider(
        id="codex", binary="codex",
        base_args=["exec", "--full-auto"],
        model_flag="--model", needs_pty=True,
        required_env_key="OPENAI_API_KEY",
        env_key_provider_id="openai",
    ),
    "pi": CodingAgentProvider(
        id="pi", binary="pi",
        base_args=["-p"],
        model_flag="-m", needs_pty=True,
        required_env_key="ANTHROPIC_API_KEY",
        env_key_provider_id="anthropic",
    ),
}
```

The registry is extensible: future providers can be added via a `register_coding_agent_provider()` function or loaded from a config file without touching the UI.

### 3.3 Model Resolution Rules

When `handle_coding_agent` builds the subprocess command:

```
1. Check coding_agent_settings for "coding_agent.model.{agent_type}"
   → If non-empty, append [provider.model_flag, value] to command args.
2. Else: do not append any model flag (CLI uses its own default).
```

For provider selection when the orchestrating LLM calls `coding_agent` without specifying `agent_type`:

```
1. Read coding_agent_settings["coding_agent.default_provider"]
   → If set and provider exists in registry, use it.
2. Else: fall back to "claude" (current behavior).
```

**No per-tool override** is proposed in this iteration. The `agent_type` parameter on the tool schema already serves as a per-invocation override chosen by the orchestrating LLM.

### 3.4 Backend API Changes

#### New Endpoint

```
GET /settings/coding-agent/providers
```

Returns available coding agent providers with their status:

```json
{
  "providers": [
    {
      "id": "claude",
      "name": "Claude Code",
      "binary": "claude",
      "installed": true,
      "has_credentials": true,
      "model_override": "claude-sonnet-4-20250514",
      "model_flag": "--model"
    },
    {
      "id": "codex",
      "name": "Codex",
      "binary": "codex",
      "installed": true,
      "has_credentials": false,
      "model_override": "",
      "model_flag": "--model"
    }
  ],
  "default_provider": "claude"
}
```

**`installed`** is determined by `shutil.which(binary)` — if the CLI binary is on PATH, it's available.
**`has_credentials`** checks whether the required env var or provider API key exists.

#### Modified Behavior

- `PUT /settings/{key}` already handles arbitrary keys — no change needed for writing `coding_agent.default_provider` or `coding_agent.model.*`.
- `handle_coding_agent` reads the new keys from `coding_agent_settings` context dict (already injected by `worker.py` via the `coding_agent.%` LIKE query).

### 3.5 Frontend UX Changes

Add a **"Coding Agent"** section to the existing **LLM tab** in the Settings page (or promote it to its own tab if the LLM tab becomes too long).

```
┌─ Coding Agent ──────────────────────────────────────┐
│                                                      │
│  Default Provider: [▾ Claude Code    ]               │
│                                                      │
│  ┌─ Provider Settings ────────────────────────────┐  │
│  │ Claude Code    ✓ installed  ✓ credentials      │  │
│  │   Model override: [________________________]   │  │
│  │                                                │  │
│  │ Codex          ✓ installed  ✗ credentials      │  │
│  │   Model override: [________________________]   │  │
│  │                                                │  │
│  │ Pi             ✗ installed  ✓ credentials      │  │
│  │   Model override: [________________________]   │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ☑ Log output to file                                │
│  ☑ Stream output to UI                               │
└──────────────────────────────────────────────────────┘
```

Key behaviors:
- **Provider list is dynamic** — fetched from `GET /settings/coding-agent/providers`, not hardcoded in the frontend.
- Default provider dropdown only shows providers where `installed == true`.
- Model override is a free-text field (we don't know what models each CLI supports).
- Credential status badges link to the API Keys tab for setup.
- Existing log/stream checkboxes move into this section for grouping.

### 3.6 Credential & Auth Implications

| Provider | Key Source | Notes |
|----------|-----------|-------|
| Claude Code | OAuth (`~/.claude/.credentials.json`) preferred; `ANTHROPIC_API_KEY` fallback | Doc 059 unifies this via pi-ai gateway OAuth flow. No change needed here. |
| Codex | `OPENAI_API_KEY` env var | Already in `provider_api_keys` table as `llm.api_key.openai`. Must be injected into subprocess env. |
| Pi | `ANTHROPIC_API_KEY` env var | Shares credential with Claude Code's fallback path. |
| Future | `{PROVIDER}_API_KEY` | `CodingAgentProvider.required_env_key` and `env_key_provider_id` fields handle this generically. |

**Action needed:** `handle_coding_agent` currently reads API keys from `os.environ`. It should also check `context["api_keys"]` (already injected by `worker.py` from the provider_api_keys table) and set the required env var in the subprocess environment if not already present. This closes the gap where a user sets an API key in the Settings UI but the coding agent doesn't see it.

### 3.7 Tool Schema Update

The `agent_type` enum in the tool definition should not be hardcoded. Instead, generate it dynamically from the provider registry:

```python
# definitions.py — at tool-definition build time
agent_types = list(BUILTIN_PROVIDERS.keys())  # + any registered providers
```

This ensures the orchestrating LLM sees all available providers in the function schema without manual updates.

---

## 4. Migration & Backward Compatibility

- **No schema migration required.** All new config uses the existing `settings` key-value table.
- **Default behavior unchanged.** Empty/missing `coding_agent.default_provider` falls back to `"claude"`. Empty model overrides mean CLI defaults (current behavior).
- **Existing settings preserved.** `coding_agent.log_to_file` and `coding_agent.stream_output` continue to work as-is.
- **AGENT_COMMANDS consumers.** Any code referencing `AGENT_COMMANDS` directly must be updated to use the new `CodingAgentProvider` registry. Grep shows only `coding_agent.py` and its tests reference it.

---

## 5. Observability & Error Handling

- **Invalid provider:** If `coding_agent.default_provider` references an unregistered provider, log a warning and fall back to `"claude"`.
- **Invalid model:** If a model override is set but the CLI rejects it, the subprocess will fail. The `done` event already captures `exit_code` and `stderr` — surface the error message in the UI stream.
- **Missing binary:** `shutil.which()` check in the providers endpoint prevents selecting an uninstalled provider as default. The handler should also check at invocation time and return a clear error.
- **Missing credentials:** Current `REQUIRED_ENV` check already fails fast. Extend it to also check `context["api_keys"]` before declaring credentials missing.

---

## 6. Test Plan

| Test | Type | Description |
|------|------|-------------|
| Provider registry returns installed/credential status | Unit | Mock `shutil.which` and `os.environ`; verify provider list |
| Model flag appended to command | Unit | Set `coding_agent.model.codex = "o3"`; verify `["codex", "exec", "--full-auto", "--model", "o3"]` |
| Empty model override = no flag | Unit | Verify no `--model` arg when setting is empty |
| Default provider from settings | Unit | Set `coding_agent.default_provider = "codex"`; call without `agent_type`; verify codex used |
| Default provider fallback | Unit | No setting → defaults to claude |
| API key injection from provider_api_keys | Integration | Set key in DB, verify subprocess env contains it |
| Settings API round-trip | Integration | PUT then GET coding-agent settings; verify persistence |
| Frontend renders dynamic provider list | E2E | Mock API; verify dropdown populated from response |
| Existing log/stream settings unaffected | Regression | Verify existing settings still read correctly |

---

## 7. Rollout Plan

1. **Phase 1 — Backend registry + settings keys + API endpoint.** No UI changes yet. Default behavior identical to current.
2. **Phase 2 — Model flag injection.** Wire `--model` / `-m` flags into subprocess command based on settings. Ship behind no flag (empty default = no behavior change).
3. **Phase 3 — Frontend UI.** Add coding-agent section to Settings page. Users can now configure provider/model.
4. **Phase 4 — Dynamic tool schema.** Generate `agent_type` enum from registry instead of hardcoding in definitions.py.

---

## 8. Related Issues & Gaps Discovered

1. **Codex uses wrong model (motivating issue).** No model flag is passed to any coding agent CLI. Codex defaults to whatever OpenAI sets as the CLI default, which may not match user expectations.

2. **API keys not injected into subprocess env from DB.** `handle_coding_agent` checks `os.environ` for required keys (line 836-846) but does not read from `context["api_keys"]` which contains keys set via the Settings UI. A user who sets their OpenAI key in Bond's API Keys tab will get a "missing OPENAI_API_KEY" error from Codex.

3. **Claude Code env var removal is fragile.** Lines 254-260 strip `ANTHROPIC_API_KEY` from Claude's subprocess env to force OAuth, but if OAuth credentials are missing, it falls back to the env var by re-adding it. This logic should be consolidated with the credential unification work in doc 059.

4. **`AGENT_COMMANDS` and `REQUIRED_ENV` are separate dicts.** They describe the same providers but are not co-located or validated together. A typo in one but not the other would silently break.

5. **Tool schema `agent_type` enum is hardcoded in definitions.py.** Adding a new provider requires updating both `AGENT_COMMANDS` in `coding_agent.py` and the enum in `definitions.py` — easy to miss.

6. **No binary availability check at invocation time.** If Codex CLI is not installed in the container, the subprocess fails with an opaque error. The handler should check `shutil.which()` and return a clear message.

7. **No provider-specific model validation.** Free-text model override means a user could set an invalid model string. The CLI will reject it, but the error path is indirect (subprocess stderr → output stream → user reads log). Consider validating known models or at minimum surfacing the CLI error prominently.

8. **LLM tab shows orchestrator config as read-only.** Users may expect to change the orchestrator model from the UI. This is out of scope for this doc but worth noting — the "from bond.json" note is confusing when other settings are editable.

9. **Pi agent shares `ANTHROPIC_API_KEY` with Claude fallback.** If a user wants Pi to use a different Anthropic key than Claude's fallback, there's no mechanism for that. The `env_key_provider_id` mapping in the proposed registry could be extended to support per-provider key overrides in the future.
