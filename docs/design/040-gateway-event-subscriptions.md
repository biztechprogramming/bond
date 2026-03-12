# Design Doc 040: Gateway Event Subscriptions (Webhooks → Agent Notifications)

## Status: Draft
## Author: Bond AI
## Date: 2026-03-12

---

## 1. Problem Statement

When a Bond agent spawns a coding agent (Claude Code, Codex, etc.), the coding agent runs asynchronously in a subprocess. The parent agent returns immediately with *"I'll let you know when it completes!"* — but **never follows up**. The user has to ask "Did this complete?" because there's no mechanism to notify the agent when work finishes.

The existing `coding-agent-completion-loop.md` (design doc) proposed using SpacetimeDB subscriptions for this, but that approach is scoped to events originating **inside Bond's own processes**. There's a broader, more general problem:

**Bond agents need to react to external events** — especially git pushes from coding agents that run in separate containers/processes and push to GitHub. The gateway already receives GitHub webhooks but only handles `push → main`. We need a general-purpose event subscription system where:

1. **External systems** (GitHub, CI/CD, etc.) push events to the gateway via webhooks
2. **The gateway** normalizes and routes these events
3. **Agents** subscribe to events they care about (e.g., "notify me when branch X is pushed to repo Y")
4. **The gateway** delivers matching events back to the subscribing agent's conversation, triggering an LLM turn

### Why This Matters

- **Coding agents push branches** — the parent agent should be notified when the push lands
- **CI/CD pipelines** — agents could react to build pass/fail
- **PR reviews** — agents could be notified when their PR gets comments
- **Future extensibility** — any webhook-driven event can trigger agent re-engagement

---

## 2. Current Architecture

### 2.1 What Exists Today

```
GitHub ──webhook──▶ Gateway /webhooks/github
                         │
                         ▼
                    webhooks.ts (signature verification)
                         │
                         ▼
                    Only handles: push → refs/heads/main
                    Action: opts.onMainMerge() (currently a TODO)
```

```
Coding Agent (subprocess in worker container)
     │
     ├── Writes files, commits, pushes to branch
     ├── GitDiffWatcher polls for changes → SSE events → frontend
     └── On exit: event_queue.put({"type": "done"}) → SSE → frontend only
```

### 2.2 What's Missing

1. **No event routing** — webhooks.ts only checks `push → main`, ignores all other events
2. **No subscription model** — no way for an agent/conversation to say "tell me when X happens"
3. **No agent re-engagement** — even if we detected the push, there's no mechanism to trigger a new LLM turn in the original conversation
4. **No webhook auto-registration** — webhooks must be manually configured in GitHub repo settings
5. **No event history** — events are fire-and-forget with no audit trail

---

## 3. Proposed Design

### 3.1 High-Level Architecture

```
GitHub ──webhook──▶ Gateway /webhooks/github
                         │
                         ▼
                    WebhookNormalizer
                    (verify signature, parse payload, normalize to GatewayEvent)
                         │
                         ▼
                    EventBus.emit(event)
                         │
                         ├── Persist to EventHistory
                         │
                         ▼
                    EventBus.match(event)
                    (find subscriptions matching repo + branch + type)
                         │
                         ▼
                    CompletionDispatcher.dispatch(event, subscription)
                    (trigger LLM turn in the subscribing conversation)
                         │
                         ▼
                    Agent gets system message with event details
                    Agent responds → streamed to user via WebSocket
```

> **Important distinction:**
> - **Webhook registration** is infrastructure setup. It happens on gateway startup (reconciliation) and when a new repo is added to an agent. It tells GitHub "send events to this URL."
> - **Event subscriptions** are per-conversation and ephemeral. They are created automatically when a `coding_agent` tool call is detected during a conversation turn. They tell the EventBus "when an event matching this filter arrives, notify this conversation."

### 3.2 Core Components

#### 3.2.1 GatewayEvent (Normalized Event Model)

```typescript
interface GatewayEvent {
  id: string;                    // ulid
  source: string;                // "github", "ci", "internal"
  type: string;                  // "push", "pull_request", "check_run"
  repo: string;                  // "owner/repo"
  branch?: string;               // "feature/fix-auth"
  actor?: string;                // "github-username"
  payload: Record<string, any>;  // raw webhook payload (trimmed)
  timestamp: number;             // unix ms
}
```

#### 3.2.2 EventSubscription

```typescript
interface EventSubscription {
  id: string;                    // ulid
  conversationId: string;        // which conversation to notify
  agentId: string;               // which agent is subscribed
  filter: EventFilter;           // what to match on
  context: string;               // human-readable context for the LLM
  createdAt: number;             // unix ms
  expiresAt: number;             // unix ms — auto-cleanup
  maxDeliveries: number;         // default 1 — auto-unsubscribe after N deliveries
  deliveryCount: number;         // how many times matched so far
}

interface EventFilter {
  source?: string;               // "github"
  type?: string;                 // "push"
  repo?: string;                 // "owner/repo"
  branch?: string;               // exact or glob: "feature/*"
  actor?: string;                // "github-username"
}
```

