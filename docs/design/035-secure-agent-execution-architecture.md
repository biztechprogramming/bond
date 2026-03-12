# Design Doc 035: Secure Agent Execution Architecture

**Status:** Draft  
**Date:** 2026-03-10  
**Depends on:** 008 (Containerized Agent Runtime), 013 (OpenSandbox Submodule), 020 (Agent Repo Autonomy)  
**Supersedes:** Portions of 008 §2.2 (communication model), 020 §Container Setup (credential handling)

---

## 1. The Problem

Bond agents run inside Docker containers and have direct access to everything needed to do their work — including raw API keys, SSH keys, GitHub tokens, and unrestricted network access. The container boundary provides process isolation but not a security boundary.

Current vulnerabilities:

- **Credential exposure.** `GITHUB_TOKEN` and `SPACETIMEDB_TOKEN` are injected as container environment variables. Any code the agent runs (including code it generates) can read them with `echo $GITHUB_TOKEN`.
- **No action gating.** The agent calls `subprocess.run(["git", "push"])` directly. Nothing evaluates whether this push should be allowed, to which remote, or for which branch.
- **Self-reported identity.** When the container calls the Gateway persistence API, it passes its own `agentId` in the request body. A compromised agent could impersonate another.
- **No audit trail for host operations.** LLM calls and tool invocations are logged via Langfuse and SpacetimeDB, but git pushes, PR creations, file writes to host-mounted paths, and outbound HTTP requests are invisible.
- **No user approval path.** There is no mechanism for the system to pause and ask the user "Agent wants to push to `main` — allow?" before executing.
- **Unrestricted network.** Containers can reach any endpoint. An agent generating code could exfiltrate data or download malicious packages.
- **Flat permissions.** Every agent gets the same vault, same tokens, same capabilities. There's no way to give Agent A git access but not Agent B.

This document describes the target architecture for secure agent execution in Bond. The Permission Broker (Doc 036) is the central component; this document covers the full system surrounding it.

---

## 2. Architecture Overview

```
┌────────────────────────────────────────────────────────────────────┐
│  HOST                                                              │
│                                                                    │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────────────┐ │
│  │ Frontend │◄──►│   Gateway    │◄──►│  Backend API             │ │
│  │ :18788   │    │   :18789     │    │  :18790                  │ │
│  └──────────┘    │              │    └──────────────────────────┘ │
│                  │  ┌────────┐  │                                  │
│                  │  │ Broker │  │    ┌──────────────────────────┐ │
│                  │  │ Engine │◄─┼───►│  Vault (encrypted)       │ │
│                  │  └───┬────┘  │    └──────────────────────────┘ │
│                  │      │       │    ┌──────────────────────────┐ │
│                  │  ┌───┴────┐  │    │  Policy Store            │ │
│                  │  │ Audit  │  │    │  (YAML / JSON configs)   │ │
│                  │  │  Log   │  │    └──────────────────────────┘ │
│                  │  └────────┘  │                                  │
│                  └──────┬───────┘                                  │
│                         │ HTTP (authenticated)                     │
│  ┌──────────────────────┼─────────────────────────────────┐       │
│  │  CONTAINER A         │                                  │       │
│  │                      ▼                                  │       │
│  │  ┌──────────────────────────────────────────────────┐   │       │
│  │  │  Agent Worker                                    │   │       │
│  │  │                                                  │   │       │
│  │  │  ┌────────────┐    ┌──────────────────────────┐  │   │       │
│  │  │  │ Agent Loop │───►│ Broker SDK (Python)      │  │   │       │
│  │  │  │            │    │                          │  │   │       │
│  │  │  │ LLM calls  │    │ broker.exec("gh pr ...") │  │   │       │
│  │  │  │ Tool exec  │    │ broker.exec("git push")  │  │   │       │
│  │  │  │            │    │ broker.exec("npm test")   │  │   │       │
│  │  │  └────────────┘    └──────────────────────────┘  │   │       │
│  │  │                                                  │   │       │
│  │  │  /workspace (bind mount, scoped)                 │   │       │
│  │  │  /data (agent data, persistent volume)           │   │       │
│  │  │  NO env vars with secrets                        │   │       │
│  │  │  NO direct outbound network (except broker)      │   │       │
│  │  └──────────────────────────────────────────────────┘   │       │
│  └─────────────────────────────────────────────────────────┘       │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │  CONTAINER B (another agent, different permissions)     │       │
│  └─────────────────────────────────────────────────────────┘       │
└────────────────────────────────────────────────────────────────────┘
```

