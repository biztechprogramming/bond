# Design Doc: Fixing the SPACETIMEDB_TOKEN Issue

## Problem Statement

The `work_plan` tool fails with the error:
> "SPACETIMEDB_TOKEN env var is not set"

This means the Bond gateway cannot authenticate with SpacetimeDB to read/write work plans (and conversations). Work plans are stored in SpacetimeDB, so without a valid token, all plan operations fail.

---

## Architecture: How the Token Flows

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Agent Sandbox   │     │   Bond Gateway    │     │   SpacetimeDB    │
│  (this container)│────▶│  (Node/Express)   │────▶│  (port 18787)    │
│                  │     │  (port 18789)     │     │                  │
│ Python backend   │     │ Plans router      │     │ bond-core-v2     │
│ also reads token │     │ Conversations     │     │ module           │
└─────────────────┘     └──────────────────┘     └──────────────────┘
```

### Who needs the token?

| Component | File | How it reads the token |
|-----------|------|----------------------|
| **Gateway** (Node.js) | `gateway/src/config/index.ts` | 1. `process.env.SPACETIMEDB_TOKEN` (preferred) |
| | | 2. Fallback: reads `~/.config/spacetime/cli.toml` and extracts `spacetimedb_token = "..."` |
| **Python backend** | `src/spacetime_client.py` | `os.environ.get("SPACETIMEDB_TOKEN")` — no fallback, falls back to SQLite if missing |
| **Python SpacetimeDB backend** | `src/backends/spacetimedb_backend.py` | `os.environ.get("SPACETIMEDB_TOKEN")` — raises error if missing |

### Where is the token generated?

The token is created when you first authenticate with SpacetimeDB using the CLI:

```bash
spacetime login --anonymous   # Creates a local identity
```

This writes the token to: `~/.config/spacetime/cli.toml`

The file looks like:
```toml
spacetimedb_token = "eyJ..."
```

---

## Current State (What's Missing)

On this agent sandbox container:

| Check | Status |
|-------|--------|
| `SPACETIMEDB_TOKEN` env var | ❌ **Not set** |
| `~/.config/spacetime/cli.toml` | ❌ **File not found** |
| `~/.bond/` directory | ❌ **Does not exist** |
| `.env` file in project root | ❌ **Does not exist** (only `.env.example`) |
| SpacetimeDB CLI installed | ❌ **Not checked** (likely not installed in sandbox) |

The gateway's `resolveSpacetimeToken()` function tries both the env var and the CLI toml file — both are missing, so it returns an empty string, which causes the `authHeaders()` function to throw.

---

## Fix Options

### Option 1: Set the env var in Docker (Recommended for production)

Pass `SPACETIMEDB_TOKEN` to the agent sandbox container via docker-compose or the agent spawn configuration.

**Where to set it:**

1. **`docker-compose.dev.yml`** — Add to the gateway and agent services:
   ```yaml
   services:
     gateway:
       environment:
         - SPACETIMEDB_TOKEN=${SPACETIMEDB_TOKEN}
     # Also for any agent sandbox containers
   ```

2. **Agent settings** — If the agent sandbox is spawned dynamically, the token needs to be passed as an environment variable in the spawn configuration.

3. **`.env` file** — Create `bond/.env` (not tracked in git) with:
   ```
   SPACETIMEDB_TOKEN=eyJ...your-token-here...
   ```

**How to get the token value:**
```bash
# On the HOST machine where SpacetimeDB was set up:
cat ~/.config/spacetime/cli.toml | grep spacetimedb_token
```

### Option 2: Mount the CLI config file into the container

If the host machine has `~/.config/spacetime/cli.toml`, mount it into the container:

```yaml
services:
  gateway:
    volumes:
      - ~/.config/spacetime:/root/.config/spacetime:ro