**Key design decisions:**

- **TTL-based expiry**: Subscriptions auto-expire (default: 2 hours). Prevents stale subscriptions from accumulating if an agent crashes.
- **maxDeliveries**: Most coding agent notifications are one-shot — "tell me when the branch is pushed" should fire once, then auto-unsubscribe.
- **Per-conversation**: Each subscription is tied to a `conversationId`. When a matching event arrives, it triggers a new LLM turn **in that specific conversation**. This is the right granularity because:
  - The user is watching that conversation
  - The agent has the full context of what it asked the coding agent to do
  - The system message can reference the original task
  - Multiple conversations can independently watch different branches of the same repo
- **In-memory storage (v1)**: Subscriptions are ephemeral. If the gateway restarts, they're lost. This is acceptable because:
  - Coding agents have a max runtime (default 30 min)
  - If the gateway restarts, the agent conversation is effectively interrupted anyway
  - Persistence can be added later via SpacetimeDB if needed (see §6)

#### 3.2.3 EventBus

The central pub/sub coordinator inside the gateway:

```typescript
class EventBus {
  private subscriptions: Map<string, EventSubscription>;
  private history: EventHistory;
  private cleanupInterval: NodeJS.Timer;

  // Called by webhook handlers after normalizing the payload
  emit(event: GatewayEvent): void;

  // Called by workers/agents via HTTP API
  subscribe(sub: Omit<EventSubscription, "id" | "createdAt" | "deliveryCount">): string;
  unsubscribe(id: string): boolean;

  // Internal: find matching subscriptions for an event
  private match(event: GatewayEvent): EventSubscription[];

  // Internal: periodic cleanup of expired subscriptions
  private cleanup(): void;
}
```

**Matching logic:**

```typescript
function matches(filter: EventFilter, event: GatewayEvent): boolean {
  if (filter.source && filter.source !== event.source) return false;
  if (filter.type && filter.type !== event.type) return false;
  if (filter.repo && filter.repo !== event.repo) return false;
  if (filter.branch) {
    if (filter.branch.includes("*")) {
      // Glob match: "feature/*" matches "feature/fix-auth"
      const regex = new RegExp("^" + filter.branch.replace(/\*/g, ".*") + "$");
      if (!event.branch || !regex.test(event.branch)) return false;
    } else {
      if (filter.branch !== event.branch) return false;
    }
  }
  if (filter.actor && filter.actor !== event.actor) return false;
  return true;
}
```

#### 3.2.4 EventHistory

Persists all events for debugging and audit:

```typescript
class EventHistory {
  private events: GatewayEvent[] = [];
  private maxAge: number = 24 * 60 * 60 * 1000; // 24 hours
  private maxEvents: number = 10_000;

  append(event: GatewayEvent): void;

  // Query recent events
  query(filter: Partial<EventFilter>, limit?: number): GatewayEvent[];

  // Periodic pruning of old events
  private prune(): void;
}
```

**Storage (v1):** In-memory ring buffer with 24h retention, max 10k events. Survives within a gateway process lifecycle. Lost on restart.

**Storage (v2):** SpacetimeDB `gateway_events` table. Survives restarts, queryable from frontend for debugging UI.

**API:**
```
GET /api/v1/events/history?repo=owner/repo&type=push&limit=50
```

#### 3.2.5 CompletionDispatcher

Handles the "trigger an agent turn" side of event delivery:

```typescript
class CompletionDispatcher {
  constructor(
    private backendClient: BackendClient,
    private webchat: WebChatChannel,
  ) {}

  async dispatch(event: GatewayEvent, subscription: EventSubscription): Promise<void> {
    // 1. Rate limit: max 3 auto-turns per conversation per 60s
    if (this.isRateLimited(subscription.conversationId)) {
      console.warn(`[events] Rate limited: ${subscription.conversationId}`);
      return;
    }

    // 2. Build system message
    const systemMessage = this.buildSystemMessage(event, subscription);

    // 3. Trigger agent turn via the existing pipeline
    // This reuses the same TurnExecutor + ResponseFanOut pipeline
    // that handles normal user messages
    const stream = this.backendClient.conversationTurnStream(
      subscription.conversationId,
      systemMessage,
      subscription.agentId,
    );

    // 4. Stream response to user via WebSocket
    for await (const chunk of stream) {
      this.webchat.sendToConversation(subscription.conversationId, chunk);
    }
  }

  private buildSystemMessage(event: GatewayEvent, sub: EventSubscription): string {
    return [
      `[SYSTEM EVENT: ${event.type} on ${event.repo}]`,
      `Branch: ${event.branch || "N/A"}`,
      `Actor: ${event.actor || "unknown"}`,
      `Context: ${sub.context}`,
      ``,
      `You previously spawned a coding agent and subscribed to be notified when it pushed.`,
      `The push has landed. Summarize the changes for the user and report the status.`,
      ``,
      `IMPORTANT: Do NOT spawn another coding agent in this response.`,
      `If there are follow-up actions needed, describe them but let the user decide.`,
    ].join("\n");
  }
}
```

---

