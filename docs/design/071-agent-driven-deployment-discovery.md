# Design Doc 071: Agent-Driven Deployment Discovery

**Status:** Draft
**Date:** 2026-03-24
**Depends on:** 044 (Remote Discovery), 052 (Simplified Deployment UX), 056 (Unified Deployment Experience), 061 (Deployment Tab Simplification)

---

## 1. Problem Statement

The deployment wizard's Step 2 — "Magic Discovery" — is the weakest link in the Ship It flow. It relies on five hardcoded shell scripts (`01-system-overview.sh` through `05-dns-networking.sh`) that run sequentially over SSH. When any script fails, is missing from disk, or returns incomplete data, that discovery layer is silently skipped and the resulting deployment plan has gaps. An incomplete plan means the **Ship It button cannot be enabled**, and the user is left staring at a form asking for information the tool was supposed to find automatically.

### Current Discovery Limitations

The existing implementation in `gateway/src/deployments/discovery.ts` has several structural problems:

| Problem | Impact |
|---------|--------|
| Scripts must exist at `~/.bond/deployments/discovery/scripts/` | Fresh installs or updates can leave scripts missing — entire layers silently skipped |
| Fixed 5-layer structure with no branching logic | A Django app and a Next.js app get the same probes in the same order |
| JSON parse or raw text fallback — no validation | Partial or malformed output becomes garbage-in for plan generation |
| No retry, no fallback, no adaptive behavior | Transient SSH failures kill an entire layer |
| No user interaction during discovery | When auto-detection fails, the only fallback is a manual form after the fact |
| Environment-scoped but not context-aware | Discovers the same way regardless of what it already knows |

**Result:** The Ship It button frequently lacks required parameters (framework, build strategy, port, env vars), forcing users into manual configuration. This accounts for the bulk of the ~60% drop-off and ~15 min setup time noted in Doc 061.

### What the Ship It Button Needs

From the `generate_plan` API (Doc 056 §2.4):

```typescript
// Minimum required parameters for Ship It to be enabled
interface DeploymentPlanRequired {
  source: string;           // repo URL or server address
  framework: string;        // nextjs, express, django, rails, etc.
  build_strategy: string;   // dockerfile, docker-compose, buildpack, script
  target_server: {
    host: string;
    port: number;
    ssh_user: string;
  };
  app_port: number;         // port the application listens on
}

// Recommended — agent should try hard to find these
interface DeploymentPlanRecommended {
  env_vars: Record<string, string>;
  health_endpoint: string;
  monitoring_config: MonitoringConfig;
}

// Optional — nice to have
interface DeploymentPlanOptional {
  deploy_strategy: "rolling" | "blue-green" | "recreate";
  rollback_plan: RollbackConfig;
  alert_channels: string[];
}
```

Today, discovery reliably fills `source` and `target_server` (because the user provides them). Everything else is hit-or-miss.

---

## 2. Proposed Solution: Agent-Driven Discovery

Replace the static shell-script discovery with Bond's own AI agent orchestrating the discovery process using tools. Instead of running a fixed sequence of scripts and hoping they produce JSON, the agent:

1. **Probes adaptively** — inspects the repo or server, then decides what to look at next based on what it found
2. **Fills gaps by reasoning** — if there's no `Dockerfile` but there is a `package.json` with a `build` script and a `next.config.js`, the agent infers "Next.js app, needs `npm run build`, port 3000"
3. **Asks targeted questions** — when genuinely stuck, surfaces a single specific question to the user ("I found ports 3000 and 8080 open — which is your main app?"), not a full configuration form
4. **Validates completeness** — checks the deployment plan against a completeness model before enabling Ship It

```
┌──────────────────────────────────────────────────────┐
│                  CURRENT FLOW                        │
│                                                      │
│  User Input ──→ 5 Shell Scripts ──→ Parse JSON ──→   │
│                   (sequential)       (or fail)       │
│                                          │           │
│                                    Incomplete Plan   │
│                                          │           │
│                              Ship It button disabled │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│                  PROPOSED FLOW                       │
│                                                      │
│  User Input ──→ Agent Discovery Loop ──→ Complete    │
│                   │    ▲                    Plan      │
│                   │    │                     │        │
│                   ▼    │               Ship It ready  │
│                 probe → analyze →                     │
│                 probe deeper →                        │
│                 ask user (if needed) →                │
│                 validate completeness                 │
└──────────────────────────────────────────────────────┘
```

