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

Code execution runs inside persistent Docker containers. Each agent has at most one container, created on first use and kept alive for the session.

```
Agent calls code_execute
    │
    ▼
SandboxManager.execute(sandbox, language, code)
    │
    ├─ Agent has sandbox_image = null?
    │    → HostExecutor (no Docker, direct on host)
    │
    ├─ Persistent container already running for this agent?
    │    → docker exec into existing container
    │
    ├─ No container yet?
    │    → docker create with:
    │       • agent's sandbox_image
    │       • workspace mounts: -v {host}:/workspace/{name}[:ro] for each
    │       • resource limits (memory, cpu)
    │       • security opts (no-new-privileges, non-root)
    │    → docker start
    │    → docker exec
    │
    ├─ Capture stdout, stderr, exit_code
    │
    ├─ Container stays alive for next command
    │   (cleaned up after idle timeout or session end)
    │
    └─ Return ExecutionResult
```

### 4.2 SandboxManager

```python
# backend/app/sandbox/manager.py

@dataclass
class ResolvedSandbox:
    """Fully resolved sandbox configuration for a specific agent execution."""
    agent_id: str
    agent_name: str
    image: str | None          # Docker image or None (host mode)
    workspace_mounts: list[WorkspaceMount]  # from agent_workspace_mounts table
    tools: list[str]           # enabled tools for this agent
    timeout: int               # seconds
    max_iterations: int
    container_id: str | None   # if persistent container already running

@dataclass
class WorkspaceMount:
    host_path: str             # /home/andrew/projects/bond
    mount_name: str            # "bond" → /workspace/bond
    readonly: bool

class SandboxManager:
    """Manages Docker sandbox lifecycle and code execution."""

    async def execute(
        self,
        sandbox: ResolvedSandbox,
        language: str,
        code: str,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """Execute code in the sandbox container.
        
        If persistent container exists for this agent, exec into it.
        Otherwise, create a new persistent container and exec.
        """
        ...

    async def get_or_create_container(
        self,
        sandbox: ResolvedSandbox,
    ) -> str:
        """Find existing persistent container or create a new one.
        
        Container is tagged with agent_id for lookup.
        Workspace mounts applied on creation:
          -v {host_path}:/workspace/{mount_name}[:ro]
        """
        ...

    async def resolve_sandbox(
        self,
        agent_id: str,
        channel_override: str | None = None,
    ) -> ResolvedSandbox:
        """Build a ResolvedSandbox from agent config + optional channel override."""
        ...

    async def stop_container(self, agent_id: str) -> None:
        """Stop the persistent container for an agent."""
        ...

    async def list_containers(self) -> list[ContainerInfo]:
        """List active sandbox containers."""
        ...

    async def cleanup_idle(self, idle_seconds: int = 1800) -> None:
        """Stop containers that have been idle beyond threshold."""
        ...
```

### 4.3 Prebuilt Sandbox Images

Bond ships Dockerfiles in `docker/sandboxes/` for common use cases. Users build them with `make sandbox-build`:

```
docker/sandboxes/
├── coding/Dockerfile      # Node 22, Python 3, git, build-essential
├── research/Dockerfile    # Chromium, curl
├── data/Dockerfile        # Python 3, pandas, matplotlib, numpy
└── minimal/Dockerfile     # Bare Ubuntu base
```

```makefile
# Makefile
sandbox-build:
	docker build -t bond-sandbox-coding docker/sandboxes/coding/
	docker build -t bond-sandbox-research docker/sandboxes/research/
	docker build -t bond-sandbox-data docker/sandboxes/data/
	docker build -t bond-sandbox-minimal docker/sandboxes/minimal/
```

Users can also use any Docker image they've pulled (`ubuntu:24.04`, `python:3.12`, custom images, etc.) — the Settings UI lets them type any image name or select from locally available images.

Future enhancement: auto-build images from package lists declared in skill metadata (per architecture doc 08).

### 4.4 Network Modes

