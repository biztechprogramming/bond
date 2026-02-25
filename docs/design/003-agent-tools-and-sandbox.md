# Design Doc 003: Agent Tools & Sandbox System

**Status:** Draft — awaiting review
**Depends on:** 001 (Knowledge Store & Memory), 002 (Entity Graph)
**Architecture refs:** [03 — Agent Runtime](../architecture/03-agent-runtime.html), [08 — Sandbox System](../architecture/08-sandbox.html)

---

## 1. Overview

Bond's agent is currently a bare chat completion — no tools, no sandbox, no RAG. This design doc specifies the full tool system that gives the agent hands:

- **13 tools** drawn from all four source projects (Agent Zero, OpenClaw, EmailPipeline, AgentClaw)
- **Tool execution pipeline** through the mediator
- **Docker-based sandbox** for code execution with named profiles
- **Agent loop upgrade** from single-shot LLM call to a tool-use loop
- **LLM routing** through the gateway (OAuth subscription support)

### Source Project Contributions

| Source | Tools Contributed | Key Pattern |
|--------|-------------------|-------------|
| **Agent Zero** | `code_execute`, `memory_save`, `search_memory`, `call_subordinate`, `respond` | Agent writes its own tools, organic growth, multi-agent hierarchy |
| **OpenClaw** | `browser`, `web_search`, `file_read`, `file_write`, `cron` | CDP browser, session model, skill pipeline, OAuth auth |
| **EmailPipeline** | `email` | Email triage, classification, draft responses |
| **AgentClaw** | `notify`, `skills` (list/load) | Cross-channel notifications, skill discovery |

---

## 2. Tool Definitions

Each tool is registered with the LLM as a function the agent can call. Tools execute through the mediator pipeline (logging, validation, transaction wrapping).

### 2.1 `respond`

Send a final response to the user or superior agent. This is how the agent "speaks."

```json
{
  "name": "respond",
  "description": "Send a response message to the user. Use this when you have a complete answer or update to share.",
  "parameters": {
    "type": "object",
    "required": ["message"],
    "properties": {
      "message": {
        "type": "string",
        "description": "The response text to send to the user"
      }
    }
  }
}
```

**Execution:** Emits the message to the active channel. In streaming mode, chunks are forwarded as they're generated. This is the terminal tool — after `respond`, the agent turn ends unless the user sends another message.

### 2.2 `search_memory`

Hybrid search across knowledge store, memories, and session summaries.

```json
{
  "name": "search_memory",
  "description": "Search your knowledge base for relevant information. Combines semantic (vector) and keyword (FTS) search with automatic ranking. Use before answering questions that might rely on past context.",
  "parameters": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural language search query"
      },
      "source_types": {
        "type": "array",
        "items": { "type": "string", "enum": ["conversation", "file", "email", "web", "memory"] },
        "description": "Filter by source type. Omit to search all."
      },
      "limit": {
        "type": "integer",
        "default": 10,
        "description": "Maximum number of results"
      },
      "time_window_days": {
        "type": "integer",
        "description": "Only return results from the last N days"
      }
    }
  }
}
```

**Execution:** Dispatches `HybridSearchQuery` through mediator → `HybridSearch` (already built in Story 7). Returns ranked results with source, content snippet, and relevance score.

### 2.3 `memory_save`

Persist a fact, solution, instruction, or preference.

```json
{
  "name": "memory_save",
  "description": "Save a fact, solution, instruction, or preference for future recall. Check for similar memories first with search_memory to avoid duplicates.",
  "parameters": {
    "type": "object",
    "required": ["type", "content"],
    "properties": {
      "type": {
        "type": "string",
        "enum": ["fact", "solution", "instruction", "preference"],
        "description": "Category of memory"
      },
      "content": {
        "type": "string",
        "description": "The memory content to save"
      },
      "importance": {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "default": 0.5,
        "description": "How important this memory is (0.0-1.0)"
      },
      "sensitivity": {
        "type": "string",
        "enum": ["normal", "personal", "secret"],
        "default": "normal",
        "description": "Privacy classification"
      }
    }
  }
}
```

