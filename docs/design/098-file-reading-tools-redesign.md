# Design Doc 098: File Reading Tools Redesign

**Status:** Proposal
**Author:** Bond AI
**Date:** 2026-04-03
**Last Updated:** 2026-04-03

---

## 1. Problem Statement

Bond's file and shell tools have three compounding problems that degrade agent performance, cause premature turn termination, and waste context window tokens.

### 1.1 The Loop Detector Bug (Critical — ROOT CAUSE of agent blocking)

Bond's agent loop detector in `backend/app/agent/iteration_handlers.py:323` (`detect_loop()`) has three detection mechanisms. **Mechanism #2 is a bug:**

1. **Consecutive repetition** (`REPETITION_THRESHOLD = 2`) — Same tool + same args called 2x in a row. This is correct behavior. Uses MD5 hash of `tool_name:json.dumps(tool_args)[:200]`.

2. **Name-only repetition** (`NAME_ONLY_THRESHOLD = 3`) — Same tool name called 3x with **different** args. **THIS IS THE BUG.** It treats `file_read("foo.py")`, `file_read("bar.py")`, `file_read("baz.py")` as a loop. The threshold is only 3, meaning an agent cannot read more than 2 different files in sequence without triggering intervention.

3. **Cyclical detection** (`CYCLE_MIN_PERIOD=2`, `CYCLE_MAX_PERIOD=8`, `CYCLE_REPEATS=2`) — Detects patterns like `file_read -> grep -> file_read -> grep`. This is reasonable but overly aggressive with the current parameters.

