# Design Doc 056: Aider RepoMap Integration

## Status
Implemented (Option C)

## Problem

Design Doc 038 introduced a three-phase agent turn architecture (Plan → Gather → Act) with a Phase 0 repo map generated from `git ls-files`. That map is a **file tree** — it tells the agent what files exist and where, but nothing about what's *inside* them.

This means Phase 1 (Plan) is guessing which files to read based on filenames alone. If the user says "fix the bug in the database connection retry logic," the agent sees `backend/app/core/spacetimedb.py` in the tree and *hopes* the retry logic is in there. It can't see that:

- `spacetimedb.py` exports `class SpacetimeDBClient` with method `async def _retry_connect()`
- `worker.py` calls `SpacetimeDBClient.connect()` in `async def agent_turn()`
- `loop.py` has `class AgentLoop` which receives the client via constructor

Without structural awareness, the plan either over-reads (request every file that might be relevant) or under-reads (miss a key file and discover it mid-loop, burning iterations).

### Aider's Solution: Tree-Sitter Repo Map

[Aider](https://github.com/paul-gauthier/aider) solved this with a **repo map** that uses tree-sitter to extract structural metadata from every file:

- **Function/method signatures** — names, parameters, return types
- **Class definitions** — names, base classes, key methods
- **Module-level symbols** — constants, imports, type aliases
- **Call graph edges** — which functions reference which other symbols

The output is a compact text format (~8-15K tokens for a medium repo) that gives an LLM enough context to know *exactly* where to look without reading any file in full.

## Proposed Solution

Replace the `git ls-files` repo map in Doc 038 Phase 0 with Aider's tree-sitter repo map. Additionally, extract the repo map as a **standalone reusable module** that can serve both the pre-gathering phase and on-demand tool calls.

### Why Aider's Implementation Specifically

- **Battle-tested** — used in production by thousands of developers, refined over 2+ years
- **Token-efficient** — uses a PageRank-like algorithm to prioritize the most "important" symbols, fitting within a token budget
- **Language coverage** — tree-sitter grammars for Python, TypeScript, JavaScript, Go, Rust, Java, C#, Ruby, PHP, and more
- **Cacheable** — output depends only on file contents (hash-based cache invalidation)
- **Apache 2.0 licensed** — compatible with Bond's licensing

### What Changes from Doc 038

| Aspect | Doc 038 (current) | Doc 056 (proposed) |
|--------|-------------------|-------------------|
| Phase 0 output | File tree (names only) | Structural map (signatures + relationships) |
| Token cost | ~4K tokens | ~8-15K tokens (tunable via budget) |
| Plan quality | Guesses from filenames | Targets from actual symbols |
| Phase 2 accuracy | Over/under-reads | Precise file selection |
| Extra dependency | None (`git ls-files`) | tree-sitter + language grammars |
| Cache invalidation | N/A (regenerated each turn) | Content-hash per file |

The net effect: Phase 0 costs ~5-10K more tokens but Phase 2 reads fewer unnecessary files and Phase 3 needs fewer course-correction iterations. Net token savings estimated at 15-30% on top of Doc 038's improvements.

## Design

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent Turn Pipeline                       │
│                                                             │
│  Phase 0: RepoMap                                           │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  RepoMapGenerator                                    │   │
│  │  ├── git ls-files → file list                       │   │
│  │  ├── tree-sitter parse → AST per file               │   │
│  │  ├── tag extraction → symbols, definitions, refs    │   │
│  │  ├── PageRank scoring → importance ranking          │   │
│  │  └── token-budget rendering → compact text map      │   │
│  └─────────────────────────────────────────────────────┘   │
│           │                                                 │
│           ▼                                                 │
│  Phase 1: Plan (Opus sees structural map)                   │
│           │                                                 │
│           ▼                                                 │
│  Phase 2: Gather (informed by precise file/symbol refs)     │
│           │                                                 │
│           ▼                                                 │
│  Phase 3: Act (Opus with pre-gathered context)              │
└─────────────────────────────────────────────────────────────┘
```

### RepoMap Module

New module: `backend/app/agent/repomap/`

```
backend/app/agent/repomap/
├── __init__.py          # Public API: generate_repo_map()
├── generator.py         # Core logic: file scanning, tree-sitter parsing, ranking
├── tags.py              # Tag extraction from tree-sitter ASTs
├── ranking.py           # PageRank-based symbol importance scoring
├── cache.py             # Content-hash based caching
└── languages.py         # Language detection + grammar loading
```

#### Public API

```python
# backend/app/agent/repomap/__init__.py

async def generate_repo_map(
    repo_root: str,
    token_budget: int = 10_000,
    focus_files: list[str] | None = None,
    refresh: bool = False,
) -> str:
    """Generate a structural repo map using tree-sitter.
    
    Args:
        repo_root: Path to the git repository root.
        token_budget: Maximum tokens for the output. The generator will
            prioritize the most important symbols to fit within budget.
        focus_files: Optional list of file paths to prioritize in the map.
            These files get full symbol detail; others get compressed.
            Useful when the user's message references specific files.
        refresh: Force regeneration, bypassing cache.
    
    Returns:
        A compact text representation of the repo's structure including
        file paths, class/function signatures, and key relationships.
    """
```

#### Tag Extraction

Tree-sitter parses each file into an AST. We extract "tags" — named symbols with their kind and location:

```python
@dataclass
class Tag:
    name: str              # e.g., "agent_turn", "SpacetimeDBClient"
    kind: str              # "function", "class", "method", "constant"
    file_path: str         # relative to repo root
    line: int              # 1-indexed line number
    signature: str         # e.g., "async def agent_turn(self, message: str) -> dict"
    references: list[str]  # symbols this tag references (for call graph)
```

Aider uses `.scm` query files (tree-sitter queries) per language to extract these. We'll adapt their query files for the languages Bond commonly encounters:

**Priority 1 (Bond codebase):** Python, TypeScript, JavaScript
**Priority 2 (common integrations):** Go, Rust, Java, C#
**Priority 3 (everything else):** Fall back to file tree (Doc 038 style)

For languages without tree-sitter grammars, the map degrades gracefully to filename-only entries — same as Doc 038.

#### Importance Ranking

Not all symbols are equally useful. Aider uses a modified PageRank algorithm:

1. Build a graph: nodes = tags (symbols), edges = references between them
2. Run PageRank to score each symbol by "importance" (how many other symbols reference it)
3. Rank files by the sum of their symbols' PageRank scores
4. Render top-ranked files with full signatures; lower-ranked files with just names

This means the map naturally highlights the "hub" files — `worker.py`, `native.py`, `definitions.py` — that are referenced by many others, while leaf files (tests, one-off scripts) get minimal representation.

```python
def rank_symbols(tags: list[Tag]) -> dict[str, float]:
    """Score symbols by structural importance using PageRank.
    
    Returns a dict of {file_path: importance_score}.
    High-score files are "hubs" — many other files reference their symbols.
    """
    # Build adjacency: file A references symbol in file B → edge A→B
    graph = defaultdict(set)
    symbol_to_file = {}
    
    for tag in tags:
        symbol_to_file[tag.name] = tag.file_path
    
    for tag in tags:
        for ref in tag.references:
            if ref in symbol_to_file:
                target_file = symbol_to_file[ref]
                if target_file != tag.file_path:
                    graph[tag.file_path].add(target_file)
    
    # PageRank iteration
    scores = {f: 1.0 for f in graph}
    damping = 0.85
    
    for _ in range(20):  # converges quickly
        new_scores = {}
        for node in scores:
            rank = (1 - damping)
            for other in scores:
                if node in graph.get(other, set()):
                    rank += damping * scores[other] / len(graph[other])
            new_scores[node] = rank
        scores = new_scores
    
    return scores
```

#### Token-Budget Rendering

The renderer fits the map within the token budget by progressively compressing less-important files:

```python
def render_map(tags: list[Tag], scores: dict[str, float], budget: int) -> str:
    """Render the repo map within a token budget.
    
    Strategy:
    1. Sort files by importance score (descending)
    2. Render top files with full signatures
    3. Render mid-tier files with class/function names only (no params)
    4. Render low-tier files as just filenames
    5. Drop files that don't fit
    """
    files_ranked = sorted(scores.items(), key=lambda x: -x[1])
    
    sections = []
    tokens_used = 0
    
    for filepath, score in files_ranked:
        file_tags = [t for t in tags if t.file_path == filepath]
        
        # Try full detail first
        full = _render_file_full(filepath, file_tags)
        full_tokens = estimate_tokens(full)
        
        if tokens_used + full_tokens <= budget:
            sections.append(full)
            tokens_used += full_tokens
            continue
        
        # Try compressed (names only)
        compressed = _render_file_compressed(filepath, file_tags)
        comp_tokens = estimate_tokens(compressed)
        
        if tokens_used + comp_tokens <= budget:
            sections.append(compressed)
            tokens_used += comp_tokens
            continue
        
        # Try filename only
        name_only = filepath
        if tokens_used + 2 <= budget:  # ~2 tokens for a filepath
            sections.append(name_only)
            tokens_used += 2
        else:
            break  # budget exhausted
    
    return "\n\n".join(sections)
```

#### Example Output

For Bond's codebase with a 10K token budget:

```
backend/app/worker.py
│ async def agent_turn(state: WorkerState, user_message: str, ...) -> str
│ async def _cancellable_llm_call(interrupt_event, *, model, messages, tools, ...) -> dict
│ async def _handle_tool_calls(response, messages, state, ...) -> bool
│ class WorkerState
│   model: str
│   conversation_id: str
│   interrupt_event: asyncio.Event
│   mcp_client: MCPProxyClient | None

backend/app/agent/tools/native.py
│ async def handle_code_execute(args: dict, context) -> dict
│ async def handle_file_read(args: dict, context) -> dict
│ async def handle_file_write(args: dict, context) -> dict
│ async def handle_file_edit(args: dict, context) -> dict
│ async def handle_respond(args: dict, context) -> dict
│ async def handle_say(args: dict, context) -> dict
│ async def handle_search_memory(args: dict, context) -> dict

backend/app/agent/tools/native_registry.py
│ def build_native_registry() -> ToolRegistry

backend/app/agent/tools/definitions.py
│ TOOL_DEFINITIONS: list[dict]

backend/app/agent/tools/db_discover.py
│ async def handle_db_discover(args: dict, context) -> dict
│ def _cache_key(connection_string: str) -> str
│ def _redact_connection_string(conn: str) -> str
│ def _maybe_summarize(schema: dict, max_tokens: int) -> dict

backend/app/agent/context_pipeline.py
│ async def build_context(state, history, user_message) -> list[dict]
│ def inject_repo_map(messages: list, repo_map: str) -> list

gateway/src/server.ts
│ class GatewayServer
│   async start(port: number): Promise<void>
│   async handleCompletion(req: CompletionRequest): Promise<CompletionResponse>

backend/app/agent/tools/coding_agent.py
│ async def handle_coding_agent(args: dict, context) -> dict

backend/app/agent/loop.py
│ class AgentLoop
│   async def run(message: str) -> str

backend/app/agent/tools/shell_utils.py
backend/app/agent/tools/file_buffer.py
backend/app/agent/tools/web.py
backend/app/agent/tools/memory.py
backend/app/agent/tools/work_plan.py
...
```

Compare to Doc 038's file tree output — same file (`worker.py`) shows up as just:

```
backend/
 app/
  worker.py
```

The agent now knows `worker.py` contains `agent_turn()` and `_cancellable_llm_call()` *before reading a single file*.

### Caching Strategy

Tree-sitter parsing is fast (~200ms for Bond's codebase) but we still cache:

```python
class RepoMapCache:
    """Cache repo maps by content hash.
    
    Cache key: SHA-256 of sorted (filepath, file_content_hash) pairs.
    If any file changes, the cache invalidates.
    
    For incremental updates: re-parse only changed files, merge with
    cached tags for unchanged files.
    """
    
    CACHE_DIR = Path("data/repomap-cache")
    
    def _repo_hash(self, repo_root: str, files: list[str]) -> str:
        """Compute a hash representing the current state of all files."""
        hasher = hashlib.sha256()
        for filepath in sorted(files):
            full_path = Path(repo_root) / filepath
            try:
                stat = full_path.stat()
                # Use mtime + size as a fast proxy for content hash
                hasher.update(f"{filepath}:{stat.st_mtime}:{stat.st_size}".encode())
            except OSError:
                continue
        return hasher.hexdigest()
    
    def get(self, repo_root: str, files: list[str], budget: int) -> str | None:
        """Return cached map if repo state hasn't changed."""
        repo_hash = self._repo_hash(repo_root, files)
        cache_file = self.CACHE_DIR / f"{repo_hash}_{budget}.txt"
        if cache_file.exists():
            return cache_file.read_text()
        return None
    
    def set(self, repo_root: str, files: list[str], budget: int, content: str) -> None:
        """Cache the generated map."""
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        repo_hash = self._repo_hash(repo_root, files)
        cache_file = self.CACHE_DIR / f"{repo_hash}_{budget}.txt"
        cache_file.write_text(content)
        self._evict_old()
    
    def _evict_old(self, max_entries: int = 20) -> None:
        """Remove oldest cache entries if over limit."""
        entries = sorted(self.CACHE_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime)
        for entry in entries[:-max_entries]:
            entry.unlink()
```

### Focus Mode

When the user's message references specific files or symbols, the map should prioritize those:

```python
# In Phase 1, before generating the map:
focus_files = extract_file_references(user_message, history)

# Pass to generator:
repo_map = await generate_repo_map(
    repo_root=working_directory,
    token_budget=12_000,
    focus_files=focus_files,  # These get full detail regardless of PageRank
)
```

Focus files get their full signatures rendered first, then PageRank fills the remaining budget. This means if the user says "update the db_discover tool," the map shows `db_discover.py` in full detail even if it's a leaf node with low PageRank.

### Integration with Doc 038

The repo map replaces **only Phase 0** of Doc 038. Everything else stays:

```python
# worker.py — Phase 0 (CHANGED)
# Before:
# repo_map = await build_repo_map(working_directory)  # git ls-files tree
# After:
from backend.app.agent.repomap import generate_repo_map
repo_map = await generate_repo_map(
    repo_root=working_directory,
    token_budget=10_000,
    focus_files=_extract_focus_files(user_message),
)

# Phase 1: Plan (UNCHANGED — but now sees structural map)
plan = await _plan_phase(repo_map, user_message, history)

# Phase 2: Gather (UNCHANGED — but plan is more accurate)
context_bundle = await _gather_phase(plan)

# Phase 3: Act (UNCHANGED — but fewer course-correction iterations)
response = await _act_phase(context_bundle, messages)
```

### Optional: `repo_map` Tool

Expose the repo map as a tool the agent can call on-demand, for cases where it's working with a *different* repo than the one it started in (e.g., after cloning a dependency):

```json
{
  "type": "function",
  "function": {
    "name": "repo_map",
    "description": "Generate a structural map of a git repository showing file paths, class/function signatures, and key relationships. Uses tree-sitter for language-aware parsing. Useful when you need to understand an unfamiliar codebase before making changes.",
    "parameters": {
      "type": "object",
      "properties": {
        "repo_path": {
          "type": "string",
          "description": "Path to the git repository root."
        },
        "token_budget": {
          "type": "integer",
          "description": "Maximum tokens for the output. Higher budget = more detail.",
          "default": 10000
        },
        "focus_files": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Optional list of file paths to prioritize in the map. These get full signature detail."
        }
      },
      "required": ["repo_path"]
    }
  }
}
```

This is Phase 4 — not needed for the Doc 038 integration but valuable for coding agent workflows where the agent clones a repo and needs to understand it before working on it.

### Dependency: tree-sitter

Aider uses the `tree-sitter` Python package plus per-language grammar packages:

```
tree-sitter>=0.21.0
tree-sitter-python
tree-sitter-javascript
tree-sitter-typescript
tree-sitter-go
tree-sitter-rust
tree-sitter-java
tree-sitter-c-sharp
```

These are pure Python wheels (pre-compiled grammars) — no build toolchain needed. Add to `Dockerfile.agent`:

```bash
RUN pip install --no-cache-dir \
    tree-sitter \
    tree-sitter-python \
    tree-sitter-javascript \
    tree-sitter-typescript \
    tree-sitter-go \
    tree-sitter-rust \
    tree-sitter-java \
    tree-sitter-c-sharp