---

## 3. Agent Discovery Architecture

### 3.1 Where the Agent Runs

The discovery agent runs inside the gateway process (`gateway/src/deployments/`). It is a standard Bond agent session with a specialized system prompt and a restricted tool set scoped to discovery. It does **not** share context with the user's coding agent — it is a short-lived, single-purpose agent that terminates once discovery is complete or times out.

### 3.2 System Prompt

The discovery agent receives a system prompt that includes:

- The completeness model (§5) — what fields are required, recommended, optional
- What the user has already provided (repo URL, server IP, etc.)
- Instructions to be methodical: broad scan first, then targeted follow-ups
- Instructions to minimize user questions — only ask when genuinely stuck

### 3.3 Adaptive Discovery Loop

```
                    ┌─────────────┐
                    │  User Input │
                    │ (repo/host) │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │ Broad Scan  │ ← clone repo, SSH overview
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │  Evaluate   │ ← check completeness model
                    │  Coverage   │
                    └──────┬──────┘
                           │
                     ┌─────┴─────┐
                     │           │
                All required   Missing fields
                fields found       │
                     │             ▼
                     │     ┌─────────────┐
                     │     │  Targeted   │ ← run specific probes
                     │     │   Probes    │   for missing fields
                     │     └──────┬──────┘
                     │            │
                     │      ┌─────┴─────┐
                     │      │           │
                     │   Found it    Still stuck
                     │      │           │
                     │      │           ▼
                     │      │    ┌─────────────┐
                     │      │    │  Ask User   │ ← targeted question
                     │      │    └──────┬──────┘
                     │      │           │
                     │      └─────┬─────┘
                     │            │
                     │            ▼
                     │     (loop back to Evaluate)
                     │
                     ▼
              ┌─────────────┐
              │  Validate   │ ← final coherence check
              │  & Present  │
              └──────┬──────┘
                     ▼
              ┌─────────────┐
              │  Ship It    │ ← button enabled
              │   Ready     │
              └─────────────┘
```

### 3.4 State Management

The agent maintains a `DiscoveryState` object that accumulates findings:

```typescript
interface DiscoveryState {
  /** What we know so far */
  findings: DeploymentPlanPartial;

  /** Confidence per field: how it was determined */
  confidence: Record<string, FieldConfidence>;

  /** What we've already tried */
  probes_run: ProbeRecord[];

  /** Questions asked and answers received */
  user_answers: UserAnswer[];

  /** Current completeness assessment */
  completeness: CompletenessReport;
}

interface FieldConfidence {
  source: "detected" | "inferred" | "user-provided";
  detail: string;    // e.g., "found Dockerfile at repo root"
  score: number;     // 0.0–1.0
}
```

This state is emitted via `emitDeploymentEvent()` so the UI can render progress in real-time.

---

## 4. Discovery Tools for the Agent

The agent has access to these tools, implemented as gateway tool handlers in `gateway/src/deployments/discovery-tools.ts`:

### 4.1 `ssh_exec`

Run a command on the target server via SSH. Uses the same SSH connection infrastructure as the current discovery scripts (`executeSshScript` from `discovery-scripts.ts`) but runs arbitrary commands rather than full scripts.

```typescript
interface SshExecTool {
  command: string;        // e.g., "docker ps --format json"
  timeout_ms?: number;    // default 10_000
  parse_as?: "json" | "lines" | "raw";
}
```

**Safety:** Commands are logged and auditable. The agent's system prompt restricts it to read-only commands. Write operations (creating files, starting services) are not permitted during discovery.

### 4.2 `repo_inspect`

Analyze repository structure. Works on the locally cloned repo (cloned during broad scan).

```typescript
interface RepoInspectTool {
  action: "list_files" | "read_file" | "find_pattern" | "tree";
  path?: string;          // relative to repo root
  pattern?: string;       // glob or regex depending on action
  max_depth?: number;     // for tree action
}
```

### 4.3 `detect_framework`

Intelligent framework detection that goes beyond simple file matching. Examines `package.json` dependencies, import statements, config files, and directory structure to determine the framework.

