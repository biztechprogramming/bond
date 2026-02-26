# Design Doc 009: Container Configuration UI

**Status:** Draft
**Depends on:** 008 (Containerized Agent Runtime)

---

## 1. The Problem

Container settings are currently scattered across the agent configuration. The sandbox image is a dropdown on the agent form. Workspace mounts are a list of paths. But there's no place to configure the container itself — the runtime environment the agent executes in.

As we add more container-level settings (user, resource limits, networking, environment variables, installed tools like Claude CLI), they don't belong on the agent form. An agent is *what* it does. A container is *where* it runs. These are separate concerns.

---

## 2. What Needs Configuration

| Setting | Default | Why |
|---------|---------|-----|
| **User** | `root` | Some tools (Claude CLI) require non-root. Security-conscious users want least-privilege. |
| **Memory limit** | `512m` | Heavy workloads (builds, large codebases) need more. |
| **CPU limit** | `1` | Parallel builds benefit from more cores. |
| **Environment variables** | `{}` | API keys, feature flags, custom paths. |
| **Network mode** | `bridge` | Some agents need host networking for local services. |
| **Extra packages** | `[]` | Per-container `apt` or `pip` packages without rebuilding the image. |
| **Startup commands** | `[]` | Run scripts after container starts (e.g., install Claude CLI, configure git). |
| **GPU access** | `false` | ML workloads need GPU passthrough. |
| **Idle timeout** | `3600s` | How long before an idle container is stopped. |
| **Auto-restart** | `true` | Restart on crash or just let it die. |

---

## 3. Proposed Architecture

### 3.1 Container Profiles

Instead of configuring every setting per-agent, introduce **container profiles** — reusable container configurations that agents reference.

```
┌─────────────────────────────────────────────────────┐
│  Container Profile: "default"                        │
│                                                      │
│  Image: bond-agent-worker                            │
│  User: root                                          │
│  Memory: 512m                                        │
│  CPUs: 1                                             │
│  Idle timeout: 1 hour                                │
│  Env: {}                                             │
│  Startup: []                                         │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  Container Profile: "claude-dev"                     │
│                                                      │
│  Image: bond-agent-worker                            │
│  User: bond                                          │
│  Memory: 2g                                          │
│  CPUs: 2                                             │
│  Idle timeout: 2 hours                               │
│  Env: { NODE_ENV: "development" }                    │
│  Startup:                                            │
│    - npm install -g @anthropic-ai/claude-code        │
│  Packages:                                           │
│    - nodejs                                          │
│    - npm                                             │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  Agent: "bond-main"                                  │
│                                                      │
│  Container profile: "default"                        │
│  Workspace mounts: [~/projects/bond → /workspace]    │
│  Tools: [respond, search_memory, ...]                │
└─────────────────────────────────────────────────────┘
```

### 3.2 Relationship to Agents

An agent references a container profile by name. The agent form keeps workspace mounts (those are agent-specific — different agents work on different projects). Everything else about the runtime moves to the profile.

```
Agent
  ├── name, model, system_prompt, tools     (what it does)
  ├── workspace_mounts                      (what it sees)
  └── container_profile → "claude-dev"      (where it runs)

Container Profile
  ├── image, user, memory, cpus             (runtime config)
  ├── env, packages, startup_commands       (environment)
  └── idle_timeout, auto_restart, gpu       (lifecycle)
```

The current `sandbox_image` field on the agent becomes the image field on the container profile. Agents that set `sandbox_image` directly keep working (backward compat) — they use an implicit default profile with that image.

---

## 4. Database Schema

```sql
CREATE TABLE container_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,              -- "default", "claude-dev", "ml-heavy"
    display_name TEXT NOT NULL,
    description TEXT DEFAULT '',

    -- Image
    image TEXT NOT NULL,                     -- "bond-agent-worker"

    -- Runtime
    container_user TEXT NOT NULL DEFAULT 'root',
    memory_limit TEXT NOT NULL DEFAULT '512m',
    cpu_limit REAL NOT NULL DEFAULT 1.0,
    gpu_enabled INTEGER NOT NULL DEFAULT 0,
    network_mode TEXT NOT NULL DEFAULT 'bridge',

    -- Environment
    env_vars JSON NOT NULL DEFAULT '{}',     -- { "KEY": "value" }
    extra_packages JSON NOT NULL DEFAULT '[]', -- ["nodejs", "npm"]
    startup_commands JSON NOT NULL DEFAULT '[]', -- ["npm install -g ..."]

    -- Lifecycle
    idle_timeout_seconds INTEGER NOT NULL DEFAULT 3600,
    auto_restart INTEGER NOT NULL DEFAULT 1,

    -- Metadata
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TRIGGER container_profiles_updated_at
    AFTER UPDATE ON container_profiles FOR EACH ROW
BEGIN
    UPDATE container_profiles SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
```