Thresholds are defined in `backend/app/agent/loop_state.py` (lines 23-34). The "Skipped — agent loop intervention" error message comes from `backend/app/worker.py:1593`, where orphaned tool calls (ones that weren't executed because the loop was detected) get filled with error responses.

### 1.2 Docker Exec Overhead in Host Mode

In `backend/app/agent/tools/files.py:78-83`, every sandbox-mode file read spawns a new process inside the container:

```python
proc = await asyncio.create_subprocess_exec(
    "docker", "exec", container_id, "cat", path_str,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
```

Problems:
- Each `docker exec` spawns a new process inside the container (~50-100ms overhead)
- No connection reuse or multiplexing
- 100KB truncation with no line-range support in sandbox mode (`files.py:89-90`)
- No outline mode in sandbox mode (only host mode has it, `files.py:115-122`)
- No mtime tracking or dedup in sandbox mode
- `file_edit` in sandbox mode does: read via `docker exec cat` then write via `docker exec tee` — TWO docker exec round-trips per edit
- `shell_utils.py`'s `_run_cmd()` spawns `asyncio.create_subprocess_exec` for every shell tool call — each `shell_grep`, `shell_ls`, `shell_head`, `shell_tail`, `shell_sed`, `shell_find` is a separate subprocess spawn through docker exec

### 1.3 Missing Features vs. Claude Code

Bond lacks mtime-based dedup, two-phase token budgets, image/PDF/Jupyter support, skill discovery on read, and conversation-level result compression. See Section 4 for full analysis.

### 1.4 Tool Fragmentation

Bond exposes 7 separate tools for reading file content: `file_read`, `shell_head`, `shell_tail`, `batch_head`, `shell_sed`, `shell_grep`, `file_smart_edit`. This wastes ~1,200 prompt tokens on tool definitions and forces the LLM to choose between overlapping tools.

---

## 2. Architecture Context

Bond has **two execution modes** for file tools. Both must be optimized.

### 2.1 Host Mode (Primary Agent Path)

The agent loop runs inside the Bond backend (Python/FastAPI on the host). File operations reach into sandbox containers via `docker exec container_id <command>`.

- **Implementation:** `backend/app/agent/tools/files.py`
- **How it works:** `handle_file_read()` calls `_get_sandbox_container()` to get a container ID, then runs `docker exec container_id cat /path` to read files
- **Used by:** The primary agent loop for all conversations with sandbox-enabled projects
- **Overhead:** ~50-100ms per `docker exec` call, no connection reuse

### 2.2 Native Mode (Coding Sub-Agents)

When a coding agent is spawned (via `coding_agent` tool), it runs **inside** the container. File operations use direct `open()` calls with no docker exec overhead.

- **Implementation:** `backend/app/agent/tools/native.py`
- **How it works:** `_resolve_path()` resolves relative paths against fallback roots (`/bond`, `/workspace`), then reads with `Path.read_text()`
- **Used by:** Coding sub-agents spawned inside containers
- **Overhead:** Minimal — direct filesystem access
- **Limitations:** 10KB max read (`_MAX_READ_BYTES = 10_000` at `native.py:27`), no outline mode, no line-range support beyond what the host-side handler provides

### 2.3 Key Difference

The previous version of this doc incorrectly stated that docker exec concerns could be removed. **Docker exec IS needed for the primary agent path** — only coding sub-agents run inside the container. Optimizing docker exec overhead is critical because the primary agent path handles the majority of file operations.

---

## 3. Current Implementation Analysis

### 3.1 Loop Detector (`iteration_handlers.py:323`)

```python
def detect_loop(tool_name, tool_args, loop_state):
    args_sig = hashlib.md5(f"{tool_name}:{json.dumps(tool_args)[:200]}".encode()).hexdigest()[:8]
    loop_state.recent_tool_calls.append((tool_name, args_sig))
    loop_state.recent_tool_names.append(tool_name)

    # 1. Consecutive repetition (exact args) — CORRECT
    if len(loop_state.recent_tool_calls) >= REPETITION_THRESHOLD:  # 2
        last_n = loop_state.recent_tool_calls[-REPETITION_THRESHOLD:]
        if all(tc == last_n[0] for tc in last_n):
            return True, "..."

    # 2. Name-only repetition — THE BUG
    if len(loop_state.recent_tool_names) >= NAME_ONLY_THRESHOLD:  # 3
        last_n_names = loop_state.recent_tool_names[-NAME_ONLY_THRESHOLD:]
        if all(n == last_n_names[0] for n in last_n_names):
            return True, "..."  # Fires after 3 file_reads with DIFFERENT paths!

    # 3. Cyclical detection — overly aggressive
    # ...
```

The name-only detector was designed to catch "wrapper tool" abuse — e.g., an agent that calls `code_execute` with `cat` to bypass `file_read` limits. But the threshold of 3 is far too low. Reading 3 different files in sequence — the most basic code review pattern — triggers it.

### 3.2 Sandbox File Read (`files.py:67-92`)

The sandbox code path is feature-poor compared to host mode:

| Feature | Host Mode (`files.py:95+`) | Sandbox Mode (`files.py:74-93`) | Native Mode (`native.py`) |
|---------|---------------------------|--------------------------------|--------------------------|
| Line ranges | Yes | No | Yes |
| Outline mode | Yes | No | No |
| Auto-buffer large files | Yes (500 lines) | No (100KB truncation) | No (10KB hard limit) |
| mtime dedup | No | No | No |
| File tracking | Yes (`track_file_read`) | No | Yes (`track_file_read`) |

### 3.3 File Edit Round-Trips

In sandbox mode, `file_edit` requires two docker exec calls:
1. Read: `docker exec container_id cat /path` to get current content
2. Write: `docker exec container_id tee /path` to write modified content

Each round-trip is ~50-100ms. A single edit operation costs 100-200ms in docker exec overhead alone.

### 3.4 Shell Utils Overhead (`shell_utils.py`)

`_run_cmd()` spawns `asyncio.create_subprocess_exec` for every shell tool call. In sandbox mode, these go through docker exec. Each of `shell_grep`, `shell_ls`, `shell_head`, `shell_tail`, `shell_sed`, `shell_find` is a separate subprocess spawn.

---

## 4. What Claude Code Does Better

### 4.1 mtime-Based Dedup

Tracks file mtime and returns `FILE_UNCHANGED_STUB` (~100 bytes) if the file hasn't changed since last read. Bond's `track_file_read()` in `file_buffer.py` records reads but **never checks them for dedup**.

### 4.2 Two-Phase Token Limits

| Limit | Default | Check | Cost | On Overflow |
|-------|---------|-------|------|-------------|
| `maxSizeBytes` | 256 KB | Total file size | 1 `stat()` | Throws pre-read |
| `maxTokens` | 25,000 | Actual output tokens | Token counter | Throws post-read |

Bond has hardcoded 500-line / 100KB thresholds with no token counting and no byte-size pre-check.

### 4.3 Dual Code Path Reader

- **Fast path:** Small files or reading from start — reads entire file, splits by lines, slices
- **Streaming path:** Large files with offset — streams line-by-line, skips to start, reads until end

Bond always reads the entire file into memory then slices (`files.py:110-111`).

### 4.4 Image/PDF/Jupyter Support

Claude Code processes images (resize/compress via Sharp, return as base64), extracts PDF text (with page-range support, max 20 pages), and renders Jupyter notebooks with cell outputs. Bond has none of these — reading an image or PDF returns raw bytes or an error.

### 4.5 Skill Discovery on Read

`activateConditionalSkillsForPaths()` auto-activates coding context based on file type and path. Bond's `load_context` is manual.

### 4.6 Conversation-Level Result Compression

`collapseReadSearch.ts` (762+ lines) collapses consecutive Read and Search tool results in conversation history into compact summaries when the conversation grows long. Instead of showing 10 separate read results, it shows a summary like "Read 10 files" with content available on expansion. Bond has no equivalent — old file reads consume context window space indefinitely.

---

## 5. Proposed Solution

### Phase 0 — Fix Loop Detector (P0, Day 1)

**Location:** `backend/app/agent/iteration_handlers.py:350-362`, `backend/app/agent/loop_state.py:27-28`

**Change 1:** Make name-only detection smarter. For info-gathering tools (`file_read`, `shell_grep`, `shell_ls`, etc.), the name-only detector should be disabled or have a much higher threshold. These tools are expected to be called many times with different arguments.

```python
# In loop_state.py — add exemption set and raise threshold
NAME_ONLY_THRESHOLD: int = 8  # Raised from 3
NAME_ONLY_EXEMPT_TOOLS: frozenset[str] = field(default_factory=lambda: frozenset({
    "file_read", "shell_grep", "shell_ls", "shell_find", "shell_head",
    "shell_tail", "shell_wc", "shell_tree", "git_info", "project_search",
}))
```

```python
# In detect_loop() — skip name-only check for exempt tools
# 2. Name-only repetition (same tool, different args)
if tool_name not in loop_state.NAME_ONLY_EXEMPT_TOOLS:
    if len(loop_state.recent_tool_names) >= loop_state.NAME_ONLY_THRESHOLD:
        last_n_names = loop_state.recent_tool_names[-loop_state.NAME_ONLY_THRESHOLD:]
        if all(n == last_n_names[0] for n in last_n_names):
            return True, "..."
```

**Change 2:** Ensure the consecutive repetition detector (mechanism #1) still catches true loops — reading the same file with the same args 2x is still flagged. This already works correctly.

**Change 3:** Raise cyclical detection thresholds slightly — `CYCLE_REPEATS: 3` (from 2) to reduce false positives in legitimate exploration patterns.

**Validation:** Add tests that verify:
- Reading 10 different files does NOT trigger loop detection
- Reading the same file 2x DOES trigger loop detection
- `file_read -> grep -> file_read -> grep -> file_read -> grep` (3 repeats) DOES trigger cyclical detection
- `file_read -> grep -> file_read -> shell_ls` does NOT trigger cyclical detection

### Phase 1 — Multi-File `file_read` Parameter (P0, Day 1)

**Location:** `backend/app/agent/tools/files.py`, `backend/app/agent/tools/native.py`

Add a `paths` parameter to `file_read` that accepts an array of up to 10 file paths:

```python
{
    "name": "file_read",
    "parameters": {
        "properties": {
            "path": {"type": "string", "description": "Single file path"},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 10,
                "description": "Read multiple files in one call (max 10)"
            },
            "line_start": {"type": "integer"},
            "line_end": {"type": "integer"},
            "outline": {"type": "boolean"}
        }
    }
}
```

When `paths` is provided, return results keyed by path:

```json
{
    "results": {
        "src/foo.py": {"content": "...", "total_lines": 42},
        "src/bar.py": {"content": "...", "total_lines": 18},
        "src/baz.py": {"error": "File not found"}
    }
}
```

In sandbox mode, execute all docker exec calls concurrently via `asyncio.gather()`:

```python
async def _multi_read_sandbox(paths: list[str], container_id: str) -> dict:
    async def read_one(p):
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "cat", p,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            content = stdout.decode("utf-8", errors="replace")
            if len(content) > 100_000:
                content = content[:100_000] + "\n... [truncated at 100KB]"
            return p, {"content": content, "size": len(stdout)}
        return p, {"error": stderr.decode("utf-8", errors="replace").strip()}

    results = await asyncio.gather(*[read_one(p) for p in paths])
    return {"results": dict(results)}
```

**Constraint:** `line_start`/`line_end`/`outline` apply uniformly to all files in multi-file mode, or are ignored in favor of auto-buffering.

### Phase 2 — Persistent Helper Process in Container (P1, Day 2-3)

**The key optimization for docker exec overhead.** Instead of spawning a new `docker exec` for every tool call, start a persistent helper process inside the container that accepts commands over stdin/stdout.

#### Architecture

```
Bond Backend (host)
    │
    ├── docker exec container_id /usr/local/bin/bond-helper  (one-time startup)
    │       │
    │       └── Persistent process: reads JSON-RPC commands from stdin
    │                                writes JSON-RPC responses to stdout
    │
    ├── file_read("foo.py")  →  write {"method":"read","params":{"path":"foo.py"}} to stdin
    │                         ←  read {"result":{"content":"..."}} from stdout
    │
    ├── file_edit(...)        →  write {"method":"edit",...} to stdin
    │                         ←  read {"result":{"ok":true}} from stdout
    │
    └── shell_grep(...)       →  write {"method":"exec","params":{"cmd":["grep",...]}} to stdin
                              ←  read {"result":{"stdout":"..."}} from stdout
```

#### Protocol

```python
# bond-helper protocol (JSON-RPC over stdio, newline-delimited)
{"jsonrpc":"2.0","id":1,"method":"read","params":{"path":"/workspace/foo.py"}}
{"jsonrpc":"2.0","id":1,"result":{"content":"...","size":1234,"mtime":1711929600.0}}

{"jsonrpc":"2.0","id":2,"method":"read_multi","params":{"paths":["/workspace/a.py","/workspace/b.py"]}}
{"jsonrpc":"2.0","id":2,"result":{"files":{"/workspace/a.py":{"content":"..."},"/workspace/b.py":{"content":"..."}}}}

{"jsonrpc":"2.0","id":3,"method":"exec","params":{"cmd":["grep","-rn","pattern","/workspace"],"timeout":10}}
{"jsonrpc":"2.0","id":3,"result":{"stdout":"...","stderr":"","returncode":0}}

{"jsonrpc":"2.0","id":4,"method":"stat","params":{"path":"/workspace/foo.py"}}
{"jsonrpc":"2.0","id":4,"result":{"size":1234,"mtime":1711929600.0,"exists":true}}
```

#### Implementation

The helper binary (`bond-helper`) is a lightweight Python or Go process bundled into the container image. It:
- Reads newline-delimited JSON from stdin
- Executes the requested operation
- Writes the JSON result to stdout
- Supports concurrent requests via request IDs

Host-side adapter in `backend/app/sandbox/`:

```python
class ContainerHelper:
    """Persistent connection to a bond-helper process inside a container."""

    def __init__(self, container_id: str):
        self.container_id = container_id
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}

    async def start(self):
        self._proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", self.container_id,
            "/usr/local/bin/bond-helper",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._read_loop())

    async def call(self, method: str, params: dict) -> dict:
        self._next_id += 1
        req_id = self._next_id
        future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        self._proc.stdin.write((msg + "\n").encode())
        await self._proc.stdin.drain()
        return await asyncio.wait_for(future, timeout=30)

    async def _read_loop(self):
        async for line in self._proc.stdout:
            resp = json.loads(line)
            future = self._pending.pop(resp["id"], None)
            if future:
                future.set_result(resp.get("result", resp.get("error")))
```

**Performance impact:** Amortizes docker exec overhead to a single startup cost (~100ms once). All subsequent tool calls are stdin/stdout writes with ~1ms overhead. For a session with 50 file operations, this saves ~2.5-5 seconds of cumulative latency.

#### Batch Tool Execution

When the LLM returns multiple tool calls in one response (which it frequently does), execute them concurrently through the helper:

```python
async def batch_execute(self, calls: list[tuple[str, dict]]) -> list[dict]:
    """Execute multiple tool calls concurrently through the helper."""
    return await asyncio.gather(*[self.call(method, params) for method, params in calls])
```

### Phase 3 — mtime Dedup + Token Budgets (P1, Day 2-3)

#### 3a. mtime-Based Dedup

Port Claude Code's dedup pattern. The helper process (Phase 2) returns `mtime` with every read, making this zero-cost in sandbox mode:

```python
@dataclass
class ReadRecord:
    mtime: float
    line_start: int | None
    line_end: int | None
    token_count: int

# Scoped per conversation session
_read_state: dict[str, ReadRecord] = {}

FILE_UNCHANGED_STUB = (
    "File unchanged since last read. The content from the earlier Read "
    "tool_result in this conversation is still current — refer to that "
    "instead of re-reading."
)

async def handle_file_read(arguments, context):
    path = arguments["path"]

    # Get mtime (via helper.stat() in sandbox mode, os.stat() in host mode)
    current_mtime = await get_mtime(path, context)

    prior = _read_state.get(path)
    if prior and prior.mtime == current_mtime:
        same_range = (
            prior.line_start == arguments.get("line_start") and
            prior.line_end == arguments.get("line_end")
        )
        if same_range:
            return {
                "content": FILE_UNCHANGED_STUB,
                "deduplicated": True,
                "saved_tokens": prior.token_count,
            }

    # Normal read...
    result = await do_read(path, arguments, context)

    _read_state[path] = ReadRecord(
        mtime=current_mtime,
        line_start=arguments.get("line_start"),
        line_end=arguments.get("line_end"),
        token_count=estimate_tokens(result["content"]),
    )
    return result
```

**Important:** Dedup state is scoped to the conversation/session. A new conversation re-reads everything.

#### 3b. Two-Phase Token Budgets

```python
MAX_FILE_SIZE_BYTES = 256 * 1024   # Pre-read gate (cheap stat)
MAX_OUTPUT_TOKENS = 25_000          # Post-read gate (token count)

async def handle_file_read(arguments, context):
    path = arguments["path"]

    # Phase 1: Pre-read byte check
    file_size = await get_file_size(path, context)
    if file_size > MAX_FILE_SIZE_BYTES:
        return {
            "error": f"File is {file_size:,} bytes ({file_size // 1024} KB), "
                     f"exceeding the {MAX_FILE_SIZE_BYTES // 1024} KB limit. "
                     f"Use line_start/line_end to read a section, or outline=true."
        }

    # Phase 2: Post-read token check
    content = await read_file(path, context)
    token_count = estimate_tokens(content)
    if token_count > MAX_OUTPUT_TOKENS:
        return {
            "error": f"File is ~{token_count:,} tokens, exceeding the "
                     f"{MAX_OUTPUT_TOKENS:,} limit. Use line_start/line_end."
        }

    # ... proceed
```

Configurable via environment:
- `BOND_FILE_READ_MAX_BYTES` (default 256KB)
- `BOND_FILE_READ_MAX_TOKENS` (default 25,000)

### Phase 4 — Volume Mount Direct Reads (P2, Week 2)

**For file reads specifically,** if the workspace is already volume-mounted into the container, the host can read files directly from the mount point instead of going through docker exec. This is zero-overhead.

The host already has the mount path from the container configuration:

```python
async def handle_file_read(arguments, context):
    path = arguments["path"]
    container_id = await _get_sandbox_container(context)

    if container_id:
        # Check if we can read via volume mount (zero overhead)
        mount_path = _resolve_mount_path(path, context.get("workspace_mounts", []))
        if mount_path and mount_path.exists():
            # Direct host read — no docker exec needed
            content = mount_path.read_text(encoding="utf-8", errors="replace")
            mtime = mount_path.stat().st_mtime
            return {"content": content, "path": path, "mtime": mtime}

        # Fall back to helper process
        return await helper.call("read", {"path": path})
```

```python
def _resolve_mount_path(container_path: str, mounts: list[dict]) -> Path | None:
    """Map a container path to the host mount point, if mounted."""
    for mount in mounts:
        # mount = {"host": "/home/user/project", "container": "/workspace"}
        container_prefix = mount["container"]
        if container_path.startswith(container_prefix):
            relative = container_path[len(container_prefix):].lstrip("/")
            host_path = Path(mount["host"]) / relative
            return host_path
    return None
```

**Scope:** Only for read operations. Writes must still go through the helper/docker exec to ensure container filesystem consistency (e.g., inotify watchers, file locks).

#### Shared tmpfs for Metadata

Use a shared tmpfs mount between host and container for file metadata (mtime cache, file listings). The host can stat files through the mount for dedup checks without any docker exec:

```python
# Container creation adds a shared tmpfs
# docker run ... --mount type=tmpfs,destination=/tmp/bond-meta ...

# Helper writes mtime data to /tmp/bond-meta/mtimes.json on file changes
# Host reads /tmp/bond-meta/mtimes.json for dedup checks — no docker exec needed
```

### Phase 5 — Tool Consolidation: 7 Tools -> 3 (P2, Week 2)

| Keep | Absorbs | Rationale |
|------|---------|-----------|
| `file_read` | `shell_head`, `shell_tail`, `batch_head`, `shell_sed` | All are "read lines from file(s)" with different syntax. `file_read` already supports `line_start`/`line_end` and `paths` (Phase 1). Add `from_end` for tail behavior. |
| `file_search` (rename `shell_grep`) | — | Searching file contents is a distinct operation. Rename to clarify it's not a shell command. |
| `file_smart_edit` | — | Edit functionality is distinct. Keep as-is. |

**Migration path:**
1. Add `from_end: bool` parameter to `file_read` (absorbs `shell_tail` and `shell_head` with `from_end=true`)
2. `paths: list[str]` already added in Phase 1 (absorbs `batch_head`)
3. Line range support already exists (absorbs `shell_sed` for extraction)
4. Rename `shell_grep` -> `file_search` in tool definitions
5. Remove deprecated tool definitions but keep handlers as aliases for one release cycle

**Prompt token savings:** Removing 4 tool definitions saves ~800 tokens per conversation from the system prompt.

### Phase 6 — Result Compression for Long Conversations (P3, Week 3)

Port the concept from Claude Code's `collapseReadSearch.ts` (762+ lines):

When the conversation history grows long, compress older file read results:

```python
def compress_old_read_results(
    messages: list[dict],
    current_turn: int,
    max_age_turns: int = 10,
) -> list[dict]:
    """Replace old file_read results with compact summaries.

    Only compresses results older than max_age_turns to avoid
    removing content the model may still be referencing.
    """
    for msg in messages:
        if (
            msg.get("role") == "tool"
            and msg.get("tool_name") in ("file_read", "file_search")
            and msg.get("turn", current_turn) < current_turn - max_age_turns
        ):
            try:
                result = json.loads(msg["content"])
                path = result.get("path", "unknown")
                lines = result.get("total_lines", result.get("content", "").count("\n"))
                original_tokens = estimate_tokens(msg["content"])
                msg["content"] = json.dumps({
                    "compressed": True,
                    "path": path,
                    "total_lines": lines,
                    "hint": "File was read earlier in conversation. Re-read if needed.",
                })
                msg["_saved_tokens"] = original_tokens - estimate_tokens(msg["content"])
            except (json.JSONDecodeError, KeyError):
                pass

    return messages
```

This prevents the context window from filling up with stale file contents read 20+ turns ago. Combined with mtime dedup (Phase 3), re-reading a compressed file will return fresh content if changed or the dedup stub if unchanged.

---

## 6. Migration Plan

| Phase | Impact | Effort | Priority | Depends On |
|-------|--------|--------|----------|------------|
| 0. Fix loop detector | Critical — unblocks everything | 0.5 day | P0, Day 1 | — |
| 1. Multi-file read | High — reduces tool calls 5-10x | 0.5 day | P0, Day 1 | — |
| 2. Persistent helper process | High — eliminates docker exec overhead | 2 days | P1, Day 2-3 | — |
| 3. mtime dedup + token budgets | High — saves ~18% context, prevents blowouts | 1 day | P1, Day 2-3 | Phase 2 (for mtime in sandbox) |
| 4. Volume mount direct reads | Medium — zero-overhead reads for mounted paths | 1 day | P2, Week 2 | — |
| 5. Tool consolidation | Medium — cleaner UX, saves prompt tokens | 1 day | P2, Week 2 | Phase 1 |
| 6. Result compression | Medium — helps long conversations | 2 days | P3, Week 3 | Phase 3 |

**Total estimated effort:** ~8 engineering days

**Rollout strategy:**
- Phase 0+1 can be deployed immediately with no breaking changes
- Phase 2 requires bundling `bond-helper` into container images — coordinate with Dockerfile updates
- Phase 5 tool consolidation uses aliases for backward compatibility during one release cycle
- All phases are independently deployable — later phases enhance but don't require earlier ones (except Phase 3 depending on Phase 2 for sandbox mtime)

---

## 7. Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Files readable per turn before intervention | 2 (name-only threshold 3, fires after 3rd) | Unlimited (unique files) |
| Redundant file reads per conversation | ~18% | <2% |
| Docker exec overhead per file operation | ~50-100ms | ~1ms (via helper) |
| Tool definitions for file operations | 7 (~1,200 tokens) | 3 (~400 tokens) |
| Agent turns terminated by false-positive loop detection | ~15% of multi-file tasks | <1% |
| File edit round-trip time (sandbox) | ~100-200ms (2x docker exec) | ~2ms (via helper) |
| Context tokens wasted on stale reads (20+ turn conversations) | Unbounded | Compressed after 10 turns |

---

## 8. Appendix: Session Transcript Evidence

During the code review session that motivated this doc, the following sequence occurred:

1. **Turn 1:** Agent called `file_read` on 4 files (adapters.py, remote_adapter.py, host_registry.py, tunnel_manager.py). Only the first file was returned; the other 3 were skipped with `"error": "Skipped — agent loop intervention"`.

2. **Turn 2:** Agent switched to `code_execute` with `cat` as a workaround. Read 2 files successfully via `cat`, then the 3rd was skipped with `"error": "Skipped — agent loop intervention"` — the detector carried over state from the previous tool type.

3. **Turn 3:** Agent switched to `shell_grep` as a third workaround. Read 2 files' grep results successfully, then the 3rd was skipped. System issued `HARD STOP` and terminated the agent's turn entirely.

**Result:** Agent needed to read 7 files for a code review. Successfully read 1 file fully, got partial grep results from 2 others, and never saw 4 files at all. The code review was incomplete, and the agent had to ask the user to start a new conversation to work around the rate limit.

**Root cause:** The loop detector's name-only mechanism (`NAME_ONLY_THRESHOLD = 3` in `loop_state.py:27`) treated "read file A, read file B, read file C" the same as "read file A, read file A, read file A." The detection at `iteration_handlers.py:350-362` only checks `tool_name`, not `tool_args`. This is a fundamentally broken heuristic that punishes breadth-first exploration — exactly what code review, refactoring, and multi-file debugging require.

### Trace of the Detection Logic

For the Turn 1 failure:

```
Call 1: file_read("adapters.py")      → recent_tool_names = ["file_read"]          → OK
Call 2: file_read("remote_adapter.py") → recent_tool_names = ["file_read", "file_read"] → OK
Call 3: file_read("host_registry.py")  → recent_tool_names = ["file_read", "file_read", "file_read"]
                                       → last 3 names all == "file_read"
                                       → NAME_ONLY_THRESHOLD (3) hit → LOOP DETECTED
Call 4: file_read("tunnel_manager.py") → orphaned, filled with "Skipped — agent loop intervention"
```

The MD5 hashes of the args are different for each call, so mechanism #1 (consecutive repetition) correctly doesn't fire. But mechanism #2 ignores args entirely and fires on the 3rd call.
