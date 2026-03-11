# Design Doc 037: Coding Agent Skill

**Status:** Draft  
**Author:** Developer Agent  
**Date:** 2026-03-11  
**Depends on:** 003-agent-tools-and-sandbox, 008-containerized-agent-runtime, 013-opensandbox-submodule, 035-secure-agent-execution-architecture

---

## 1. Problem Statement

Bond agents can execute code snippets and shell commands (`code.py`, `files.py`), but they cannot delegate complex, multi-step coding tasks to a specialized sub-agent that iterates on a codebase — exploring files, writing code, running tests, and committing. Today's `subordinate.py` is a stub ("coming in Phase 2") and `skills.py` says "coming in Phase 3."

Additionally, the current **interrupt/stop mechanism has a critical UX gap**: when a user clicks "Stop" during an agent turn, the stop doesn't take effect until the current iteration of the agent loop completes (which could be a long LLM call or a slow tool execution). The user cannot inject additional context mid-turn — sending a new message only queues it for *after* the current turn finishes.

This design addresses both:
1. **Coding Agent Skill** — letting Bond spawn and manage coding sub-agents (Claude Code, Codex, Pi, etc.)
2. **Responsive Interruption** — making stop/pause/inject-context actually responsive during a running turn

---

## 2. Goals

| # | Goal | Metric |
|---|------|--------|
| G1 | Bond agent can delegate coding tasks to a sub-agent (Claude Code, Codex, Pi) | Sub-agent completes task, commits to branch |
| G2 | User can stop/pause a running agent within 2 seconds | Stop button → agent halts within 2s regardless of LLM call state |
| G3 | User can inject additional context mid-turn without waiting for loop completion | New message appears in agent context on next iteration, not after full turn |
| G4 | Sub-agent progress is visible in real-time via SSE | Tool activity feed shows sub-agent stdout streaming |
| G5 | Sub-agent runs sandboxed (container or permission-scoped) | No unrestricted host access unless explicitly configured |

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (Next.js)                │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │  Stop Button  │  │ Context Input│  │ Activity  │ │
│  │  (immediate)  │  │ (mid-turn)   │  │ Feed      │ │
│  └──────┬───────┘  └──────┬───────┘  └─────▲─────┘ │
└─────────┼─────────────────┼────────────────┼────────┘
          │ WS: interrupt   │ WS: inject     │ SSE: tool_call
          ▼                 ▼                │
┌─────────────────────────────────────────────────────┐
│                  Gateway (TypeScript)                │
│  ┌─────────────────────────────────────────────────┐│
│  │ webchat.ts: handleInterrupt / handleInject      ││
│  │            → worker-client.ts                   ││
│  └─────────────────────────────────────────────────┘│
└─────────────────────┬───────────────────────────────┘
                      │ HTTP: /interrupt, /inject
                      ▼
┌─────────────────────────────────────────────────────┐
│                 Worker (Python/FastAPI)              │
│  ┌────────────────┐  ┌────────────────────────────┐ │
│  │ Interrupt       │  │ Agent Loop                 │ │
│  │ Controller      │  │  ┌──────────────────────┐  │ │
│  │ (asyncio.Event  │  │  │ coding_agent tool     │  │ │
│  │  + cancel scope)│  │  │ → spawn process       │  │ │
│  │                 │  │  │ → stream stdout/stderr│  │ │
│  │                 │  │  │ → kill on interrupt   │  │ │
│  └────────────────┘  │  └──────────────────────┘  │ │
│                       └────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

---

## 4. Part A: Coding Agent Skill

### 4.1 New Tool: `coding_agent`

A new tool handler at `backend/app/agent/tools/coding_agent.py`.

**Tool Schema:**

