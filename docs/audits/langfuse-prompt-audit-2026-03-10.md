# Langfuse Prompt Injection Audit — 2026-03-10

## Overview

Analyzed 100 traces across 3 sessions from the self-hosted Langfuse instance (`http://localhost:18786`). Focus: prompt fragment efficiency, Tier 3 semantic router, and container image size.

---

## What's Working

### Tier 1 (Always-On) ✅
- All 10 fragments injected every turn. Deterministic, no misses.
- **~3,329 tokens** (10 fragments from `universal/` + `must-compile`)
- Consistent across all 100 traces

### Tier 2 (Lifecycle) ✅
- Phase detection works. Observed clean transitions:
  - `IDLE → IMPLEMENTING` → injects `bugfix`, `code-quality`, `engineering`, `file-operations` (5 frags, ~821 tokens)
  - `IMPLEMENTING → PLANNING` → swaps to `planning`, `spec-building` (2 frags, ~443 tokens)
- Old Tier 2 fragments are stripped before new ones are injected
- Langfuse tags update correctly with `phase:IMPLEMENTING` / `phase:PLANNING`

### Langfuse Metadata ✅
- Every trace has: fragment names, token counts, system prompt hash, phase tags
- Filterable by `prompt:bugfix`, `phase:IMPLEMENTING`, `cost:high`
- Full audit trail of which fragments were injected per LLM call

---

## Issues Found

### 1. Tier 3 Never Fired in Production (0/100 traces)

**Symptom:** Zero Tier 3 (semantic router) fragments appeared in any Langfuse trace. Every trace shows only Tier 1 + Tier 2 fragments.

**Root Cause:** Not a code bug — the router works correctly. Local testing shows 7/8 test messages match expected fragments with good scores (0.48–0.94). The issue is that the 3 captured sessions all had task-specific messages ("Please correct these issues with rooms not loading") that don't match the utterance patterns defined in `manifest.yaml`.

**Local test results (router works):**

| User Message | Matched Fragments | Top Score |
|---|---|---|
| "Help me write a FastAPI endpoint for user auth" | fastapi, routing, auth | 0.76 |
| "Fix the Docker container networking issue" | docker, compose | 0.70 |
| "Write a React component for the dashboard" | react, server-components, frontend | 0.68 |
| "Add a SQLite migration for the new table" | migrations, sqlite, wal-mode | 0.64 |
| "The SpacetimeDB reducer is failing" | reducers, spacetimedb, sql | 0.85 |
| "Write unit tests for the worker module" | testing | 0.48 |
| "Set up JWT authentication" | jwt, auth | 0.94 |
| "Please correct these issues with rooms not loading" | *(no match)* | — |

**Verdict:** The router is working but undertested in real conversations. We need more production data to judge hit rate. The utterances in `manifest.yaml` are biased toward "greenfield" requests and miss bugfix/troubleshooting patterns.

**Recommendation:**
- Add more utterances for common real patterns: bugfix instructions, "this is broken", "fix the X", troubleshooting
- Consider lowering `SCORE_THRESHOLD` from `0.4` to `0.35` for borderline matches
- Add a Langfuse tag `tier3:miss` when no Tier 3 fragments are selected, to track false-negative rate over time

---

### 2. Container Image Size: 12.9GB (PyTorch is 1.7GB of that)

**Symptom:** `bond-agent-worker` image is 12.9GB. `torch` alone is 1.7GB. `transformers` adds 55MB. `numpy` is 33MB.

**Dep chain:** `sentence-transformers` → `torch` (1.7GB) + `transformers` (55MB) + `huggingface-hub` (model download at startup)

**Current stack:**
```
sentence-transformers (5.2.3) → torch (2.10.0, 1.7GB)
semantic-router (0.1.12)      → numpy (2.4.3, 33MB)
all-MiniLM-L6-v2 model       → ~80MB downloaded from HuggingFace on first run
```

**Alternatives evaluated:**

| Approach | Deps | Image Size Impact | Accuracy | Startup Time | API Calls |
|---|---|---|---|---|---|
| **Current** (HuggingFaceEncoder + sentence-transformers) | torch, transformers, numpy | +1.8GB | Excellent (dense semantic) | ~3s model load | None |
| **FastEmbed + ONNX** (`FastEmbedEncoder`) | onnxruntime, numpy | +~200MB | Very good (ONNX MiniLM) | ~1s | None |
| **TF-IDF** (`TfidfEncoder`) | numpy only | +0 (numpy already needed) | Moderate (keyword overlap, not semantic) | <100ms | None |
| **Pre-computed embeddings** (custom) | numpy only | +0 | Excellent (same model, offline) | <100ms | None |
| **LiteLLM Encoder** (`LiteLLMEncoder`) | litellm (already installed) | +0 | Depends on provider | <100ms | Yes (per turn) |

#### Recommended: Option A — FastEmbed + ONNX Runtime

**Why:** Best tradeoff. ONNX Runtime CPU is ~200MB vs 1.7GB for PyTorch. Uses the same MiniLM model (BAAI/bge-small-en-v1.5 by default, or can load all-MiniLM-L6-v2). No API calls. Accuracy comparable to sentence-transformers.