**Execution:** Dispatches `SaveMemoryCommand` through mediator → `MemoryRepository.save()` (Story 8). Returns memory ID and any dedup warnings.

### 2.4 `memory_update`

Update an existing memory with corrected or refined content.

```json
{
  "name": "memory_update",
  "description": "Update an existing memory with corrected or refined content. Preserves change history.",
  "parameters": {
    "type": "object",
    "required": ["memory_id", "content", "reason"],
    "properties": {
      "memory_id": {
        "type": "string",
        "description": "ID of the memory to update"
      },
      "content": {
        "type": "string",
        "description": "Updated memory content"
      },
      "reason": {
        "type": "string",
        "description": "Why this memory is being updated"
      }
    }
  }
}
```

**Execution:** Dispatches `UpdateMemoryCommand` → `MemoryRepository.update()`. Creates a version record in `memory_versions`.

### 2.5 `code_execute`

Run code in the Docker sandbox. The agent's primary way of using the computer as a tool.

```json
{
  "name": "code_execute",
  "description": "Execute code in a sandboxed Docker container. The sandbox has the tools and packages defined in your sandbox profile. Use for: running scripts, installing packages, processing data, testing code, system commands.",
  "parameters": {
    "type": "object",
    "required": ["language", "code"],
    "properties": {
      "language": {
        "type": "string",
        "enum": ["python", "javascript", "bash", "typescript"],
        "description": "Programming language or shell"
      },
      "code": {
        "type": "string",
        "description": "The code to execute"
      },
      "timeout": {
        "type": "integer",
        "default": 120,
        "description": "Maximum execution time in seconds"
      }
    }
  }
}
```

**Execution:** Dispatches `ExecuteCodeCommand` → `SandboxManager.execute()`. See Section 4 for sandbox details.

**Return format:**
```json
{
  "stdout": "...",
  "stderr": "...",
  "exit_code": 0,
  "execution_time_ms": 1234
}
```

### 2.6 `file_read`

Read a file from the workspace.

```json
{
  "name": "file_read",
  "description": "Read the contents of a file. Path is relative to the workspace root. For large files, use offset and limit to read in chunks.",
  "parameters": {
    "type": "object",
    "required": ["path"],
    "properties": {
      "path": {
        "type": "string",
        "description": "File path relative to workspace root"
      },
      "offset": {
        "type": "integer",
        "default": 0,
        "description": "Line number to start reading from (0-indexed)"
      },
      "limit": {
        "type": "integer",
        "default": 500,
        "description": "Maximum number of lines to read"
      }
    }
  }
}
```

**Execution:** Reads from the sandbox workspace mount. In `sandbox: none` mode, reads from host filesystem (with path validation to prevent traversal). Returns file content as string, or error if file not found.

### 2.7 `file_write`

Write or create a file in the workspace.

```json
{
  "name": "file_write",
  "description": "Write content to a file. Creates the file and parent directories if they don't exist. Overwrites existing content.",
  "parameters": {
    "type": "object",
    "required": ["path", "content"],
    "properties": {
      "path": {
        "type": "string",
        "description": "File path relative to workspace root"
      },
      "content": {
        "type": "string",
        "description": "Content to write to the file"
      }
    }
  }
}
```

**Execution:** Writes to sandbox workspace. Path traversal prevention enforced.

### 2.8 `call_subordinate`

Spawn or continue a sub-agent for delegated work.