```json
{
  "name": "coding_agent",
  "description": "Spawn a coding sub-agent to perform complex coding tasks. The sub-agent will have access to the specified working directory and can read/write files, run commands, and commit changes. Use for tasks that require multi-step file exploration, writing code across multiple files, running tests, and iterating.",
  "parameters": {
    "type": "object",
    "properties": {
      "task": {
        "type": "string",
        "description": "Detailed description of the coding task. Include: what to build/fix, acceptance criteria, files to focus on, and any constraints."
      },
      "working_directory": {
        "type": "string",
        "description": "Absolute path to the project root. The sub-agent will be scoped to this directory."
      },
      "agent_type": {
        "type": "string",
        "enum": ["claude", "codex", "pi"],
        "description": "Which coding agent to use. Defaults to 'claude' if not specified.",
        "default": "claude"
      },
      "branch": {
        "type": "string",
        "description": "Git branch to create/checkout before starting. Optional."
      },
      "timeout_minutes": {
        "type": "integer",
        "description": "Maximum time the sub-agent can run. Default: 30.",
        "default": 30
      }
    },
    "required": ["task", "working_directory"]
  }
}
```

### 4.2 Implementation: `coding_agent.py`

```python
"""Coding agent tool — spawns a coding sub-agent process."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger("bond.agent.tools.coding_agent")

# Agent command templates
AGENT_COMMANDS = {
    "claude": {
        "binary": "claude",
        "args": ["--dangerously-skip-permissions", "--print"],
        "needs_pty": False,
    },
    "codex": {
        "binary": "codex",
        "args": ["exec", "--full-auto"],
        "needs_pty": True,
    },
    "pi": {
        "binary": "pi",
        "args": ["-p"],
        "needs_pty": True,
    },
}


class CodingAgentProcess:
    """Manages a coding sub-agent subprocess."""

    def __init__(
        self,
        agent_type: str,
        task: str,
        working_directory: str,
        timeout_minutes: int = 30,
    ):
        self.agent_type = agent_type
        self.task = task
        self.working_directory = working_directory
        self.timeout = timeout_minutes * 60
        self.process: asyncio.subprocess.Process | None = None
        self.output_lines: list[str] = []
        self.start_time: float = 0
        self._killed = False

    async def start(self) -> None:
        config = AGENT_COMMANDS.get(self.agent_type)
        if not config:
            raise ValueError(f"Unknown agent type: {self.agent_type}")

        binary = shutil.which(config["binary"])
        if not binary:
            raise FileNotFoundError(
                f"{config['binary']} not found in PATH. "
                f"Install it or choose a different agent_type."
            )

        cmd = [binary] + config["args"] + [self.task]
        logger.info(
            "Spawning %s in %s (timeout=%ds)",
            self.agent_type, self.working_directory, self.timeout,
        )

        self.start_time = time.monotonic()
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.working_directory,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"},
        )

    async def stream_output(self) -> AsyncIterator[str]:
        """Yield lines as the sub-agent produces them."""
        if not self.process or not self.process.stdout:
            return
        async for raw_line in self.process.stdout:
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            self.output_lines.append(line)
            yield line

    async def wait(self) -> int:
        """Wait for process with timeout. Returns exit code."""
        if not self.process:
            return -1
        try:
            return await asyncio.wait_for(
                self.process.wait(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            logger.warning("Coding agent timed out after %ds", self.timeout)
            await self.kill()
            return -1

    async def kill(self) -> None:
        if self.process and not self._killed:
            self._killed = True
            try:
                self.process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self.process.kill()
            except ProcessLookupError:
                pass

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start_time if self.start_time else 0

    def get_output(self, last_n: int = 200) -> str:
        return "\n".join(self.output_lines[-last_n:])


# Global registry of active coding agent processes (per-agent-id)
_active_processes: dict[str, CodingAgentProcess] = {}


async def handle_coding_agent(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Spawn and run a coding sub-agent."""
    task = arguments.get("task", "")
    working_dir = arguments.get("working_directory", "")
    agent_type = arguments.get("agent_type", "claude")
    branch = arguments.get("branch")
    timeout_minutes = arguments.get("timeout_minutes", 30)
    agent_id = context.get("agent_id", "default")

    # Validate working directory
    if not Path(working_dir).is_dir():
        return {"error": f"Directory not found: {working_dir}"}

    # Kill any existing process for this agent
    if agent_id in _active_processes:
        await _active_processes[agent_id].kill()

    # Optional: checkout branch
    if branch:
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", "-B", branch,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {"error": f"Git checkout failed: {stderr.decode()}"}

    # Start the coding agent
    cap = CodingAgentProcess(agent_type, task, working_dir, timeout_minutes)
    _active_processes[agent_id] = cap

    try:
        await cap.start()
    except (FileNotFoundError, ValueError) as e:
        _active_processes.pop(agent_id, None)
        return {"error": str(e)}

    # Stream output, emitting progress via event_queue if available
    event_queue = context.get("event_queue")
    output_chunks: list[str] = []

    async def _stream():
        async for line in cap.stream_output():
            output_chunks.append(line)
            if event_queue and len(output_chunks) % 5 == 0:
                await event_queue.put({
                    "event": "tool_call",
                    "data": {
                        "tool": "coding_agent",
                        "summary": f"[{agent_type}] {line[:120]}",
                    },
                })

    # Run streaming and timeout concurrently
    stream_task = asyncio.create_task(_stream())
    exit_code = await cap.wait()
    await stream_task

    _active_processes.pop(agent_id, None)

    output = cap.get_output(last_n=300)
    elapsed = cap.elapsed

    return {
        "status": "completed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "agent_type": agent_type,
        "working_directory": working_dir,
        "elapsed_seconds": round(elapsed, 1),
        "output": output,
    }


async def kill_coding_agent(agent_id: str) -> bool:
    """Kill an active coding agent. Called by interrupt handler."""
    if agent_id in _active_processes:
        await _active_processes[agent_id].kill()
        _active_processes.pop(agent_id, None)
        return True
    return False
```