```

The gateway already has fallback code to read from this file (`resolveSpacetimeToken()` in `gateway/src/config/index.ts`).

### Option 3: Run `spacetime login` inside the container

Install the SpacetimeDB CLI in the container and run:
```bash
spacetime login --anonymous
```

This creates the `cli.toml` file. However, this generates a **new identity** — it won't match the identity that owns the existing SpacetimeDB module, so you'd lose access to existing data.

---

## Recommended Fix

**Option 1** is the cleanest approach:

### Step-by-step:

1. **On your host machine**, get the token:
   ```bash
   cat ~/.config/spacetime/cli.toml
   # Copy the spacetimedb_token value
   ```

2. **Create `bond/.env`** (gitignored):
   ```
   SPACETIMEDB_TOKEN=eyJ...paste-token-here...
   ```

3. **Update `docker-compose.dev.yml`** to pass the env var to all services that need it:
   ```yaml
   services:
     gateway:
       environment:
         - SPACETIMEDB_TOKEN=${SPACETIMEDB_TOKEN}
   ```

4. **For agent sandbox containers**, ensure the token is passed through. Check the agent spawn logic — likely in the gateway or backend code that creates sandbox containers — and add `SPACETIMEDB_TOKEN` to the environment variables passed to new containers.

5. **Verify:**
   ```bash
   # Inside the container:
   echo $SPACETIMEDB_TOKEN  # Should print the token
   curl -H "Authorization: Bearer $SPACETIMEDB_TOKEN" \
        http://localhost:18787/v1/database/bond-core-v2/sql \
        -d "SELECT * FROM work_plans LIMIT 1"
   ```

---

## Testing the Fix

Once you've set the token, here's how to verify each layer is working:

### 1. Environment Variable Check (Sanity)
```bash
# Inside any container that should have the token:
echo $SPACETIMEDB_TOKEN
# Should print a non-empty value (eyJ... or similar)
```

### 2. SpacetimeDB Connectivity (Direct API)
Test that the token is valid and SpacetimeDB is reachable:
```bash
# Run a simple SQL query against SpacetimeDB
curl -s -X POST \
  -H "Authorization: Bearer $SPACETIMEDB_TOKEN" \
  "https://spacetimedb.com/api/v1/database/bond-core-v2/sql" \
  -d "SELECT * FROM work_plans LIMIT 1"

# Expected: JSON response with rows (or empty rows [])
# Failure: 401 Unauthorized = bad token, connection refused = wrong URL
```

### 3. Python Backend (SpacetimeDB vs SQLite fallback)
Check which backend the Python agent is actually using:
```bash
# From inside the Python backend container:
python3 -c "
import os
token = os.environ.get('SPACETIMEDB_TOKEN')
print(f'Token present: {bool(token)}')
if token:
    from src.backends.spacetimedb_backend import SpacetimeDBBackend
    backend = SpacetimeDBBackend(token=token)
    print(f'Backend: {backend.backend_name}')
    print(f'Available: {backend.is_available()}')
else:
    print('FALLING BACK TO SQLITE - token not set')
"
```

### 4. Gateway Work Plans (The Actual Failure Point)
The gateway's persistence router is where `work_plan` calls fail. Test it:
```bash
# Hit the gateway's plans endpoint directly
curl -s -X POST http://localhost:3001/persistence/plans \
  -H "Content-Type: application/json" \
  -d '{"title": "Test Plan", "description": "Testing token fix"}'

# Expected: {"id": "...", "status": "saved"}
# Failure: {"error": "..."} - check the gateway logs for details
```

### 5. End-to-End (Agent Sandbox -> Gateway -> SpacetimeDB)
This is the real test - does `work_plan` work from inside the agent sandbox?
```bash
# From inside the agent sandbox container (where Bond runs):
# Check the token is present
echo $SPACETIMEDB_TOKEN

# Then ask Bond to create a work plan (or use the API directly)
curl -s -X POST http://gateway:3001/persistence/plans \
  -H "Content-Type: application/json" \
  -d '{"title": "Token Fix Verification", "description": "If you see this plan, the fix works"}'
```

### 6. Cleanup
After verifying, delete the test plan:
```bash
# Use the plan ID returned from step 5
curl -s -X DELETE http://localhost:3001/persistence/plans/<plan-id>
```

### Test Summary Checklist
| Test | What it proves | Pass criteria |
|------|---------------|---------------|
| Env var check | Token is injected into container | Non-empty value printed |
| Direct SQL query | Token is valid, SpacetimeDB reachable | JSON response (not 401) |
| Python backend check | Backend uses SpacetimeDB, not SQLite | `is_available()` returns True |
| Gateway plans POST | Gateway can write through to SpacetimeDB | `{"status": "saved"}` response |
| Agent sandbox E2E | Full chain works from sandbox | Plan created successfully |

---

## Also Consider

- **`.env.example`** should be updated to include `SPACETIMEDB_TOKEN` as a documented variable (it's currently missing from the example file).
- **The Python backend** silently falls back to SQLite when the token is missing. This means conversations might work (via SQLite) while plans don't (gateway-only, no SQLite fallback). This split behavior could be confusing.
- **Token rotation**: If the SpacetimeDB identity is ever regenerated, all running containers need the new token. Consider a shared secret store or config file mount rather than baking the token into multiple places.
