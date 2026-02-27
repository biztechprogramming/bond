# C6: Context Optimization — Reduce Primary Model Token Usage

**Goal:** Dramatically reduce tokens sent to the primary model per turn without meaningfully affecting quality. Three phases targeting history, tool results, and tool definitions.

---

## Phase 1: Biggest Impact, Least Effort

### Task 1: Sliding Window + Rolling Summary

**Files:** `backend/app/worker.py`, `migrations/000012_conversation_summary.up.sql`

Replace unbounded history loading with a fixed window of recent messages plus a rolling summary.

**Migration:**
```sql
ALTER TABLE conversations ADD COLUMN rolling_summary TEXT DEFAULT '';
ALTER TABLE conversations ADD COLUMN summary_covers_to INTEGER DEFAULT 0;
```

Note: The `conversations` table is in the main knowledge.db (created in migration 000006).

**Implementation:**
1. In `_compress_history()` (or replace it), after a turn completes:
   - If total messages > WINDOW_SIZE (20), summarize messages outside the window
   - Update `rolling_summary` on the conversation row
   - Update `summary_covers_to` with the message index covered
2. When building context for the next turn:
   - Load only last WINDOW_SIZE messages from DB
   - Prepend rolling summary as a system-adjacent message
   - Skip the old `_compress_history` pipeline entirely for the primary model
3. Summary update uses the utility model (already cheap)
4. On first turn or short conversations: no summary needed, send all messages

**Config constants:**
```python
HISTORY_WINDOW_SIZE = 20          # Messages loaded per turn
SUMMARY_UPDATE_THRESHOLD = 10     # Update summary when this many new messages since last summary
```

**Key difference from current approach:** Never load more than WINDOW_SIZE messages from DB. Current code loads ALL messages then compresses in Python. This is a DB-level optimization.

---

### Task 2: Progressive Decay on Tool Results

**File:** `backend/app/worker.py`

Apply content-aware compression to ALL tool results based on age, not just old messages.

**Decay tiers:**
- **Turn 0** (just returned): Full content, but capped at MAX_TOOL_RESULT_TOKENS (1500)
- **Turn 1-2**: Head/tail (first 15 + last 15 lines for file content, last 30 lines for execution output)
- **Turn 3-5**: One-line summary: `[file_read: src/main.py — 245 lines, TypeScript]` or `[code_execute: exit 0, 47 lines output]`
- **Turn 6+**: Tool name + args only: `[called file_read(path=src/main.py)]`

**Content-aware rules by tool type:**
- `file_read` → keep imports + function/class signatures at tier 1-2
- `code_execute` → keep exit code + last N lines of stderr/stdout
- `search_memory` → keep top 2 results, drop rest
- `web_search` / `web_read` → keep titles + URLs + first sentence
- `file_write` → just confirmation: `[wrote 45 lines to src/main.py]`
- All others → generic head/tail truncation

**Implementation:**
1. Add `turn_number` tracking to messages (or infer from position in list)
2. Create `_decay_tool_result(msg, turns_ago)` function
3. Apply decay to ALL messages in the context window, including verbatim/recent ones
4. Apply BEFORE sending to the primary model, not at storage time (preserve full results in DB)

---

### Task 3: Heuristic Tool Selection

**File:** `backend/app/worker.py` (or new `backend/app/agent/tool_selection.py`)

Select relevant tools per turn using keyword/pattern matching instead of sending all 16.

**Always include:** `respond` (required for every turn)

**Keyword mapping:**
```python
TOOL_KEYWORDS = {
    "file_read": ["file", "read", "look at", "show me", "open", "cat", "source", "code in", "check the"],
    "file_write": ["write", "create file", "save to", "update file", "edit", "modify", "add to file"],
    "code_execute": ["run", "execute", "test", "install", "build", "compile", "script", "command", "terminal", "shell", "pip", "npm"],
    "search_memory": ["remember", "recall", "search", "find", "what did", "do you know", "previously", "last time", "history"],
    "memory_save": ["remember this", "save this", "note that", "store", "keep in mind"],
    "memory_update": ["update memory", "correct that", "change what you remember"],
    "memory_delete": ["forget", "delete memory", "remove that memory"],
    "web_search": ["search the web", "google", "look up", "find online", "search for", "what is", "who is", "latest"],
    "web_read": ["read this url", "fetch", "visit", "browse to", "open url", "http"],
    "browser": ["browser", "screenshot", "click", "navigate", "webpage"],
    "email": ["email", "send mail", "inbox"],
    "cron": ["schedule", "cron", "timer", "recurring", "every hour", "every day"],
    "notify": ["notify", "alert", "ping me", "let me know"],
    "skills": ["skill", "ability", "capability"],
    "call_subordinate": ["delegate", "subordinate", "sub-agent", "hand off"],
}
```

**Logic:**
1. Check user message + last assistant message against keywords
2. Match tools by keyword presence (case-insensitive)
3. If no tools matched (generic question), include only `respond`
4. If conversation has been using specific tools (last 3 turns), include those too (momentum)
5. Always cap at max 8 tools per turn
6. Fallback: if the model returns a tool_call for a tool not in the current set, retry with that tool added

**Estimated savings:** ~2,000 tokens/turn (from 16 tools to avg 4-5)

---

## Phase 2: Refinements

### Task 4: Compressed Tool Schemas

**File:** `backend/app/agent/tools/definitions.py` (or new `backend/app/agent/tools/compact.py`)

Create compact versions of tool schemas that strip verbose descriptions.

