# Design Doc 036: Permission Broker

**Status:** Draft  
**Date:** 2026-03-10  
**Depends on:** 035 (Secure Agent Execution Architecture)  
**Parent:** 035

---

## 1. Overview

The Permission Broker is a module embedded in the Bond Gateway that mediates agent interactions with the host. When an agent needs to do something privileged — run a git command, create a PR, execute a build, install a package — it sends the command to the broker. The broker evaluates the command against a policy, runs it on the host (where credentials and auth already exist), logs the decision and result, and returns the output.

The broker is not an API that reimplements git, npm, or gh. It is a **policy-enforced command executor**. The agent says "run this command." The broker decides whether to allow it, runs it on the host if allowed, and returns stdout/stderr. The host's existing authentication context (gh auth, SSH keys, npm tokens) handles credentials — the agent never sees them.

### Design Decisions

- **Embedded in Gateway (Option 2 from architecture review).** The broker runs as an Express router in the existing Gateway process. This is a single-user system; the threat model is agent mistakes and LLM hallucinations, not adversarial container escape. Extraction to a standalone daemon (Option 4) is straightforward if the threat model changes.
- **General command execution, not bespoke methods.** There is no `create_pr` endpoint. The agent calls `broker.exec("gh pr create --title '...' --base main --head feat/weather")` and the broker runs it on the host. New capabilities = new policy rules, not new code.
- **Host auth context.** The host has `gh` authenticated, SSH keys in `~/.ssh`, npm tokens in `~/.npmrc`. The broker runs commands as the host user. No credential injection per-request — the host environment already has what it needs.
- **Agent reads remote from mounted volume.** The agent's repo clone has `.git/config` with the remote URL. The agent includes the remote or repo identifier in its command. The host has the same remote configured.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  GATEWAY PROCESS                                                │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Existing: WebSocket, Channels, Pipeline, Persistence     │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Broker Router  POST /api/v1/broker/exec                  │  │
│  │                                                           │  │
│  │  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐  │  │
│  │  │   Token      │  │   Policy     │  │   Command       │  │  │
│  │  │   Validator  │─►│   Engine     │─►│   Executor      │  │  │
│  │  └─────────────┘  └──────────────┘  └─────────────────┘  │  │
│  │                                           │               │  │
│  │  ┌─────────────┐  ┌──────────────┐        │               │  │
│  │  │   Approval   │  │   Audit      │◄───────┘               │  │
│  │  │   Manager    │  │   Logger     │                        │  │
│  │  └─────────────┘  └──────────────┘                        │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Agent HTTP ◄──── host.docker.internal:18789/api/v1/broker/     │
└────────────────────────────────────────────────────────────────┘
```

### 2.1 Request Flow

```
1. Agent:      broker.exec("gh pr create --title 'feat: weather' --base main --head feat/weather")
2. HTTP:       POST /api/v1/broker/exec  { command, cwd, timeout }  Authorization: Bearer <token>
3. Token:      Validate HMAC token → extract agentId, sessionId
4. Policy:     Match command against rules → allow / deny / prompt
5. Execute:    child_process.execFile("sh", ["-c", command], { cwd, env: process.env })
6. Audit:      Log { agent, command, decision, exit_code, duration }
7. Response:   { status, exit_code, stdout, stderr }
```

The command runs as the Gateway's OS user on the host. It has access to everything the host user has — `gh`, `git`, `ssh`, `npm`, `docker`, etc. — with their existing authentication.

---

## 3. API

### 3.1 Execute Command

```
POST /api/v1/broker/exec
Authorization: Bearer <agent-token>
Content-Type: application/json

{
  "command": "gh pr create --title 'feat: weather' --base main --head feat/weather",
  "cwd": "~/bond",
  "timeout": 60,
  "env": {}              // optional: additional env vars (non-secret only)
}
```

**Response (allowed + success):**
```json
{
  "status": "ok",
  "decision": "allow",
  "exit_code": 0,
  "stdout": "https://github.com/biztechprogramming/bond/pull/42\n",
  "stderr": "",
  "duration_ms": 2341
}
```

**Response (denied):**
```json
{
  "status": "denied",
  "decision": "deny",
  "reason": "Command not in allowlist: curl https://example.com",
  "policy_rule": "default#rule-catchall"
}
```

**Response (allowed + execution error):**
```json
{
  "status": "ok",
  "decision": "allow",
  "exit_code": 1,
  "stdout": "",
  "stderr": "fatal: not a git repository",
  "duration_ms": 58
}
```

### 3.2 Token Renewal

```
POST /api/v1/broker/token/renew
Authorization: Bearer <agent-token>
```

Returns a fresh token with extended expiry.

### 3.3 Health Check

```
GET /api/v1/broker/health
```

Returns broker status, policy rule count, uptime. No auth required (used by container startup healthcheck).

---

## 4. Token System

### 4.1 Purpose

Agent tokens replace self-reported agent IDs. The container receives a short-lived token at creation. The broker validates it on every request and extracts the agent identity server-side.

### 4.2 Implementation

```typescript
// gateway/src/broker/tokens.ts