### 2.1 Key Principles

1. **Default deny.** Agents cannot do anything on the host unless a policy explicitly allows it.
2. **Agents never see secrets.** The broker injects credentials at execution time. The container has no env vars, mounted key files, or vault access.
3. **Every action is audited.** The broker logs every request with: timestamp, agent ID, session ID, action, arguments, policy decision, result.
4. **Identity is cryptographic.** Each agent gets a short-lived token at container creation. The broker verifies it. Self-reporting agent IDs is eliminated.
5. **Policies are data, not code.** Admins configure permissions in YAML files. No code changes needed to adjust what an agent can do.
6. **User stays in the loop.** Sensitive actions can require real-time user approval via any Bond surface (web UI, Telegram, CLI).

### 2.2 Component Map

| Component | Location | Role |
|---|---|---|
| **Permission Broker** | `gateway/src/broker/` | Policy-enforced command executor embedded in the Gateway. Evaluates requests, runs allowed commands on the host, logs everything. See Doc 036. |
| **Policy Store** | `~/.bond/policies/` | YAML policy files scoped per-user, per-agent, per-session. Command allowlists, deny lists, and prompt rules. |
| **Host Auth Context** | `~/.config/gh/`, `~/.ssh/`, `~/.npmrc`, etc. | The host user's existing CLI authentication. The broker runs commands as the host user — no per-request credential injection needed. |
| **Audit Log** | `~/.bond/data/broker-audit.jsonl` | Append-only structured log of every broker request and decision. |
| **Broker SDK** | `backend/app/agent/broker_client.py` | Python client with one key method: `broker.exec(command)`. Replaces direct subprocess calls for host operations. |
| **Agent Token Service** | `gateway/src/broker/tokens.ts` | Issues and validates short-lived HMAC tokens for agent authentication. |
| **Approval Flow** | `gateway/src/broker/approval.ts` | Manages prompt-mode actions: sends approval requests to UI, waits for response. |
| **Command Executor** | `gateway/src/broker/executor.ts` | Runs commands on the host via `child_process`. Uses the host user's full environment. |

---

## 3. Container Runtime Changes

### 3.1 Network Isolation

Containers will be created on an isolated Docker network with outbound traffic blocked except to the Gateway broker endpoint.

```bash
# One-time setup
docker network create --driver bridge --internal bond-agent-net

# Per-container: connect to isolated network, expose only broker endpoint
docker run --network bond-agent-net \
  --add-host broker.bond.internal:<gateway-ip> \
  ...
```

The `--internal` flag blocks all outbound traffic. The `--add-host` entry provides a single resolvable hostname for the broker. The agent SDK connects to `http://broker.bond.internal:18789/api/v1/broker/`.

**Exception:** LLM API calls. Two options:
- **Option A (preferred):** Proxy LLM calls through the broker. Broker injects API keys and forwards to provider endpoints. Adds latency but keeps containers fully sealed.
- **Option B:** Allowlist specific LLM provider IPs via iptables rules in the container. Faster but leaks which providers are in use.

Phase 1 will use **Option B** (allowlist) for pragmatism, with Option A as a Phase 2 enhancement.

### 3.2 Credential Removal

The following will be removed from container creation (`SandboxManager._create_worker_container`):

```python
# REMOVE — agents must not see these
cmd.extend(["-e", f"GITHUB_TOKEN={github_token}"])
cmd.extend(["-e", f"SPACETIMEDB_TOKEN={stdb_token}"])
cmd.extend(["-v", f"{vault_data_dir}:/bond-home/data:rw"])
cmd.extend(["-v", f"{ssh_dir}:/tmp/.ssh:ro"])
```