## 4. Automatic Subscription (No Manual API Calls)

### 4.1 The Problem with Explicit Subscriptions

The original design had workers calling `POST /api/v1/events/subscribe` after spawning a coding agent. This is fragile:
- The worker has to know about the gateway's event system
- The branch name might not be known until the coding agent creates it
- It adds coupling between the worker and the gateway

### 4.2 Solution: Gateway Auto-Subscribes on `coding_agent_started`

The gateway already sees `coding_agent_started` events in the SSE stream from the worker (see `turn-executor.ts` line 97). **The gateway should automatically create a subscription when it sees this event.**

```
User message → Gateway Pipeline → TurnExecutor → Worker SSE stream
                                       │
                                       ├── event: "coding_agent_started"
                                       │   data: { agent_type, repo, branch }
                                       │
                                       └── Gateway auto-creates subscription:
                                           {
                                             conversationId: current conversation,
                                             agentId: current agent,
                                             filter: { source: "github", type: "push", repo, branch: "feature/*" },
                                             context: "Coding agent (claude) spawned for this conversation",
                                             maxDeliveries: 1,
                                             expiresAt: now + 2h
                                           }
```

**What changes in the worker:** The `coding_agent_started` SSE event needs to include `repo` and `branch` in its payload. Currently it only sends `agent_type`. We add:

```python
# In worker.py, coding_agent_started event
yield _sse_event("coding_agent_started", {
    "agent_type": session.agent_type,
    "repo": _detect_repo(session.working_directory),     # NEW
    "branch": session.branch or _detect_branch(session), # NEW
    "working_directory": session.working_directory,       # NEW
})
```

**What changes in the gateway:** `TurnExecutor` gains a hook:

```typescript
case "coding_agent_started":
  await context.emit("coding_agent_started", {
    agent_type: event.data.agent_type,
    conversationId: message.conversationId,
  });
  // AUTO-SUBSCRIBE for push notification
  if (event.data.repo && this.eventBus) {
    this.eventBus.subscribe({
      conversationId: message.conversationId!,
      agentId: message.agentId || "",
      filter: {
        source: "github",
        type: "push",
        repo: event.data.repo,
        branch: event.data.branch || "*",  // wildcard if branch unknown
      },
      context: `Coding agent (${event.data.agent_type}) spawned for conversation ${message.conversationId}`,
      expiresAt: Date.now() + 2 * 60 * 60 * 1000,
      maxDeliveries: 1,
    });
  }
  break;
```

### 4.3 Branch Name Reliability

**Q: Why does the branch parameter matter for subscription reliability?**

When a coding agent is spawned, the `branch` parameter in the `coding_agent` tool call determines what branch the agent checks out and pushes to. If the agent specifies `branch: "fix/auth-bug"`, we can create a precise subscription for that exact branch. The webhook will match.

But if `branch` is omitted, the coding agent creates its own branch name (usually prefixed like `feature/`, `fix/`, `chore/`). The parent agent doesn't know the exact name. In that case we have two options:

1. **Wildcard subscription**: `branch: "*"` — matches any push to the repo. Risk: false positives from unrelated pushes.
2. **Infer from convention**: `branch: "feature/*|fix/*|chore/*"` — matches the common prefixes. Better but still imprecise.
3. **Report back from worker**: The coding agent's `git push` output includes the branch name. The worker can emit a `coding_agent_pushed` event with the exact branch, and the gateway can narrow the subscription.

**Recommendation for v1:** Use option 3. The worker already monitors the coding agent process. When it detects a push (via the GitDiffWatcher or process output), it emits `coding_agent_pushed` with the branch name. The gateway refines the subscription filter. If no push event arrives before the process exits, the subscription expires harmlessly.

---

## 5. Automatic Webhook Registration

### 5.1 The Problem

Currently, GitHub webhooks must be manually configured per-repo (Settings → Webhooks → Add webhook). This is error-prone and doesn't scale.

### 5.2 Solution: UI-Driven Repo Discovery + SpacetimeDB TrackedRepo Table

Repo discovery is a **one-time UI interaction** when a user adds or edits a workspace mount for an agent. The repos the user selects are stored in SpacetimeDB. The gateway reads from SpacetimeDB at startup — no filesystem scanning at all.

#### Repo Discovery Flow (UI Interaction)

When a user adds or edits a workspace mount for an agent in the UI:

1. The UI calls `POST /api/v1/repos/scan` with the mount path
2. The gateway runs `discoverRepos(mountPath)` — finds all `.git` directories with GitHub remotes
3. Discovered repos are returned to the UI as a list
4. The UI presents a checkbox list: the user selects which repos to track
5. The confirmed selections are saved to the `TrackedRepo` table in SpacetimeDB
6. The gateway registers webhooks for the newly selected repos and sets `webhook_registered = true`

**If new repos are added to the directory later**, the user can trigger a re-scan via a "Re-scan" button on the agent settings page, or add repos manually.

#### discoverRepos() — UI Scan Endpoint Only

The `discoverRepos()` function is **only** called from the UI scan flow. It is exposed as an API endpoint:

```
POST /api/v1/repos/scan
Body: { "path": "/workspace/bond" }
Response: { "repos": [{ "localPath": "...", "remote": "...", "owner": "...", "name": "..." }] }
```

It is **not** called on gateway startup.

Given a workspace path (e.g., `/workspace`), `discoverRepos(workspacePath)`:

1. Check if `workspacePath` itself is a git repo (has a `.git` directory at its root)
2. Scan immediate subdirectories for `.git` directories (max depth 2–3 levels, avoiding `node_modules`, etc.)
3. Skip git submodules — `.git` entries that are **files** (pointing to `../.git/modules/`) rather than directories
4. For each discovered `.git` directory, run `git -C <path> remote get-url origin` to get the remote URL
5. Filter to GitHub remotes only — parse `owner/repo` from `git@github.com:owner/repo.git` or `https://github.com/owner/repo.git` URLs; log and skip non-GitHub remotes
6. Deduplicate (multiple paths may point to the same repo)

```typescript
interface DiscoveredRepo {
  localPath: string;   // absolute path to the repo root
  remote: string;      // raw remote URL
  owner: string;       // "biztechprogramming"
  repo: string;        // "bond"
}

// Called only from POST /api/v1/repos/scan (UI flow — not startup)
async function discoverRepos(workspacePath: string): Promise<DiscoveredRepo[]>
```

#### TrackedRepo — SpacetimeDB Table

Repos selected by the user are stored in a dedicated SpacetimeDB table — **not** as a JSON field on the mount. A single mount can have zero, one, or many tracked repos (one-to-many relationship).

```
TrackedRepo table:
  - id: u64 (autoinc, primary key)
  - agent_id: u64 (FK to agent)
  - owner: string (GitHub org/user, e.g. "biztechprogramming")
  - name: string (repo name, e.g. "bond")
  - remote_url: string (e.g. "git@github.com:biztechprogramming/bond.git")
  - local_path: string (e.g. "/workspace/bond")
  - webhook_registered: bool (tracks whether webhook was successfully created)
  - created_at: Timestamp
```

Workspace paths come from **agent configurations** (the mount path selected in the UI). Repos are never hard-coded in config files.

### 5.3 How It Works

#### 5.3.1 Trigger Points for Webhook Registration

Webhook registration happens at two distinct points — both use the same `WebhookRegistrar.ensureWebhooks()` under the hood.

**Trigger A: Gateway process startup (reconciliation)**

When the gateway process starts, it queries SpacetimeDB for all tracked repos and reconciles webhooks — no filesystem scanning:

```
Gateway process starts
     │
     ├── Load config (bond.json + env vars)
     ├── Initialize EventBus + EventHistory
     │
     ├── Query SpacetimeDB TrackedRepo table
     │   (all rows — webhook_registered may be true or false)
     │
     └── WebhookRegistrar.ensureWebhooks(trackedRepos)
         └── For each repo:
             ├── GET /repos/{owner}/{repo}/hooks
             │   → Check if our webhook already exists (match on URL)
             │
             ├── If missing:
             │   POST /repos/{owner}/{repo}/hooks
             │   {
             │     "config": {
             │       "url": "https://<gateway-host>/webhooks/github",
             │       "content_type": "json",
             │       "secret": "<GITHUB_WEBHOOK_SECRET>"
             │     },
             │     "events": ["push", "pull_request", "check_run"],
             │     "active": true
             │   }
             │   → Set webhook_registered = true in SpacetimeDB
             │
             └── If exists but misconfigured:
                 PATCH /repos/{owner}/{repo}/hooks/{hook_id}
                 → Update URL, events, or secret as needed
```