```json
{
  "name": "call_subordinate",
  "description": "Delegate a subtask to a specialized sub-agent. The sub-agent runs independently and reports back. Use for: parallel work, specialized tasks (coding, research, data analysis), or when a task needs a different skill set.",
  "parameters": {
    "type": "object",
    "required": ["message"],
    "properties": {
      "message": {
        "type": "string",
        "description": "The task description or message for the sub-agent"
      },
      "agent_profile": {
        "type": "string",
        "description": "Named agent profile to use (e.g., 'coder', 'researcher'). Determines model, system prompt, and sandbox."
      },
      "reset": {
        "type": "boolean",
        "default": true,
        "description": "If true, start a fresh sub-agent. If false, continue an existing conversation."
      }
    }
  }
}
```

**Execution:** Creates a new agent session with the specified profile. The sub-agent runs its own tool loop and returns its final `respond` output to the calling agent.

### 2.9 `web_search`

Search the web using Brave Search API (or configurable provider).

```json
{
  "name": "web_search",
  "description": "Search the web for current information. Returns titles, URLs, and snippets from search results.",
  "parameters": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "query": {
        "type": "string",
        "description": "Search query"
      },
      "count": {
        "type": "integer",
        "default": 10,
        "description": "Number of results to return"
      }
    }
  }
}
```

**Execution:** Calls web search API. Requires `search.api_key.brave` in settings. Returns list of `{title, url, snippet}`.

### 2.10 `browser`

CDP-based browser control for web interaction.

```json
{
  "name": "browser",
  "description": "Control a browser to navigate, interact with, and extract data from web pages. Runs headless Chromium in the sandbox.",
  "parameters": {
    "type": "object",
    "required": ["action"],
    "properties": {
      "action": {
        "type": "string",
        "enum": ["navigate", "snapshot", "click", "type", "scroll", "back", "close"],
        "description": "Browser action to perform"
      },
      "url": {
        "type": "string",
        "description": "URL to navigate to (for 'navigate' action)"
      },
      "selector": {
        "type": "string",
        "description": "CSS selector for click/type actions"
      },
      "text": {
        "type": "string",
        "description": "Text to type (for 'type' action)"
      }
    }
  }
}
```

**Execution:** Dispatches to a CDP (Chrome DevTools Protocol) client running in the `research` sandbox. `snapshot` returns a text extraction of the current page (not a screenshot — keeps token cost down). Future: optional screenshot mode.

### 2.11 `email`

Email operations — search, read, classify, draft responses.

```json
{
  "name": "email",
  "description": "Interact with email: search, read, classify, or draft responses. Requires email integration to be configured.",
  "parameters": {
    "type": "object",
    "required": ["action"],
    "properties": {
      "action": {
        "type": "string",
        "enum": ["search", "read", "classify", "draft_reply", "list_unread"],
        "description": "Email action to perform"
      },
      "query": {
        "type": "string",
        "description": "Search query (for 'search' action)"
      },
      "email_id": {
        "type": "string",
        "description": "Email ID (for 'read', 'classify', 'draft_reply' actions)"
      },
      "instructions": {
        "type": "string",
        "description": "Instructions for draft or classification"
      }
    }
  }
}
```

**Execution:** Dispatches to email feature module (Phase 3). Returns email content, classification results, or draft text. Not available until email integration is configured.

### 2.12 `cron`

Schedule recurring or one-shot jobs.

```json
{
  "name": "cron",
  "description": "Schedule a task to run later or on a recurring schedule. The task will run as a new agent session.",
  "parameters": {
    "type": "object",
    "required": ["action"],
    "properties": {
      "action": {
        "type": "string",
        "enum": ["create", "list", "delete"],
        "description": "Cron action"
      },
      "schedule": {
        "type": "string",
        "description": "Cron expression or natural language (e.g., 'every day at 9am', '*/5 * * * *')"
      },
      "task": {
        "type": "string",
        "description": "Description of what to do when triggered"
      },
      "job_id": {
        "type": "string",
        "description": "Job ID (for 'delete' action)"
      }
    }
  }
}
```

**Execution:** Creates a cron entry in the database. A scheduler service picks up entries and spawns agent sessions at the specified times.