Instead, the container receives only:
- A **broker token** (`BOND_BROKER_TOKEN`) — short-lived, scoped to this agent + session.
- The **broker URL** (`BOND_BROKER_URL=http://broker.bond.internal:18789`).

### 3.3 Agent Token Issuance

At container creation, `SandboxManager` calls the broker token service:

```typescript
// gateway/src/broker/tokens.ts
interface AgentToken {
  token: string;        // HMAC-signed JWT or opaque token
  agentId: string;
  sessionId: string;
  issuedAt: number;
  expiresAt: number;    // short-lived: 1 hour, renewable
}

function issueAgentToken(agentId: string, sessionId: string): AgentToken;
function validateAgentToken(token: string): AgentToken | null;
```

The token is passed as an env var. The agent SDK includes it in every broker request. The broker validates it server-side — no more self-reported agent IDs.

---

## 4. Policy System

### 4.1 Policy File Format

Policies live in `~/.bond/policies/` as YAML files. They are evaluated in specificity order: session > agent > user > default.

```yaml
# ~/.bond/policies/default.yaml
# Base policy — applies to all agents unless overridden
version: "1"
name: default
description: Default-deny baseline

rules:
  # Git — read-only operations
  - commands: ["git status*", "git log*", "git diff*", "git branch*", "git show*"]
    decision: allow

  # Git — branch, commit, add
  - commands: ["git checkout*", "git switch*", "git add*", "git commit*"]
    decision: allow

  # Git — push to feature/fix branches
  - commands: ["git push*feat/*", "git push*fix/*", "git push*agent/*"]
    decision: allow

  # Git — push to main/master blocked
  - commands: ["git push*main*", "git push*master*"]
    decision: deny
    reason: "Direct push to protected branches is not allowed"

  # GitHub CLI — PR operations
  - commands: ["gh pr create*", "gh pr list*", "gh pr view*"]
    decision: allow

  # GitHub CLI — merge requires approval
  - commands: ["gh pr merge*"]
    decision: prompt
    timeout: 120

  # Build and test tools
  - commands: ["npm test*", "npm run build*", "npx vitest*", "uv run*", "python -m pytest*"]
    decision: allow

  # Package install — prompt
  - commands: ["npm install*", "pip install*"]
    decision: prompt
    timeout: 120

  # Dangerous commands — deny
  - commands: ["curl*", "wget*", "sudo*", "rm -rf /*"]
    decision: deny

  # Catch-all — deny anything not explicitly allowed
  - commands: ["*"]
    decision: deny
    reason: "Command not in allowlist"
```

```yaml
# ~/.bond/policies/agents/bond-default.yaml
# Override for the default Bond agent — more permissive
version: "1"
name: bond-default-agent
extends: default
agent_id: "01JBOND0000000000000DEFAULT"

rules:
  # This agent can do docker builds
  - commands: ["docker build*", "docker run*"]
    decision: allow

  # This agent can publish (with approval)
  - commands: ["npm publish*"]
    decision: prompt
    timeout: 180
```

### 4.2 Policy Resolution Order

1. **Session override** — temporary escalation ("trust this agent for git operations this session")
2. **Agent-specific policy** — `~/.bond/policies/agents/{agent-id}.yaml`
3. **Profile policy** — `~/.bond/policies/profiles/{profile-name}.yaml` (e.g., "development", "production")
4. **Default policy** — `~/.bond/policies/default.yaml`
5. **Built-in baseline** — hardcoded default-deny if no files exist

First matching rule wins. If no rule matches, the action is denied.

### 4.3 No Action Type Taxonomy — Just Commands

The broker does not define its own action taxonomy. There is no `git.create_pr` or `filesystem.read` action type. The agent sends a shell command string, and the policy engine matches it against glob patterns.

This means:
- **No API bloat.** Adding a new capability = adding policy rules, not code.
- **Agents use tools they know.** `gh pr create`, `git push`, `npm test` — not a broker-specific API.
- **The policy is the product.** The broker's value is deciding which commands to allow, not wrapping every CLI tool.

See Doc 036 §5 for the policy rule format and matching algorithm.