| Mode | Docker flag | Description |
|------|------------|-------------|
| `off` | `--network=none` | No network access at all |
| `lan` | `--network=bond-lan` (custom bridge, no internet gateway) | Local network only |
| `web` | default bridge + outbound HTTP/S only (iptables rules) | Internet web access |
| `full` | `--network=host` or default bridge | Unrestricted |

### 4.5 Workspace Mounts

Each agent has a list of workspace directory mappings (configured in the UI). These are the ONLY host directories the container can see.

```
Agent "coder" workspace mounts:
  /home/andrew/projects/bond    → /workspace/bond     (rw)
  /home/andrew/projects/webapp  → /workspace/webapp    (rw)
  /home/andrew/docs             → /workspace/docs      (ro)

Becomes:
  docker create ... \
    -v /home/andrew/projects/bond:/workspace/bond:rw \
    -v /home/andrew/projects/webapp:/workspace/webapp:rw \
    -v /home/andrew/docs:/workspace/docs:ro \
    ...
```

No mounts configured = no host filesystem access (container has only its own filesystem).

For `sandbox_image: null` (host mode), the workspace mounts serve as an **allowlist** — `file_read`/`file_write` only work on paths within the listed directories.

### 4.6 Sandbox Resolution

Resolution is straightforward — the agent's config determines everything:

1. Look up the agent handling this session
2. Check if the channel has a `sandbox_override` → use that image instead of agent's
3. Agent's `sandbox_image` is null → host mode (no container)
4. Agent's `sandbox_image` is set → Docker mode with that image

Skill sandbox requirements (from architecture doc 08) are a future enhancement. For now, skills run in the agent's sandbox.

### 4.7 Host Mode (`sandbox_image: null`)

For agents without a sandbox image (like the default "Bond" agent), code executes directly on the host. This is the owner's direct assistant with full access.

Host mode enforces:
- **Workspace allowlist** — `file_read`/`file_write` restricted to directories listed in `agent_workspace_mounts` (if any are configured; if none, the agent has full filesystem access)
- Timeout limits on code execution
- Audit logging of all commands

### 4.8 Docker Availability

Bond must work without Docker (degraded mode):
- If Docker is not available and an agent has `sandbox_image` set, `code_execute` returns an error: "Docker is required for this agent's sandbox. Install Docker or switch to an agent without a sandbox."
- Agents with `sandbox_image: null` always work (host execution)
- Startup logs a warning if Docker is unavailable but agents have sandbox images configured
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

### 6.1 Agent Configuration (Database — managed via Settings UI)

Agents are the primary unit of configuration in Bond. Each agent is a named profile stored in the database and managed through the Settings UI. The user creates agents, picks a model, assigns a sandbox image, selects which tools to enable, maps workspace directories, and writes a system prompt.

#### Agent Schema

```sql
-- Migration 000005: Agent profiles
CREATE TABLE agents (
    id TEXT PRIMARY KEY,                    -- ULID
    name TEXT NOT NULL UNIQUE,              -- 'bond', 'coder', 'researcher'
    display_name TEXT NOT NULL,             -- 'Bond (Main)', 'Coder', 'Researcher'
    system_prompt TEXT NOT NULL,            -- The agent's persona / instructions
    model TEXT NOT NULL,                    -- 'anthropic/claude-sonnet-4-20250514'
    sandbox_image TEXT,                     -- Docker image name, NULL = host execution
    tools JSON NOT NULL DEFAULT '[]',       -- JSON array of enabled tool names
    max_iterations INTEGER NOT NULL DEFAULT 25,
    auto_rag INTEGER NOT NULL DEFAULT 1,    -- boolean: auto-search before each turn
    auto_rag_limit INTEGER NOT NULL DEFAULT 5,
    is_default INTEGER NOT NULL DEFAULT 0,  -- exactly one agent is the default
    is_active INTEGER NOT NULL DEFAULT 1,   -- soft disable
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Each agent can have multiple workspace directory mappings
-- These become subdirectories under /workspace in the container
CREATE TABLE agent_workspace_mounts (
    id TEXT PRIMARY KEY,                    -- ULID
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    host_path TEXT NOT NULL,                -- e.g., '/home/andrew/projects/bond'
    mount_name TEXT NOT NULL,               -- e.g., 'bond' → /workspace/bond
    readonly INTEGER NOT NULL DEFAULT 0,    -- mount as read-only?
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(agent_id, mount_name)
);

CREATE INDEX idx_awm_agent ON agent_workspace_mounts(agent_id);

-- Which communication channels each agent listens on
CREATE TABLE agent_channels (
    id TEXT PRIMARY KEY,                    -- ULID
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,                  -- 'webchat', 'signal', 'telegram', etc.
    sandbox_override TEXT,                  -- Override sandbox image for this channel
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(agent_id, channel)
);

CREATE INDEX idx_ac_agent ON agent_channels(agent_id);
CREATE INDEX idx_ac_channel ON agent_channels(channel);
```