### 2.13 `notify`

Send a notification to the user on any channel.

```json
{
  "name": "notify",
  "description": "Send a notification to the user on a specific channel. Use for important alerts, task completions, or time-sensitive information.",
  "parameters": {
    "type": "object",
    "required": ["message"],
    "properties": {
      "message": {
        "type": "string",
        "description": "Notification message"
      },
      "channel": {
        "type": "string",
        "enum": ["webchat", "signal", "telegram", "discord", "email"],
        "description": "Channel to send on. Defaults to the current channel."
      },
      "priority": {
        "type": "string",
        "enum": ["low", "normal", "high"],
        "default": "normal",
        "description": "Notification priority"
      }
    }
  }
}
```

**Execution:** Routes through the gateway to the specified channel adapter.

### 2.14 `skills`

List and load skill files for specialized capabilities.

```json
{
  "name": "skills",
  "description": "Discover and load skill files that provide specialized instructions for specific tasks.",
  "parameters": {
    "type": "object",
    "required": ["action"],
    "properties": {
      "action": {
        "type": "string",
        "enum": ["list", "load"],
        "description": "list: show available skills. load: read a skill's instructions into context."
      },
      "skill_name": {
        "type": "string",
        "description": "Name of the skill to load (for 'load' action)"
      }
    }
  }
}
```

**Execution:** `list` scans skill directories and returns names + descriptions. `load` reads the SKILL.md file and appends its instructions to the agent's context for the current turn.

---

## 3. Agent Loop Upgrade

The current agent loop (Sprint 1) is a single LLM call with no tools. This upgrade adds the tool-use loop.

### 3.1 Tool Loop Architecture

```
User message
    │
    ▼
┌─────────────────────────────────────────────────┐
│  1. Auto-RAG: search_memory(user_message)       │
│     → inject top results as context              │
│                                                  │
│  2. Build messages:                              │
│     [system_prompt + RAG context + tools]        │
│     [history]                                    │
│     [user_message]                               │
│                                                  │
│  3. LLM call (with tool definitions)             │
│     │                                            │
│     ├─ Text response? → emit via respond         │
│     │                                            │
│     └─ Tool calls? ──┐                           │
│                       ▼                          │
│  4. Execute each tool call via mediator          │
│     │                                            │
│     ▼                                            │
│  5. Append tool results to messages              │
│     │                                            │
│     └─→ Go to step 3 (loop until respond        │
│          or max_iterations reached)              │
└─────────────────────────────────────────────────┘
```

### 3.2 Auto-RAG

Before every agent turn, Bond automatically searches the knowledge store with the user's message and injects relevant context. This happens transparently — the agent doesn't need to call `search_memory` explicitly for basic context (though it can for targeted searches).

```python
# Pseudo-code
async def agent_turn(user_message, history):
    # Auto-RAG
    rag_results = await hybrid_search(query=user_message, limit=5)
    rag_context = format_rag_results(rag_results)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + rag_context},
        *history,
        {"role": "user", "content": user_message},
    ]

    # Tool loop
    for i in range(MAX_ITERATIONS):
        response = await llm_call(messages, tools=TOOL_DEFINITIONS)

        if response.has_tool_calls:
            for call in response.tool_calls:
                result = await mediator.send(call.to_command())
                messages.append(tool_result_message(call, result))
        else:
            return response.content

    raise MaxIterationsExceeded()
```

### 3.3 Max Iterations

Safety limit: **25 iterations** per agent turn (configurable via `agent.max_iterations` setting). If exceeded, the agent responds with an apology and summary of what it accomplished.

### 3.4 Tool Availability

Not all tools are available in every context. Tool availability depends on:

1. **Agent profile** — each profile declares which tools it can use
2. **Sandbox profile** — sandbox can restrict tools (e.g., `minimal` sandbox only gets `respond` + `search_memory`)
3. **Feature availability** — `email` tool only available if email integration is configured; `browser` only if Chromium is in the sandbox
4. **Channel trust** — untrusted channels get fewer tools