```

### Extraction from Aider

Aider's repo map code lives in:
- `aider/repomap.py` — main generator (~600 lines)
- `aider/queries/` — tree-sitter `.scm` query files per language

Options for extraction:

**Option A: Vendor the module.** Copy `repomap.py` + queries into `backend/app/agent/repomap/`, adapt to Bond's async patterns and caching. Aider is Apache 2.0, so this is license-compatible. ~2 days of adaptation work.

**Option B: Use as a library.** `pip install aider-chat` and import the `RepoMap` class directly. Downside: pulls in aider's full dependency tree (which is large — includes litellm, openai, etc., many of which Bond already has but at possibly different versions).

**Option C: Extract + simplify.** Aider's `RepoMap` has features Bond doesn't need (chat history awareness, edit tracking, auto-refresh on file changes). Extract just the core: tag extraction + PageRank + rendering. ~1.5 days, cleaner result.

**Recommendation: Option C.** We want the algorithm, not the framework integration. Extract the ~300 lines of core logic (tag extraction, ranking, rendering) and the query files. Skip the chat-awareness and edit-tracking features — Bond's Phase 1 plan handles that role.

## Implementation Plan

### Phase 1: Core Module (MVP)
- [ ] Create `backend/app/agent/repomap/` module structure
- [ ] Extract tree-sitter tag extraction from Aider (adapt `.scm` queries for Python, TypeScript, JavaScript)
- [ ] Implement PageRank scoring
- [ ] Implement token-budget renderer
- [ ] Add content-hash caching in `data/repomap-cache/`
- [ ] Add tree-sitter dependencies to `Dockerfile.agent`
- [ ] Test against Bond's own codebase — verify output quality and token count

### Phase 2: Replace Doc 038 Phase 0
- [ ] Replace `build_repo_map()` in `worker.py` with `generate_repo_map()`
- [ ] Update Phase 1 plan prompt to reference structural symbols (not just filenames)
- [ ] Add focus file extraction from user message
- [ ] A/B test: measure plan accuracy (files requested vs files actually used in Phase 3)
- [ ] Langfuse instrumentation for repo map generation time and token count

### Phase 3: Additional Languages
- [ ] Add tree-sitter grammars for Go, Rust, Java, C#
- [ ] Test against polyglot repos
- [ ] Graceful fallback to filename-only for unsupported languages

### Phase 4: On-Demand Tool
- [ ] Add `repo_map` tool definition to `definitions.py`
- [ ] Implement `handle_repo_map` handler
- [ ] Register in `native_registry.py`
- [ ] Useful for coding agent workflows that clone external repos

## Security Considerations

- **No new attack surface.** Tree-sitter parses files locally — no network calls, no external services.
- **Cache in `data/repomap-cache/`** — follows Bond's data lifecycle, included in backups.
- **Memory safety.** Tree-sitter's Python bindings are compiled C — same safety profile as any native extension. Well-audited, used by GitHub, Neovim, etc.

## Success Criteria

- [ ] Repo map generation completes in <500ms for Bond's codebase (~800 files)
- [ ] Output fits within 10K token budget while showing signatures for top ~50 files
- [ ] Phase 1 plan accuracy improves — measured by % of Phase 3 file reads that were predicted in the plan
- [ ] Overall turn cost decreases vs Doc 038 baseline (fewer wasted reads in Phase 2/3)
- [ ] Graceful degradation for unsupported languages (falls back to filename listing)

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| Keep `git ls-files` tree (Doc 038) | Filenames alone aren't enough for accurate planning — leads to over/under-reading |
| ctags/etags | No call graph, no relationship data, less accurate than tree-sitter |
| Language server protocol (LSP) | Requires running a language server per language — heavy, slow startup |
| Full-file embeddings + vector search | Expensive to compute, non-deterministic, overkill for structural discovery |
| `aider-chat` as pip dependency | Pulls in large transitive dependency tree, version conflicts likely |

## References

- [Aider RepoMap source](https://github.com/paul-gauthier/aider/blob/main/aider/repomap.py)
- [Aider tree-sitter queries](https://github.com/paul-gauthier/aider/tree/main/aider/queries)
- [Aider blog: Building a better repo map](https://aider.chat/docs/repomap.html)
- [Design Doc 038: Utility Model Pre-Gathering](038-utility-model-pre-gathering.md)
- [tree-sitter Python bindings](https://github.com/tree-sitter/py-tree-sitter)