#### Default Agent (seeded on first run)

```json
{
  "name": "bond",
  "display_name": "Bond",
  "system_prompt": "You are Bond, a helpful personal AI assistant running locally on the user's machine. Be concise, helpful, and friendly. You have tools to search your memory, save information, read and write files, and execute code. Use them when needed.",
  "model": "anthropic/claude-sonnet-4-20250514",
  "sandbox_image": null,
  "tools": ["respond", "search_memory", "memory_save", "memory_update",
            "code_execute", "file_read", "file_write", "web_search",
            "skills", "notify"],
  "max_iterations": 25,
  "is_default": true,
  "workspace_mounts": [],
  "channels": ["webchat"]
}
```

When `sandbox_image` is null, the agent runs in host mode (no Docker container). The default "Bond" agent starts this way — it's the owner's direct assistant with full access.

#### Settings UI — Agent Configuration Page

The Settings UI gets a new **Agents** section (`/settings/agents`) where the user can:

1. **List agents** — cards showing name, model, sandbox, tool count, workspace count
2. **Create agent** — form with:
   - Name and display name
   - System prompt (textarea)
   - **Model dropdown** — populated from the LLM providers list (same source as current LLM settings). Shows provider + model name.
   - **Sandbox image dropdown** — "None (host execution)" + list of available Docker images (pulled from `docker images` or pre-configured). User can also type a custom image name.
   - **Tools checklist** — all 14 tools listed with checkboxes. All checked by default. Each tool shows its name and one-line description.
   - **Workspace directories** — add/remove host paths with a mount name for each:
     - Host path: file picker or text input (e.g., `/home/andrew/projects/bond`)
     - Mount name: auto-derived from last path segment, editable (e.g., `bond`)
     - Read-only toggle
     - Multiple directories supported — each becomes `/workspace/{mount_name}` in the container
   - Max iterations slider (1–50, default 25)
   - Auto-RAG toggle + result count
