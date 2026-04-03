# Design Doc 098: File Reading Tools Redesign

**Status:** Proposal
**Author:** Bond AI
**Date:** 2026-04-03

---

## 1. Problem Statement

Bond's file reading tools have three systemic issues that degrade agent performance, cause premature turn termination, and waste context window tokens.

### 1.1 The Loop Detector Bug (Critical — P0)

Bond's agent loop detector (`backend/app/agent/iteration_handlers.py:323`, function `detect_loop()`) has a **name-only repetition** check that fires when the same tool is called 3 times with *different* arguments. This means reading 3 different files — `file_read("foo.py")`, `file_read("bar.py")`, `file_read("baz.py")` — triggers the same intervention as retrying the same file 3 times in an infinite loop.

**Thresholds** (from `backend/app/agent/loop_state.py`):

| Detector | Threshold | What It Checks | Correct? |
|----------|-----------|----------------|----------|
| Consecutive repetition | `REPETITION_THRESHOLD = 2` | Same tool + same args (MD5 hash) | ✅ Yes |
| Name-only repetition | `NAME_ONLY_THRESHOLD = 3` | Same tool name, **ignores args** | ❌ **Bug** |
| Cyclical detection | `CYCLE_REPEATS = 2`, period 2-8 | Pattern like A→B→A→B | ✅ Yes |

When the name-only detector fires, it injects a SYSTEM message telling the agent to stop. If the agent continues (because it has legitimate work to do), the system escalates to `HARD STOP` and terminates the turn entirely. Orphaned tool calls get filled with `{"error": "Skipped — agent loop intervention"}` (worker.py:1593).

**Impact:** Any task requiring breadth-first exploration (code review, multi-file refactoring, project discovery) is crippled. The agent is limited to ~2-3 file reads per turn before being terminated.

### 1.2 Docker Exec Overhead (High — P1)

Bond has two execution modes:

| Mode | Where Agent Loop Runs | How Files Are Read | Tool Handlers |
|------|----------------------|-------------------|---------------|
| **Host mode** (primary) | Backend on host | `docker exec container_id cat path` | `files.py` |
| **Native mode** (coding sub-agents) | Inside container | Direct `open()` | `native.py` |

In host mode, **every file operation spawns a new `docker exec` process** inside the container:

```python
# files.py:78 — every file_read
proc = await asyncio.create_subprocess_exec(
    "docker", "exec", container_id, "cat", path_str,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
```

This has ~50-100ms overhead per call. Worse:

- **`file_edit` requires TWO round-trips:** `docker exec cat` (read) → edit in Python on host → `docker exec tee` (write back)
- **No line-range support in sandbox mode** — always reads entire file via `cat`, then the 100KB truncation kicks in
- **No outline mode in sandbox mode** — outline extraction only works in host mode's direct filesystem path
- **No mtime tracking in sandbox mode** — the `track_file_read()` call only happens in host mode
- **Shell tools have the same overhead** — every `shell_grep`, `shell_ls`, `shell_find`, `shell_head`, `shell_tail`, `shell_sed` spawns its own `docker exec`
- **Sequential execution** — even when the LLM batches multiple tool calls, each docker exec runs sequentially

### 1.3 Missing Features vs Claude Code (Medium — P2)

Analysis of `claude-code-source/src/tools/FileReadTool/` reveals 6 capabilities Bond lacks:

| Feature | Claude Code | Bond |
|---------|------------|------|
| **mtime dedup** | Tracks mtime per file; returns ~100-byte `FILE_UNCHANGED_STUB` if unchanged | `track_file_read()` records reads but never checks for dedup |
| **Two-phase token limits** | Pre-read byte gate (256KB stat) + post-read token gate (25K tokens) | Hardcoded 500-line / 100KB thresholds |
| **Streaming large file reader** | Fast path for small files, streaming with offset for large files | Always reads entire file into memory, then slices |
| **Image/PDF/Jupyter** | Processes images (Sharp), extracts PDF text, renders notebooks | None |
| **Skill discovery on read** | Auto-activates coding context based on file type | `load_context` is manual only |
| **Result compression** | `collapseReadSearch.ts` (762+ lines) compresses old read results in long conversations | Nothing — old reads consume full context forever |

