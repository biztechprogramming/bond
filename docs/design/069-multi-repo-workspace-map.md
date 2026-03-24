# Design Doc 069: Multi-Repo Workspace Map

**Status:** Draft  
**Date:** 2026-03-23  
**Depends on:** 038 (Utility Model Pre-Gathering)

---

## 1. Problem

Bond's pre-gather phase assumes `/workspace` is a single git repo. It checks for `/workspace/.git`, and when that doesn't exist, the entire plan phase is skipped:

```
Pre-gather: no repo map (not a git repo?), skipping plan phase
```

In practice, `/workspace` is a **workspace root** containing multiple git repos as subdirectories:

```
/workspace/
├── bond/          (.git ✓)
├── openclaw/      (.git ✓)
├── some-service/  (.git ✓)
└── docs/          (not a git repo)
```

This means pre-gathering — the plan phase, adaptive budgeting, file pre-reads, and coding agent delegation — is completely disabled for every turn. The agent enters every task blind.

## 2. Proposal

Replace the single-repo `.git` check with a two-phase approach:

1. **Workspace Overview** — a cheap, shallow directory listing (no tree-sitter) that shows the agent what repos exist and their top-level structure.
2. **Targeted Repo Map** — the agent's plan selects which repo(s) need the full aider-style tree-sitter map. Only those repos get the expensive treatment.

### Phase 0a: Workspace Overview (new)

Scan `/workspace` for git repos (1-2 levels deep). For each discovered repo, show 3 levels of directory contents. Non-git directories get a 1-level listing.

```
=== bond/ (git) ===
  backend/
    app/
      agent/        [14 items]
      core/         [6 items]
      worker.py
    tests/          [12 items]
  gateway/
    src/            [18 items]
  frontend/         [22 items]
  docs/             [8 items]

=== openclaw/        (git) ===
  src/
    server/         [5 items]
    client/         [3 items]
  package.json

=== docs/            (no git) ===
  guides/           [4 items]
  api-reference/    [7 items]
```

Cost: a few `os.listdir` calls. No git commands, no tree-sitter. Estimated ~500-1500 tokens for a typical workspace.

### Phase 0b: Targeted Repo Map (existing, re-targeted)

After the plan phase selects repos, run `generate_repo_map()` on each with `repo_root` pointed at the selected subdirectory. The existing aider-style repomap code is unchanged.

### Phase 1: Plan (modified)

The plan prompt gets the workspace overview instead of a full repomap. New field in the plan schema:

```json
{
  "complexity": "moderate",
  "approach": "...",
  "repos_to_map": ["bond"],
  "files_to_read": ["bond/backend/app/worker.py"],
  "grep_patterns": [{"pattern": "repo_root", "directory": "bond/backend/"}],
  "delegate_to_coding_agent": false,
  "estimated_iterations": 5
}
```

`repos_to_map` is a list of subdirectory names (relative to workspace root) that need the full tree-sitter map.

### Phase 1b: Deep Map (new, between plan and gather)

For each repo in `repos_to_map`:
1. Run `generate_repo_map(repo_root=/workspace/<repo>, ...)` 
2. Prepend the result to the plan context so gather can use it for targeted file reads

### Phase 2: Gather (mostly unchanged)

File paths in `files_to_read` and `grep_patterns` now include the repo prefix (e.g., `bond/backend/app/worker.py`). The gather phase resolves them relative to `/workspace`.

---

## 3. Issues & Considerations

### 3.1 Token Budget Allocation

Currently `generate_repo_map` gets a flat 10,000 token budget. With multiple repos:

- **Option A: Split budget** — divide 10K across selected repos (e.g., 2 repos → 5K each). Risk: insufficient detail for large repos.
- **Option B: Per-repo budget** — each repo gets 10K. Risk: plan context balloons if agent selects 3+ repos.
- **Option C: Primary + secondary budgets** — first repo gets 8K, additional repos get 3K each. Assumes one repo is the focus.

**Recommendation:** Option C. Most tasks involve one primary repo. The plan phase should also cap `repos_to_map` at 3 to bound total cost.

### 3.2 Two LLM Calls for Planning

The current flow is: repo_map → plan (1 LLM call) → gather.

The new flow is: workspace_overview → plan (LLM call 1) → deep repo_map → ??? → gather.

After the deep repomap is generated, the agent needs to select files from it. Two options:

- **Option A: Second plan call** — a lightweight LLM call that receives the deep repomap and outputs `files_to_read` and `grep_patterns`. This is an extra LLM round-trip (~300-500ms).
- **Option B: Combine plan fields** — the first plan call outputs `repos_to_map` AND `files_to_read` based on the shallow overview. The files list is less precise because the agent only saw 3 levels of directories, not full signatures. But it may be good enough — the agent can still read files during the main loop.
- **Option C: Merge into plan prompt** — run the deep repomap, then do a single plan call with both workspace overview + deep repomap. But this means we can't know which repos to map until we've already mapped them (circular).

