# 057 — Workspace Cloning for Concurrent Containers

## Status: Draft

## Problem

Currently, containers share workspace directories via direct volume mounts — the host path is bind-mounted straight into the container at `/workspace/<name>`. This means only one container can safely operate on a given workspace at a time. Concurrent writes to the same files cause corruption, merge conflicts, and unpredictable behavior.

To support multiple containers running simultaneously, each container needs its own isolated copy of the workspace.

## Decision

**Always clone workspace directories into per-container copies.** Every container gets a fully independent working tree. Cloning is the default behavior whenever a container is created — there are no UI toggles or opt-in checkboxes.

The container still sees its files at `/workspace/<name>` as usual. The cloning is transparent.

## Directory Detection

A user can map any directory as a workspace mount. That directory might be a git repo, inside a git repo, contain multiple git repos, or have no git at all. The system detects which case applies and handles it automatically.

### Case 1: Directory IS a git repo root

The directory has a `.git/` at its root (e.g., `~/bond`).

**Strategy:** `git clone --depth 1` from the local repo as a file remote. Check out the same branch the source has checked out.

```
Host source:   ~/bond                     (has .git/)
Host clone:    data/agents/<id>/workspaces/bond/
Container:     /workspace/bond
```

### Case 2: Directory is INSIDE a git repo

The directory itself has no `.git/`, but an ancestor does (e.g., `~/bond/frontend` where `~/bond` is the repo root).

**Strategy:** Do not silently clone. Prompt the user:

> "This directory is inside a git repo rooted at `~/bond`. Do you want to mount the repo root instead?"

If the user says **yes**, update the workspace mapping to use the detected repo root and treat it as Case 1 (clone the full repo).

If the user says **no**, keep the direct bind mount as-is — no cloning, no copying. This mount will be shared across containers, same as today, which means it **cannot support concurrency**. The system should flag this mount as non-clonable so that concurrent container operations know to avoid conflicting writes on it.

**Rationale:** Mounting a subdirectory of a repo means the container has no access to `.git/` — git operations would fail. Copying the files without git gives an isolated snapshot but with no way to commit or push changes, which defeats the purpose. The only real options are: mount the whole repo (git works, cloning works) or keep the direct mount (no isolation, no concurrency).

### Case 3: Directory CONTAINS git repos

The directory is not itself a repo, but contains repos in its subdirectories (e.g., `~/projects` with `api/`, `web/`, `shared/` as separate repos).

**Strategy:** Recursively scan the directory (up to 3 levels deep) for `.git/` directories. Clone each discovered repo individually. Copy any non-repo files and directories as-is.

```
Host source:   ~/projects/
Scan finds:    ~/projects/api/.git
               ~/projects/web/.git
               ~/projects/shared/.git

Host clone:    data/agents/<id>/workspaces/projects/
               ├── api/        (cloned)
               ├── web/        (cloned)
               ├── shared/     (cloned)
               └── README.md   (copied)

Container:     /workspace/projects
```

### Case 4: Directory is NOT a git repo

No `.git/` found — not in the directory, not in any ancestor, not in any subdirectory (within the scan depth).

**Strategy:** Prompt the user:

> "This directory is not a git repo. Would you like to initialize one so it can support concurrent containers?"

If the user says **yes**, initialize a git repo in the source directory: generate a typical `.gitignore` (based on detected languages/frameworks), `git init`, `git add .`, and commit. Then treat it as Case 1 (clone the new repo).

If the user says **no**, keep the direct bind mount as-is — no cloning, no concurrency support for this mount.

### Detection Algorithm

```
given directory D:

1. if D/.git/ exists             → Case 1: clone
2. walk UP from D:
   if ancestor/.git/ exists      → Case 2: prompt user
                                    "yes, mount repo root" → Case 1
                                    "no"                   → direct mount (no clone, no concurrency)
3. walk DOWN from D (max 3 levels):
   if any child/.git/ found      → Case 3: clone each, copy the rest
4. else                          → Case 4: prompt user
                                    "yes, init repo" → git init + Case 1
                                    "no"             → direct mount (no clone, no concurrency)
```

This runs once per workspace mapping when a container is created. The result is a clone plan.

## Clone Plan

Each workspace mapping produces a plan before any cloning starts:

```typescript
interface ClonePlan {
  case: 1 | 3;               // Cases 2 and 4 resolve to Case 1 or direct mount
  repos: Array<{
    repoRoot: string;        // host path to the repo root
    remote: string;          // clone source (file:// URL or remote)
    branch: string;          // branch to check out
    targetPath: string;      // host path for the clone
  }>;
  copies: Array<{
    source: string;          // host path to copy from
    target: string;          // host path to copy to
  }>;
  directMount?: boolean;     // true if user declined Case 2 — no clone, no concurrency
}
```