---

## 2. Architecture Context

### 2.1 Host Mode (Primary Path)

```
User → Gateway (Node.js :18789) → Backend (FastAPI :18488)
                                      ↓
                                  Agent Loop (loop.py)
                                      ↓ tool calls
                                  files.py handlers
                                      ↓
                                  docker exec container_id <cmd>
                                      ↓
                                  Sandbox Container (isolated filesystem)
```

The agent loop runs as an async task inside the FastAPI backend. It calls LiteLLM, processes tool calls via handler functions, and stores results in the database. The sandbox container provides an isolated filesystem and shell, but the "brain" stays in the backend.

**Why this design:** The backend has direct access to the database, API keys, vault, and settings. The sandbox is disposable — it can be killed/recreated without losing agent state.

**The tradeoff:** Every file/shell operation pays the docker exec tax.

### 2.2 Native Mode (Coding Sub-Agents)

```
Backend → docker exec container_id python -m backend.app.agent.coding_loop
              ↓
          Coding agent process (INSIDE container)
              ↓ tool calls
          native.py handlers
              ↓
          Direct open() / subprocess
```

When a coding agent is spawned, it runs inside the container with direct filesystem access. No docker exec overhead. But it loses direct database/API access.

### 2.3 The Loop Detector Pipeline

```
LLM returns tool_calls → for each tool_call:
    1. detect_loop(tool_name, tool_args, loop_state)
       → checks consecutive, name-only, cyclical
       → if detected: inject SYSTEM warning message, skip remaining tool calls
    2. execute tool handler
    3. detect_empty_result(tool_name, result, loop_state)
       → if empty: inject SYSTEM nudge
    4. append result to messages
```

The loop detector runs BEFORE tool execution. When it fires, all remaining tool calls in the batch are orphaned and filled with `{"error": "Skipped — agent loop intervention"}`.

---

## 3. Proposed Solution

### Phase 0: Fix Loop Detector (P0 — Day 1)

**File:** `backend/app/agent/iteration_handlers.py`

The name-only detector needs to be smarter. Instead of just counting tool name occurrences, it should consider:

1. **Argument diversity** — If the args are different each time, it's exploration, not a loop
2. **Result diversity** — If each call returns different content, it's working correctly
3. **Tool category** — Read-only tools (`file_read`, `shell_grep`, `shell_ls`) should have higher thresholds than write tools

**Proposed changes:**

```python
# In detect_loop(), replace the name-only check:

# Current (broken):
if len(loop_state.recent_tool_names) >= loop_state.NAME_ONLY_THRESHOLD:
    last_n_names = loop_state.recent_tool_names[-loop_state.NAME_ONLY_THRESHOLD:]
    if all(n == last_n_names[0] for n in last_n_names):
        return True, "SYSTEM: ..."  # Fires on 3 different file reads!

# Proposed (fixed):
READ_TOOLS = {"file_read", "shell_grep", "shell_ls", "shell_head",
              "shell_tail", "shell_sed", "shell_find", "batch_head",
              "project_search", "shell_wc", "shell_tree"}

if len(loop_state.recent_tool_names) >= loop_state.NAME_ONLY_THRESHOLD:
    last_n_names = loop_state.recent_tool_names[-loop_state.NAME_ONLY_THRESHOLD:]
    if all(n == last_n_names[0] for n in last_n_names):
        tool = last_n_names[0]

        # For read-only tools, check if args are actually diverse
        if tool in READ_TOOLS:
            last_n_sigs = loop_state.recent_tool_calls[-loop_state.NAME_ONLY_THRESHOLD:]
            unique_sigs = set(sig for _, sig in last_n_sigs)
            if len(unique_sigs) == len(last_n_sigs):
                # All different args — this is exploration, not a loop
                # Raise threshold dynamically for this sequence
                if len(loop_state.recent_tool_names) < loop_state.NAME_ONLY_THRESHOLD * 3:
                    return False, ""

        return True, "SYSTEM: ..."
```