import crypto from "node:crypto";

interface AgentTokenPayload {
  sub: string;        // agent ID (ULID)
  sid: string;        // session/conversation ID
  iat: number;        // issued at (unix seconds)
  exp: number;        // expires at (default: +1 hour)
}

// HMAC secret — generated on first run, stored at ~/.bond/data/.broker_secret
let BROKER_SECRET: Buffer;

function getSecret(): Buffer {
  if (BROKER_SECRET) return BROKER_SECRET;
  const secretPath = path.join(bondDataDir, ".broker_secret");
  if (fs.existsSync(secretPath)) {
    BROKER_SECRET = fs.readFileSync(secretPath);
  } else {
    BROKER_SECRET = crypto.randomBytes(32);
    fs.writeFileSync(secretPath, BROKER_SECRET, { mode: 0o600 });
  }
  return BROKER_SECRET;
}

export function issueToken(agentId: string, sessionId: string, ttlSeconds = 3600): string {
  const payload: AgentTokenPayload = {
    sub: agentId,
    sid: sessionId,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + ttlSeconds,
  };
  const data = Buffer.from(JSON.stringify(payload));
  const sig = crypto.createHmac("sha256", getSecret()).update(data).digest();
  return data.toString("base64url") + "." + sig.toString("base64url");
}

export function validateToken(token: string): AgentTokenPayload | null {
  const [dataStr, sigStr] = token.split(".");
  if (!dataStr || !sigStr) return null;

  const data = Buffer.from(dataStr, "base64url");
  const expected = crypto.createHmac("sha256", getSecret()).update(data).digest();
  const actual = Buffer.from(sigStr, "base64url");

  if (!crypto.timingSafeEqual(expected, actual)) return null;

  const payload: AgentTokenPayload = JSON.parse(data.toString());
  if (payload.exp < Math.floor(Date.now() / 1000)) return null;

  return payload;
}
```

### 4.3 Token Lifecycle

1. **Issue:** `SandboxManager.ensure_running()` requests a token from the Gateway before creating the container.
2. **Inject:** Token passed as `BOND_BROKER_TOKEN` env var to the container. This is the *only* secret the container receives.
3. **Use:** Broker SDK includes it in every request: `Authorization: Bearer <token>`.
4. **Renew:** SDK renews proactively when >75% of TTL has elapsed.
5. **Revoke:** On session end or container destroy, token is added to an in-memory revocation set (bounded LRU, evicts after token would have expired naturally).

---

## 5. Policy Engine

### 5.1 What It Does

The policy engine takes a command string and decides: **allow**, **deny**, or **prompt** (ask the user). That's it.

### 5.2 Rule Format

```typescript
interface PolicyRule {
  commands: string[];                  // glob patterns matched against the full command
  decision: "allow" | "deny" | "prompt";
  reason?: string;                     // human-readable (for deny/prompt)
  timeout?: number;                    // prompt timeout in seconds (default: 120)
  cwd?: string[];                      // allowed working directories (glob)
}

interface Policy {
  version: string;
  name: string;
  extends?: string;
  agent_id?: string;
  rules: PolicyRule[];
}
```

### 5.3 Policy File Format (YAML)

```yaml
# ~/.bond/policies/default.yaml
version: "1"
name: default
description: Default policy — allowlisted commands only

rules:
  # Git — read-only operations, always allowed
  - commands: ["git status*", "git log*", "git diff*", "git branch*", "git show*", "git rev-parse*"]
    decision: allow

  # Git — branch and commit on any branch
  - commands: ["git checkout*", "git switch*", "git add*", "git commit*", "git stash*"]
    decision: allow

  # Git — push to feature/fix branches only
  - commands: ["git push*feat/*", "git push*fix/*", "git push*agent/*"]
    decision: allow

  # Git — push to main/master blocked
  - commands: ["git push*main*", "git push*master*"]
    decision: deny
    reason: "Direct push to protected branches is not allowed"

  # Git — catch-all push (prompt for unrecognized branch patterns)
  - commands: ["git push*"]
    decision: prompt
    timeout: 120

  # GitHub CLI — PR operations
  - commands: ["gh pr create*", "gh pr list*", "gh pr view*", "gh pr status*"]
    decision: allow

  # GitHub CLI — merge requires approval
  - commands: ["gh pr merge*"]
    decision: prompt
    timeout: 120

  # Build and test tools
  - commands: [
      "npm test*", "npm run build*", "npm run lint*", "npm run dev*",
      "npx vitest*", "npx tsc*",
      "uv run*", "python -m pytest*", "python -m mypy*",
      "make *",
      "cargo test*", "cargo build*",
      "go test*", "go build*",
    ]
    decision: allow

  # Package install — prompt
  - commands: ["npm install*", "npm ci*", "pip install*", "uv pip install*", "apt*"]
    decision: prompt
    timeout: 120

  # Read-only filesystem/info commands
  - commands: [
      "ls *", "cat *", "head *", "tail *", "grep *", "find *", "wc *",
      "tree *", "file *", "stat *", "du *", "df *",
      "which *", "env", "pwd", "whoami", "uname*", "date",
    ]
    decision: allow

  # File mutation — allow in workspace paths
  - commands: ["mkdir *", "cp *", "mv *", "touch *"]
    decision: allow
    cwd: ["/home/*/bond*", "/workspace/*"]

  # Dangerous commands — always deny
  - commands: [
      "rm -rf /*", "rm -rf /",
      "chmod 777*", "chown*",
      "curl*", "wget*",
      "sudo*",
      "docker rm*", "docker stop*", "docker kill*",
      "kill*", "killall*",
      "shutdown*", "reboot*",
    ]
    decision: deny
    reason: "Command is on the deny list"

  # Catch-all — deny anything not explicitly allowed
  - commands: ["*"]
    decision: deny
    reason: "Command not in allowlist"