### 4.4 Decision Modes

| Mode | Behavior |
|---|---|
| `allow` | Execute the command on the host immediately. Log the action. Return stdout/stderr. |
| `deny` | Block. Log the denial with reason. Return error to agent. |
| `prompt` | Pause execution. Send approval request to user via configured surface. Wait up to `timeout` seconds. Deny if no response. |

---

## 5. Credential Isolation

### 5.1 Current State (Broken)

```
Container env: GITHUB_TOKEN=ghp_xxxxxxxxxxxx
Agent code:    os.getenv("GITHUB_TOKEN")  →  raw token visible
```

### 5.2 Target State

```
Container env: BOND_BROKER_TOKEN=eyJhbGciOiJIUzI1NiJ9...  (agent-scoped, short-lived)
               BOND_BROKER_URL=http://host.docker.internal:18789
               BOND_HOST_REPO_PATH=~/bond
Agent code:    result = await broker.exec(
                   "gh pr create --title 'feat: weather' --base main --head feat/weather",
                   cwd="~/bond",
               )
               ↓
Broker:        1. Validate agent token
               2. Evaluate policy: "gh pr create*" → allow
               3. Run on host: sh -c "gh pr create --title ..."
                  (host has gh authenticated — no credential injection needed)
               4. Log: {agent, command, decision: "allow", exit_code: 0}
               5. Return {stdout: "https://github.com/.../pull/42", exit_code: 0}
               ↓
Agent:         receives result, never saw any credentials
```

The key insight: **the host already has authenticated CLI tools.** `gh auth login` was run once. `~/.ssh/id_ed25519` exists. `~/.npmrc` has tokens. The broker just runs commands as the host user — no vault reads or credential injection per-request. The agent never has access to any of these because they're on the host filesystem, not mounted into the container.

### 5.3 What the Host Provides

| Tool | Auth Source (host-side) |
|---|---|
| `gh` | `~/.config/gh/hosts.yml` (from `gh auth login`) |
| `git push` (SSH) | `~/.ssh/id_ed25519` (SSH agent) |
| `git push` (HTTPS) | `~/.gitconfig` credential helper |
| `npm publish` | `~/.npmrc` token |
| `docker` | `~/.docker/config.json` |

No vault scoping is needed for Phase 1 — the host auth context *is* the scope. The broker decides which *commands* an agent can run, not which *secrets* it can access.

---

## 6. Audit System

### 6.1 Log Format

Every broker request produces one audit record:

```jsonl
{"ts":"2026-03-10T19:22:14.331Z","agent_id":"01JBOND...","session_id":"conv-abc123","command":"gh pr create --title 'feat: weather' --base main --head feat/weather","cwd":"~/bond","policy_rule":"default#rule-8","decision":"allow","exit_code":0,"stdout_len":62,"duration_ms":2341}
{"ts":"2026-03-10T19:23:01.552Z","agent_id":"01KAGENT...","session_id":"conv-def456","command":"curl https://evil.com","cwd":"/workspace","policy_rule":"default#rule-deny-curl","decision":"deny","reason":"Command is on the deny list","duration_ms":0}
{"ts":"2026-03-10T19:24:33.119Z","agent_id":"01JBOND...","session_id":"conv-abc123","command":"npm publish","cwd":"~/bond","policy_rule":"default#rule-prompt","decision":"prompt_approved","approval":{"surface":"webchat","wait_ms":8200,"approver":"andrew"},"exit_code":0,"duration_ms":11412}
```

### 6.2 Storage

- **Primary:** `~/.bond/data/broker-audit.jsonl` — append-only, one JSON object per line.
- **Rotation:** Daily rotation, gzip compression. Keep 90 days by default.
- **Tamper resistance:** The audit file is on the host filesystem, outside all container mounts. Agents cannot modify it. The broker process appends; nothing else writes.
- **Optional:** Forward to SpacetimeDB for queryable UI display (Phase 2).

### 6.3 Queryability

A CLI tool for querying the audit log:

```bash
bond audit list --agent 01JBOND... --action "git.*" --since 2h
bond audit list --decision deny --limit 50
bond audit list --session conv-abc123
bond audit stats --since 24h
```

---

## 7. User Approval Flow

### 7.1 Prompt-Mode Lifecycle

```
Agent calls broker.exec("npm publish")
  ↓
Broker evaluates policy → decision: "prompt"
  ↓
Broker sends approval request to user via active surface(s)
  ┌──────────────────────────────────────────────────┐
  │  🔒 Agent "bond" requests approval:              │
  │                                                    │
  │  Action: process.exec                              │
  │  Command: npm publish                              │
  │  Session: feat/weather-tool                        │
  │                                                    │
  │  [Allow]  [Allow All This Session]  [Deny]        │
  └──────────────────────────────────────────────────┘
  ↓
User responds (or timeout after 120s → deny)
  ↓
Broker executes or denies, logs the full exchange
```

### 7.2 Escalation Options

| Button | Effect |
|---|---|
| **Allow** | Execute this one action. |
| **Allow All This Session** | Create a session-scoped override for this action type. Logged as an escalation event. Auto-revoked when session ends. |
| **Allow for 1 Hour** | Time-boxed escalation. |
| **Deny** | Block this action. Agent receives denial error. |

### 7.3 Transport

Approval requests are delivered through Bond's existing channel infrastructure:

- **WebChat:** WebSocket event → modal dialog
- **Telegram:** Inline keyboard message
- **CLI:** Interactive prompt via stdin

The broker waits on an async callback. If no surface is connected, the request times out and is denied.

---

## 8. Multi-Agent Permission Inheritance

When Agent A spawns Agent B (via `call_subordinate` or `ParallelWorkerPool`):

1. Agent B inherits Agent A's **session context** but not its **escalations**.
2. Agent B's permissions are the **intersection** of Agent A's current permissions and Agent B's own policy. (A child cannot exceed its parent's permissions.)
3. Session-scoped escalations granted to Agent A do **not** cascade to Agent B.
4. The audit log records the spawn event with the parent-child relationship.

```jsonl
{"ts":"...","agent_id":"01KAGENTB...","parent_agent_id":"01JBOND...","action":"session.spawn","args":{"task":"run unit tests"},"inherited_permissions":["process.exec:npm test","filesystem.read:/workspace/**"]}
```

---

## 9. Migration Path

### Phase 1: Broker + Command Execution (this doc + Doc 036)

- Build the broker as an Express router embedded in the Gateway
- Implement policy engine with hardcoded command allowlists (default-deny)
- Implement command executor (runs commands on host as host user)
- Add agent token issuance and validation (HMAC-signed)
- Add audit logging (append-only JSONL)
- Replace `handle_repo_pr` to use `broker.exec("gh pr create ...")` instead of subprocess + direct GitHub API
- Build Broker SDK (Python client with `broker.exec(command)`)
- Remove `GITHUB_TOKEN`, `SPACETIMEDB_TOKEN`, SSH keys, vault mounts from containers
- Inject `BOND_BROKER_TOKEN` and `BOND_BROKER_URL` instead

**Agents can still:** Run arbitrary subprocess commands inside the container, make outbound HTTP (LLM calls), read/write their workspace directly.

**Agents can no longer:** See GitHub tokens or SSH keys, run host-side commands without policy approval, operate without audit logging.

### Phase 2: Policy Config + Network Isolation

- YAML policy loading from `~/.bond/policies/` (replace hardcoded rules)
- Hot-reload on policy file changes
- Per-agent policy overrides
- Network isolation (Docker `--internal` network)
- LLM API call proxying through broker
- Audit log rotation (daily, gzip, 90-day retention)

### Phase 3: Approval Flow + Hardening

- User approval flow UI (prompt mode — modal in WebChat, inline keyboard in Telegram)
- Session-scoped escalation ("allow all git operations this session")
- Rate limiting per agent (concurrent command limit, requests/minute)
- Command parsing for policy matching (beyond glob — parse program + args to prevent quoting tricks)
- Audit log UI in frontend

### Phase 4: Multi-Agent + Hardening