**Also raise the threshold:**

```python
# loop_state.py
NAME_ONLY_THRESHOLD: int = 5  # was 3 — too aggressive for exploration
```

**Rationale:** A threshold of 3 means you can only read 2 files before being warned. Even 5 is conservative — Claude Code has no equivalent limit. But combined with the arg-diversity check, 5 allows legitimate 4-file reads while still catching actual loops (where the same tool with similar args keeps failing).

### Phase 1: Multi-File file_read (P0 — Day 1)

**Files:** `backend/app/agent/tools/files.py`, `backend/app/agent/tools/native.py`

Add a `paths` array parameter to `file_read` so multiple files can be read in a single tool call:

```json
{
    "name": "file_read",
    "parameters": {
        "path": {"type": "string", "description": "Single file path"},
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
            "description": "Multiple file paths (max 10). Overrides 'path'."
        },
        "line_start": {"type": "integer"},
        "line_end": {"type": "integer"},
        "outline": {"type": "boolean"}
    }
}
```

Response format for multi-file:

```json
{
    "results": {
        "src/foo.py": {"content": "...", "total_lines": 42},
        "src/bar.py": {"content": "...", "total_lines": 18},
        "src/baz.py": {"error": "File not found"}
    }
}
```

This immediately eliminates the loop detector problem for the most common case (reading multiple files for context). One tool call = one name-only count, regardless of how many files.

**For sandbox mode:** Execute all reads through a single `docker exec sh -c 'cat file1; echo DELIMITER; cat file2; ...'` command to avoid N separate docker exec calls.

### Phase 2: Persistent Helper Process (P1 — Day 2-3)

**New file:** `backend/app/sandbox/helper_protocol.py`

Instead of spawning a new `docker exec` for every tool call, start a persistent helper process inside the container that accepts commands over stdin/stdout using a JSON-RPC protocol:

```
Backend starts:
    docker exec -i container_id /usr/local/bin/bond-helper

Helper process (Python, runs inside container):
    while True:
        request = json.loads(stdin.readline())
        result = handle(request)  # file_read, file_write, grep, ls, etc.
        stdout.write(json.dumps(result) + "\n")
        stdout.flush()
```

**Benefits:**
- Amortizes container exec overhead to a single startup cost
- All subsequent operations are stdin/stdout IPC (~1ms vs ~50-100ms)
- Can batch multiple operations in a single request
- Can maintain state (mtime cache, file handles) across calls
- Can run concurrent operations via asyncio inside the helper

**Protocol:**

```json
// Request
{"id": 1, "method": "file_read", "params": {"path": "/workspace/foo.py", "line_start": 1, "line_end": 50}}

// Response
{"id": 1, "result": {"content": "...", "total_lines": 200, "mtime": 1712170000.0}}

// Batch request
{"id": 2, "method": "batch", "params": {"calls": [
    {"method": "file_read", "params": {"path": "a.py"}},
    {"method": "file_read", "params": {"path": "b.py"}},
    {"method": "shell_grep", "params": {"pattern": "TODO", "path": "."}}
]}}
```

**Fallback:** If the helper process dies, fall back to individual `docker exec` calls (current behavior). Restart the helper on the next tool call.

### Phase 3: mtime Dedup + Token Budgets (P1 — Day 2-3)

**Files:** `backend/app/agent/tools/file_buffer.py`, `backend/app/agent/tools/files.py`

#### 3a. mtime-Based Dedup

Port Claude Code's pattern. Track file mtime in conversation state:

```python
@dataclass
class ReadRecord:
    mtime: float
    line_start: int | None
    line_end: int | None
    token_count: int

# In handle_file_read:
stat = os.stat(path)  # or via helper process
prev = read_state.get(path)
if prev and prev.mtime == stat.st_mtime and prev.line_start == line_start and prev.line_end == line_end:
    return {
        "path": path,
        "status": "unchanged",
        "note": f"File has not changed since last read ({prev.token_count} tokens saved)",
        "total_lines": total_lines,
    }
```

**For sandbox mode with helper process:** The helper maintains the mtime cache locally and returns a `"unchanged": true` flag, avoiding even the IPC overhead for repeated reads.

#### 3b. Two-Phase Token Budget

Replace the hardcoded 500-line / 100KB limits with a proper budget:

```python
MAX_PRE_READ_BYTES = 256_000   # Phase 1: cheap stat() check
MAX_POST_READ_TOKENS = 25_000  # Phase 2: actual token count after read

async def handle_file_read(...):
    # Phase 1: byte gate (cheap)
    size = os.path.getsize(path)
    if size > MAX_PRE_READ_BYTES:
        return {"error": f"File is {size:,} bytes ({size // 1024}KB). Use line_start/line_end to read a section, or outline mode."}

    # Read file...
    content = read_file(path, line_start, line_end)

    # Phase 2: token gate (after read)
    token_count = estimate_tokens(content)
    if token_count > MAX_POST_READ_TOKENS:
        # Auto-truncate with guidance
        truncated = truncate_to_tokens(content, MAX_POST_READ_TOKENS)
        return {
            "content": truncated,
            "truncated": True,
            "total_tokens": token_count,
            "returned_tokens": MAX_POST_READ_TOKENS,
            "hint": "File exceeds token budget. Use line_start/line_end for specific sections.",
        }
```

### Phase 4: Volume Mount Direct Reads (P2 — Week 2)

**File:** `backend/app/sandbox/manager.py`, `backend/app/agent/tools/files.py`