```python
# fragment_router.py change:
- from semantic_router.encoders import HuggingFaceEncoder
+ from semantic_router.encoders import FastEmbedEncoder

- encoder = HuggingFaceEncoder(name=ENCODER_MODEL)
+ encoder = FastEmbedEncoder(name="BAAI/bge-small-en-v1.5")
```

```dockerfile
# Dockerfile.agent change:
- "sentence-transformers>=2.0,<4.0"
+ fastembed
```

**Savings:** ~1.5GB off image size. No PyTorch. No HuggingFace model download at startup (FastEmbed caches ONNX models locally).

#### Alternative: Option B — Pre-computed Embeddings (Zero Extra Deps)

If even 200MB is too much: pre-compute utterance embeddings at build time (in CI or dev), save to `prompts/embeddings.json`, and do cosine similarity at runtime with just numpy.

```python
# build step (dev machine with torch):
python scripts/precompute_embeddings.py  # outputs prompts/embeddings.json

# runtime (container, numpy only):
embeddings = json.load(open("prompts/embeddings.json"))
# cosine_sim with numpy — 10 lines of code, no library needed
```

**Savings:** ~1.8GB off image. No torch, no transformers, no sentence-transformers, no semantic-router. The model never runs in the container. Downside: must re-run the build script when utterances change.

---

### 3. Phase Detection Flicker

**Symptom:** In the main session (74 traces), phase transitions include:
```
IMPLEMENTING → unknown → IMPLEMENTING
PLANNING → unknown → PLANNING
```

**Cause:** Between tool-call batches, the lifecycle detector sees iterations without tool calls and can't classify the phase, so it falls back to unknown/IDLE. This triggers a system prompt rewrite cycle: strip Tier 2 → re-inject Tier 2 on the next iteration.

**Impact:** Unnecessary system prompt mutations. Each flicker causes two string operations on the system prompt (strip + re-inject). Also creates noisy phase transitions in Langfuse.

**Recommendation:** Add hysteresis — require 2+ consecutive non-matching iterations before dropping the phase. Or: keep the current phase until a *different* phase is positively detected (i.e., IDLE requires explicit signal, not absence of signal).

---

### 4. Prompt Cache Metrics Not Visible in Langfuse

**Symptom:** All observations show `cache_read_input_tokens: None` and `cache_creation_input_tokens: None`, despite the worker setting `cache_control: {"type": "ephemeral"}` on system prompt blocks.

**Impact:** Can't verify whether Anthropic prompt caching is actually working. At $0.056 avg per call on Opus with ~3,900 token system prompts, caching could save 90% on repeated system prompt reads.

**Possible causes:**
1. LiteLLM's Langfuse callback doesn't forward Anthropic-specific usage fields
2. The usage fields use different key names than what Langfuse expects
3. Caching is actually working but the metrics aren't being reported

**Recommendation:** Check LiteLLM response objects for `cache_read_input_tokens` in the raw API response. If present but not forwarded, patch the Langfuse callback. If absent, debug the cache_control headers.

---

### 5. Utility Model Calls Create Orphan Traces

**Symptom:** 7 traces have `sessionId: None` and no meaningful metadata. These are the `acompletion` calls for history compression and tool result filtering.

**Impact:** These calls are invisible in Langfuse's session view. Can't see the full cost picture for a conversation.

**Recommendation:** Pass `session_id` in the metadata for compression and filter calls:
```python
# In worker.py, when calling utility model for compression:
metadata = {"session_id": conversation_id, "trace_name": f"compression-{conversation_id}"}
```

---

### 6. System Prompt Overhead

**Numbers from Langfuse:**
- Base system prompt (agent.system.main.md + sandbox instructions): ~545 tokens
- Tier 1 fragments: ~3,329 tokens (10 fragments)
- Category manifest: ~347 tokens
- Tier 2 (when active): ~443–821 tokens
- **Total system prompt: 3,894 tokens** (constant across all traces)

**Fragment overhead is 6x the base prompt.** The fragments are the dominant cost in the system prompt. Some overlap exists:
- `progress-tracking` (430 tokens) and `work-planning` (420 tokens) both cover task management
- `proactive-workflow` (249 tokens) overlaps with both

Not necessarily a problem — these are the "always-on" behavioral instructions. But worth reviewing if any can be consolidated.

---

## Token/Cost Summary (from 50 observations)

| Metric | Value |
|---|---|
| Total cost (50 calls) | $2.80 |
| Avg cost per call | $0.056 |
| Avg input tokens per call | 7,904 |
| Avg output tokens per call | 268 |
| Model | claude-opus-4-6 |
| System prompt overhead per call | ~3,894 tokens (49% of avg input) |

---

## Action Items

| Priority | Item | Effort | Impact |
|---|---|---|---|
| **P1** | Switch to FastEmbed encoder (drop PyTorch, save ~1.5GB) | S | Container size |
| **P1** | Add bugfix/troubleshooting utterances to manifest.yaml | S | Tier 3 hit rate |
| **P2** | Add `tier3:miss` tag to Langfuse when no Tier 3 matches | S | Observability |
| **P2** | Fix phase detection flicker (add hysteresis) | M | Prompt stability |
| **P2** | Forward session_id to utility model calls | S | Langfuse completeness |
| **P3** | Investigate prompt cache metrics in LiteLLM → Langfuse | M | Cost verification |
| **P3** | Review Tier 1 fragment overlap (progress/planning/workflow) | S | Token efficiency |
