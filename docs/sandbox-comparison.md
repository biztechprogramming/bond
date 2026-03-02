# Bond vs OpenSandbox — Sandbox Comparison

A comparison of Bond's Docker sandbox (`backend/app/sandbox/`) against [OpenSandbox](https://github.com/alibaba/OpenSandbox) to identify missing functionality.

## Major Gaps

| Capability | Bond | OpenSandbox |
|---|---|---|
| **Code Interpreter (stateful)** | ❌ Runs `python3 -c` each time — no state between executions | ✅ Jupyter-backed sessions with persistent context across runs |
| **Multi-language support** | Python + shell only | Python, JavaScript, TypeScript, Java, Go, Bash |
| **File operations API** | ❌ Must shell out to `cp`, `cat`, etc. | ✅ Full CRUD: upload, download, search (glob), replace, permissions, move |
| **Streaming output (SSE)** | ❌ Blocks until completion | ✅ Real-time SSE streaming of stdout/stderr |
| **Background commands** | ❌ Not supported | ✅ Background execution with polling for status/output |
| **Command interruption** | ❌ Timeout kills only | ✅ Graceful interrupt via API (signal forwarding with process groups) |
| **Sandbox lifecycle states** | Running or dead | Pending → Running → Pausing → Paused → Stopping → Terminated |
| **Pause/Resume** | ❌ | ✅ Pause and resume sandbox execution |
| **TTL / Expiration renewal** | Idle cleanup only (1hr hardcoded) | ✅ Explicit timeout on creation + API-based TTL renewal |
| **Network policy (egress/ingress)** | ❌ No network controls | ✅ Ingress gateway + per-sandbox egress controls |
| **Resource limits (GPU)** | CPU + memory only | CPU + memory + GPU |
| **System metrics** | ❌ None | ✅ Real-time CPU/memory monitoring inside sandbox |
| **Multi-language SDKs** | Python only | Python, TypeScript/JS, Java/Kotlin, C#/.NET |
| **Kubernetes runtime** | ❌ Docker only | ✅ K8s controller with pool management, batch sandboxes, scheduling strategies |
| **API auth** | None | Token-based (`X-EXECD-ACCESS-TOKEN`, API key header) |

## Moderate Gaps

- **Sandbox images / environments**: OpenSandbox ships pre-built images for code-interpreter, Chrome/Playwright, desktop (VNC), VS Code. Bond just takes any Docker image with no specialized environments.
- **Chunked upload/download with resume**: OpenSandbox supports range requests and chunked file transfers. Bond has no file transfer API at all.
- **Batch sandboxes**: OpenSandbox's K8s runtime supports batch sandbox scheduling (multiple sandboxes as a unit). Bond is single-container per agent.

## What Bond Does Well

- **Container recovery after restart** — Handles Docker inspect + re-adopt of running containers when the backend restarts.
- **Port allocation** — Port pool approach with host-port verification works for single-host deployments.
- **Workspace mounts** — Flexible mount configuration with read-only support.
- **SSH key forwarding** — Copies `~/.ssh` into containers automatically; OpenSandbox doesn't by default.
- **Worker health checks** — Polls `/health` with timeout, diagnostics on failure, and container log capture.

## Highest-Impact Improvements

If closing the gap without adopting OpenSandbox wholesale, these would have the most practical impact:

1. **Stateful code execution** — Add a Jupyter kernel or persistent REPL so agents can incrementally build up computation across calls instead of losing all state each time.
2. **File operations API** — Direct file read/write/search endpoints to avoid shelling out for every file operation.
3. **SSE streaming** — Real-time output streaming so agents (and users) get feedback during long-running executions instead of blocking until completion.

## Summary

Bond's sandbox is essentially `docker run` + `docker exec` with lifecycle tracking. It works for simple agent code execution but lacks the stateful code interpreter, file operations API, streaming, and network controls that OpenSandbox provides. The biggest practical gap is stateful execution — Bond loses all state between code runs, which limits agents that need to incrementally build up work.