Agent table change:

```sql
-- Add column (migration)
ALTER TABLE agents ADD COLUMN container_profile_id TEXT
    REFERENCES container_profiles(id) ON DELETE SET NULL;
```

When `container_profile_id` is set, it takes precedence over `sandbox_image`. When null, fall back to `sandbox_image` for backward compat.

---

## 5. UI Design

### 5.1 Settings Navigation

Add a new tab to the Settings page:

```
Settings
  ├── General
  ├── API Keys
  ├── Embeddings
  ├── Agents          ← existing
  └── Containers      ← NEW
```

### 5.2 Containers Tab — List View

```
┌──────────────────────────────────────────────────────────┐
│  Container Profiles                        [+ New Profile]│
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌────────────────────────────┐  ┌─────────────────────┐│
│  │ default            Default │  │ claude-dev          ││
│  │ bond-agent-worker          │  │ bond-agent-worker    ││
│  │ root · 512m · 1 CPU        │  │ bond · 2g · 2 CPUs   ││
│  │ 1 agent using this         │  │ 0 agents using this  ││
│  └────────────────────────────┘  └─────────────────────┘│
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 5.3 Containers Tab — Edit View

```
┌──────────────────────────────────────────────────────────┐
│  Editing: claude-dev                      [Save] [Cancel]│
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Name (slug)         Display Name                        │
│  [claude-dev       ] [Claude Development               ] │
│                                                          │
│  Description                                             │
│  [Container with Claude CLI for autonomous coding      ] │
│                                                          │
│  ── Image ──────────────────────────────────────────     │
│                                                          │
│  Sandbox Image                                           │
│  [▼ bond-agent-worker                                  ] │
│                                                          │
│  ── Runtime ─────────────────────────────────────────    │
│                                                          │
│  User               Memory           CPUs                │
│  [bond            ] [2g            ] [2              ]   │
│                                                          │
│  Network Mode       GPU Access                           │
│  [▼ bridge        ] [ ] Enabled                          │
│                                                          │
│  ── Environment ─────────────────────────────────────    │
│                                                          │
│  Environment Variables                        [+ Add]    │
│  NODE_ENV      = [development     ]              [X]     │
│  EDITOR        = [vim             ]              [X]     │
│                                                          │
│  Extra Packages (installed at startup)        [+ Add]    │
│  [nodejs] [npm] [git-lfs]                                │
│                                                          │
│  Startup Commands (run after container starts) [+ Add]   │
│  [npm install -g @anthropic-ai/claude-code           ]   │
│                                                          │
│  ── Lifecycle ───────────────────────────────────────    │
│                                                          │
│  Idle Timeout       Auto-Restart                         │
│  [3600       ] sec  [✓] Enabled                          │
│                                                          │
│  ── Status ──────────────────────────────────────────    │
│                                                          │
│  Agents using this profile: bond-coder, bond-reviewer    │
│                                                          │
│                              [Set Default] [Delete]      │
└──────────────────────────────────────────────────────────┘
```

### 5.4 Agent Form Changes

Replace the "Sandbox Image" dropdown with a "Container Profile" dropdown:

```
Before:
  Sandbox Image
  [▼ bond-agent-worker        ]

After:
  Container Profile
  [▼ claude-dev               ]
  (or "None — host execution" for no container)
