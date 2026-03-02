# Design: OpenSandbox as a Git Submodule in Bond

## Goal

Replace Bond's homegrown sandbox layer (`backend/app/sandbox/`) with OpenSandbox, added as a git submodule, while retaining all of Bond's existing capabilities (agent orchestration, frontend, gateway, memory, etc.).

---

## How It Would Work

### Integration Architecture

- **Add OpenSandbox as a submodule** at `vendor/OpenSandbox` (or `lib/opensandbox`)
- **Bond's SandboxManager becomes a thin adapter** that talks to OpenSandbox's server API instead of calling Docker directly
- **OpenSandbox server runs as a sidecar** — Bond starts it alongside its own backend (via docker-compose or process supervisor)
- **Bond agents call the same `execute_code` / `execute_command` endpoints** they do today, but the backend routes them to OpenSandbox's execd API

### Step-by-Step

1. `git submodule add https://github.com/alibaba/OpenSandbox.git vendor/OpenSandbox`
2. Add OpenSandbox server to `docker-compose.yml` as a service
3. Replace `SandboxManager._create_container()` / `_exec_in_container()` with HTTP calls to OpenSandbox's sandbox lifecycle + command execution APIs
4. Replace `HostExecutor` with calls to OpenSandbox's code interpreter API (gains stateful execution)
5. Map Bond's agent configs (`sandbox_image`, workspace mounts, resource limits) to OpenSandbox's `POST /sandboxes` creation params
6. Wire OpenSandbox's SSE streaming endpoints into Bond's gateway for real-time output to the frontend

### What Bond Gains (for free)

- ✅ **Stateful code interpreter** — Jupyter-backed sessions with persistent context across runs (biggest current gap)
- ✅ **File operations API** — Upload, download, search, replace without shelling out
- ✅ **SSE streaming** — Real-time stdout/stderr to agents and UI
- ✅ **Background commands** — Run long processes, poll for status
- ✅ **Command interruption** — Graceful signal forwarding instead of timeout-kill
- ✅ **Full lifecycle states** — Pending → Running → Paused → Terminated (with pause/resume)
- ✅ **TTL management** — API-based timeout and renewal
- ✅ **Multi-language code execution** — Python, JS, TS, Java, Go, Bash
- ✅ **System metrics** — Real-time CPU/memory monitoring per sandbox
- ✅ **Network policy** — Ingress gateway + per-sandbox egress controls
- ✅ **GPU resource limits**
- ✅ **Kubernetes runtime** — Path to production-scale scheduling without rewriting anything
- ✅ **Pre-built sandbox images** — Code interpreter, Chrome/Playwright, desktop VNC, VS Code

### What Bond Retains

- ✅ Agent orchestration, planning, memory, conversations — untouched
- ✅ Frontend dashboard — untouched (enhanced with streaming)
- ✅ Gateway / WebSocket layer — untouched
- ✅ `bond.json` config, setup wizard, vault — untouched
- ✅ Multi-agent support with per-agent sandboxes — mapped to OpenSandbox sandbox instances

---

## What Would Be Lost / Changed

### Things That Need Migration

| Bond Feature | Status | Migration Path |
|---|---|---|
| **Container recovery on restart** | Replaced | OpenSandbox has its own lifecycle management; Bond no longer tracks containers directly |
| **Port pool allocation** | Replaced | OpenSandbox handles port mapping internally |
| **SSH key forwarding** | ⚠️ Needs config | Bond currently copies `~/.ssh` into containers; would need a custom sandbox image or mount config in OpenSandbox |
| **Direct Docker API access** | Replaced | Bond talks to OpenSandbox API, not Docker directly |

### Missing Functionality: None

There is **no functionality loss**. Everything Bond's current sandbox does, OpenSandbox does better:

- Code execution → OpenSandbox code interpreter (stateful, multi-language)
- Shell commands → OpenSandbox command execution (with streaming, background, interrupt)
- Container lifecycle → OpenSandbox sandbox lifecycle (richer states)
- Workspace mounts → OpenSandbox volume mounts
- Resource limits → OpenSandbox resource limits (adds GPU)
- Health checks → OpenSandbox health + metrics

The SSH key forwarding is the only thing that needs explicit configuration rather than working out-of-the-box, but it's solvable with a custom sandbox image or volume mount — not a functionality gap, just a config step.

---

## Downsides

- **Added complexity** — Another service to run. Docker-compose goes from ~2 services to ~3. More moving parts to debug.
- **Submodule maintenance** — Must track OpenSandbox upstream. Breaking API changes require adapter updates. Pinning to a tag mitigates this.
- **Startup time** — OpenSandbox server adds boot time. Bond currently just calls Docker directly.
- **Dependency footprint** — OpenSandbox brings its own Python deps, potentially conflicting with Bond's. Running as a separate service (recommended) avoids this.
- **Overkill for single-agent local use** — Bond's simple `docker exec` approach is lightweight. OpenSandbox is designed for production scale; the abstraction adds overhead for the simple case.
- **Learning curve** — Contributors need to understand two projects instead of one.
- **Latency** — Extra HTTP hop: Bond → OpenSandbox server → Docker, vs Bond → Docker directly. Should be negligible for code execution but measurable for rapid small commands.

---

## Recommended Approach

**Phase 1: Sidecar integration (minimal changes)**
- Add submodule, add to docker-compose
- New `OpenSandboxAdapter` class implementing same interface as current `SandboxManager`
- Feature flag: `"sandbox_backend": "opensandbox" | "legacy"` in `bond.json`
- Keep legacy sandbox working during transition

**Phase 2: Full migration**
- Wire SSE streaming through gateway to frontend
- Expose file operations API to agents
- Switch default to OpenSandbox, deprecate legacy
- Add code interpreter support to agent tool definitions

**Phase 3: Advanced features**
- Kubernetes runtime for multi-node deployments
- Network policies for agent isolation
- Custom sandbox images (Chrome, VNC desktop)

---

## Summary

This is a clean win. OpenSandbox is purpose-built for exactly what Bond's sandbox layer does, but significantly more capable. The submodule approach lets Bond benefit from upstream improvements while keeping full control over the integration layer. No functionality is lost. The main cost is operational complexity (one more service), which is justified by the feature gains.