**For each tool definition:**
- Keep: function name, parameter names and types, required array
- Strip: long descriptions (replace with 1-line), parameter descriptions, examples
- Keep enum values (they're small and important)

**Implementation:**
```python
def compact_tool_schema(tool_def: dict) -> dict:
    """Strip verbose descriptions from tool schema, keeping structure."""
    func = tool_def["function"]
    compact = {
        "type": "function",
        "function": {
            "name": func["name"],
            "description": func["description"].split(".")[0] + ".",  # First sentence only
            "parameters": _compact_params(func.get("parameters", {})),
        }
    }
    return compact

def _compact_params(params: dict) -> dict:
    """Strip parameter descriptions, keep types and constraints."""
    if "properties" not in params:
        return params
    compact_props = {}
    for name, prop in params["properties"].items():
        compact_prop = {"type": prop.get("type", "string")}
        if "enum" in prop:
            compact_prop["enum"] = prop["enum"]
        compact_props[name] = compact_prop
    return {
        "type": "object",
        "properties": compact_props,
        "required": params.get("required", []),
    }
```

Use compact schemas by default. Full schemas available via fallback if model makes errors.

**Estimated savings:** ~1,500 tokens/turn on top of Task 3

---

### Task 5: Conversation-Aware Tool Pruning

**File:** `backend/app/worker.py` or `backend/app/agent/tool_selection.py`

Track which tools were actually used in recent turns and boost/demote accordingly.

**Implementation:**
1. After each turn, record which tools were called (already tracked in tool_calls_made)
2. Store recent tool usage on the conversation: `recent_tools_used` (JSON array of last 10 tool names)
3. In tool selection (Task 3), boost tools that were used in last 3 turns
4. Demote tools not used in last 10 turns (unless keyword-matched)

**Migration:**
```sql
ALTER TABLE conversations ADD COLUMN recent_tools_used TEXT DEFAULT '[]';
```

This stacks with Task 3 — heuristics for new turns, momentum for ongoing work.

---

### Task 6: Tool Result Caching with References

**File:** `backend/app/worker.py`, new table or reuse existing

When a tool result exceeds a threshold, store it and replace inline with a reference.

**Implementation:**
1. New table (or reuse conversation_messages with a flag):
```sql
CREATE TABLE tool_result_cache (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_args TEXT,
    result_content TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
2. After tool execution, if result > 500 tokens:
   - Store full result in cache
   - Replace inline with: `[Result ref:abc123 — file_read src/main.py, 245 lines. Use recall_tool_result to retrieve.]`
3. Add `recall_tool_result` tool that retrieves by reference ID
4. Deduplication: before executing file_read, check cache for same path. If exists and file hasn't changed, return reference to existing result.

**Estimated savings:** 25-35% for tool-heavy turns, plus dedup avoids re-reading same files.

---

## Phase 3: Advanced (If Needed)

### Task 7: Conversation Chapters (Semantic Topic Boundaries)

**File:** `backend/app/worker.py` or new `backend/app/agent/chapters.py`

Upgrade the rolling summary from Task 1 with semantic boundary detection.

**Implementation:**
1. After each turn, use the utility model to classify: "Is this the same topic as the previous turn? YES/NO"
2. If NO → close current chapter, summarize it, start new chapter
3. Rolling summary becomes a list of chapter summaries
4. Current chapter messages are kept verbatim (like agent-zero's current topic)
5. Chapter summaries are hierarchical: recent chapters get 2-3 sentences, older chapters get 1 sentence

**When to implement:** Only if Task 1's simple rolling summary produces quality issues (agent forgets mid-conversation decisions).

---

### Task 8: Lazy History with Retrieval Tool

**File:** `backend/app/worker.py`, new tool definition

Add a `recall_conversation` tool that searches conversation history.

**Implementation:**
1. Load only last 4 messages + rolling summary for context
2. Add tool: `recall_conversation(query: str)` → searches conversation_messages by content similarity
3. Uses FTS or embedding search on the conversation's message history
4. Returns top 3 matching messages with their context (1 message before/after)

**When to implement:** Only if agents frequently need details from >20 turns ago that the rolling summary doesn't capture.

---

## Definition of Done

### Phase 1
- [ ] Sliding window: only last 20 messages loaded, rolling summary prepended
- [ ] Progressive decay: tool results compressed by age, content-aware rules per tool type
- [ ] Heuristic tool selection: avg 4-5 tools/turn instead of 16
- [ ] All existing tests pass
- [ ] Backend starts and runs without errors
- [ ] Conversations work correctly (manual test)

### Phase 2
- [ ] Compact tool schemas: ~60% smaller definitions
- [ ] Conversation-aware tool pruning: recent tools boosted
- [ ] Tool result caching: large results stored by reference
- [ ] recall_tool_result tool works

### Phase 3
- [ ] Conversation chapters with semantic boundaries
- [ ] recall_conversation tool for deep history search

---

## Estimated Total Savings

| Component | Before | After Phase 1 | After Phase 2 | After Phase 3 |
|-----------|--------|---------------|---------------|---------------|
| History (long convo) | 15-50K tokens | 4-8K tokens | 4-8K tokens | 3-6K tokens |
| Tool definitions | ~3,400 tokens | ~1,200 tokens | ~600 tokens | ~600 tokens |
| Tool results in context | 2-10K tokens | 1-3K tokens | 0.5-2K tokens | 0.5-2K tokens |
| **Total per turn** | **20-60K** | **6-12K** | **5-10K** | **4-9K** |