The agent loop filters the tool list before each LLM call based on the active context.

---

## 4. Sandbox System

### 4.1 Docker Container Model

Code execution runs inside Docker containers. Each sandbox profile maps to a Docker image.

```
Agent calls code_execute
    │
    ▼
SandboxManager.execute(profile, language, code)
    │
    ├─ Resolve sandbox profile for current context
    │
    ├─ Image exists? → use it
    │  Image missing? → build from profile spec
    │
    ├─ persistent=true?
    │    → find or create long-lived container
    │    → exec into existing container
    │
    ├─ persistent=false?
    │    → docker run --rm (fresh container, destroyed after)
    │
    ├─ Apply resource limits (memory, cpu, timeout)
    │
    ├─ Mount workspace volume (based on filesystem setting)
    │
    └─ Return stdout, stderr, exit_code
```

### 4.2 SandboxManager

```python
# backend/app/sandbox/manager.py

class SandboxProfile:
    """Resolved sandbox configuration."""
    name: str
    image: str | None          # Docker image or None (auto-build)
    packages: list[str]        # apt packages
    pip: list[str]             # pip packages
    npm: list[str]             # npm packages
    network: str               # "off" | "lan" | "web" | "full"
    filesystem: str            # "none" | "readonly" | "workspace" | "full"
    memory: str                # "512m", "2g", etc.
    cpu: float                 # CPU limit
    timeout: int               # seconds
    persistent: bool           # keep container alive between commands
    tools: list[str] | None    # allowed tools (None = all)
    env: dict[str, str]        # environment variables
    mounts: list[str]          # additional bind mounts
    ports: list[str]           # port forwards

class SandboxManager:
    """Manages Docker sandbox lifecycle and code execution."""

    async def execute(
        self,
        profile: SandboxProfile,
        language: str,
        code: str,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """Execute code in the sandbox."""
        ...

    async def ensure_image(self, profile: SandboxProfile) -> str:
        """Build or pull the Docker image for a profile."""
        ...

    async def resolve_profile(
        self,
        agent: str | None,
        skill: str | None,
        channel: str | None,
    ) -> SandboxProfile:
        """Resolve which sandbox profile to use based on context."""
        ...

    async def list_containers(self) -> list[ContainerInfo]:
        """List active sandbox containers."""
        ...

    async def cleanup(self) -> None:
        """Stop and remove stale containers."""
        ...
```

### 4.3 Image Auto-Build

When a profile specifies `packages` but no `image`, Bond generates a Dockerfile and builds it:

```dockerfile
# Auto-generated for profile "coding"
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs python3 python3-pip git gcc make \
    && rm -rf /var/lib/apt/lists/*

# pip packages (if specified)
RUN pip3 install --no-cache-dir pandas matplotlib

WORKDIR /workspace
```

Built images are tagged `bond-sandbox-{profile_name}` and cached. Rebuild triggers:
- Profile packages changed in `bond.json`
- User runs `bond sandbox rebuild {name}`
- Skill sandbox requirements changed

### 4.4 Network Modes

| Mode | Docker flag | Description |
|------|------------|-------------|
| `off` | `--network=none` | No network access at all |
| `lan` | `--network=bond-lan` (custom bridge, no internet gateway) | Local network only |
| `web` | default bridge + outbound HTTP/S only (iptables rules) | Internet web access |
| `full` | `--network=host` or default bridge | Unrestricted |

### 4.5 Filesystem Modes

| Mode | Docker mount | Description |
|------|-------------|-------------|
| `none` | No volumes | No host filesystem access |
| `readonly` | `-v workspace:/workspace:ro` | Read-only workspace |
| `workspace` | `-v workspace:/workspace:rw` | Read-write workspace |
| `full` | `-v /:/host:rw` | Full host filesystem (dangerous, trusted only) |