- Permission inheritance for spawned agents
- Audit log forwarding to SpacetimeDB
- CLI audit query tool
- Secret rotation support
- Policy validation/linting tool
- Container network egress monitoring

---

## 10. Impact on Existing Components

| Component | Change |
|---|---|
| `SandboxManager` | Stop injecting `GITHUB_TOKEN`, `SPACETIMEDB_TOKEN`, SSH keys, vault mounts. Inject `BOND_BROKER_TOKEN`, `BOND_BROKER_URL`, and `BOND_HOST_REPO_PATH` instead. |
| `handle_repo_pr` (native.py) | Replace subprocess git + direct GitHub API calls with `broker.exec("gh pr create ...")` and `broker.exec("git push ...")`. |
| `code_execute` tool | Phase 1: no change (still runs inside container for in-container work). Phase 2: option to route through broker for auditing. |
| `PersistenceClient` | Add broker token to requests for identity verification. |
| `Vault` | No changes needed for Phase 1. The broker doesn't read the vault — it runs commands as the host user, whose CLI tools are already authenticated. |
| Gateway server.ts | Mount broker router at `/api/v1/broker/`. |
| Frontend | Phase 3: Add approval flow UI components (modal for prompt-mode decisions). |
| `Dockerfile.agent` | Remove SSH key setup from entrypoint. Remove vault mount. |
| `agent-entrypoint.sh` | Remove SSH key copy logic. Add broker healthcheck on startup. |
| `lifecycle.py` | No change — lifecycle detection still works based on tool call patterns. The tools themselves change, not the detection. |

---

## 11. Non-Goals (For Now)

- **Kubernetes runtime.** OpenSandbox integration (Doc 013) handles this separately.
- **Multi-tenant isolation.** Single-user system for now. Multi-user adds authentication complexity.
- **Code signing.** Verifying that agent-generated code hasn't been tampered with. Useful but out of scope.
- **Sandbox escape detection.** We harden the boundary; detecting escapes is a different discipline.

---

## 12. Design Decisions (Resolved)

1. **Broker hosting: Embedded in Gateway (Option 2).** Single-user system; threat model is agent mistakes, not adversarial container escape. The broker runs as an Express router in the Gateway process. Extraction to a standalone daemon on a Unix socket (Option 4) is straightforward if the threat model changes later. See Doc 036 §1.

2. **General command execution, not bespoke capability methods.** The broker has one endpoint: `/exec`. The agent sends a shell command, the broker evaluates policy and runs it on the host. There is no `create_pr` method — the agent calls `broker.exec("gh pr create ...")`. New capabilities = new policy rules, not new code. See Doc 036 §1.

3. **Host auth context, not per-request credential injection.** The host user has `gh`, `git`, `ssh`, `npm` already authenticated. The broker runs commands as the host user. No vault reads or credential injection per-request. The agent never sees credentials because they're on the host filesystem, never mounted into containers. See Doc 036 §6.3.

4. **Agent reads git remote from mounted volume.** The `.git/config` in the agent's mounted volume has the remote URL. The agent includes this in its broker command. The host has the same remote configured.

## 13. Open Questions

1. **LLM API key injection.** Should the broker proxy all LLM calls (full isolation but adds latency) or continue injecting API keys as env vars (faster but breaks the "agents never see secrets" principle)?

2. **Workspace file access.** Agents currently read/write workspace files via bind mounts. This stays direct (mount-based). Only host-side operations (git push, PR creation, package publishing) go through the broker.

3. **execd integration.** OpenSandbox's execd daemon runs inside the container for structured command execution. The broker does not replace execd — execd handles in-container operations, the broker handles host operations. They coexist.

4. **CWD mapping.** The agent's repo is at `/bond` inside the container, but `~/bond` on the host. The agent needs to know the host path for broker commands. Solved by injecting `BOND_HOST_REPO_PATH` as an env var at container creation.

5. **Glob pattern evasion.** Agents could craft commands with shell quoting tricks to bypass string-based glob matching. Mitigated by catch-all deny rule. Phase 2 should consider parsing commands into (program, args) before matching. See Doc 036 §14.