### 4.3 Tool Registration

In `definitions.py`, add the `coding_agent` tool schema.

In `native_registry.py`, register:
```python
from backend.app.agent.tools.coding_agent import handle_coding_agent
TOOL_HANDLERS["coding_agent"] = handle_coding_agent
```

### 4.4 Container Setup: Coding Agents in Docker

The coding agent binaries (claude, codex, pi) must be **baked into the container images**. This is the primary execution mode — host mode is a secondary option for development only.

#### 4.4.1 Non-Root Requirement (Critical)

Claude Code **cannot run as root** when using `--dangerously-skip-permissions`. It refuses to start. The container must run as a non-root user.

This is a breaking change from the current `Dockerfile.agent` which runs everything as root. All agent worker images need a non-root user.

Reference implementation: `C:\dev\ai\claude-code-sandbox\docker\` — the `Dockerfile` and `Dockerfile.claudecode` containers demonstrate the pattern with a `claude` user.

#### 4.4.2 Base Image Changes (`Dockerfile.agent`)

Add to the base agent worker image:

```dockerfile
# --- Non-root user ---
# Claude Code refuses --dangerously-skip-permissions as root.
# All coding agent CLIs work better as a regular user.
RUN useradd -m -s /bin/bash bond-agent && \
    echo 'bond-agent ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers && \
    usermod -aG sudo bond-agent

# --- Coding Agent CLIs ---

# Claude Code (requires Node.js — already in node/python/dotnet images,
# but must also be in the base image for the coding_agent tool)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    npm install -g @anthropic-ai/claude-code@latest

# OpenAI Codex CLI
RUN npm install -g @openai/codex@latest

# Pi (Anthropic's lightweight agent — npm package)
# TODO: Replace with actual package name once Pi is publicly released.
#       For now, skip if not available.
# RUN npm install -g @anthropic-ai/pi@latest || true

# Pre-create Claude Code config directory so it doesn't fail on first run
RUN mkdir -p /home/bond-agent/.claude && \
    chown -R bond-agent:bond-agent /home/bond-agent

# Ensure workspace is owned by bond-agent
RUN chown -R bond-agent:bond-agent /workspace /data
```

#### 4.4.3 Entrypoint Changes (`agent-entrypoint.sh`)

The entrypoint currently runs as root and execs the worker as root. It needs to:
1. Do privileged setup (SSH keys, git config) as root
2. Fix permissions on mounted volumes
3. Drop to `bond-agent` user via `exec gosu bond-agent ...`

```bash
# At the end of agent-entrypoint.sh, replace:
#   exec python -m backend.app.worker "$@"
# With:
# Fix ownership of runtime dirs that may be mounted from host
chown -R bond-agent:bond-agent /data /workspace /config 2>/dev/null || true