```typescript
interface DetectFrameworkTool {
  // No parameters — operates on the cloned repo
}

// Returns:
interface FrameworkDetection {
  framework: string;              // e.g., "nextjs"
  version?: string;               // e.g., "14.1.0"
  confidence: number;             // 0.0–1.0
  evidence: string[];             // what files/signals were used
  runtime: "node" | "python" | "ruby" | "go" | "java" | "php" | "rust" | "other";
}
```

### 4.4 `detect_build_strategy`

Find how the application is built and containerized.

```typescript
interface DetectBuildStrategyTool {}

// Returns:
interface BuildStrategyDetection {
  strategy: "dockerfile" | "docker-compose" | "buildpack" | "script" | "static";
  dockerfile_path?: string;       // if found
  compose_path?: string;          // if found
  build_command?: string;         // e.g., "npm run build"
  confidence: number;
  evidence: string[];
}
```

### 4.5 `detect_services`

Find databases, caches, message queues, and other services the app depends on.

```typescript
interface DetectServicesTool {
  search_scope: "repo" | "server" | "both";
}

// Returns:
interface ServiceDetection {
  services: Array<{
    type: "postgres" | "mysql" | "redis" | "mongo" | "rabbitmq" | "kafka" | string;
    source: "docker-compose" | "env-var-reference" | "config-file" | "running-process";
    connection_string?: string;   // redacted
    port?: number;
  }>;
}
```

### 4.6 `detect_env_vars`

Find required environment variables from `.env.example`, `.env.template`, config files, and code references.

```typescript
interface DetectEnvVarsTool {}

// Returns:
interface EnvVarDetection {
  variables: Array<{
    name: string;
    required: boolean;
    has_default: boolean;
    source: string;               // where it was found
    description?: string;         // from comments near the reference
  }>;
}
```

### 4.7 `detect_ports`

Find what ports the application listens on by examining code, config, Dockerfiles, and running processes.

```typescript
interface DetectPortsTool {
  search_scope: "repo" | "server" | "both";
}

// Returns:
interface PortDetection {
  ports: Array<{
    port: number;
    source: "dockerfile-expose" | "code-listen" | "config-file" | "running-process";
    likely_purpose: "app" | "debug" | "metrics" | "admin" | "unknown";
  }>;
}
```

### 4.8 `detect_health_endpoint`

Find health check routes by examining code, framework conventions, and running server responses.

```typescript
interface DetectHealthEndpointTool {}

// Returns:
interface HealthEndpointDetection {
  endpoints: Array<{
    path: string;                 // e.g., "/health", "/api/health", "/healthz"
    source: "code-route" | "framework-convention" | "live-response";
    http_method: string;
  }>;
}
```

### 4.9 `ask_user`

Present a targeted question to the user within the wizard UI. The agent uses this only when it cannot determine a required field through probing.

```typescript
interface AskUserTool {
  question: string;               // clear, specific question
  context: string;                // what the agent already found (shown to user)
  field: string;                  // which deployment plan field this resolves
  options?: string[];             // multiple choice if applicable
  default?: string;               // suggested answer based on inference
}
```

---

## 5. Completeness Model

The completeness model defines what the agent needs to find before Ship It can be enabled.

### 5.1 Field Categories

| Category | Fields | Ship It gated? |
|----------|--------|----------------|
| **Required** | `source`, `framework`, `build_strategy`, `target_server`, `app_port` | Yes — all must be filled |
| **Recommended** | `env_vars`, `health_endpoint`, `monitoring_config` | No — but agent should try |
| **Optional** | `deploy_strategy`, `rollback_plan`, `alert_channels` | No — defaults used if missing |

### 5.2 Confidence Scoring

Each field has a confidence score indicating how it was determined:

| Score Range | Source | Meaning |
|-------------|--------|---------|
| 0.9–1.0 | `detected` | Found explicit evidence (Dockerfile, `EXPOSE 3000`, `app.listen(3000)`) |
| 0.6–0.8 | `inferred` | Derived from indirect evidence (Next.js detected → port is probably 3000) |
| 1.0 | `user-provided` | User answered a question or provided input directly |
| 0.0–0.5 | — | Not confident enough — agent should probe more or ask user |

### 5.3 Completeness Check

```typescript
interface CompletenessReport {
  ready: boolean;                          // true if all required fields are filled with score > 0.5
  required_coverage: number;               // 0.0–1.0 fraction of required fields filled
  recommended_coverage: number;            // 0.0–1.0 fraction of recommended fields filled
  missing_required: string[];              // fields the agent still needs
  low_confidence: string[];                // fields filled but with score < 0.6
}
```