The workspace directory is `~/.bond/workspace` by default (configurable).

### 4.6 Profile Resolution Order

Per architecture doc 08:

1. Active skill declares sandbox → use skill's sandbox
2. Agent has assigned sandbox → use agent's sandbox
3. Channel has assigned sandbox → use channel's sandbox
4. Session-level override → use that
5. Main session → `none` (host, no container)
6. Fallback → global default sandbox

### 4.7 `sandbox: none` Mode

For trusted contexts (main webchat session), code executes directly on the host. `file_read`/`file_write` operate on the real filesystem. This is the default for the owner's direct chat — sandboxing yourself creates friction without security benefit.

Host execution still enforces:
- Timeout limits
- Path traversal prevention on file operations
- Audit logging of all commands

### 4.8 Docker Availability

Bond must work without Docker (degraded mode):
- If Docker is not available, `code_execute` returns an error: "Docker is required for sandboxed code execution. Install Docker or set sandbox to 'none' for trusted sessions."
- `sandbox: none` always works (host execution)
- Startup logs a warning if Docker is unavailable but sandbox profiles are configured
- `capabilities` endpoint reports Docker availability

---

## 5. LLM Routing Through Gateway

Per architecture doc 03, the Python backend should route LLM calls through the TypeScript gateway to leverage OAuth subscription support.

### 5.1 Current State (Sprint 1)

Backend calls LLM directly via litellm → Anthropic API with API key.

### 5.2 Target State

```
Backend (Python)                    Gateway (TypeScript)
┌───────────────┐                  ┌──────────────────────┐
│ Agent loop    │                  │ LLM Proxy            │
│ constructs    │──POST /llm/chat─▶│  • pi-agent-core     │
│ prompt+tools  │                  │  • AuthProfileStore   │
│               │◀─SSE stream─────│  • OAuth + API key    │
│ Handles tool  │                  │  • Failover/rotation  │
│ calls locally │                  └──────────────────────┘
└───────────────┘
```

### 5.3 Migration Path

**Phase 1 (now):** Keep litellm direct calls. API keys stored in settings DB (already built). This works today.

**Phase 2 (later):** Add `/llm/chat` endpoint to gateway. Backend switches to calling gateway instead of litellm. Gateway handles OAuth token refresh, profile rotation, failover.

**This design doc implements Phase 1 only.** Gateway LLM proxy is a separate design doc.

---

## 6. Configuration

### 6.1 Sandbox Profiles in bond.json

```json
{
  "sandboxes": {
    "coding": {
      "packages": ["nodejs", "python3", "git", "gcc", "make"],
      "network": "lan",
      "filesystem": "workspace",
      "memory": "2g",
      "timeout": 300
    },
    "research": {
      "packages": ["chromium"],
      "tools": ["browser", "web_search", "web_fetch", "search_memory", "respond"],
      "network": "web",
      "filesystem": "readonly"
    },
    "data": {
      "packages": ["python3", "python3-pip"],
      "pip": ["pandas", "matplotlib", "numpy"],
      "network": "off",
      "filesystem": "workspace"
    },
    "minimal": {
      "packages": [],
      "network": "off",
      "filesystem": "none",
      "tools": ["respond", "search_memory"]
    }
  },

  "agents": {
    "default": {
      "sandbox": "none",
      "tools": ["respond", "search_memory", "memory_save", "memory_update",
                "code_execute", "file_read", "file_write", "web_search",
                "skills", "notify"]
    },
    "coder": {
      "sandbox": "coding",
      "tools": ["respond", "code_execute", "file_read", "file_write",
                "search_memory", "memory_save"]
    },
    "researcher": {
      "sandbox": "research",
      "tools": ["respond", "web_search", "browser", "search_memory",
                "memory_save", "file_write"]
    }
  },

  "channels": {
    "webchat": { "sandbox": "none" },
    "signal": { "sandbox": "none" },
    "telegram": { "sandbox": "minimal" },
    "discord": { "sandbox": "minimal" }
  }
}
```