```

Workspace mounts stay on the agent form — they're agent-specific.

---

## 6. API Endpoints

```
GET    /api/v1/container-profiles           — List all profiles
POST   /api/v1/container-profiles           — Create profile
GET    /api/v1/container-profiles/:id       — Get profile detail
PUT    /api/v1/container-profiles/:id       — Update profile
DELETE /api/v1/container-profiles/:id       — Delete (fails if agents reference it)
POST   /api/v1/container-profiles/:id/default — Set as default profile
```

### Request/Response

```typescript
interface ContainerProfile {
  id: string;
  name: string;
  display_name: string;
  description: string;
  image: string;
  container_user: string;        // "root", "bond", "node", etc.
  memory_limit: string;          // "512m", "2g"
  cpu_limit: number;             // 1, 2, 0.5
  gpu_enabled: boolean;
  network_mode: string;          // "bridge", "host"
  env_vars: Record<string, string>;
  extra_packages: string[];
  startup_commands: string[];
  idle_timeout_seconds: number;
  auto_restart: boolean;
  is_default: boolean;
  agent_count: number;           // read-only: how many agents use this
}
```

---

## 7. SandboxManager Integration

The `SandboxManager._create_worker_container()` method currently hardcodes `--memory 512m` and `--cpus 1`. It will read these from the container profile instead.

```python
async def _create_worker_container(self, agent: dict, key: str, port: int, config_path: Path) -> str:
    profile = agent.get("container_profile", {})

    cmd = ["docker", "run", "-d", "--name", key]

    # Runtime limits from profile
    cmd.extend(["--memory", profile.get("memory_limit", "512m")])
    cmd.extend(["--cpus", str(profile.get("cpu_limit", 1))])

    # User
    container_user = profile.get("container_user", "root")
    if container_user != "root":
        cmd.extend(["--user", container_user])

    # Network
    network_mode = profile.get("network_mode", "bridge")
    if network_mode != "bridge":
        cmd.extend(["--network", network_mode])

    # GPU
    if profile.get("gpu_enabled"):
        cmd.extend(["--gpus", "all"])

    # Environment variables
    for key, value in profile.get("env_vars", {}).items():
        cmd.extend(["-e", f"{key}={value}"])

    # ... rest of mount logic unchanged ...
```

### Startup Commands

After the container starts and passes health check, run startup commands:

```python
async def _run_startup_commands(self, container_id: str, commands: list[str]) -> None:
    for cmd_str in commands:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "sh", "-c", cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "Startup command failed in %s: %s\nstderr: %s",
                container_id, cmd_str, stderr.decode(),
            )
```

### Extra Packages

Installed as part of startup commands, prepended automatically:

```python
packages = profile.get("extra_packages", [])
if packages:
    pkg_cmd = f"apt-get update -qq && apt-get install -y -qq {' '.join(packages)}"
    startup_commands = [pkg_cmd] + profile.get("startup_commands", [])
```

---

## 8. Migration & Backward Compatibility

1. Create `container_profiles` table
2. Insert a "default" profile matching current hardcoded values (root, 512m, 1 CPU, bond-agent-worker)
3. Add `container_profile_id` column to `agents` table (nullable)
4. Existing agents with `sandbox_image` set continue to work — `SandboxManager` checks for profile first, falls back to `sandbox_image`

```python
# Resolution order in ensure_running:
if agent.get("container_profile"):
    image = agent["container_profile"]["image"]
    user = agent["container_profile"]["container_user"]
    # ... use profile settings
elif agent.get("sandbox_image"):
    image = agent["sandbox_image"]
    user = "root"  # legacy default
    # ... use hardcoded defaults
```

---

## 9. Implementation Plan

| ID | Story | Effort |
|----|-------|--------|
| CP1 | DB migration: `container_profiles` table + `agents.container_profile_id` | S |
| CP2 | Backend API: CRUD for container profiles | M |
| CP3 | Frontend: Containers tab (list + edit form) | M |
| CP4 | Frontend: Agent form — replace sandbox_image with profile dropdown | S |
| CP5 | SandboxManager: read profile settings instead of hardcoded values | M |
| CP6 | Startup commands + extra packages execution | S |
| CP7 | Seed default profile on first run / migration | S |

---

## 10. Design Decisions

| Decision | Rationale |
|----------|-----------|
| Profiles are separate from agents | Same container config reused across agents. Change once, applies everywhere. |
| Agents keep workspace mounts | Mounts are agent-specific (different agents, different projects). |
| `sandbox_image` fallback preserved | No migration pressure. Existing setups keep working. |
| Startup commands run after health check | Worker is up first, then we customize. Avoids blocking the health check. |
| Extra packages are apt-based | The base image is Debian. pip packages go in startup commands. |
| Default profile seeded automatically | First-run experience works without manual setup. |
| User defaults to root | Matches current behavior. Users opt into non-root when needed. |