This is **idempotent** — it checks existing webhooks and creates/updates only as needed. Purpose: catch webhook drift (deleted from GitHub's side, config changed, gateway restarted after a new repo was added).

**Trigger B: User selects repos in the UI (after mount scan)**

When a user confirms their checkbox selections after scanning a workspace mount, the gateway registers webhooks for the newly selected repos:

```
User confirms repo selections in agent settings UI
     │
     └── POST /api/v1/repos/register  (or SpacetimeDB mutation)
             │
             ├── Save each selected repo to TrackedRepo in SpacetimeDB
             │   (webhook_registered = false initially)
             │
             └── WebhookRegistrar.ensureWebhooks(selectedRepos)
                 │   → Same create/update logic as startup reconciliation
                 │
                 └── Set webhook_registered = true for each success
```

Log results: "Webhooks configured for N repos"

#### 5.3.2 Accessing the GitHub API from the Gateway

The gateway runs on the host as a long-running process — not inside a Docker container. This means it already has direct access to the host's `gh` CLI and its stored credentials. Two practical approaches:

**Option B: GitHub App or Personal Access Token (PAT) in the environment** ✅ Recommended for production
- Set `GITHUB_TOKEN` as an environment variable (from `.env` or a secrets manager)
- Use the GitHub REST API directly via `fetch()` — no external dependencies
- The same token that `gh` uses on the host (`gh auth token` outputs it)
- Scopes needed: `admin:repo_hook` (to create/manage webhooks) + `repo` (to list repos)

**Option C: Read the token from `gh auth token` at gateway startup** ✅ Recommended for development
- Since the gateway runs on the host where `gh` is already authenticated, it can simply call `gh auth token` directly at startup — no mounted config or Docker socket tricks needed
- Simpler than managing a separate PAT during local development

```typescript
// In WebhookRegistrar or config loading (development convenience)
import { execSync } from "child_process";
const token = process.env.GITHUB_TOKEN
  ?? execSync("gh auth token", { encoding: "utf-8" }).trim();
```

**Recommendation:** Option B for production (explicit `GITHUB_TOKEN` env var), Option C for development (auto-read from `gh`). For local development, add to `.env`:

```bash
# .env
GITHUB_TOKEN=$(gh auth token)       # or paste directly: GITHUB_TOKEN=ghp_xxxx
GITHUB_WEBHOOK_SECRET=mysecret
GATEWAY_EXTERNAL_URL=https://abc123.trycloudflare.com
```

#### 5.3.3 Determining the Gateway's External URL

Webhooks need a publicly-reachable URL. The gateway needs to know its own external URL:

- **Production:** `GATEWAY_EXTERNAL_URL` env var (e.g., `https://bond.example.com`)
- **Development:** Use a tunnel. The gateway can auto-detect if `cloudflared` or `ngrok` is running and extract the tunnel URL. Or require `GATEWAY_EXTERNAL_URL` to be set manually.
- **Fallback:** If no external URL is configured, skip webhook registration and log a warning.

#### 5.3.4 WebhookRegistrar Implementation

```typescript
class WebhookRegistrar {
  constructor(
    private githubToken: string,
    private webhookSecret: string,
    private externalUrl: string,
  ) {}

  /**
   * Called on gateway startup or when a new workspace is added.
   * Ensures webhooks exist for all discovered repos.
   * repos: "owner/repo" strings (already parsed by discoverRepos)
   */
  async ensureWebhooks(repos: string[]): Promise<void> {
    const webhookUrl = `${this.externalUrl}/webhooks/github`;

    for (const repo of repos) {
      try {
        const existing = await this.listHooks(repo);
        const ours = existing.find((h: any) => h.config?.url === webhookUrl);

        if (ours) {
          // Verify config is correct
          if (!this.isCorrectlyConfigured(ours)) {
            await this.updateHook(repo, ours.id, webhookUrl);
            console.log(`[webhooks] Updated webhook for ${repo}`);
          } else {
            console.log(`[webhooks] Webhook already configured for ${repo}`);
          }
        } else {
          await this.createHook(repo, webhookUrl);
          console.log(`[webhooks] Created webhook for ${repo}`);
        }
      } catch (err) {
        console.error(`[webhooks] Failed to configure webhook for ${repo}:`, err);
      }
    }
  }

  /**
   * Discover all GitHub repos under a workspace path.
   * Called ONLY from POST /api/v1/repos/scan (UI flow — not gateway startup).
   *
   * Strategy:
   *   1. Check if workspacePath itself is a git repo (.git directory at root)
   *   2. Scan subdirectories up to MAX_DEPTH for .git directories
   *   3. Skip submodules (.git files pointing to ../.git/modules/)
   *   4. Run `git -C <path> remote get-url origin` for each discovered repo
   *   5. Filter to GitHub remotes; log and skip non-GitHub or missing remotes
   *   6. Deduplicate by owner/repo
   *   7. Don't follow symlinks (avoids cycles)
   */
  async discoverRepos(workspacePath: string): Promise<DiscoveredRepo[]> {
    const MAX_DEPTH = 3;
    const results = new Map<string, DiscoveredRepo>(); // keyed by "owner/repo" to deduplicate

    const scan = async (dir: string, depth: number): Promise<void> => {
      if (depth > MAX_DEPTH) return;

      let entries: fs.Dirent[];
      try {
        entries = await fs.promises.readdir(dir, { withFileTypes: true });
      } catch (err) {
        console.warn(`[webhooks] Cannot read directory ${dir}: ${err}`);
        return;
      }

      for (const entry of entries) {
        if (entry.name === ".git") {
          // Don't follow symlinks
          if (entry.isSymbolicLink()) continue;

          if (entry.isDirectory()) {
            // Real git repo — not a submodule
            const repoPath = dir;
            try {
              const remote = this.getGitRemote(repoPath);
              const parsed = this.parseGitHubUrl(remote);
              if (parsed) {
                const [owner, repo] = parsed.split("/");
                results.set(parsed, { localPath: repoPath, remote, owner, repo });
              } else {
                console.debug(`[webhooks] Non-GitHub remote at ${repoPath}: ${remote} — skipping`);
              }
            } catch (err) {
              console.debug(`[webhooks] No remote for repo at ${repoPath}: ${err}`);
            }
          } else if (entry.isFile()) {
            // .git file → submodule — skip
            console.debug(`[webhooks] Skipping submodule at ${dir}`);
          }
          // Found .git — don't recurse deeper into this repo
          continue;
        }

        // Skip node_modules and other common non-repo directories
        if (entry.name === "node_modules" || entry.name === ".cache") continue;

        if (entry.isDirectory() && !entry.isSymbolicLink()) {
          await scan(path.join(dir, entry.name), depth + 1);
        }
      }
    };

    await scan(workspacePath, 0);
    return [...results.values()];
  }

  private async listHooks(repo: string): Promise<any[]> {
    const res = await fetch(`https://api.github.com/repos/${repo}/hooks`, {
      headers: { Authorization: `Bearer ${this.githubToken}`, Accept: "application/vnd.github+json" },
    });
    if (!res.ok) throw new Error(`GitHub API ${res.status}: ${await res.text()}`);
    return res.json();
  }

  private async createHook(repo: string, url: string): Promise<void> {
    const res = await fetch(`https://api.github.com/repos/${repo}/hooks`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.githubToken}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        config: { url, content_type: "json", secret: this.webhookSecret },
        events: ["push", "pull_request", "check_run"],
        active: true,
      }),
    });
    if (!res.ok) throw new Error(`GitHub API ${res.status}: ${await res.text()}`);
  }

  private async updateHook(repo: string, hookId: number, url: string): Promise<void> {
    const res = await fetch(`https://api.github.com/repos/${repo}/hooks/${hookId}`, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${this.githubToken}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        config: { url, content_type: "json", secret: this.webhookSecret },
        events: ["push", "pull_request", "check_run"],
        active: true,
      }),
    });
    if (!res.ok) throw new Error(`GitHub API ${res.status}: ${await res.text()}`);
  }

  private isCorrectlyConfigured(hook: any): boolean {
    const events = new Set(hook.events || []);
    return events.has("push") && events.has("pull_request") && hook.active;
  }

  private parseGitHubUrl(remote: string): string | null {
    // SSH: git@github.com:owner/repo.git
    const ssh = remote.match(/github\.com[:/]([^/]+\/[^/.]+)/);
    if (ssh) return ssh[1];
    // HTTPS: https://github.com/owner/repo.git
    const https = remote.match(/github\.com\/([^/]+\/[^/.]+)/);
    if (https) return https[1];
    return null;
  }

  /**
   * Returns the remote URL for the git repo at repoPath.
   * The gateway runs on the host with full filesystem access, so we can
   * call git directly via child_process.
   * Throws if the path has no remote or is not a git repo.
   */
  private getGitRemote(repoPath: string): string {
    return execSync(`git -C ${JSON.stringify(repoPath)} remote get-url origin`, {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  }
}
```

### 5.4 Edge Cases

The `discoverRepos` function must handle several filesystem and git edge cases gracefully:

#### Workspace IS a repo root

`discoverRepos("/workspace/bond")` — the workspace path itself contains `.git` at its root. The scanner checks for `.git` at depth 0 before recursing into subdirectories. Result: `[{ localPath: "/workspace/bond", owner: "org", repo: "bond" }]`.

#### Workspace contains multiple repos

`discoverRepos("/workspace")` — subdirectories `/workspace/bond`, `/workspace/ecoinspector`, `/workspace/openclaw` each have their own `.git`. The scanner finds all three. Result: three entries.

#### Git submodules

A `.git` **file** (not a directory) indicates a submodule — the file contains a pointer like `gitdir: ../.git/modules/submodule-name`. These are **skipped** with a debug log. Only `.git` directories indicate standalone repos.

#### No remote configured

`git remote get-url origin` throws if no remote is configured (local-only repo). These are silently skipped (caught and ignored in `scan()`).

#### Non-GitHub remotes

If `parseGitHubUrl` returns `null` (e.g., GitLab, Bitbucket, or a bare filesystem remote), log a debug message and skip: `[webhooks] Non-GitHub remote at <path>: <url> — skipping`. GitHub-only for now; other platforms can be added later.

#### Permission errors

If `readdir` fails on a directory (e.g., `EACCES`), log a warning and skip that subtree: `[webhooks] Cannot read directory <path>: <err>`. Continues scanning other paths.

#### Symlinks

`entry.isSymbolicLink()` is checked before `entry.isDirectory()`. Symlinked directories are not followed during the scan to avoid infinite cycles (e.g., a symlink pointing to a parent directory).

---

## 6. Lifecycle & Integration

### 6.1 Gateway Startup Sequence

```
Gateway process starts
     │
     ├── 1. loadConfig() — bond.json + env vars
     │
     ├── 2. Connect to SpacetimeDB
     │
     ├── 3. Initialize EventBus + EventHistory
     │
     ├── 4. Initialize WebhookRegistrar
     │       ├── Read GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET, GATEWAY_EXTERNAL_URL
     │       ├── Query SpacetimeDB TrackedRepo table for all registered repos
     │       │   + merge in bond.json gateway.webhooks.additionalRepos (if any)
     │       └── WebhookRegistrar.ensureWebhooks(trackedRepos) [idempotent]
     │           └── If GATEWAY_EXTERNAL_URL not set: skip, log warning
     │
     ├── 5. Wire EventBus into webhooks.ts
     │       └── createWebhookRouter({ eventBus, onMainMerge })
     │
     ├── 6. Start HTTP/WS server (existing flow)
     │
     └── 7. Start EventBus cleanup interval (prune expired subscriptions)
```

> **Note:** Event subscriptions are **not** created at startup. They are created at runtime, automatically, when the gateway detects a `coding_agent_started` SSE event during a conversation turn (see §4). The startup sequence only concerns itself with infrastructure — EventBus wiring and webhook reconciliation.

### 6.2 Environment Configuration

The gateway runs as a host process, so environment variables are read from the host environment (or a `.env` file loaded at startup):

```bash
# .env
GITHUB_TOKEN=ghp_xxxx           # or: $(gh auth token) for development
GITHUB_WEBHOOK_SECRET=mysecret  # shared secret for HMAC verification
GATEWAY_EXTERNAL_URL=https://abc123.trycloudflare.com  # tunnel URL
```

The gateway process loads these at startup (e.g., via `dotenv`). No Docker-level environment injection is needed since the gateway is not containerized.

### 6.3 Config Changes

```typescript
// config/index.ts additions
export interface GatewayConfig {
  // ... existing fields ...
  githubToken: string;
  githubWebhookSecret: string;
  gatewayExternalUrl: string;
  webhookAdditionalRepos: string[];  // from bond.json gateway.webhooks.additionalRepos (edge cases)
}
```

---

## 7. Full Sequence: Coding Agent → Push → Notification

```
User          Bond Agent       Worker          Gateway         GitHub
  │               │               │               │               │
  │  "Fix auth"   │               │               │               │
  ├──────────────▶│               │               │               │
  │               │ coding_agent()│               │               │
  │               ├──────────────▶│               │               │
  │               │               │               │               │
  │               │  SSE: coding_agent_started     │               │
  │               │  {agent_type, repo, branch}    │               │
  │               │◀──────────────┤               │               │
  │               │               │               │               │
  │               │ (Gateway sees SSE event,       │               │
  │               │  auto-creates subscription)    │               │
  │               │               │  EventBus      │               │
  │               │               │  .subscribe()  │               │
  │               │               │──────────────▶ │               │
  │               │               │               │               │
  │◀──────────────┤ "Working..."  │               │               │
  │               │               │               │               │
  │               │          Claude Code runs...   │               │
  │               │               │               │               │
  │               │               │  git push     │               │
  │               │               ├───────────────┼──────────────▶│
  │               │               │               │               │
  │               │               │               │  POST webhook │
  │               │               │               │◀──────────────┤
  │               │               │               │               │
  │               │               │  EventBus     │               │
  │               │               │  matches sub  │               │
  │               │               │               │               │
  │               │               │  CompletionDispatcher          │
  │               │               │  triggers turn│               │
  │               │               │               │               │
  │               │  LLM turn     │               │               │
  │               │◀──────────────┼───────────────┤               │
  │               │               │               │               │
  │◀──────────────┤ "Done! The    │               │               │
  │               │  coding agent │               │               │
  │               │  pushed fix/" │               │               │
```

---

## 8. Failure Modes & Edge Cases

### 8.1 Coding Agent Crashes (Never Pushes)

If the coding agent crashes, no webhook fires. The subscription expires after its TTL (2h default). The user sees the coding agent's error in the SSE stream (existing `coding_agent_error` event).

**Enhancement (v2):** The worker emits `coding_agent_done` or `coding_agent_error` SSE events. The gateway can also auto-subscribe to these internal events and trigger a follow-up turn:

```
"The coding agent failed with exit code 1. Here's the error output: ..."
```

### 8.2 Gateway Restarts

Subscriptions are in-memory (v1) — lost on restart. The coding agent may still push, but no one is listening. Acceptable for v1 because:
- Gateway restarts are rare in practice
- The user can always ask "did it complete?" manually
- v2 can persist subscriptions to SpacetimeDB

### 8.3 Duplicate Webhooks

GitHub may retry failed webhook deliveries. The EventBus should deduplicate by `event.id` (GitHub's `X-GitHub-Delivery` header).

### 8.4 Rate Limiting

- Max 3 auto-turns per conversation per 60 seconds
- Prevents runaway loops if multiple pushes happen rapidly
- The system message includes "Do NOT spawn another coding agent" to prevent recursive spawning

### 8.5 Multiple Coding Agents in One Conversation

A user might spawn multiple coding agents in the same conversation. Each `coding_agent_started` event creates a separate subscription. Each push triggers its own notification. The rate limiter prevents flooding.

---

## 9. Event History API

### 9.1 Endpoints

```
GET  /api/v1/events/history
     ?repo=owner/repo
     &type=push
     &branch=feature/*
     &since=1710000000000
     &limit=50

GET  /api/v1/events/subscriptions
     ?conversationId=xxx
     ?active=true
```

### 9.2 Use Cases

- **Debugging:** "Did the webhook arrive?" — check event history
- **Audit:** "What events triggered agent turns in the last 24h?"
- **Frontend UI (v2):** Show event timeline alongside conversation

---

## 10. Multi-Gateway Scaling (Future)

If multiple gateway instances run behind a load balancer:

1. **Webhook delivery:** GitHub sends to one instance. That instance must be able to notify subscriptions held by other instances.
2. **Shared subscription store:** Move subscriptions to SpacetimeDB. All instances subscribe to the same table.
3. **Shared event history:** Same — SpacetimeDB table.
4. **Event fan-out:** The instance receiving the webhook writes to SpacetimeDB. Other instances react via SpacetimeDB subscription callbacks.

This is a natural evolution — SpacetimeDB already provides real-time subscriptions. The in-memory EventBus becomes a thin wrapper around SpacetimeDB queries.

---

## 11. Implementation Plan

### Phase 1: Core EventBus + Webhook Routing (v1)
- [ ] `EventBus` class with in-memory subscriptions
- [ ] `EventHistory` class with in-memory ring buffer
- [ ] Expand `webhooks.ts` to normalize all GitHub events (not just push→main)
- [ ] `CompletionDispatcher` to trigger agent turns
- [ ] Wire EventBus into `server.ts` startup
- [ ] Event history API endpoint
- [ ] `TrackedRepo` table in SpacetimeDB (schema + reducers)

### Phase 2: UI Repo Scan Flow + Webhook Registration
- [ ] `discoverRepos(workspacePath)` implementation in `WebhookRegistrar`
- [ ] `POST /api/v1/repos/scan` endpoint — calls `discoverRepos()`, returns repo list to UI
- [ ] `POST /api/v1/repos/register` endpoint (or SpacetimeDB mutation) — saves selected repos to `TrackedRepo`, calls `WebhookRegistrar.ensureWebhooks()`
- [ ] UI: checkbox list of discovered repos in agent settings / workspace mount editor
- [ ] UI: "Re-scan" button to re-trigger scan for an existing mount
- [ ] Add `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `GATEWAY_EXTERNAL_URL` to config
- [ ] Add `gateway.webhooks.additionalRepos` to `bond.json` schema (optional edge-case list)
- [ ] Document new env vars in `.env.example`

### Phase 3: Startup Reconciliation
- [ ] On gateway startup: query SpacetimeDB `TrackedRepo` table, call `WebhookRegistrar.ensureWebhooks()` (Trigger A)
- [ ] `getGitRemote` implementation (used by `discoverRepos`)
- [ ] Merge `bond.json gateway.webhooks.additionalRepos` into startup reconciliation

### Phase 4: Auto-Subscribe on Coding Agent Start
- [ ] Modify worker `coding_agent_started` SSE event to include `repo` + `branch`
- [ ] Modify `TurnExecutor` to auto-create subscription on `coding_agent_started`
- [ ] Add `coding_agent_pushed` SSE event from worker (for branch name refinement)

### Phase 5: Hardening
- [ ] Webhook delivery deduplication (by `X-GitHub-Delivery` header)
- [ ] Rate limiting on CompletionDispatcher
- [ ] Subscription cleanup on conversation delete
- [ ] Tests for EventBus matching, webhook normalization, auto-subscribe flow
- [ ] Tests for `discoverRepos()` (submodule detection, symlink handling, non-GitHub remotes)

### Phase 6: Persistence & Scaling (v2)
- [ ] Move subscriptions to SpacetimeDB
- [ ] Move event history to SpacetimeDB
- [ ] Multi-gateway fan-out via SpacetimeDB subscriptions
- [ ] Frontend event timeline UI

---

## 12. Open Questions

1. **Event retention period** — 24h default. Should this be configurable? Should we keep events indefinitely in SpacetimeDB (v2)?
2. **Webhook events scope** — Starting with `push`, `pull_request`, `check_run`. Should we subscribe to everything (`"*"`) and filter server-side?
3. **Tunnel auto-detection** — Should the gateway auto-detect cloudflared/ngrok tunnel URLs, or always require `GATEWAY_EXTERNAL_URL`?
4. **Worker-to-gateway push notifications** — For `coding_agent_done`/`coding_agent_error`, should the worker call the gateway's event API directly, or should the gateway detect these from the SSE stream?
5. **Re-scan workspace button** — Should there be a "Re-scan workspace" button in the UI for when new repos are added to an existing mount? Or should the user add repos manually in that case?

---

## Appendix A: Config Example

```json
{
  "gateway": {
    "host": "0.0.0.0",
    "port": 18789,
    "webhooks": {
      "additionalRepos": ["biztechprogramming/private-tool"]
    }
  }
}
```

Repos are discovered via the UI (see §5.2) and stored in SpacetimeDB — they are **not** listed in `bond.json`. The gateway queries SpacetimeDB at startup for all `TrackedRepo` rows.

`additionalRepos` is optional and covers edge cases where a repo that should receive webhooks is **not** checked out locally in any agent workspace (e.g., a repo owned by the team but worked on elsewhere, or a repo you want to track without a local clone).

```bash
# .env
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GITHUB_WEBHOOK_SECRET=your-webhook-secret-here
GATEWAY_EXTERNAL_URL=https://your-tunnel.trycloudflare.com
```
