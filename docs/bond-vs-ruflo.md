# Bond vs Ruflo — Comparison

Bond is a local-first AI assistant with a Python/FastAPI backend, TypeScript gateway, and Docker sandbox execution. Ruflo (formerly Claude Flow) is a multi-agent orchestration framework built in TypeScript, designed around swarm coordination and Claude Code integration.

They solve different problems with some overlap.

## Architecture

| Aspect | Bond | Ruflo |
|---|---|---|
| **Primary language** | Python (backend) + TypeScript (gateway) | TypeScript (Node.js 20+) |
| **Core pattern** | Single-agent loop with tool-use | Multi-agent swarm with hierarchical coordination |
| **LLM integration** | LiteLLM (any provider) | Multi-provider with Q-Learning router |
| **Interface** | Web UI (frontend) + API | CLI + MCP (Claude Code native) |
| **Data storage** | SQLite (aiosqlite) + sqlite-vec | SQLite + AgentDB + HNSW vector index |
| **Deployment** | Docker Compose (backend + gateway + frontend) | npm package (`npx ruflo@latest`) |

## Agent Capabilities

| Capability | Bond | Ruflo |
|---|---|---|
| **Agent count** | Single default agent (multi-agent stubbed "Phase 2") | 60+ specialized agents (coder, tester, reviewer, architect, security, etc.) |
| **Multi-agent coordination** | ❌ Stubbed | ✅ Swarm topologies: mesh, hierarchical, ring, star |
| **Consensus protocols** | ❌ | ✅ Raft, Byzantine (BFT), Gossip, CRDT |
| **Agent spawning** | ❌ | ✅ Queen/worker hierarchy, sub-worker spawning |
| **Agent specialization** | Generic tool-use loop | Role-specific agents with optimized prompts |
| **Self-learning** | ❌ | ✅ SONA, ReasoningBank, 9 RL algorithms |
| **Drift control** | ❌ | ✅ Hierarchical checkpoints, queen oversight |

## Tool Ecosystem

| Tool | Bond | Ruflo |
|---|---|---|
| **Code execution** | ✅ Docker sandbox or host exec | ✅ Agent Booster (WASM for simple transforms) + LLM |
| **File operations** | ✅ Read/write/list in sandbox | ✅ Via Claude Code tools |
| **Web search** | ✅ DuckDuckGo | ❌ Not built-in (relies on Claude Code) |
| **Browser automation** | ✅ Browser tool | ❌ Not built-in |
| **Memory (semantic)** | ✅ sqlite-vec embeddings, save/search/update | ✅ HNSW vector search, AgentDB |
| **Email** | ✅ Email tool + classification | ❌ |
| **Cron/scheduling** | ✅ Built-in scheduler | ✅ Background daemon with 12 workers |
| **Notifications** | ✅ Notify tool | ❌ |
| **Work planning** | ✅ Work plan tool | ✅ Swarm-level task decomposition |
| **Skills/plugins** | ✅ Skills tool | ✅ Plugin SDK + IPFS marketplace |

## Code Execution

| Aspect | Bond | Ruflo |
|---|---|---|
| **Sandbox** | Docker containers with lifecycle management | No sandbox — executes via Claude Code's native tools |
| **Languages** | Python + shell | TypeScript focus; WASM for simple transforms |
| **Stateful execution** | ❌ Stateless (`python3 -c` each time) | N/A (delegates to Claude Code) |
| **Container recovery** | ✅ Survives backend restarts | N/A |
| **Resource limits** | ✅ CPU + memory per container | N/A |
| **Port management** | ✅ Dynamic port allocation pool | N/A |

## Memory & Learning

| Aspect | Bond | Ruflo |
|---|---|---|
| **Vector search** | sqlite-vec (basic) | HNSW (150x–12,500x faster than linear) |
| **Embeddings** | Voyage / Gemini / local options | ONNX MiniLM (local, 75x faster than API) |
| **Entity graph** | ✅ Entity extraction + knowledge graph | ✅ MemoryGraph with PageRank + communities |
| **Context management** | ✅ Progressive decay, sliding window, compression | ✅ Flash Attention (2.5–7.5x speedup) |
| **Learning from outcomes** | ❌ | ✅ SONA self-optimization, EWC++ (no forgetting), LoRA fine-tuning |
| **Cross-agent knowledge** | ❌ (single agent) | ✅ Agent memory scopes + collective memory |

## Security

| Aspect | Bond | Ruflo |
|---|---|---|
| **Sandbox isolation** | ✅ Docker containers with resource limits | ❌ No sandbox |
| **Credential management** | ✅ Encrypted vault (credentials.enc + .vault_key) | ✅ Secure credential handling |
| **Input validation** | Basic | ✅ Zod schema validation, AIDefence module |
| **Prompt injection protection** | ❌ | ✅ AIDefence with injection detection |
| **Path traversal prevention** | ❌ | ✅ Built-in |

## Integration

| Aspect | Bond | Ruflo |
|---|---|---|
| **Claude Code** | Not integrated | ✅ Native MCP integration |
| **MCP (Model Context Protocol)** | ❌ | ✅ MCP-first API design |
| **GitHub** | ❌ | ✅ PR, Issues, Workflows |
| **Multi-provider failover** | ✅ Via LiteLLM | ✅ Automatic failover + cost-based routing |
| **Web UI** | ✅ Frontend app | ❌ CLI only |

## Cost Optimization

| Aspect | Bond | Ruflo |
|---|---|---|
| **Smart routing** | ❌ Single model per agent | ✅ Q-Learning router picks cheapest viable model |
| **Token compression** | ✅ Context compression pipeline | ✅ 30–50% token reduction |
| **LLM bypass** | ❌ | ✅ WASM Agent Booster skips LLM for simple edits (<1ms) |
| **Claimed savings** | — | 250% extension of Claude Code subscription |

## What Bond Has That Ruflo Doesn't

- **Docker sandbox** with full container lifecycle (create, health check, recover, destroy)
- **Web UI** for chat interaction
- **Email integration** with classification and intelligence
- **Browser automation** tool
- **Web search** (DuckDuckGo)
- **Notification system**
- **Encrypted credential vault**
- **Workspace mount management** with SSH key forwarding

## What Ruflo Has That Bond Doesn't

- **Multi-agent swarm coordination** with 60+ specialized agents
- **Consensus protocols** (Raft, BFT, Gossip)
- **Self-learning pipeline** (SONA, ReasoningBank, RL algorithms)
- **MCP/Claude Code integration**
- **WASM-based fast transforms** that skip LLM entirely
- **Smart cost routing** across providers
- **Plugin marketplace** (IPFS-based)
- **Prompt injection protection**
- **GitHub integration**

## Summary

**Bond** is a self-contained local assistant — it has its own UI, sandbox, tools, and runs as a standalone service. It's practical and works today for single-agent tasks with real tool execution in Docker containers. Multi-agent is planned but not implemented.

**Ruflo** is an orchestration layer on top of Claude Code — it doesn't execute code itself but coordinates swarms of agents with sophisticated routing, learning, and consensus. It's more ambitious architecturally (swarms, self-learning, WASM optimization) but depends on Claude Code as the execution substrate.

They're complementary more than competitive. Bond could benefit from Ruflo's multi-agent coordination and smart routing. Ruflo could benefit from Bond's sandbox execution, web UI, and standalone tool ecosystem.