The Ship It button is enabled when `ready === true`. The UI shows confidence indicators so the user can see which values were auto-detected vs inferred.

---

## 6. Agent Loop Flow

### Step 1: User Provides Entry Point

Same as current Step 1 from Doc 056 — user enters a repo URL, server IP, or both. This becomes the `source` and optionally `target_server` in the discovery state.

### Step 2: Agent Begins Discovery

**Phase A — Broad Scan (5–15 seconds)**

The agent runs initial discovery in parallel where possible:

1. If repo URL provided: clone repo, run `detect_framework`, `detect_build_strategy`, `repo_inspect` (tree)
2. If server IP provided: `ssh_exec` for system overview (`uname -a`, `docker --version`, `ls /etc/nginx/`), `detect_ports` on server, `detect_services` on server
3. If both: run repo and server scans concurrently

**Phase B — Gap Analysis**

Agent evaluates the completeness model. Example state after Phase A:

```
✅ source:          https://github.com/acme/api (user-provided, 1.0)
✅ framework:       express (detected from package.json, 0.95)
✅ build_strategy:  dockerfile (detected at ./Dockerfile, 1.0)
✅ target_server:   192.168.1.50:22 (user-provided, 1.0)
❌ app_port:        — not yet determined
⚠️  env_vars:       DATABASE_URL, REDIS_URL found in .env.example (0.8)
❌ health_endpoint: — not yet determined
```

**Phase C — Targeted Probes**

For each missing or low-confidence field, the agent runs targeted probes:

- `app_port` missing → `repo_inspect` to read server startup code, `detect_ports` on repo, check Dockerfile for `EXPOSE`
- `health_endpoint` missing → `detect_health_endpoint`, `repo_inspect` searching for `/health` route patterns

**Phase D — User Questions (if needed)**

If targeted probes don't resolve a required field, the agent asks:

```
Agent: I found your Express app listens on port 3000 in development
       (from src/index.ts line 42) but your Dockerfile EXPOSEs port 8080.
       Which port should I use for the deployment?

       ○ 3000 (from code)
       ○ 8080 (from Dockerfile)
       ○ Other: ___
```

The agent presents at most 2–3 questions per discovery session. If more are needed, it presents them one at a time, using each answer to refine the next question.

### Step 3: Agent Presents Deployment Plan

Once the completeness model reports `ready: true`, the agent presents the full plan with confidence annotations:

```
Deployment Plan for acme/api
─────────────────────────────
Source:         https://github.com/acme/api          ✓ provided
Framework:      Express 4.18                          ✓ detected
Build:          Dockerfile (multi-stage)              ✓ detected
Target:         192.168.1.50:22 (ubuntu)              ✓ provided
App Port:       8080                                  ✓ user confirmed
Health Check:   GET /healthz                          ~ inferred
Env Vars:       DATABASE_URL, REDIS_URL, JWT_SECRET   ✓ detected
Monitoring:     Auto-configured                       ✓ default
Strategy:       Rolling (2 replicas)                  ✓ default
```

### Step 4: Ship It

The button is enabled. Clicking it invokes `generate_plan` (Doc 056 §2.4) with all discovered parameters.

---

## 7. UX Integration

### 7.1 Wizard Step 2 Becomes Agent Activity View

The current Step 2 ("Magic Discovery") is a spinner that shows nothing until discovery completes (or fails). The new Step 2 is a live activity view that shows what the agent is doing:

```
┌────────────────────────────────────────────────┐
│  Discovering your application...               │
│                                                │
│  ✅ Cloned repository                          │
│  ✅ Detected Express framework (v4.18)         │
│  ✅ Found Dockerfile (multi-stage build)       │
│  🔍 Checking application port...               │
│  ⬚  Looking for health check endpoint          │
│  ⬚  Scanning for environment variables         │
│                                                │
│  ┌──────────────────────────────────────────┐  │
│  │ Deployment Plan                    3/5   │  │
│  │ ████████████████░░░░░░░░░░░        60%   │  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
```

### 7.2 Inline Agent Questions

When the agent needs user input, the question appears inline in the activity view — not as a modal or separate form. The user answers and the agent continues. This keeps the user in flow.

### 7.3 Real-Time Plan Assembly

