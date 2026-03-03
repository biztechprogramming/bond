# Design Doc: Migrating Bond to SpacetimeDB v2.0

## Overview
This document outlines the strategy for migrating the Bond central data store (currently SQLite/SQLAlchemy) to **SpacetimeDB v2.0**, leveraging its new TypeScript SDK support. We aim to move the source of truth for global state (Agents, Models, MCP Servers, Users) and all agent-specific data (history, logs) to SpacetimeDB.

## Proposed Architecture

### 1. Centralized State (SpacetimeDB)
All shared metadata and global configurations will reside in a SpacetimeDB module.
- **Agents:** Definitions, configuration, and status.
- **Models & MCP Servers:** Global catalog and availability.
- **Users & Sessions:** Identity management and top-level session metadata.
- **Benefit:** Real-time synchronization across multiple gateway instances and seamless backend/frontend state sharing via the SpacetimeDB TS/Rust SDKs.

### 2. Agent Data: Local LibSQL vs. SpacetimeDB
**Recommendation: Consolidate to SpacetimeDB.**

Since SpacetimeDB and the agent workers will typically share the same host, the previous concerns regarding network latency and "local-first" speed are largely mitigated. We can shift to a more unified architecture.

#### Revised Strategy
- **Eliminate Local LibSQL:** Move message history, tool logs, and agent-specific metadata directly into SpacetimeDB.
- **Why?**
    1. **Transactional Integrity:** A single transaction can update agent state and log the message that caused the update.
    2. **Universal Real-time Monitoring:** The UI can subscribe to a specific agent's `message_history` table and see logs appearing in real-time as the worker writes them, without a custom WebSocket bridge or log-tailing.
    3. **Simplified Backup:** Backing up the SpacetimeDB module captures the entire system state—no more hunting for individual `agent.db` files. We've implemented a rotation-based backup script (`scripts/backup-spacetimedb.sh`) that maintains daily, weekly, and monthly snapshots (retaining the last 5 of each).

#### Edge Case: Vector Embeddings
Large vector indices (e.g., Chroma/LanceDB) should still likely remain as optimized local files or specialized sidecars, as they don't fit the relational/reducer model of SpacetimeDB well.

## Communication Architecture: Gateway as the Message Hub

### 1. Unified Routing
The Gateway (TypeScript) serves as the central router for all traffic—both LLM calls and Agent-to-Agent (A2A) communication.
- **Persistence Agents:** Specialized "Database Agents" will be the first class of agents implemented. Other agents will communicate with these via the Gateway to perform complex data operations.
- **Standardized Transport:** All inter-agent messages are routed through the Gateway. This allows for:
    - **Observability:** Centralized logging of A2A interactions in SpacetimeDB.
    - **Security:** Permission checks and rate limiting at the router level.
    - **Real-time UI:** Because messages pass through the Gateway, the UI can subscribe to these conversations via SpacetimeDB reducers immediately.

### 2. Persistence Strategy: API-First vs. Local Storage

### 1. Unified Gateway API
The Gateway (TypeScript) becomes the primary interface for all persistence operations.
- **Agent Workers:** Instead of direct DB access, workers communicate with the Gateway API (e.g., `/api/v1/messages`, `/api/v1/tool-logs`) to load context, log tools, and save messages.
- **SpacetimeDB Integration:** The Gateway uses the SpacetimeDB TS SDK to commit these changes to the central store.
- **Real-time Flow:** Worker → Gateway API → SpacetimeDB → UI (via Subscription).

### 2. Configurable Persistence Modes
Agent persistence is explicitly configured. There is no silent behavior change; the agent will only use the transport it is configured for.

| Mode | Transport | Best For |
| :--- | :--- | :--- |
| **`api`** | HTTP/gRPC to Gateway | **Same-machine or high-speed LAN.** Simplifies architecture and ensures real-time UI updates via SpacetimeDB. |
| **`sqlite`** | Local LibSQL file | **Edge/Remote deployments.** Higher latency environments or when the agent needs to operate in isolation from the central Gateway. |

#### Implementation Details
- **Explicit Configuration:** The `PERSISTENCE_MODE` setting is stored in the agent's configuration. Once set, the agent strictly adheres to that mode.
- **Initial Setup Auto-detection:** During the initial agent creation or first-run setup, the system can perform a one-time check for Gateway connectivity to suggest or automatically set the `PERSISTENCE_MODE`.
- **Docker Connectivity:** Since agent workers run in isolated containers, the `api` mode configuration must include the correct container-to-host or container-to-container networking address. 
    - **Dynamic Resolution:** The system will attempt to resolve the Gateway's address based on the host OS (e.g., `host.docker.internal` on macOS/Windows/WSL, or the bridge IP `172.17.0.1` on Linux).
    - **UI Configuration:** This address will be exposed in the Agent Settings UI, allowing users to manually override the Gateway URL if they are using a custom Docker network or non-standard deployment.
- **Strict Execution:** After the initial configuration is set, there is no silent switching. If configured for `api` and the Gateway is unreachable from within the container, the agent worker will fail loudly with a connection error.
- **Syncing:** Agents running in `sqlite` mode will require a periodic sync or "push-on-reconnect" mechanism to move their local history into the central SpacetimeDB store for UI visibility.

### 3. Migration Path (Updated)
1. **Phase 1: Dual-Write & API Scaffolding**
   - Implement the persistence API in the Gateway.
   - Workers still write to local LibSQL but *also* send a copy to the Gateway API (Shadow Mode).
   - **Environment Setup:** Added `scripts/setup-spacetimedb.sh` to handle CLI installation, module initialization (`spacetimedb/`), and local instance management via Docker. Integrated into `make install`.
2. **Phase 2: Gateway & UI Transition**
   - Frontend switches to SpacetimeDB subscriptions for all real-time data.
   - Gateway becomes the primary state manager.
3. **Phase 3: Configurable Workers**
   - Introduce the `persistence_mode` config.
   - Set default to `api` for same-machine deployments.
   - Legacy `agent.db` files are migrated to SpacetimeDB and then deprecated for `api` mode users.

## Implementation Strategy: Language & Protocol Selection

### 1. SpacetimeDB Module Language
We will use **TypeScript** for the SpacetimeDB backend module.
- **Rationale:** Since the Gateway and Frontend are already TypeScript, using the TS SDK for the SpacetimeDB module ensures a unified type system across the entire stack. This makes the "reactive UI" assumption correct—we can share types between the DB schema and the UI components seamlessly.

### 2. Polyglot API Protocol
The Worker-to-Gateway persistence API will support both **REST/JSON** and **gRPC/Connect** as needed.
- **UI-Facing Methods:** Will prioritize TypeScript/REST for maximum reactivity and ease of use in the browser.
- **High-Performance Worker Methods:** Can leverage gRPC/Connect for type-safe, low-latency persistence (e.g., streaming tool logs or large message histories).

### 3. Authentication & Security
- **Container Identity:** Each agent container will be injected with a unique `AGENT_TOKEN`.
- **Validation:** The Gateway will validate this token before committing any data to SpacetimeDB on behalf of that agent, ensuring isolation even in a shared DB environment.

## Summary
| Feature | Location | Rationale |
| :--- | :--- | :--- |
| **Global Config** | SpacetimeDB | Real-time sync, central source of truth. |
| **Agent Registry** | SpacetimeDB | Easy discovery and status monitoring. |
| **Message History** | SpacetimeDB | Unified history, real-time UI streaming via subscriptions. |
| **Vector Index** | Local File | Performance and isolation for large binary blobs. |