```

```yaml
# ~/.bond/policies/agents/bond-default.yaml
version: "1"
name: bond-default-agent
extends: default
agent_id: "01JBOND0000000000000DEFAULT"

rules:
  # This agent gets slightly more latitude
  - commands: ["docker build*", "docker run*"]
    decision: allow

  - commands: ["npm publish*"]
    decision: prompt
    timeout: 180
```

### 5.4 Evaluation Algorithm

```typescript
function evaluate(
  command: string,
  cwd: string | undefined,
  agentId: string,
  sessionId: string,
): PolicyDecision {
  // Load policies in specificity order (most specific first)
  const policies = [
    ...getSessionOverrides(sessionId),
    getAgentPolicy(agentId),
    getActiveProfile(),
    getDefaultPolicy(),
  ].filter(Boolean);

  // Flatten rules — most specific policies' rules come first
  const allRules = policies.flatMap(p => p.rules);

  // First matching rule wins
  for (const rule of allRules) {
    if (!commandMatchesAny(command, rule.commands)) continue;
    if (rule.cwd && cwd && !pathMatchesAny(cwd, rule.cwd)) continue;

    return {
      decision: rule.decision,
      reason: rule.reason,
      timeout: rule.timeout,
      source: `${rule._policyName}#rule-${rule._index}`,
    };
  }

  // No rule matched → deny
  return {
    decision: "deny",
    reason: "No matching policy rule",
    source: "built-in-default-deny",
  };
}
```

### 5.5 Pattern Matching

Command patterns use **glob-style matching** against the full command string:

- `*` matches any sequence of characters
- `git push*feat/*` matches `git push origin feat/weather` and `git push -u origin feat/my-branch`
- `npm test*` matches `npm test`, `npm test -- --watch`, `npm test:unit`

Implementation: convert glob to regex at policy load time, cache compiled patterns.

### 5.6 Hardcoded Phase 1 Policy

Before YAML loading is implemented, Phase 1 uses hardcoded rules equivalent to the default.yaml above. The evaluation logic is the same — only the loading source changes.

### 5.7 Policy Loading & Hot Reload

Policies are loaded from `~/.bond/policies/` at startup. A `fs.watch` on the directory triggers reload on file changes. No Gateway restart needed for policy updates.

```typescript
class PolicyStore {
  private policies: Map<string, Policy> = new Map();

  async load(policyDir: string): Promise<void>;
  getDefaultPolicy(): Policy | null;
  getAgentPolicy(agentId: string): Policy | null;
  getActiveProfile(): Policy | null;
  getSessionOverrides(sessionId: string): PolicyRule[];
  addSessionOverride(sessionId: string, rule: PolicyRule): void;
  clearSessionOverrides(sessionId: string): void;
}
```

---

## 6. Command Executor

### 6.1 Implementation

The executor runs commands on the host using `child_process.execFile`. It uses the host user's full environment — including any authenticated CLI tools.

```typescript
// gateway/src/broker/executor.ts

import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

interface ExecResult {
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_ms: number;
}

export async function executeCommand(
  command: string,
  options: {
    cwd?: string;
    timeout?: number;      // milliseconds
    env?: Record<string, string>;   // additional env vars (merged with process.env)
  } = {},
): Promise<ExecResult> {
  const start = Date.now();
  const timeout = options.timeout || 60_000;

  const env = {
    ...process.env,
    ...(options.env || {}),
  };

  try {
    const { stdout, stderr } = await execFileAsync(
      "sh", ["-c", command],
      {
        cwd: options.cwd || process.env.HOME,
        env,
        timeout,
        maxBuffer: 10 * 1024 * 1024,   // 10MB — git diff can be large
      },
    );

    return {
      exit_code: 0,
      stdout,
      stderr,
      duration_ms: Date.now() - start,
    };
  } catch (err: any) {
    return {
      exit_code: err.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER" ? -2 :
                 err.killed ? -1 :
                 err.status ?? 1,
      stdout: err.stdout || "",
      stderr: err.stderr || err.message || "",
      duration_ms: Date.now() - start,
    };
  }
}
```

### 6.2 Working Directory

The agent specifies `cwd` in the request. This is typically the host-side path to the repo. The agent knows this because:

1. The agent reads the remote URL from `.git/config` in the mounted volume
2. The agent knows its workspace mount path from the container config
3. For the Bond repo specifically, the host path is in the `BOND_REPO_PATH` env var (set at container creation) or defaults to `~/bond`

The policy engine can constrain `cwd` per-rule if needed (§5.3, `cwd` field).

### 6.3 What the Host Provides

The broker doesn't inject credentials. The host already has them:

| Tool | Auth Source |
|---|---|
| `gh` | `~/.config/gh/hosts.yml` (from `gh auth login`) |
| `git push` (SSH) | `~/.ssh/id_ed25519` (SSH agent) |
| `git push` (HTTPS) | `~/.gitconfig` credential helper or `gh auth setup-git` |
| `npm publish` | `~/.npmrc` token |
| `docker` | `~/.docker/config.json` |
| `aws` | `~/.aws/credentials` |

The agent never sees any of these. The broker runs the command as the host user who already has them configured.

---

## 7. Audit Logger

### 7.1 Log Entry

Every broker request produces one log entry, regardless of decision:

```jsonl
{"ts":"2026-03-10T19:22:14.331Z","agent_id":"01JBOND...","session_id":"conv-abc123","command":"gh pr create --title 'feat: weather' --base main --head feat/weather","cwd":"~/bond","policy_rule":"default#rule-8","decision":"allow","exit_code":0,"stdout_len":62,"duration_ms":2341}
{"ts":"2026-03-10T19:23:01.552Z","agent_id":"01KAGENT...","session_id":"conv-def456","command":"curl https://evil.com","cwd":"/workspace","policy_rule":"default#rule-deny-curl","decision":"deny","reason":"Command is on the deny list","duration_ms":0}
{"ts":"2026-03-10T19:24:33.119Z","agent_id":"01JBOND...","session_id":"conv-abc123","command":"npm publish","cwd":"~/bond","policy_rule":"default#rule-prompt","decision":"prompt_approved","approval":{"surface":"webchat","wait_ms":8200,"approver":"andrew"},"exit_code":0,"duration_ms":11412}
```

**Note:** stdout/stderr content is not logged by default (could contain secrets from command output). Only `stdout_len` and `stderr_len` are logged. Full output can be enabled per-rule or via a config flag for debugging.

### 7.2 Implementation

```typescript
// gateway/src/broker/audit.ts

class AuditLogger {
  private fd: number;

  constructor(logDir: string) {
    const logPath = path.join(logDir, "broker-audit.jsonl");
    this.fd = fs.openSync(logPath, "a", 0o640);
  }

  log(entry: AuditEntry): void {
    const line = JSON.stringify(entry) + "\n";
    fs.writeSync(this.fd, line);
  }

  close(): void {
    fs.closeSync(this.fd);
  }
}
```

### 7.3 Storage

- **Location:** `~/.bond/data/broker-audit.jsonl`
- **Format:** Append-only, one JSON object per line
- **Rotation:** Daily rotation with gzip, 90-day retention (Phase 2)
- **Tamper resistance:** File is on host filesystem, outside all container mounts. Agents cannot modify it.

---

## 8. Approval Flow

### 8.1 When It Triggers

When the policy engine returns `decision: "prompt"` for a command, the broker pauses execution and asks the user for approval.

### 8.2 Flow

```
Agent: broker.exec("npm publish")
  ↓
Policy: "npm publish*" → prompt (timeout: 120s)
  ↓
Broker → WebSocket event to connected frontends:
  ┌──────────────────────────────────────────────────┐
  │  🔒 Agent "bond" requests approval               │
  │                                                    │
  │  Command: npm publish                              │
  │  Directory: ~/bond                      │
  │  Session: feat/weather-tool                        │
  │                                                    │
  │  [Allow]  [Allow All This Session]  [Deny]        │
  └──────────────────────────────────────────────────┘
  ↓
User clicks Allow (or timeout after 120s → deny)
  ↓
Broker executes or denies. Logs the full exchange.
```

### 8.3 Escalation Options

| Option | Effect |
|---|---|
| **Allow** | Execute this one command. |
| **Allow All This Session** | Add a session-scoped override rule matching this command pattern. Auto-revoked when session ends. |
| **Allow for 1 Hour** | Time-boxed override. |
| **Deny** | Block. Agent receives denial error. |

### 8.4 Transport

Approval requests use the Gateway's existing WebSocket infrastructure:

```typescript
// Gateway → Frontend
{
  type: "broker_approval_request",
  id: "01JABCDEF...",
  agentName: "bond",
  command: "npm publish",
  cwd: "~/bond",
  timeout: 120,
}

// Frontend → Gateway
{
  type: "broker_approval_response",
  id: "01JABCDEF...",
  approved: true,
  escalation: "session",   // optional
}
```

Since the broker is embedded in the Gateway, it has direct access to the WebSocket connections and channel manager. No IPC needed.

### 8.5 Multi-Surface

Approval requests are sent to all connected surfaces (WebChat, Telegram, CLI). First response wins. If no surface is connected, the request times out and is denied.

---

## 9. Broker SDK (Agent-Side Python Client)

The SDK is intentionally simple. One method that matters: `exec`.

```python
# backend/app/agent/broker_client.py

"""Broker SDK — agent-side client for the Permission Broker.

All privileged host operations go through the broker. The agent calls
broker.exec(command) instead of subprocess.run(command).

Usage::

    broker = BrokerClient()

    # Create a PR
    result = await broker.exec(
        "gh pr create --title 'feat: weather' --base main --head feat/weather",
        cwd="~/bond",
    )

    # Push a branch
    result = await broker.exec("git push -u origin feat/weather", cwd="~/bond")

    # Run tests
    result = await broker.exec("npm test", cwd="~/bond")
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("bond.agent.broker")


class BrokerError(Exception):
    """Raised when the broker denies a request or is unreachable."""

    def __init__(self, message: str, decision: str = "error", reason: str = ""):
        super().__init__(message)
        self.decision = decision
        self.reason = reason


class BrokerClient:
    """Client for the Permission Broker running on the Gateway."""

    def __init__(self) -> None:
        self.broker_url = os.environ.get(
            "BOND_BROKER_URL", "http://host.docker.internal:18789"
        ).rstrip("/")
        self.token = os.environ.get("BOND_BROKER_TOKEN", "")
        if not self.token:
            logger.warning("BOND_BROKER_TOKEN not set — broker calls will fail auth")

        self._client = httpx.AsyncClient(
            base_url=f"{self.broker_url}/api/v1/broker",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=120.0,  # commands can take a while (builds, tests)
        )

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 60,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a command on the host via the broker.

        Args:
            command: Shell command to run (e.g., "gh pr create --title '...'")
            cwd: Working directory on the host
            timeout: Command timeout in seconds
            env: Additional env vars (non-secret, passed to command)

        Returns:
            {"status": "ok", "exit_code": 0, "stdout": "...", "stderr": "..."}

        Raises:
            BrokerError: If the broker denies the request or is unreachable
        """
        body: dict[str, Any] = {"command": command, "timeout": timeout}
        if cwd:
            body["cwd"] = cwd
        if env:
            body["env"] = env

        try:
            resp = await self._client.post("/exec", json=body)
        except httpx.ConnectError:
            logger.error("Cannot reach broker at %s", self.broker_url)
            raise BrokerError("Broker unreachable", decision="error")

        data = resp.json()

        if data.get("status") == "denied":
            logger.warning(
                "Broker denied: %s — %s (rule: %s)",
                command,
                data.get("reason", ""),
                data.get("policy_rule", "unknown"),
            )
            raise BrokerError(
                f"Command denied: {data.get('reason', 'no reason')}",
                decision=data["decision"],
                reason=data.get("reason", ""),
            )

        return data

    async def renew_token(self) -> None:
        """Renew the broker token before it expires."""
        resp = await self._client.post("/token/renew")
        if resp.status_code == 200:
            data = resp.json()
            self.token = data["token"]
            self._client.headers["Authorization"] = f"Bearer {self.token}"
            logger.info("Broker token renewed")
        else:
            logger.error("Token renewal failed: %d", resp.status_code)

    async def close(self) -> None:
        await self._client.aclose()
```

### 9.1 Usage in Agent Tools

The `repo_pr` tool migrates from subprocess to broker:

```python
# Before (current — runs inside container, needs GITHUB_TOKEN):
async def handle_repo_pr(arguments, context):
    subprocess.run(["git", "push", "-u", "origin", branch], cwd="/bond", ...)
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://api.github.com/repos/.../pulls",
            headers={"Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}"}, ...)

# After (broker — runs on host, no secrets in container):
async def handle_repo_pr(arguments, context):
    broker = context["broker"]
    repo_path = context.get("host_repo_path", "~/bond")

    # Agent already did git add + commit inside the container.
    # Broker handles push and PR creation on the host.
    await broker.exec(f"git push -u origin {branch}", cwd=repo_path)
    result = await broker.exec(
        f"gh pr create --title {shlex.quote(title)} "
        f"--body {shlex.quote(body)} "
        f"--base main --head {branch}",
        cwd=repo_path,
    )
```

---

## 10. Agent Tool Definition

The agent (LLM) needs to know the broker exists and when to use it. This means a tool definition in `definitions.py` and a tool handler in the native tools registry.

### 10.1 Tool Schema

```python
# In backend/app/agent/tools/definitions.py — added to TOOL_DEFINITIONS

{
    "type": "function",
    "function": {
        "name": "host_exec",
        "description": (
            "Execute a command on the host machine via the permission broker. "
            "Use this for operations that need host-side access: git push, "
            "creating PRs (gh pr create), running builds/tests against the host repo, "
            "or any command that needs host credentials (SSH keys, GitHub auth, npm tokens). "
            "The host has authenticated CLI tools (gh, git, ssh, npm) — you do not need "
            "to provide credentials. "
            "Commands run in the specified working directory on the host filesystem. "
            "Returns exit_code, stdout, and stderr. "
            "NOT for: reading/writing files in your workspace (use file_read/file_write), "
            "running code inside your container (use code_execute), "
            "or commands that don't need host access."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute on the host (e.g., 'gh pr create --title ...')",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory on the host (e.g., '~/bond'). Defaults to host repo path.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default 60.",
                    "default": 60,
                },
            },
            "required": ["command"],
        },
    },
},
```

### 10.2 Tool Handler

```python
# In backend/app/agent/tools/native.py

async def handle_host_exec(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute a command on the host via the permission broker."""
    broker = context.get("broker")
    if not broker:
        return {
            "error": "Broker not available. Host execution requires a broker connection.",
            "hint": "Use code_execute for in-container commands instead.",
        }

    command = arguments.get("command", "")
    if not command:
        return {"error": "command is required"}

    cwd = arguments.get("cwd") or context.get("host_repo_path")
    timeout = arguments.get("timeout", 60)

    try:
        result = await broker.exec(command, cwd=cwd, timeout=timeout)
    except BrokerError as e:
        return {
            "error": f"Broker denied: {e.reason}",
            "decision": e.decision,
            "hint": "This command is not allowed by the host policy. Try a different approach.",
        }

    # Return structured result the LLM can interpret
    output = {
        "exit_code": result.get("exit_code", -1),
        "success": result.get("exit_code", -1) == 0,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }

    # Truncate long output to avoid blowing up the context window
    max_output = 8000  # characters
    if len(output["stdout"]) > max_output:
        output["stdout"] = output["stdout"][:max_output] + f"\n... (truncated, {len(result['stdout'])} chars total)"
    if len(output["stderr"]) > max_output:
        output["stderr"] = output["stderr"][:max_output] + f"\n... (truncated, {len(result['stderr'])} chars total)"

    return output
```

### 10.3 Tool Registration

```python
# In backend/app/agent/tools/native_registry.py

from backend.app.agent.tools.native import handle_host_exec

registry.register("host_exec", handle_host_exec)
```

### 10.4 When the Agent Uses Each Tool

The agent has three execution tools. The description and context guide the LLM:

| Tool | Runs Where | Use For |
|---|---|---|
| `code_execute` | Inside the container | Running code, in-container shell commands, file manipulation within workspace |
| `host_exec` | On the host (via broker) | Git push, PR creation, builds/tests against host repo, anything needing host credentials |
| `repo_pr` | **(deprecated → migrated)** | Was: all-in-one PR creation. Now: replaced by `host_exec` calls to `git push` + `gh pr create` |

### 10.5 Response Interpretation

The LLM receives the tool result and uses it to:

1. **Check success:** `exit_code == 0` and `success == true` means the command worked.
2. **Read output:** `stdout` contains the command's output (e.g., the PR URL from `gh pr create`).
3. **Handle errors:** Non-zero `exit_code` + `stderr` tells the agent what went wrong. The agent can retry, try a different approach, or report the error to the user.
4. **Handle denials:** If the broker denied the command, the `error` field explains why and `hint` suggests alternatives. The agent should not retry the same denied command.

Example flow the agent sees:

```
Agent calls: host_exec(command="gh pr create --title 'feat: weather' --base main --head feat/weather")

Tool returns:
{
  "exit_code": 0,
  "success": true,
  "stdout": "https://github.com/biztechprogramming/bond/pull/42\n",
  "stderr": ""
}

Agent responds to user: "PR created: https://github.com/biztechprogramming/bond/pull/42"
```

```
Agent calls: host_exec(command="curl https://example.com")

Tool returns:
{
  "error": "Broker denied: Command is on the deny list",
  "decision": "deny",
  "hint": "This command is not allowed by the host policy. Try a different approach."
}

Agent responds: "I can't make outbound HTTP requests from the host. Let me try a different approach."
```

### 10.6 Broker Context Injection

The `BrokerClient` instance is created during worker startup and passed to tool handlers via the `context` dict:

```python
# In backend/app/worker.py — during startup

from backend.app.agent.broker_client import BrokerClient

broker = BrokerClient()  # reads BOND_BROKER_TOKEN and BOND_BROKER_URL from env

# In the tool context passed to handlers:
tool_context = {
    "agent_id": agent_id,
    "broker": broker,
    "host_repo_path": os.environ.get("BOND_HOST_REPO_PATH", "~/bond"),
    # ... existing context ...
}
```

---

## 11. Broker Router

```typescript
// gateway/src/broker/router.ts

import { Router } from "express";
import { validateToken } from "./tokens.js";
import { PolicyEngine } from "./policy.js";
import { AuditLogger } from "./audit.js";
import { executeCommand } from "./executor.js";
import { ApprovalManager } from "./approval.js";

export function createBrokerRouter(config: BrokerConfig): Router {
  const router = Router();
  const policy = new PolicyEngine(config.policyDir);
  const audit = new AuditLogger(config.dataDir);

  // Auth middleware — all broker routes require a valid token
  router.use((req: any, res: any, next: any) => {
    const auth = req.headers.authorization;
    if (!auth?.startsWith("Bearer ")) {
      return res.status(401).json({ status: "error", error: "Missing token" });
    }
    const payload = validateToken(auth.slice(7));
    if (!payload) {
      return res.status(401).json({ status: "error", error: "Invalid or expired token" });
    }
    req.agentId = payload.sub;
    req.sessionId = payload.sid;
    next();
  });

  // POST /exec — the only endpoint that matters
  router.post("/exec", async (req: any, res: any) => {
    const { command, cwd, timeout, env } = req.body;
    const { agentId, sessionId } = req;
    const start = Date.now();

    if (!command || typeof command !== "string") {
      return res.status(400).json({ status: "error", error: "command is required" });
    }

    // 1. Evaluate policy
    const decision = policy.evaluate(command, cwd, agentId, sessionId);

    // 2. Handle deny
    if (decision.decision === "deny") {
      audit.log({
        agent_id: agentId,
        session_id: sessionId,
        command,
        cwd,
        policy_rule: decision.source,
        decision: "deny",
        reason: decision.reason,
        duration_ms: Date.now() - start,
      });
      return res.status(403).json({
        status: "denied",
        decision: "deny",
        reason: decision.reason,
        policy_rule: decision.source,
      });
    }

    // 3. Handle prompt
    if (decision.decision === "prompt") {
      const approval = await config.approvalManager.requestApproval(
        agentId, sessionId, command, cwd, decision.timeout || 120,
      );
      if (!approval.approved) {
        const finalDecision = approval.timedOut ? "prompt_timeout" : "prompt_denied";
        audit.log({
          agent_id: agentId,
          session_id: sessionId,
          command,
          cwd,
          policy_rule: decision.source,
          decision: finalDecision,
          duration_ms: Date.now() - start,
        });
        return res.status(403).json({
          status: "denied",
          decision: finalDecision,
          reason: approval.timedOut ? "Approval timed out" : "User denied the request",
        });
      }
      // Approved — fall through to execute
    }

    // 4. Execute
    const result = await executeCommand(command, {
      cwd: cwd || process.env.HOME,
      timeout: (timeout || 60) * 1000,
      env,
    });

    // 5. Audit
    const finalDecision = decision.decision === "prompt" ? "prompt_approved" : "allow";
    audit.log({
      agent_id: agentId,
      session_id: sessionId,
      command,
      cwd,
      policy_rule: decision.source,
      decision: finalDecision,
      exit_code: result.exit_code,
      stdout_len: result.stdout.length,
      stderr_len: result.stderr.length,
      duration_ms: Date.now() - start,
    });

    // 6. Respond
    return res.json({
      status: "ok",
      decision: finalDecision,
      exit_code: result.exit_code,
      stdout: result.stdout,
      stderr: result.stderr,
      duration_ms: result.duration_ms,
    });
  });

  // Token renewal
  router.post("/token/renew", (req: any, res: any) => {
    const newToken = issueToken(req.agentId, req.sessionId);
    res.json({ token: newToken });
  });

  // Health
  router.get("/health", (_req: any, res: any) => {
    res.json({
      status: "ok",
      rules: policy.ruleCount(),
      uptime: process.uptime(),
    });
  });

  return router;
}
```

### 11.1 Mount in Gateway

```typescript
// gateway/src/server.ts — one line addition

import { createBrokerRouter } from "./broker/router.js";

// ... existing routes ...

app.use("/api/v1/broker", createBrokerRouter(brokerConfig));
```

---

## 12. File Structure

```
gateway/src/broker/
├── router.ts           # Express router — /exec, /token/renew, /health
├── tokens.ts           # HMAC token issue/validate/revoke
├── policy.ts           # Policy engine — rule loading, matching, evaluation
├── executor.ts         # Command execution via child_process
├── audit.ts            # Append-only JSONL audit logger
├── approval.ts         # User approval flow (prompt mode)
├── types.ts            # Shared interfaces
└── __tests__/
    ├── tokens.test.ts
    ├── policy.test.ts
    ├── executor.test.ts
    ├── audit.test.ts
    └── broker-router.test.ts

backend/app/agent/
├── broker_client.py    # Agent-side SDK
└── ...
```

Policy files:
```
~/.bond/policies/
├── default.yaml        # Base rules (allowlist + deny list)
├── profiles/
│   ├── development.yaml
│   └── production.yaml
└── agents/
    └── {agent-id}.yaml  # Per-agent overrides
```

---

## 13. Phase 1 Build Order

1. **`tokens.ts`** — issue, validate, revoke (~100 lines)
2. **`audit.ts`** — append-only JSONL writer (~60 lines)
3. **`policy.ts`** — hardcoded rules, glob matching, evaluate() (~200 lines)
4. **`executor.ts`** — child_process wrapper (~60 lines)
5. **`router.ts`** — Express router, tie it all together (~120 lines)
6. **Mount in `server.ts`** — one line
7. **`broker_client.py`** — Python SDK (~100 lines)
8. **`host_exec` tool definition + handler** — tool schema in `definitions.py`, handler in `native.py`, registration in `native_registry.py` (~80 lines)
9. **Migrate `repo_pr` tool** — deprecate, replace with `host_exec` calls to `git push` + `gh pr create`
10. **Broker context injection** — instantiate `BrokerClient` in worker startup, pass via tool context
11. **Update `SandboxManager`** — inject `BOND_BROKER_TOKEN`, `BOND_BROKER_URL`, `BOND_HOST_REPO_PATH`, remove `GITHUB_TOKEN`
12. **Tests** — unit tests for each module + integration test

**Estimated total:** ~1000 lines of new code, ~3-4 days.

### What Works After Phase 1

- Agent calls `broker.exec("gh pr create ...")` → runs on host → PR created
- Agent calls `broker.exec("git push -u origin feat/weather")` → runs on host → pushed
- Agent calls `broker.exec("npm test")` → runs on host → test results returned
- Agent calls `broker.exec("curl https://evil.com")` → denied by policy
- Every request logged to `~/.bond/data/broker-audit.jsonl`
- Agent token validated on every request — no more self-reported identity

### What Comes in Phase 2

- YAML policy loading from `~/.bond/policies/` (replace hardcoded rules)
- Hot-reload on policy file changes
- Approval flow UI in frontend
- Per-agent policy overrides
- Session-scoped escalation
- Audit log rotation

---

## 14. Testing Strategy

### Unit Tests

- **`tokens.test.ts`** — issue, validate, expire, tamper detection, timing-safe comparison
- **`policy.test.ts`** — glob matching, rule ordering, agent override, session override, default deny, cwd constraints
- **`executor.test.ts`** — success, non-zero exit, timeout, max buffer, env merging
- **`audit.test.ts`** — log write, JSON validity, file append, no stdout content logged by default
- **`broker-router.test.ts`** — auth rejection (no token, bad token, expired token), policy deny (403), policy allow (200), prompt flow

### Integration Test

```typescript
// Spin up broker, issue a token, make a real request
test("full lifecycle: issue token → exec allowed command → verify audit", async () => {
  const token = issueToken("test-agent", "test-session");

  const resp = await fetch("http://localhost:18789/api/v1/broker/exec", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ command: "echo hello", cwd: "/tmp" }),
  });

  expect(resp.status).toBe(200);
  const data = await resp.json();
  expect(data.exit_code).toBe(0);
  expect(data.stdout.trim()).toBe("hello");

  // Verify audit log has the entry
  const auditLine = fs.readFileSync(auditLogPath, "utf-8").trim().split("\n").pop();
  const entry = JSON.parse(auditLine);
  expect(entry.command).toBe("echo hello");
  expect(entry.decision).toBe("allow");
  expect(entry.agent_id).toBe("test-agent");
});
```

### Agent-Side Tests

- **`test_broker_client.py`** — exec, deny handling, timeout, BrokerError, token renewal
- **`test_repo_pr_migration.py`** — verify `repo_pr` tool uses broker.exec, not subprocess

---

## 15. Security Notes

### What the Broker Prevents

- Agent reading host secrets (no env vars, no vault mount, no SSH key mount)
- Agent running commands the policy doesn't allow (default deny)
- Agent impersonating another agent (cryptographic tokens)
- Unaudited host operations (every request logged)

### What the Broker Does NOT Prevent (Phase 1)

- Agent running arbitrary code inside its own container (subprocess, file ops within container)
- Agent making outbound HTTP from inside the container (network isolation is Phase 2)
- A compromised Gateway process accessing broker internals (embedded, same memory space)
- Agent reading the `BOND_BROKER_TOKEN` env var (by design — it's how it authenticates)
- Agent crafting creative command strings to bypass glob patterns (mitigated by catch-all deny, but glob matching has limits)

### Glob Pattern Limitations

Glob patterns match command *strings*, not parsed commands. An agent could try:
- `git push origin main` → caught by `git push*main*`
- `git push origin ma""in` → shell quoting tricks could bypass string matching
- `$(echo git) push origin main` → command substitution in the string

**Mitigation:** The broker runs the command through `sh -c`, so quoting tricks work at the shell level. But policy matching happens on the raw string *before* shell expansion. For Phase 2, consider parsing commands into (program, args) before matching. For Phase 1, the catch-all deny rule handles unrecognized patterns.

---

## 16. Open Questions

1. **CWD mapping.** The agent runs in a container with `/bond` as the repo. The host has it at `~/bond`. When the agent says `broker.exec("npm test", cwd="/bond")`, should the broker translate `/bond` → `~/bond`? Or should the agent know the host path? Recommendation: agent sends the host path (stored in a config env var like `BOND_HOST_REPO_PATH`).

2. **Output size limits.** Commands like `git diff` can produce megabytes of output. Should the broker truncate? Stream? Return a reference to a file? Phase 1: truncate at 10MB (the `maxBuffer` setting). Add streaming in Phase 2.

3. **Concurrent requests.** Should the broker limit concurrent command executions per agent? An agent could DOS the host by firing 100 `npm install` commands. Recommendation: rate limit per agent (e.g., 5 concurrent, 30/minute).

4. **Backward compatibility.** During migration, `repo_pr` should try broker first, fall back to direct execution with a deprecation warning. Remove fallback in Phase 2.