# Copy git/ssh config to bond-agent user
cp -r /root/.ssh /home/bond-agent/.ssh 2>/dev/null || true
cp /root/.gitconfig /home/bond-agent/.gitconfig 2>/dev/null || true
chown -R bond-agent:bond-agent /home/bond-agent/.ssh /home/bond-agent/.gitconfig 2>/dev/null || true

# Drop privileges and exec worker
exec gosu bond-agent python -m backend.app.worker "$@"
```

Add `gosu` to the base image apt-get install list:
```dockerfile
RUN apt-get install -y --no-install-recommends git curl openssh-client gosu sudo && \
```

#### 4.4.4 Variant Image Changes

Each variant image (`Dockerfile.node`, `Dockerfile.python`, `Dockerfile.dotnet`) inherits from `bond-agent-worker:latest` and already has Node.js. They get Claude Code and Codex for free from the base image. No changes needed in the variants unless they override `USER` (currently they don't — they stay root and rely on the entrypoint to drop privileges).

#### 4.4.5 API Key Passthrough

Coding agent CLIs need API keys. These are passed via environment variables at container start:

| CLI | Required Env Var |
|-----|-----------------|
| Claude Code | `ANTHROPIC_API_KEY` |
| Codex | `OPENAI_API_KEY` |
| Pi | `ANTHROPIC_API_KEY` (shared with Claude Code) |

The `coding_agent.py` tool should validate the required env var exists before spawning:

```python
REQUIRED_ENV = {
    "claude": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
    "pi": "ANTHROPIC_API_KEY",
}

async def handle_coding_agent(arguments, context):
    agent_type = arguments.get("agent_type", "claude")
    env_var = REQUIRED_ENV.get(agent_type)
    if env_var and not os.environ.get(env_var):
        return {"error": f"{agent_type} requires {env_var} to be set"}
    # ...
```

#### 4.4.6 Host Mode (Development Fallback)

For local development without Docker, the worker spawns coding agents directly on the host via `asyncio.create_subprocess_exec`. This is the same code path — the `CodingAgentProcess` class works identically in both modes. The only difference is where the binary lives.

Detection is implicit: if the binary is in PATH, it works. No mode flag needed.

### 4.5 PTY Handling for Codex/Pi

Codex and Pi need a PTY. Use `asyncio.create_subprocess_exec` with `pty` allocation:

```python
import pty
import os

if config["needs_pty"]:
    master_fd, slave_fd = pty.openpty()
    self.process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=self.working_directory,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env={**os.environ, "TERM": "xterm-256color"},
    )
    os.close(slave_fd)
    # Read from master_fd using asyncio
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await asyncio.get_event_loop().connect_read_pipe(
        lambda: protocol, os.fdopen(master_fd, "rb")
    )
    self._pty_reader = reader
    self._pty_transport = transport
```

---

## 5. Part B: Responsive Interruption

### 5.1 The Problem

Current interrupt flow:

```
User clicks Stop
  → Frontend sends WS { type: "interrupt" }
  → Gateway calls backend /interrupt or worker /interrupt
  → Worker sets interrupt_event
  → BUT: interrupt_event is only checked at the TOP of each loop iteration
  → If the agent is mid-LLM-call (could take 30-60s), nothing happens
  → User waits, frustrated
