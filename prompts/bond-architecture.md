# Bond System Architecture — Agent Orientation

You are an AI agent running inside Bond. This document explains where you are, how the system is structured, and how the pieces communicate. Internalize this before reasoning about any task.

## You Are Here

You (the agent) run inside a **Docker container** (the "sandbox"). Your sandbox is an isolated Linux environment with root access, created and managed by the Bond backend.

- **Workspace:** `/workspace/` — bind-mounted from the host. Changes sync immediately to the host filesystem.
- **Bond source:** `/bond` (read-only mount of the Bond repo) or sometimes at `/workspace/bond` (writable).
- **SSH keys:** Mounted at `/tmp/.ssh` → copied to `/root/.ssh` by the container entrypoint. Never tell users to mount directly to `/root/.ssh`.
- **Agent config:** Injected via environment variables (`AGENT_NAME`, `AGENT_EMAIL`, API keys, `BOND_AGENT_TOKEN`).
- **Installed packages do not persist** across container restarts. If you need something, install it at the start of each session.

## The Four Services

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │────▶│   Gateway    │────▶│   Backend    │────▶│ SpacetimeDB  │
│  (Next.js)   │     │  (WebSocket) │     │  (FastAPI)   │     │  (Database)  │
│  :18788      │     │  :18789      │     │  :18790      │     │  :18787      │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                                                │
                                                ▼
                                     ┌──────────────────┐
                                     │  Agent Sandbox   │
                                     │  (Docker)        │
                                     │  :18793+         │
                                     └──────────────────┘
```

| Service | Tech | Port | Role |
|---------|------|------|------|
| **Frontend** | Next.js 15 / React 19 | 18788 | Web UI — chat, settings, deployments |
| **Gateway** | TypeScript / Express / WS | 18789 | WebSocket server, message routing, channels (Telegram, Discord, Slack, WhatsApp), broker |
| **Backend** | Python / FastAPI | 18790 | LLM orchestration, agent turns, tool execution, memory, vault, sandbox management |
| **SpacetimeDB** | SpacetimeDB v2 | 18787 | Real-time database for agents, conversations, system events |
| **Agent Sandbox** | Docker container | 18793+ | Your container. Runs a worker process that the backend communicates with |

## Message Flow (User → Agent → Response)

1. **User types a message** in the Frontend (or sends via Telegram/Discord/Slack/WhatsApp).
2. **Frontend sends it over WebSocket** to the Gateway (`:18789/ws`).
3. **Gateway's WebChatChannel** receives the message, resolves which agent should handle it, and calls the Backend.
4. **Gateway → Backend HTTP POST** to `/api/v1/conversations/{id}/turn` (or `/api/v1/agent/turn` for non-streaming). The Backend streams SSE events back.
5. **Backend resolves the agent** — determines if it runs in `host` mode (directly) or `container` mode (in a sandbox).
6. **If container mode:** Backend ensures the sandbox container is running (`SandboxManager.ensure_running()`), then proxies the request to the worker inside the container.
7. **The agent worker** (that's you) receives the turn, calls the LLM, executes tools, and streams results back.
8. **Response flows back:** Worker → Backend → Gateway (SSE) → Frontend (WebSocket) → User sees streaming text.

## Communication Patterns

| Path | Protocol | Details |
|------|----------|---------|
| Frontend ↔ Gateway | WebSocket | `:18789/ws` — bidirectional, real-time |
| Gateway → Backend | HTTP REST + SSE | `:18790/api/v1/...` — agent turns stream as SSE events |
| Backend → LLM Provider | HTTPS | Anthropic, OpenAI, Ollama, etc. via provider API |
| Backend → SpacetimeDB | SpacetimeDB SDK | `:18787` — agents, conversations, system events |
| Backend → Agent Sandbox | HTTP | `worker_url` (`:18793+`) — proxied agent turns |
| Agent Sandbox → Host | `host.docker.internal` | How your container reaches host services |
| Agent Sandbox → Gateway Broker | HTTP + JWT | `/api/v1/broker/*` — shell execution, MCP tools, deploy commands |

## The Broker (How You Run Commands on the Host)

Your sandbox is isolated — you can't directly access the host filesystem outside `/workspace/`. The **Broker** (hosted by the Gateway) lets you execute approved commands on the host:

- **Endpoint:** `POST /api/v1/broker/exec` on the Gateway
- **Auth:** JWT token injected as `BOND_AGENT_TOKEN` env var
- **Policy engine:** Commands are evaluated against allow/deny rules before execution
- **MCP proxy:** `GET /api/v1/broker/mcp/tools` and `POST /api/v1/broker/mcp/call` — access MCP tools through the broker with policy filtering

## Sandbox Lifecycle

1. **Creation:** When a message targets an agent with a `sandbox_image`, the Backend's `SandboxManager` creates a Docker container with the right mounts, env vars, and network config.
2. **Network:** Containers join `bond-network` and can reach host services via `host.docker.internal`.
3. **Mounts:** Workspace directories (configured per-agent in SpacetimeDB) are bind-mounted. Bond source is mounted at `/bond`. Credentials (SSH, GPG) are mounted from the host.
4. **Idle cleanup:** Containers idle for >1 hour may be destroyed. State in `/workspace/` persists (it's on the host).
5. **Recovery:** On backend restart, existing containers are recovered — not recreated.

## Key Paths (Inside Your Container)

| Path | What | Writable? |
|------|------|-----------|
| `/workspace/` | Project files (bind-mounted from host) | ✅ Yes |
| `/workspace/<project>/` | Individual project directories | ✅ Yes |
| `/bond/` | Bond source code | ❌ Read-only |
| `/root/.ssh/` | SSH keys (copied from `/tmp/.ssh`) | ✅ Yes |
| `/root/.gitconfig` | Git identity (set from `AGENT_NAME`/`AGENT_EMAIL`) | ✅ Yes |

## Key Paths (On the Host)

| Path | What |
|------|------|
| `~/.bond/` | Bond home — config, vault (encrypted secrets), database |
| `~/.bond/vault/` | Encrypted credential storage |
| `bond.json` | Primary config — LLM provider, model, ports, SpacetimeDB connection |
| `data/` | Gateway data directory — channel configs, audit logs, backups |

## Configuration (`bond.json`)

```json
{
  "llm": { "provider": "anthropic", "model": "claude-sonnet-4-20250514" },
  "backend": { "host": "0.0.0.0", "port": 18790 },
  "gateway": { "host": "0.0.0.0", "port": 18789 },
  "frontend": { "port": 18788 },
  "spacetimedb": { "url": "http://localhost:18787", "module": "bond-core-v2" }
}
```

## What You Should NOT Do

- **Don't try to reach services at `localhost`** from inside your container — use `host.docker.internal` instead.
- **Don't assume packages persist** — your container may be recreated at any time.
- **Don't modify `/bond/`** — it's read-only. Work in `/workspace/`.
- **Don't hardcode ports** — read them from environment variables or config when possible.