3. **Edit agent** — same form, pre-populated
4. **Delete agent** — with confirmation (can't delete the default agent)
5. **Set as default** — one agent is always the default (handles the main chat)

#### How Workspace Mounts Work

When the agent's sandbox container starts, each workspace mount becomes a subdirectory:

```bash
# Agent "coder" has two workspace mounts:
#   /home/andrew/projects/bond → mount_name: "bond"
#   /home/andrew/projects/webapp → mount_name: "webapp"

docker run --rm \
  -v /home/andrew/projects/bond:/workspace/bond:rw \
  -v /home/andrew/projects/webapp:/workspace/webapp:rw \
  bond-sandbox-coding \
  ...

# Inside the container:
# /workspace/
# ├── bond/        ← /home/andrew/projects/bond
# └── webapp/      ← /home/andrew/projects/webapp
```

The agent sees a clean `/workspace` with named subdirectories. It can work across multiple projects. The host paths are validated on save (must exist, must be absolute).

For `sandbox_image: null` (host mode), workspace mounts are informational — `file_read`/`file_write` use the host paths directly, restricted to the listed directories (allowlist). This prevents the host-mode agent from accessing arbitrary files.

### 6.2 Sandbox Images

Sandbox images are Docker images available for agents to use. Bond provides a few prebuilt ones and users can add custom images.

#### Prebuilt Images

| Image | Contents | Network | Use Case |
|-------|----------|---------|----------|
| `bond-sandbox-coding` | Node 22, Python 3, git, build-essential | lan | General development |
| `bond-sandbox-research` | Chromium, curl | web | Web research |
| `bond-sandbox-data` | Python 3, pandas, matplotlib, numpy | off | Data analysis |
| `bond-sandbox-minimal` | (empty base) | off | Restricted execution |

Images are built from Dockerfiles in `docker/sandboxes/` and cached locally. Users can also reference any Docker image they have pulled.

#### Container Defaults

- **`persistent=true`** (default) — container stays alive between commands. Faster, retains state (installed packages, running processes). Cleaned up on session end or after idle timeout (configurable, default 30 minutes).
- Non-root user inside container
- `--security-opt=no-new-privileges`
- Resource limits from agent config or sandbox image defaults

### 6.3 Communication Channels (per agent)

Each agent has a set of enabled communication channels, configured via checkboxes in the agent edit form within the Settings UI.

#### Available Channels

| Channel | Description | Trust Level |
|---------|-------------|-------------|
| `webchat` | Local web UI (default, always available) | Trusted |
| `signal` | Signal messenger | Trusted |
| `telegram` | Telegram bot | Semi-trusted |
| `discord` | Discord bot | Untrusted |
| `whatsapp` | WhatsApp (via gateway) | Semi-trusted |
| `email` | Email (inbound) | Untrusted |
| `slack` | Slack bot | Semi-trusted |

#### Settings UI — Channel Checkboxes

In the agent edit form, a **Channels** section shows checkboxes:

```
Channels
  ☑ Webchat (always enabled for default agent)
  ☐ Signal
  ☐ Telegram         [Advanced ▾] Sandbox override: bond-sandbox-minimal
  ☐ Discord          [Advanced ▾] Sandbox override: bond-sandbox-minimal
  ☐ WhatsApp
  ☐ Email
  ☐ Slack
```

Each channel can optionally override the agent's sandbox image (e.g., enable Discord but force `bond-sandbox-minimal`). This is an advanced option — collapsed by default, expandable per channel.

#### Channel Routing

When a message arrives on a channel, Bond resolves which agent handles it:

1. Find agents that have this channel enabled
2. If exactly one → use that agent
3. If multiple → use the default agent (if it has the channel enabled), otherwise first match
4. If none → reject the message (channel not configured)

Different agents can handle different channels. Your "Bond" agent handles webchat and Signal, while a locked-down "Discord Bot" agent handles Discord with a minimal sandbox.

#### Default Agent Channel Setup

The seeded "Bond" agent starts with only `webchat` enabled. The user enables additional channels as they configure them (each channel requires its own setup — API tokens, bot registration, etc.).

### 6.4 Settings Table Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `sandbox.default_timeout` | int | 120 | Default code execution timeout (seconds) |
| `sandbox.idle_timeout` | int | 1800 | Seconds before idle persistent container is stopped |
| `sandbox.docker_available` | bool | (auto-detected) | Whether Docker is available |
| `search.api_key.brave` | string | null | Brave Search API key (encrypted) |

---

## 7. Module Structure

```
backend/app/
├── agent/
│   ├── loop.py              # Agent loop with tool-use cycle (UPGRADE)
│   ├── profiles.py          # Agent profile loading from DB
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
├── api/v1/
│   ├── agents.py            # Agent CRUD API (for Settings UI)
│   └── ...
├── sandbox/
│   ├── __init__.py           # Currently empty, gets real code
│   ├── manager.py            # SandboxManager — Docker lifecycle + persistent containers
│   ├── docker_client.py      # Docker client wrapper (docker-py)
│   └── host.py               # Host execution for sandbox_image: null
```

---

## 8. Implementation Stories

### Story 11a: Migration 000005 — Agent Profiles
- Create migration `000005_agents.up.sql` with `agents`, `agent_workspace_mounts`, and `agent_channels` tables
- Seed default "bond" agent (no sandbox, all tools, `is_default=true`, webchat channel enabled)
- Down migration drops all three tables
- Tests: migration up/down, default agent seeded, webchat channel seeded

### Story 11b: Agent CRUD API + Settings UI
- Create `backend/app/api/v1/agents.py` — full CRUD for agents and workspace mounts
  - `GET /api/v1/agents` — list all agents
  - `GET /api/v1/agents/{id}` — get agent with workspace mounts
  - `POST /api/v1/agents` — create agent
  - `PUT /api/v1/agents/{id}` — update agent
  - `DELETE /api/v1/agents/{id}` — delete (can't delete default)
  - `POST /api/v1/agents/{id}/default` — set as default
  - `GET /api/v1/agents/tools` — list all available tools with descriptions
  - `GET /api/v1/agents/sandbox-images` — list available Docker images
- Create `frontend/src/app/settings/agents/page.tsx` — agent management UI
  - Agent list with cards
  - Create/edit form: name, system prompt, model dropdown, sandbox image, tool checkboxes, workspace mounts, channel checkboxes (with optional sandbox override per channel), max iterations, auto-RAG
  - Delete with confirmation
  - Set as default
  - Channel CRUD: `POST/DELETE /api/v1/agents/{id}/channels`
- Tests: all CRUD endpoints, validation, default agent protection

### Story 11c: Tool Registry & Definitions
- Create `backend/app/agent/tools/definitions.py` with all 14 tool JSON schemas
- Create `backend/app/agent/tools/__init__.py` with a `ToolRegistry` that maps tool names to handlers
- Tool filtering based on agent's `tools` list from DB
- Tests: tool definition validation, registry lookup, filtering

### Story 11d: Agent Loop Upgrade
- Upgrade `backend/app/agent/loop.py` to a tool-use loop
- Load agent config from DB (system prompt, model, tools, max_iterations, auto_rag)
- Auto-RAG: search knowledge store with user message before each turn
- Iterate: LLM call → tool execution → append results → repeat until `respond` or max iterations
- Handle both streaming and non-streaming modes
- Tests: mock LLM returning tool calls, verify loop execution, max iteration safety

### Story 11e: Core Tool Handlers (respond, search, memory)
- Implement `respond`, `search_memory`, `memory_save`, `memory_update` handlers
- Wire to existing mediator commands (already built in Story 10)
- Tests: each handler with mock mediator

### Story 11f: File Tool Handlers
- Implement `file_read` and `file_write` with path traversal prevention
- Workspace allowlist from `agent_workspace_mounts` — only allowed directories
- Host mode: direct filesystem access within allowlist
- Docker mode: paths map to `/workspace/{mount_name}/...`
- Tests: read/write in workspace, path traversal rejection, allowlist enforcement

### Story 11g: SandboxManager — Docker Code Execution
- Implement `SandboxManager` with persistent container lifecycle
- `get_or_create_container()` — find running container for agent or create new one
- Workspace mounts applied from agent config
- `execute()` — `docker exec` into persistent container
- `cleanup_idle()` — stop containers idle beyond threshold
- `code_execute` tool handler
- Graceful degradation when Docker unavailable
- Tests: mock Docker client, container lifecycle, workspace mount generation

### Story 11h: Host Execution (sandbox_image: null)
- Implement `HostExecutor` for agents without a sandbox image
- Subprocess-based code execution with timeout, stdout/stderr capture
- Workspace allowlist enforcement
- Security: timeout, audit logging
- Tests: execute Python/bash, timeout enforcement, allowlist enforcement

### Story 11i: Stub Tool Handlers
- Implement stubs for `web_search`, `browser`, `email`, `cron`, `notify`, `call_subordinate`, `skills`
- Stubs return clear "not yet configured" or "coming in Phase N" messages
- Tests: each stub returns appropriate message

### Story 11j: Integration Test — Full Agent Turn with Tools
- End-to-end test: user sends message → agent loaded from DB → auto-RAG → LLM returns tool calls → tools execute → final response
- Test with mock LLM that returns a `search_memory` call followed by `respond`
- Test max iteration safety
- Test tool filtering by agent config

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