## File Handling After Cloning

### Dependencies

After cloning, detect lockfiles and run the appropriate installer. This happens inside the container after it starts, using the container's toolchain.

| Lockfile | Command |
|---|---|
| `bun.lock` / `bun.lockb` | `bun install` |
| `package-lock.json` | `npm ci` |
| `yarn.lock` | `yarn install --frozen-lockfile` |
| `pnpm-lock.yaml` | `pnpm install --frozen-lockfile` |
| `requirements.txt` | `pip install -r requirements.txt` |
| `pyproject.toml` + `uv.lock` | `uv sync` |
| `Pipfile.lock` | `pipenv install` |
| `go.sum` | `go mod download` |

### Environment Files

Copy `.env`, `.env.local`, and `.env.*` files from the source into the clone verbatim. Then apply per-instance overrides to avoid conflicts between containers:

- **Ports**: increment based on instance index
- **Database paths**: append instance identifier
- **Instance ID**: inject `CONTAINER_INSTANCE_ID`

### Build Artifacts

Skip directories that are regenerated by build tools: `dist/`, `.next/`, `__pycache__/`, `.cache/`, `*.pyc`, `.turbo/`, `.parcel-cache/`. These rebuild naturally.

### Other Unversioned Files

For Case 3 and Case 4 (non-repo files being copied), a `.cloneignore` file at the workspace root can exclude additional paths using gitignore syntax.

## Storage

Clones are stored **outside the project tree** to avoid triggering dev-server file watchers (e.g., uvicorn's `--reload`). The default location is:

```
~/.bond/workspaces/<agent-id>/<mount-name>/
```

This separates ephemeral clone data from the project source and from persistent agent data in `data/agents/<id>/`. Clones are throwaway — they exist only for the lifetime of the container.

The path is configurable via the `BOND_WORKSPACE_CLONE_DIR` environment variable (defaults to `~/.bond/workspaces`).

The cloned workspace is bind-mounted into the container at `/workspace/<mount-name>`, replacing the direct host mount. The container sees no difference.

On container destruction, the clone directory at `~/.bond/workspaces/<agent-id>/` is deleted.

## Clone Lifecycle

```
Container Create
  │
  ├─ For each workspace mapping:
  │   ├─ Run detection algorithm (Cases 1-4)
  │   ├─ Generate clone plan
  │   ├─ Execute: clone repos / copy files
  │   ├─ Copy .env files → apply per-instance overrides
  │   └─ Mount clone at /workspace/<name> instead of host path
  │
  ├─ Start container
  │
  ├─ Post-start: detect lockfiles → install dependencies
  │
  └─ Ready

Container Destroy
  │
  └─ Delete ~/.bond/workspaces/<agent-id>/
```

## Minimal UI Changes

Cloning is always-on and transparent for most cases. The user configures workspace mounts the same way they do today.

The only UI interactions are:

- **Case 2 prompt**: When a directory is detected inside a git repo, the UI prompts the user to mount the repo root instead. If declined, the mount is flagged as non-clonable (no concurrency support).
- **Case 4 prompt**: When a directory has no git at all, the UI offers to initialize a repo. If accepted, a `.gitignore` is generated and the directory is committed. If declined, the mount is flagged as non-clonable.
- **Optional `.cloneignore`**: A file at the workspace root to exclude paths from the copy step (advanced users only).

## Trade-offs

| | Pro | Con |
|---|---|---|
| **Disk usage** | Shallow clones are small | Dependencies duplicated per container |
| **Provisioning time** | Local file:// clone is fast | Dependency install adds 30s+ |
| **Simplicity** | No decisions for the user | No way to share a directory (intentional) |
| **Isolation** | Containers can't corrupt each other | Changes in one don't appear in others |

### Future: Shared Mounts

If a use case emerges where containers need a shared directory (large datasets, shared output), a `shared: true` flag on individual workspace mappings could bypass cloning. Intentionally deferred.

## Implementation Order

1. Detection algorithm: walk up/down to classify each mount (Cases 1-4)
2. Clone pipeline: shallow clone repos, rsync non-repo content
3. Integration with `sandbox/manager.py`: swap direct bind mounts for clone mounts
4. Post-start dependency installation with lockfile detection
5. Per-instance `.env` overrides
6. Cleanup on container destroy