**Recommendation:** Option A (second plan call) for quality, but consider Option B for latency-sensitive deployments. The second call is cheap — small prompt, small output, utility model.

### 3.3 File Path Resolution

Today, `files_to_read` paths are relative to the single repo root. With multiple repos, paths need a repo prefix. This affects:

- `gather_phase()` — resolves `files_to_read` to absolute paths via `os.path.join(repo_root, path)`
- `_run_grep()` — runs grep in a directory relative to `repo_root`
- Any downstream code that assumes a single `repo_root`

Either:
- **Prefix all paths** with repo name (e.g., `bond/backend/app/worker.py`) and resolve from `/workspace`
- **Pass repo_root per file** in the plan output

**Recommendation:** Prefix approach. Simpler, and the agent sees the full workspace-relative path in context.

### 3.4 Workspace Discovery Overhead

Finding git repos requires scanning subdirectories. Potential issues:

- **node_modules, .venv, etc.** — could contain `.git` dirs from dependencies. Must skip known vendor directories.
- **Nested git repos** — `bond/gateway/` could theoretically be a submodule with its own `.git`. Should we treat submodules as separate repos or part of the parent?
- **Symlinks** — could cause infinite loops or duplicates.

**Recommendation:**
- Skip: `node_modules`, `.venv`, `venv`, `__pycache__`, `.git`, `.cache`, `vendor`, `dist`, `build`
- Only scan 2 levels deep from workspace root for `.git` directories
- Don't follow symlinks
- Treat git submodules as part of the parent repo (they'll appear in the parent's `git ls-files`)

### 3.5 Cache Invalidation

The existing `RepoMapCache` keys on `(repo_root, file_list_hash, token_budget)`. This still works per-repo. The workspace overview is cheap enough to regenerate every turn (no cache needed).

However, workspace structure changes (new repo cloned, repo deleted) should be reflected immediately. Since the overview is uncached, this happens naturally.

### 3.6 Fallback Behavior

If workspace scanning finds zero git repos (weird but possible — maybe everything is in a monorepo without subdirectories, or workspace truly isn't set up), the system should fall back to the simple `build_repo_map` on `/workspace` itself, or skip gracefully as it does today.

If only one git repo is found, skip the two-phase dance — go directly to the aider-style repomap on that repo (effectively today's behavior, but correctly targeted).

### 3.7 Plan Prompt Size

The workspace overview adds tokens to the plan prompt. For a workspace with 5-8 repos, the overview might be 1000-2000 tokens. The plan prompt currently includes the full repomap (~10K tokens). The workspace overview is much smaller, so the first plan call is actually **cheaper** than today's single call.

The second plan call (with deep repomap) is comparable to today's cost. Net: roughly the same total tokens, but split across two calls with an extra round-trip.

### 3.8 Interaction with Parallel Pre-Gathering (Doc 068)

Doc 068 proposes parallelizing gather operations. The new deep-map phase between plan and gather is inherently sequential (plan must finish before we know which repos to map). However:

- Multiple repo maps can be generated **in parallel** with each other
- The deep-map + second-plan sequence could overlap with other non-dependent work if any exists

This is compatible with 068 but adds a sequential step that can't be parallelized away.

### 3.9 Single-Repo Shortcut

If the user's message explicitly mentions files with a clear repo prefix (e.g., "fix the bug in bond/backend/app/worker.py"), the workspace overview could auto-select that repo for deep mapping, skipping the first plan call entirely.

---

## 4. Proposed File Changes

| File | Change |
|---|---|
| `backend/app/agent/workspace_map.py` | **New.** Discover git repos, build shallow workspace overview. |
| `backend/app/agent/pre_gather_integration.py` | Use workspace map when `repo_root` has no `.git`. Orchestrate two-phase planning. |
| `backend/app/agent/pre_gather.py` | Update plan schema to include `repos_to_map`. Add second plan prompt for file selection after deep map. |
| `backend/app/worker.py` | No changes needed — already passes `repo_root`. |

---

## 5. Open Questions

1. **Budget split strategy** — should it be configurable or hardcoded? For now, hardcoded Option C seems right.
2. **Second LLM call** — is the latency cost (~300-500ms) worth the precision? Could prototype both options and A/B test.
3. **Max repos** — cap at 3? 5? Should the plan be allowed to say "I don't need any repo mapped" for simple conversational turns?
4. **Depth of workspace overview** — 3 levels feels right, but should it be configurable per-repo based on repo size?