### 6.2 Settings Table Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `agent.max_iterations` | int | 25 | Max tool-loop iterations per turn |
| `agent.auto_rag` | bool | true | Auto-search knowledge store before each turn |
| `agent.auto_rag_limit` | int | 5 | Number of RAG results to inject |
| `sandbox.workspace_path` | string | `~/.bond/workspace` | Host path for workspace mounts |
| `sandbox.default_timeout` | int | 120 | Default code execution timeout (seconds) |
| `sandbox.docker_available` | bool | (auto-detected) | Whether Docker is available |
| `search.api_key.brave` | string | null | Brave Search API key (encrypted) |

---

## 7. Module Structure

```
backend/app/
├── agent/
│   ├── loop.py              # Agent loop with tool-use cycle (UPGRADE)
│   ├── tools/
│   │   ├── __init__.py      # Tool registry
│   │   ├── definitions.py   # All tool JSON definitions
│   │   ├── respond.py       # respond tool handler
│   │   ├── search.py        # search_memory tool handler
│   │   ├── memory.py        # memory_save, memory_update handlers
│   │   ├── code.py          # code_execute handler → SandboxManager
│   │   ├── files.py         # file_read, file_write handlers
│   │   ├── subordinate.py   # call_subordinate handler
│   │   ├── web.py           # web_search handler
│   │   ├── browser.py       # browser handler (stub until Phase 4)
│   │   ├── email.py         # email handler (stub until Phase 3)
│   │   ├── cron.py          # cron handler (stub until Phase 3)
│   │   ├── notify.py        # notify handler
│   │   └── skills.py        # skills list/load handler
│   └── llm.py               # LLM client (unchanged for now)
├── sandbox/
│   ├── __init__.py           # Currently empty, gets real code
│   ├── manager.py            # SandboxManager — Docker lifecycle
│   ├── profiles.py           # Profile loading from bond.json
│   ├── docker.py             # Docker client wrapper (docker-py)
│   └── host.py               # Host execution for sandbox: none
```

---

## 8. Implementation Stories

### Story 11a: Tool Registry & Definitions
- Create `backend/app/agent/tools/definitions.py` with all 14 tool JSON schemas
- Create `backend/app/agent/tools/__init__.py` with a `ToolRegistry` that maps tool names to handlers
- Tool filtering based on context (agent profile, sandbox, channel)
- Tests: tool definition validation, registry lookup, filtering

### Story 11b: Agent Loop Upgrade
- Upgrade `backend/app/agent/loop.py` to a tool-use loop
- Auto-RAG: search knowledge store with user message before each turn
- Iterate: LLM call → tool execution → append results → repeat until `respond` or max iterations
- Handle both streaming and non-streaming modes
- Tests: mock LLM returning tool calls, verify loop execution, max iteration safety

### Story 11c: Core Tool Handlers (respond, search, memory)
- Implement `respond`, `search_memory`, `memory_save`, `memory_update` handlers
- Wire to existing mediator commands (already built in Story 10)
- Tests: each handler with mock mediator

### Story 11d: File Tool Handlers
- Implement `file_read` and `file_write` with path traversal prevention
- Workspace path resolution (sandbox mode vs host mode)
- Tests: read/write in workspace, path traversal rejection

### Story 11e: SandboxManager — Docker Code Execution
- Implement `SandboxManager` with Docker container lifecycle
- Profile resolution from `bond.json`
- Image auto-build from package lists
- `code_execute` tool handler
- Graceful degradation when Docker unavailable
- Tests: mock Docker client, profile resolution, image build

### Story 11f: Host Execution (sandbox: none)
- Implement `HostExecutor` for trusted sessions without Docker
- Subprocess-based code execution with timeout, stdout/stderr capture
- Security: timeout enforcement, audit logging
- Tests: execute Python/bash, timeout enforcement

