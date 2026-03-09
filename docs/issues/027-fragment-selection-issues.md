# Doc 027 Fragment Selection — Open Issues

**Created:** 2026-03-09
**Source:** Langfuse trace analysis + code review of phases 1–3

---

## 1. ~~🔴 Tier 3 semantic router is dead in production~~ → 🟡 Tier 3 metadata missing from Langfuse traces

**Severity:** ~~Critical~~ → Medium (observability gap, not functional failure)
**Component:** `backend/app/worker.py` (Langfuse metadata builder)

**Original diagnosis was wrong.** `semantic-router` and `sentence-transformers` ARE installed in both `pyproject.toml` and `Dockerfile.agent`. Container logs confirm Tier 3 is working: "Built semantic route layer: 43 routes from 43 Tier 3 fragments" and selections are being made. The issue is that Tier 3 fragment metadata (`_tier3_meta`) was computed but never added to `_audit_fragments` in the Langfuse metadata builder — so traces show `fragment_count: 11` (only Tier 1) even when Tier 3 fragments are injected.

**Fix (applied):** Added `_tier3_meta` entries to `_audit_fragments` in the Langfuse metadata builder block.

---

## 2. ~~🔴 LANGFUSE env vars not forwarded to worker containers~~ → ✅ Already handled

**Severity:** N/A — not an issue
**Component:** `Dockerfile.agent`

**Original diagnosis was wrong.** The `Dockerfile.agent` bakes in `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` as ENV directives with the auto-init keys from `docker-compose.langfuse.yml`. Container logs confirm: "Langfuse observability enabled (host=http://host.docker.internal:18786)". No fix needed.

---

## 3. 🟡 No token budget enforcement

**Severity:** Medium — risk of context blowup
**Component:** `backend/app/worker.py` (system prompt assembly)

10 Tier 1 + up to 10 Tier 2 + up to 5 Tier 3 fragments can all inject simultaneously with no cap on total injected tokens. A bad manifest change could blow up context window usage.

**Fix:** Add a `MAX_FRAGMENT_TOKENS` budget. Prioritize Tier 1 > Tier 2 > Tier 3, drop lowest-confidence Tier 3 picks first.

---

## 4. 🟡 Orphan utility model traces in Langfuse

**Severity:** Medium — cost attribution gap
**Component:** `backend/app/worker.py` (utility model calls)

4 traces named `litellm-acompletion` appear with no session ID, no tags, no metadata. These are utility model calls (summarization, context selection) that bypass the Langfuse metadata builder.

**Fix:** Pass `metadata=` with at least `session_id` and `trace_name` to utility `acompletion()` calls.

---

## 5. ✅ Tier 2 lifecycle fragments missing from Langfuse metadata (FIXED)

**Severity:** Medium — audit gap
**Component:** `backend/app/worker.py` (langfuse metadata builder)

Lifecycle fragments inject into the system prompt (visible as `Current Phase: IMPLEMENTING` in trace input) but weren't reflected in `fragment_names`, `fragment_count`, or `fragments_injected` metadata. The audit metadata only showed Tier 1 + manifest.

**Fix (applied):** When lifecycle phase changes and fragments are injected, Tier 2 metadata is now appended to `_audit_fragments` and `_langfuse_meta` is updated with new counts, names, tags, and a `lifecycle_phase` field. Old Tier 2 entries are replaced when the phase changes.

---

## 6. 🟢 Lifecycle detection is string-based (fragile)

**Severity:** Low — works today, maintenance risk
**Component:** `backend/app/agent/lifecycle.py` → `detect_phase()`

Phase detection matches serialized tool call strings (`"git commit" in lower`). This is fragile if the tool call serialization format changes.

**Fix:** Refactor to match on structured `(tool_name, tool_args)` tuples. Lower priority since the current approach has good test coverage.

---

## 7. 🟢 No Tier 3 fallback on low confidence

**Severity:** Low — degrades gracefully (injects nothing)
**Component:** `backend/app/agent/fragment_router.py`

When the best semantic similarity score is below `LOW_CONFIDENCE_THRESHOLD` (0.6), the router logs a warning but injects nothing. A general-purpose coding fallback could help.

**Fix:** Define a default Tier 3 fragment (e.g., `engineering/code-quality.md`) that injects when all scores are below threshold.

---

## 8. 🟢 Pydantic deprecation warnings in semantic-router

**Severity:** Low — not breaking, future risk
**Component:** `semantic_router/route.py` (upstream dependency)

28 warnings per test run: `.dict()` deprecated in favor of `.model_dump()`. Will break on Pydantic V3.

**Fix:** Pin `semantic-router` version or wait for upstream fix. Monitor.

---

## 9. 🟢 Vestigial `prompt_fragments` in test fixture

**Severity:** Trivial — no runtime impact
**Component:** `backend/tests/test_sandbox_manager.py` line 52

Still has `"prompt_fragments": []` from the old checkbox model. Inconsistent with Phase 1 removal.

**Fix:** Remove the field from the test fixture.

---

## 10. 🟡 `test_langfuse_audit.py` has 4 broken tests (stale imports)

**Severity:** Medium — test suite noise
**Component:** `tests/test_langfuse_audit.py`

4 tests import `_select_relevant_fragments` (from `context_pipeline`) and `load_universal_fragments_with_meta` (from `dynamic_loader`), both removed during Phase 1. These tests were written for the old checkbox model and need to be rewritten against the manifest-based system.

**Fix:** Rewrite tests to use `manifest.py` / `fragment_router.py` APIs, or delete and rely on the existing `test_lifecycle.py` + `test_fragment_router.py` suites (64 tests) which cover the same functionality.