The deployment plan panel builds up incrementally as fields are discovered. Each field shows its confidence source (detected/inferred/user-provided) via a subtle icon. Users can click any field to override the agent's finding.

### 7.4 Component Changes

| Component | Change |
|-----------|--------|
| `OneClickShipWizard.tsx` (Doc 061) | Step 2 content replaced with agent activity view |
| `DiscoverStackWizard.tsx` | Deleted (was already marked for deletion in Doc 061) |
| New: `AgentDiscoveryView.tsx` | Renders agent activity stream and inline questions |
| New: `DeploymentPlanPanel.tsx` | Shows plan building up in real-time with confidence indicators |

---

## 8. Fallback & Error Handling

### 8.1 Degraded Discovery Modes

| Scenario | Agent Behavior |
|----------|---------------|
| SSH connection fails | Discover from repo only; ask user for server-side info (port, running services) |
| Repo clone fails (private repo, bad URL) | Discover from server only via SSH; ask user about build strategy |
| Both fail | Fall back to targeted questions for all required fields (structured interview) |
| Agent timeout (> 60 seconds) | Present whatever is complete, ask user for remaining required fields |

### 8.2 No Silent Skipping

The current discovery silently skips failed layers. The agent-driven approach **never** silently drops a required field. For every required field, exactly one of these must be true:

1. The agent detected it with confidence > 0.5
2. The agent inferred it and flagged the inference to the user
3. The agent asked the user and received an answer
4. The agent timed out and explicitly told the user what's missing

### 8.3 Error Reporting

All discovery probes and their results are logged to `~/.bond/deployments/discovery/agent-log.jsonl` for debugging. If a user reports "discovery didn't find my framework," the log shows exactly what the agent tried and what it saw.

---

## 9. Migration Path

### Phase 1: Agent Discovery as Alternative (Week 1–2)

- Implement `discovery-tools.ts` with the tool definitions from §4
- Implement `AgentDiscoveryView.tsx` and `DeploymentPlanPanel.tsx`
- Add a feature flag `agent_discovery` (default: off)
- When enabled, Step 2 uses the agent; when disabled, uses current shell scripts
- Both paths produce the same `DeploymentManifest` output

### Phase 2: Agent Discovery as Default (Week 3–4)

- Enable `agent_discovery` by default
- Shell scripts become the fallback if the agent fails to initialize
- Monitor discovery completion rates and user question counts
- Iterate on agent system prompt based on real discovery sessions

### Phase 3: Remove Shell Script Discovery (Week 5+)

- Delete `~/.bond/deployments/discovery/scripts/` and the 5 shell scripts
- Remove `executeSshScript` codepath from `discovery-scripts.ts`
- Simplify `discovery.ts` to be a thin wrapper around the agent session
- The `DEFAULT_LAYERS` constant and sequential layer execution are removed

---

## 10. Success Metrics

| Metric | Current (Shell Scripts) | Target (Agent Discovery) |
|--------|------------------------|--------------------------|
| Discovery completion rate (all required fields found) | ~40% estimated | > 95% |
| Time to complete discovery | 20–45s (sequential SSH) | 10–30s (parallel + adaptive) |
| User questions needed per discovery | 0 (no mechanism to ask) | < 2 on average |
| Ship It button enable rate | ~40% (matches completion) | > 95% |
| Time from start to Ship It | ~15 min (manual config needed) | < 3 min |
| Discovery-related support requests | Baseline | -80% |

### Measurement Plan

- Emit `discovery.completed` events via `emitDeploymentEvent()` with completeness report payload
- Track `discovery.user_question` events to measure question frequency
- Track `discovery.ship_it_enabled` to measure enable rate
- A/B test during Phase 2 (agent vs shell scripts) to validate improvement

---

## 11. Open Questions

1. **Agent model cost** — Each discovery session is an agent run with 5–20 tool calls. What's the acceptable token budget per discovery? Should we use a smaller/faster model for discovery vs the main coding agent?

2. **Concurrent discoveries** — If a user starts discovery for multiple apps, do we run multiple agent sessions in parallel? Rate limiting considerations?

3. **Caching** — If a user re-runs discovery on the same repo + server, should we cache previous findings and only re-probe fields that might have changed?

4. **Security audit scope** — The `ssh_exec` tool gives the agent ability to run commands on user servers. Even read-only, this needs a security review. Should commands be allowlisted rather than relying on prompt-level restrictions?