If the workspace is volume-mounted into the container (which it always is — that's how workspaces work), the host can read files directly from the mount point instead of going through docker exec. Zero overhead.

```python
# In SandboxManager, track mount mappings:
self._mount_map: dict[str, str] = {}  # container_path → host_path

# When creating container with -v /host/workspace:/workspace:
self._mount_map["/workspace"] = "/host/workspace"

# In handle_file_read, before falling back to docker exec:
host_path = manager.resolve_to_host_path(container_path)
if host_path and os.path.exists(host_path):
    # Direct read — no docker exec needed
    content = open(host_path).read()
```

**Caveat:** This only works for volume-mounted paths. Files created inside the container (not on a mount) still need docker exec. The helper process (Phase 2) handles those.

**Security:** The allowlist check must still apply — only paths within configured workspace mounts are accessible.

### Phase 5: Tool Consolidation (P2 — Week 2)

Reduce 7 overlapping file/shell tools to 3:

| Current Tools | Consolidated Into |
|--------------|-------------------|
| `file_read`, `shell_head`, `shell_tail`, `batch_head`, `shell_sed` (line extraction) | **`file_read`** (with `paths`, `line_start`, `line_end`, `outline`) |
| `shell_grep`, `project_search` | **`file_search`** (rename for clarity) |
| `shell_ls`, `shell_find`, `shell_tree`, `shell_wc` | **`file_list`** (unified directory exploration) |

**Benefits:**
- Fewer tool definitions = fewer tokens in system prompt
- Less confusion for the model about which tool to use
- Each consolidated tool counts as one name for loop detection purposes
- Simpler handler code

**Migration:** Keep old tool names as aliases for 2 weeks, logging deprecation warnings. Remove after confirming no regressions.

### Phase 6: Result Compression (P3 — Week 3)

**New file:** `backend/app/agent/context_compression.py`

In long conversations, old file read results consume full context forever. Claude Code's `collapseReadSearch.ts` solves this by compressing old results into compact summaries.

**Approach:**

After N tool-call iterations (e.g., 10), scan the message history for old `file_read` results and compress them:

```python
# Before compression (in messages):
{"role": "tool", "content": "{\"content\": \"<2000 lines of code>\", \"path\": \"src/foo.py\"}"}

# After compression:
{"role": "tool", "content": "{\"path\": \"src/foo.py\", \"summary\": \"Python module (847 lines). Classes: FooService, FooRepository. Key functions: process_order(), validate_input(). Read at iteration 3.\", \"compressed\": true}"}
```

**Rules:**
- Never compress the most recent read of a file (agent may still be working with it)
- Never compress results from the current iteration batch
- Preserve error results (they're small and informative)
- If the agent re-reads a compressed file, serve fresh content

---

## 4. Migration Plan

### Week 1 (P0)
1. Fix `detect_loop()` name-only check with arg-diversity awareness
2. Raise `NAME_ONLY_THRESHOLD` from 3 to 5
3. Add `paths` parameter to `file_read` in both `files.py` and `native.py`
4. Update system prompt to document multi-file read capability
5. **Test:** Run the code review scenario that triggered this doc — agent should read 7 files without intervention

### Week 2 (P1)
1. Implement `bond-helper` persistent process and protocol
2. Add mtime dedup to both host mode and native mode
3. Implement two-phase token budget
4. **Test:** Measure tool call latency before/after helper process. Target: <5ms per file read (down from ~75ms)

### Week 3 (P2)
1. Implement volume mount direct reads
2. Consolidate tools (with alias period)
3. **Test:** End-to-end agent session with 20+ file reads — no loop detection, no truncation surprises

### Week 4 (P3)
1. Implement result compression
2. **Test:** Long conversation (50+ iterations) — context window usage should plateau, not grow linearly

---

## 5. Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Max files readable per turn (without intervention) | 2-3 | Unlimited |
| File read latency (sandbox mode) | ~75ms | <5ms (helper) or <1ms (volume mount) |
| False positive loop detections per 100 turns | ~15 | <1 |
| Context tokens wasted on re-reads | ~18% (estimated) | <3% (with dedup) |
| Tool definitions in system prompt | 7 file/shell tools | 3 |
| Context growth in long conversations | Linear | Sublinear (with compression) |

---

## 6. Appendix: Session Transcript Evidence

The following is a real transcript from the session that motivated this design doc. The agent was asked to perform a code review of 7 new files.

### Turn 1: Initial Discovery
- Agent called `file_read` on `adapters.py` (675 lines) — **success**, full review produced
- Agent called `file_read` on `remote_adapter.py` — **success**
- Agent called `file_read` on `host_registry.py` — **BLOCKED**: system injected "You have called 'file_read' 3 times with different arguments but getting the same kind of results. STOP."

### Turn 2: Workaround via code_execute
- Agent switched to `code_execute` with `cat` commands to read files
- Read 1 file successfully via `cat`
- System still counted this toward the loop threshold
- Subsequent reads were blocked

### Turn 3: Escalation
- Agent attempted `shell_grep` as another workaround
- Got partial results from 2 files
- System issued `HARD STOP` and terminated the turn entirely

### Turn 4-6: Continued Investigation (for this design doc)
- Agent read `files.py`, `shell_utils.py`, `native.py` outlines — system blocked after 3 reads
- Agent switched to `code_execute` with `sed` and `grep` to extract code sections
- System issued `HARD STOP` again after 3 shell commands
- Agent used `code_execute` with compound commands to get remaining data in single calls
- System issued `HARD STOP` a third time

**Total turns lost to false loop detection: 4+**
**Files the agent never fully read: 4 out of 7**
**Result:** Incomplete code review, multiple workaround attempts, user had to intervene

### Root Cause
The loop detector treated "read file A, read file B, read file C" the same as "read file A, read file A, read file A" — a fundamentally broken heuristic that punishes breadth-first exploration, which is exactly what code review, refactoring, and project discovery require.