```

The `for _iteration in range(max_iterations)` loop checks `interrupt_event.is_set()` **once per iteration, at the top**. An LLM call or slow tool execution within an iteration blocks for the entire duration.

### 5.2 Solution: Cancellation Scope + Abort Controller

**Approach:** Use `asyncio.TaskGroup` (or manual task cancellation) to make the LLM call itself cancellable, and check for interrupts during tool execution.

#### 5.2.1 Worker Changes (`worker.py`)

```python
# New: Cancellable LLM call wrapper
async def _cancellable_llm_call(
    messages: list,
    model: str,
    tools: list,
    interrupt_event: asyncio.Event,
    **kwargs,
) -> dict | None:
    """Run LLM call, but abort if interrupt_event fires."""
    llm_task = asyncio.create_task(
        litellm.acompletion(model=model, messages=messages, tools=tools, **kwargs)
    )
    interrupt_task = asyncio.create_task(interrupt_event.wait())

    done, pending = await asyncio.wait(
        {llm_task, interrupt_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if llm_task in done:
        return llm_task.result()
    else:
        # Interrupted — LLM call was cancelled
        logger.info("LLM call interrupted by user")
        return None  # Caller handles graceful exit
```

Replace the current `litellm.acompletion` call in the agent loop with `_cancellable_llm_call`.

When the return is `None`, the loop should:
1. Check if there are `pending_messages` (user wants to inject context → append and continue)
2. If no pending messages (pure stop) → break the loop and emit `status: idle`
3. Kill any active coding agent sub-processes

#### 5.2.2 Tool Execution Cancellation

For long-running tools (especially `coding_agent`), register the running task so it can be killed:

```python
# In the tool execution section of the agent loop:
tool_task = asyncio.create_task(execute_tool(tool_call, context))
interrupt_task = asyncio.create_task(_state.interrupt_event.wait())

done, pending = await asyncio.wait(
    {tool_task, interrupt_task},
    return_when=asyncio.FIRST_COMPLETED,
)

for task in pending:
    task.cancel()

if tool_task in done:
    result = tool_task.result()
else:
    # Tool was interrupted
    await kill_coding_agent(agent_id)  # Kill sub-agent if running
    result = {"error": "Tool execution interrupted by user"}
    _state.interrupt_event.clear()
    # Check for injected context...
```

#### 5.2.3 Frontend: Immediate Stop Feedback

The frontend should optimistically show the agent as stopped:

```typescript
// page.tsx - handleStop
const handleStop = useCallback(() => {
  if (!wsRef.current?.connected || !conversationId) return;
  wsRef.current.interrupt(conversationId);
  setLoading(false);  // Immediately show as stopped
  // Add a "⏹ Stopped by user" system message
  setMessages(prev => [...prev, {
    role: "system",
    content: "Agent stopped by user.",
    status: "complete",
  }]);
}, [conversationId]);
```

### 5.3 New: Context Injection (Mid-Turn)

#### 5.3.1 New WebSocket Message Type: `inject`

```typescript
// Frontend ws.ts
inject(conversationId: string, content: string): void {
  if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
  this.ws.send(JSON.stringify({
    type: "inject",
    conversationId,
    content,
  }));
}
```

#### 5.3.2 Gateway: `handleInject`

```typescript
// webchat.ts
case "inject":
  await this.handleInject(socket, session.id, msg);
  break;

private async handleInject(
  socket: WebSocket,
  sessionId: string,
  msg: IncomingMessage,
): Promise<void> {
  const session = this.sessionManager.getSession(sessionId);
  if (!session) return;

  const conversationId = msg.conversationId || session.conversationId;
  if (!conversationId || !msg.content) return;

  try {
    // Call worker /interrupt with the new message
    await this.workerClient.interrupt([
      { role: "user", content: msg.content },
    ]);
    this.send(socket, {
      type: "injected",
      sessionId,
      conversationId,
      content: msg.content,
    });
  } catch (err) {
    this.send(socket, {
      type: "error",
      sessionId,
      error: err instanceof Error ? err.message : "Failed to inject context",
    });
  }
}
```

#### 5.3.3 Frontend UX: Input While Agent Is Running

Currently, when the agent is busy, the input area shows a "Stop" button. Change this to allow **both** stopping and sending additional context:

```
┌──────────────────────────────────────────────────────────┐
│  ┌──────────────────────────────────────────────┐  ┌──┐ │
│  │  Add context... (agent is working)           │  │⏹ │ │
│  └──────────────────────────────────────────────┘  └──┘ │
└──────────────────────────────────────────────────────────┘
       ↑ Still typeable                              ↑ Stop button
```

When the user types and presses Enter while the agent is running:
- Send a `type: "inject"` message (not a regular `type: "message"`)
- Display the injected message in the chat with a visual indicator (e.g., 💉 icon or "injected while agent was working" label)
- The agent receives it on its next interrupt check (within 2s thanks to Part B)

This replaces the current queue-and-wait behavior.

### 5.4 Updated Interrupt Flow (After)

```
User clicks Stop
  → Frontend sends WS { type: "interrupt" }
  → Gateway calls worker /interrupt (empty messages)
  → Worker sets interrupt_event
  → asyncio.wait detects event within current LLM call
  → LLM call cancelled, loop breaks
  → Agent status → idle (< 2 seconds)

User sends context mid-turn
  → Frontend sends WS { type: "inject", content: "..." }
  → Gateway calls worker /interrupt with [{ role: "user", content }]
  → Worker sets interrupt_event + adds to pending_messages
  → asyncio.wait detects event
  → LLM call cancelled, pending messages injected into context
  → Loop continues with new context
```

---

## 6. Database/Schema Changes

**None.** This feature uses:
- Existing `interrupt_event` / `pending_messages` mechanism (enhanced)
- Process management (in-memory, no persistence needed for sub-agents)
- Existing tool registration patterns

---

## 7. Migration Plan

### Phase 1: Responsive Interruption (1-2 days)
1. Implement `_cancellable_llm_call` in `worker.py`
2. Add interrupt checks around tool execution
3. Add `inject` WS message type to frontend/gateway
4. Update frontend input to allow typing while agent is busy
5. **Tests:** Unit test for cancellable LLM wrapper, integration test for inject flow

### Phase 2: Coding Agent Tool — MVP (2-3 days)
1. Update `Dockerfile.agent` — add non-root `bond-agent` user, install `gosu`, install Node.js + Claude Code + Codex CLIs
2. Update `agent-entrypoint.sh` — privilege drop via `gosu bond-agent`
3. Rebuild all variant images (`Dockerfile.node`, `Dockerfile.python`, `Dockerfile.dotnet`)
4. Implement `coding_agent.py` (Claude Code + Codex, running inside container as `bond-agent`)
5. Register tool in `definitions.py` and `native_registry.py`
6. SSE streaming of sub-agent output to activity feed
7. Sub-agent kill on interrupt
8. API key validation before spawn
9. **Tests:** Unit test for process lifecycle, integration test for task completion, container smoke test (build image, spawn Claude Code, verify non-root)

### Phase 3: Multi-Agent Support + Pi (1-2 days)
1. Add Pi support (once publicly available)
2. Add PTY handling for Codex/Pi
3. Agent type auto-selection based on binary availability
4. **Tests:** Per-agent-type spawn/kill tests

---

## 8. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Cancelling LLM mid-call loses partial response | Medium | Log partial response, include in context on resume |
| Sub-agent process leak (not cleaned up) | High | Timeout + atexit handler + periodic reaper |
| PTY handling complexity for Codex/Pi | Medium | Start with Claude Code (no PTY), add PTY support iteratively |
| Sub-agent writes to unexpected paths | High | Validate `working_directory` is under allowed paths; container isolation via non-root user |
| Race condition: inject arrives between iterations | Low | `asyncio.Event` is thread-safe; pending_messages list protected by GIL |
| `litellm.acompletion` doesn't cancel cleanly | Medium | The HTTP connection may linger; add explicit `httpx` client cancellation if needed |
| Non-root migration breaks existing containers | High | `gosu` privilege drop is additive — root still does setup, only the worker process runs as `bond-agent`. Existing volume mounts need `chown` in entrypoint. Test with existing agent configs before merging. |
| Claude Code as root is refused | Blocker | Non-root user is mandatory. Claude Code exits with error if `--dangerously-skip-permissions` is used as root. The `bond-agent` user resolves this. |
| Missing API keys at runtime | Medium | `coding_agent.py` validates required env vars before spawning. Clear error message tells user which key is missing. |
| Image size increase from Node.js + coding CLIs in base image | Low | ~200MB increase. Acceptable — coding agents are a core capability. Variant images that already have Node.js see minimal increase (just the npm packages). |

---

## 9. Testing Strategy

### Unit Tests
- `test_coding_agent.py`:
  - Spawn mock process, verify output collection
  - Timeout kills process
  - Kill cleans up
  - Unknown agent_type returns error
  - Missing binary returns error

- `test_cancellable_llm.py`:
  - Interrupt during LLM call returns None
  - Normal completion returns response
  - Pending messages preserved after interrupt

### Integration Tests
- Spawn a real Claude Code process on a test repo, verify it produces output
- Send inject message during a running turn, verify context appears
- Stop button during LLM call, verify < 2s response time

---

## 10. Open Questions

1. **Should the coding agent tool be synchronous (block the loop) or return immediately with a session ID?**
   - Synchronous: simpler, agent waits for result and acts on it
   - Async: agent can do other work, but needs a "check_coding_agent" tool to poll
   - **Recommendation:** Synchronous for MVP. The agent loop is already async internally; blocking the tool call is fine since the whole point is delegating a chunk of work.

2. **Should injected context restart the current LLM call or wait for the next iteration?**
   - Restart: more responsive, but wastes the partial LLM call
   - Next iteration: simpler, but user has to wait for current call to finish
   - **Recommendation:** Cancel + restart. The user injected context for a reason; they want the agent to see it *now*.

3. **Token budget for sub-agent output?**
   - Sub-agents can produce thousands of lines of output
   - Need to truncate/summarize before injecting into the parent agent's context
   - **Recommendation:** Keep last 300 lines, apply a 4000-token cap. If exceeded, summarize the middle and keep first 20 + last 100 lines.

4. **Permission model for coding agent?**
   - Which directories should be accessible?
   - Should there be a whitelist in agent settings?
   - **Recommendation:** For now, validate that `working_directory` exists and is under a configurable `allowed_workspace_roots` list in bond.json. Default: `["/home", "/workspace", "/tmp"]`.

---

## 11. Appendix: Reference Implementations

### Container Non-Root Pattern (claude-code-sandbox)

The `C:\dev\ai\claude-code-sandbox\docker\` directory contains working examples:

| File | Key Pattern |
|------|-------------|
| `Dockerfile` | Base image: Ubuntu 22.04, Node.js 22, `claude` user with sudo, `npm install -g @anthropic-ai/claude-code@latest`, git wrapper for branch protection |
| `Dockerfile.claudecode` | Extends base with zsh, bun, delta, tmux; runs as `node` user; creates `/workspace/.claude` |
| `Dockerfile.python` | Python 3.11-slim base with Node.js for Claude Code; `claude` user; Playwright pre-installed |
| `Dockerfile.dotnet` | Extends base with .NET 8+9 SDKs; runs as `node` user |

Key takeaways from the reference:
1. **Always create a non-root user** (`claude` or `node`) — Claude Code refuses `--dangerously-skip-permissions` as root
2. **`npm install -g @anthropic-ai/claude-code@latest`** — installs globally while still root, available to non-root user
3. **Pre-create `.claude` config dir** and `chown` to the user
4. **Entrypoint runs as root** for SSH/git setup, then the actual work happens as the non-root user
5. **`NO_COLOR=1` and `TERM=dumb`** — suppress ANSI when capturing output programmatically

## 12. Appendix: Current Code Pointers

| Component | File | Notes |
|-----------|------|-------|
| Agent loop | `backend/app/worker.py:1096` | `for _iteration in range(max_iterations)` — interrupt check at top |
| Interrupt endpoint | `backend/app/worker.py:314` | `/interrupt` POST, sets event + pending messages |
| Interrupt event | `backend/app/worker.py:229` | `self.interrupt_event: asyncio.Event` |
| Tool dispatch | `backend/app/agent/tools/native_registry.py` | `TOOL_HANDLERS` dict |
| Tool definitions | `backend/app/agent/tools/definitions.py` | JSON schemas |
| Subordinate stub | `backend/app/agent/tools/subordinate.py` | "coming in Phase 2" |
| Skills stub | `backend/app/agent/tools/skills.py` | "coming in Phase 3" |
| WS interrupt (FE) | `frontend/src/lib/ws.ts:297` | `interrupt()` and `pause()` methods |
| WS handler (GW) | `gateway/src/channels/webchat.ts:105` | `case "interrupt"` / `case "pause"` |
| Stop button (FE) | `frontend/src/app/page.tsx:303` | `handleStop` callback |
| Backend interrupt | `gateway/src/backend/client.ts:211` | Calls backend `/api/v1/conversations/{id}/interrupt` |
| Worker interrupt | `gateway/src/backend/worker-client.ts:82` | Calls worker `/interrupt` directly |
| Message queueing | `gateway/src/channels/webchat.ts:147` | When `agentBusy`, queues instead of starting new turn |
