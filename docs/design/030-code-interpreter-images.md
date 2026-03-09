# Design 030: Code Interpreter Agent Images

## Problem

Bond agent containers can write code but can't run or test it in a structured way. The current `SandboxManager.execute()` method does raw `docker exec python3 -c <code>` or `docker exec sh -c <code>` — no persistent state between executions, no structured output, no support for running test suites and getting pass/fail results.

OpenSandbox (vendored at `vendor/OpenSandbox`) solves this with a Jupyter-backed code interpreter pattern. The question: can we adopt this by changing the base image, or does it require larger architectural changes?

## Answer: Both — but the base image change is small

### What we can do with just a base image change

Replace `python:3.12-slim` in `Dockerfile.agent` with a Bond-specific base that includes:
- **execd** (OpenSandbox's execution daemon) — handles command execution, file operations, and code interpreter over HTTP
- **Jupyter + ipykernel** — stateful code execution with persistent variables across calls
- **Language runtimes** — already covered by our per-stack images (Dockerfile.python, Dockerfile.node, Dockerfile.dotnet)

This gets us a code interpreter *inside* the container. The agent worker (or the host backend) can hit `localhost:44772/code` inside the container to execute code with stateful context.

### What requires backend changes

The `OpenSandboxAdapter` (already written, feature-flagged in `bond.json` as `"sandbox_backend": "opensandbox"`) needs to be wired into the agent tool loop. Specifically:

1. **`handle_code_execute` in `tools/code.py`** must route through the adapter when `sandbox_backend == "opensandbox"`
2. **Stateful contexts** — the adapter's `create_code_context()` / `execute_code()` support persistent sessions, but nothing in the agent loop creates or manages contexts yet
3. **Test execution** — agents need a tool that runs `npm test`, `pytest`, etc. and parses structured results (exit code + stdout/stderr is the minimum; TAP/JUnit parsing is nice-to-have)

## Architecture

### Option A: Hybrid Image (Recommended)

Keep `bond-agent-worker` as the base but add execd + Jupyter as an optional layer. Each stack image extends it:

```
python:3.12-slim
  └── bond-agent-worker:latest        (worker + Python deps + entrypoint)
       ├── bond-agent-python:latest   (+ Node, Playwright, pytest, ipykernel)
       ├── bond-agent-node:latest     (+ Node 22, Bun, pnpm, tsx)
       ├── bond-agent-dotnet:latest   (+ .NET 8/9, Node 22, Bun)
       └── bond-agent-ci:latest       (NEW: + execd + Jupyter + all stack kernels)
```

The new `bond-agent-ci` image would:
- Install execd from the OpenSandbox vendor directory
- Install Jupyter + ipykernel (Python) + tslab (Node/TS)
- Run execd as a sidecar process alongside the worker (via the entrypoint script)
- Expose code interpreter on an internal port (44772)

### Option B: OpenSandbox Base Image

Rebase onto `opensandbox/code-interpreter:v1.0.1` and layer Bond's worker on top:

```
ubuntu:24.04
  └── opensandbox/code-interpreter-base:latest  (multi-lang runtimes)
       └── opensandbox/code-interpreter:v1.0.1  (+ Jupyter kernels)
            └── bond-agent-worker-os:latest     (+ Bond worker + deps)
```

**Pro:** Full OpenSandbox code interpreter out of the box.
**Con:** Much larger image (~2GB vs ~500MB), includes Go/Java/multi-Python we may not need, harder to customize, ties us to their release cycle.

### Option C: Sidecar Container

Don't change the base image at all. Run OpenSandbox as a separate container (already supported by `OpenSandboxAdapter`). Bond's backend creates sandboxes via the OpenSandbox server API.

**Pro:** Zero image changes, clean separation.
**Con:** Extra service to manage, more latency, doesn't solve "agent tests its own code" since the code is in one container and the interpreter is in another.

## Recommendation: Option A, phased

### Phase 1: Add execd to stack images (this PR)

Add the OpenSandbox `execd` binary to each stack-specific image. This gives every agent container:
- `POST /command` — structured command execution with SSE streaming
- `POST /files/upload`, `GET /files/download` — file operations over HTTP
- No Jupyter yet — just better command execution

Changes:
- Copy `execd` binary from `vendor/OpenSandbox/components/execd` into images
- Update `agent-entrypoint.sh` to start execd in the background
- Update `OpenSandboxAdapter` to talk to execd on `localhost:44772` inside the container

### Phase 2: Add code interpreter to Python and Node images

Install Jupyter + language-specific kernels:
- `bond-agent-python`: ipykernel (Python REPL with state)
- `bond-agent-node`: tslab (TypeScript/JS REPL with state)

Changes:
- Install Jupyter + kernels in stack Dockerfiles
- Update entrypoint to start Jupyter alongside execd + worker
- Add `run_tests` agent tool that executes test commands and returns structured results
- Wire `tools/code.py` to use code interpreter when available

### Phase 3: Unified CI image

Create `bond-agent-ci` with all stacks + kernels for agents that need to work across languages.

## Implementation Details

### execd Integration

The execd binary is a Go static binary (~15MB) that provides:
```
POST   /command          — execute shell command (SSE streaming)
DELETE /command?id=X     — interrupt running command
GET    /command/status/X — poll command status
POST   /files/upload     — upload file
GET    /files/download   — download file
GET    /files/info       — file metadata
GET    /files/search     — glob search
DELETE /files            — delete files
POST   /code             — execute code (requires Jupyter)
POST   /code/context     — create stateful session
```

### Entrypoint Changes

```bash
#!/bin/bash
# Start execd in background (if present)
if [ -x /opt/opensandbox/execd ]; then
    /opt/opensandbox/execd --port 44772 &
fi

# Start Jupyter in background (if present)
if command -v jupyter &>/dev/null; then
    jupyter notebook --ip=127.0.0.1 --port=44771 \
        --allow-root --no-browser \
        --NotebookApp.token="${JUPYTER_TOKEN:-bond}" &
fi

# Start worker (existing behavior)
exec python -m backend.app.worker "$@"
```

### Backend Routing

```python
# tools/code.py — updated
async def handle_code_execute(arguments, context):
    backend = get_sandbox_backend()

    if backend == "opensandbox":
        adapter = get_opensandbox_adapter()
        sandbox_id = context.get("sandbox_id") or context.get("container_id")
        return await adapter.execute_code(
            sandbox_id,
            arguments.get("language", "python"),
            arguments.get("code", ""),
            context_id=context.get("code_context_id"),
        )
    else:
        # Legacy path (existing behavior)
        ...
```

### New Agent Tool: `run_tests`

```json
{
    "name": "run_tests",
    "description": "Run the project's test suite and return results",
    "parameters": {
        "command": "pytest tests/ -v",
        "timeout": 120,
        "working_directory": "/workspace/project"
    }
}
```

This is really just `execute_command` with a longer timeout and structured result parsing, but naming it explicitly helps agents understand when to use it.

## Image Size Impact

| Image | Current | + execd | + execd + Jupyter |
|-------|---------|---------|-------------------|
| bond-agent-worker | ~350MB | ~365MB | N/A (base only) |
| bond-agent-python | ~1.1GB | ~1.1GB | ~1.3GB |
| bond-agent-node | ~650MB | ~665MB | ~850MB |
| bond-agent-dotnet | ~1.5GB | ~1.5GB | ~1.7GB |

execd adds ~15MB. Jupyter + one kernel adds ~150-200MB.

## Migration Path

1. Add execd to images (backward-compatible, existing agents unaffected)
2. Set `"sandbox_backend": "opensandbox"` in `bond.json` to opt in
3. Old `docker exec` path continues to work for agents not using the new backend
4. Once stable, flip default and deprecate legacy `SandboxManager.execute()`

## Open Questions

- [ ] Should execd run on a fixed port or should the worker discover it?
- [ ] Do we need Jupyter for test execution, or is `execd /command` sufficient?
- [ ] Should code contexts persist across agent turns, or reset per conversation?