### Story 11g: Stub Tool Handlers
- Implement stubs for `web_search`, `browser`, `email`, `cron`, `notify`, `call_subordinate`, `skills`
- Stubs return clear "not yet configured" or "coming in Phase N" messages
- Tests: each stub returns appropriate message

### Story 11h: Integration Test — Full Agent Turn with Tools
- End-to-end test: user sends message → auto-RAG → LLM returns tool calls → tools execute → final response
- Test with mock LLM that returns a `search_memory` call followed by `respond`
- Test max iteration safety
- Test tool filtering by context

---

## 9. Phased Rollout

| Phase | Tools Active | Sandbox |
|-------|-------------|---------|
| **Phase 2 (Know Me)** — current | `respond`, `search_memory`, `memory_save`, `memory_update`, `file_read`, `file_write`, `code_execute`, `skills` | `none` (host) + `coding` |
| **Phase 3 (Help Me)** | + `email`, `cron`, `notify`, `web_search` | + `research`, `minimal` |
| **Phase 4 (Do For Me)** | + `browser`, `call_subordinate` | + `data`, full multi-agent |

Tools for later phases are registered as stubs that return "This feature is coming in Phase N" so the LLM knows they exist but aren't yet functional.

---

## 10. Security Considerations

### Path Traversal Prevention
All file operations validate paths:
- Resolve path relative to workspace root
- Check resolved path starts with workspace root (no `../../../etc/passwd`)
- Reject absolute paths outside workspace
- Reject symlinks that escape workspace

### Sandbox Escape Prevention
- Containers run as non-root user
- `--security-opt=no-new-privileges`
- Read-only root filesystem where possible
- Resource limits always enforced (memory, CPU, timeout)
- No Docker socket mounted (no container-in-container)

### Audit Trail
All tool executions are logged to `audit_log` table:
- Tool name, parameters (sensitive values redacted), result summary
- Execution time, sandbox profile used
- Agent session ID for traceability

### API Key Handling
- Search API keys (Brave, etc.) stored encrypted in settings (same as LLM keys)
- Keys passed to sandbox containers via environment variables, not persisted in container filesystem
- Keys masked in audit logs

---

## 11. Test Matrix

### Unit Tests

| Module | Tests |
|--------|-------|
| `ToolRegistry` | register, lookup, filter by context, unknown tool error |
| `AgentLoop` | single turn no tools, tool call loop, max iterations, auto-RAG injection |
| `respond` | emits message, ends turn |
| `search_memory` | dispatches query, formats results |
| `memory_save/update` | dispatches commands, returns IDs |
| `file_read/write` | read/write workspace, path traversal rejection |
| `SandboxManager` | profile resolution, image build, execute, cleanup |
| `HostExecutor` | execute Python/bash, timeout, audit logging |
| Stubs | each returns appropriate stub message |

### Integration Tests

| Scenario | What It Tests |
|----------|---------------|
| Message → auto-RAG → respond | Full loop with context injection |
| Message → search_memory → memory_save → respond | Multi-tool turn |
| code_execute in sandbox | Docker execution end-to-end |
| code_execute sandbox: none | Host execution |
| Untrusted channel → tool filtering | Only allowed tools available |
| Docker unavailable → code_execute | Graceful degradation |

---

## 12. Dependencies

| Package | Purpose | Required |
|---------|---------|----------|
| `docker` (docker-py) | Docker client for sandbox management | Optional (graceful degradation) |
| `httpx` | HTTP client for web_search API | Required for web_search |
| Existing: `litellm`, `sqlalchemy`, `aiosqlite` | Already installed | — |

```toml
# pyproject.toml additions
dependencies = [
    # ... existing ...
    "docker>=7.0.0",
    "httpx>=0.27.0",
]
```
