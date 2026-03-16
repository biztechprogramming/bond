#!/bin/bash
# seed-spacetimedb.sh — Re-seed SpacetimeDB from SQLite source data
# Safe to run multiple times — checks for existing data before inserting.

set -e

SPACETIMEDB_URL="${SPACETIMEDB_URL:-http://localhost:18787}"
SPACETIMEDB_DB="${SPACETIMEDB_DATABASE:-bond-core-v2}"
SQLITE_DB="$HOME/.bond/data/knowledge.db"

if [ ! -f "$SQLITE_DB" ]; then
  echo "Error: SQLite database not found at $SQLITE_DB"
  exit 1
fi

echo "Seeding SpacetimeDB from SQLite ($SQLITE_DB)..."

python3 - "$SQLITE_DB" "$SPACETIMEDB_URL" "$SPACETIMEDB_DB" <<'PYEOF'
import sys, sqlite3, subprocess, time

sqlite_path = sys.argv[1]
stdb_url = sys.argv[2]
stdb_db = sys.argv[3]

conn = sqlite3.connect(sqlite_path)
conn.row_factory = sqlite3.Row

def sql(query):
    result = subprocess.run(
        ["spacetime", "sql", stdb_db, "--server", stdb_url, query],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "error" in stderr.lower() and "WARNING" not in stderr:
            print(f"      SQL error: {stderr[:200]}")
    return result.stdout

def count(table):
    out = subprocess.run(
        ["spacetime", "sql", stdb_db, "--server", stdb_url, f"SELECT COUNT(*) as c FROM {table}"],
        capture_output=True, text=True
    ).stdout
    for line in out.strip().split("\n"):
        line = line.strip()
        if line.isdigit():
            return int(line)
    return 0

def esc(s):
    if s is None:
        return ""
    return str(s).replace("'", "''")

now = int(time.time() * 1000)

# ── Agents ──────────────────────────────────────────────────────────────
if count("agents") > 0:
    print(f"  Agents: already seeded ({count('agents')} found). Skipping.")
else:
    print("  Seeding agents...")
    for agent in conn.execute("SELECT * FROM agents WHERE is_active = 1"):
        print(f"    + {agent['display_name']} ({agent['name']})")
        sql(f"""INSERT INTO agents (id, name, displayName, systemPrompt, model, utilityModel, tools, sandboxImage, maxIterations, isActive, isDefault, createdAt) VALUES (
            '{esc(agent["id"])}', '{esc(agent["name"])}', '{esc(agent["display_name"])}',
            '{esc(agent["system_prompt"])}', '{esc(agent["model"])}', '{esc(agent["utility_model"])}',
            '{esc(agent["tools"])}', '{esc(agent["sandbox_image"])}',
            {agent["max_iterations"] or 50}, true, {"true" if agent["is_default"] else "false"}, {now})""")

    for mount in conn.execute("SELECT * FROM agent_workspace_mounts"):
        print(f"    + mount: {mount['host_path']} -> {mount['container_path']}")
        sql(f"""INSERT INTO agent_workspace_mounts (id, agentId, hostPath, mountName, containerPath, readonly) VALUES (
            '{esc(mount["id"])}', '{esc(mount["agent_id"])}', '{esc(mount["host_path"])}',
            '{esc(mount["mount_name"])}', '{esc(mount["container_path"])}', {"true" if mount["readonly"] else "false"})""")

    for ch in conn.execute("SELECT * FROM agent_channels"):
        print(f"    + channel: {ch['agent_id'][:8]}... -> {ch['channel']}")
        sql(f"""INSERT INTO agent_channels (id, agentId, channel, sandboxOverride, enabled, createdAt) VALUES (
            '{esc(ch["id"])}', '{esc(ch["agent_id"])}', '{esc(ch["channel"])}', '', true, {now})""")

# ── Providers ───────────────────────────────────────────────────────────
if count("providers") > 0:
    print(f"  Providers: already seeded ({count('providers')} found). Skipping.")
else:
    print("  Seeding providers...")
    for p in conn.execute("SELECT * FROM providers"):
        print(f"    + {p['display_name']} ({p['id']})")
        sql(f"""INSERT INTO providers (id, displayName, litellmPrefix, apiBaseUrl, modelsEndpoint, modelsFetchMethod, authType, isEnabled, config, createdAt, updatedAt) VALUES (
            '{esc(p["id"])}', '{esc(p["display_name"])}', '{esc(p["litellm_prefix"])}',
            '{esc(p["api_base_url"] or "")}', '{esc(p["models_endpoint"] or "")}',
            '{esc(p["models_fetch_method"])}', '{esc(p["auth_type"])}',
            {"true" if p["is_enabled"] else "false"}, '{esc(p["config"] or "{}")}',
            {now}, {now})""")

# ── Provider API Keys ──────────────────────────────────────────────────
if count("provider_api_keys") > 0:
    print(f"  Provider API keys: already seeded ({count('provider_api_keys')} found). Skipping.")
else:
    print("  Seeding provider API keys...")
    for k in conn.execute("SELECT * FROM provider_api_keys"):
        print(f"    + key for provider: {k['provider_id']}")
        sql(f"""INSERT INTO provider_api_keys (providerId, encryptedValue, keyType, createdAt, updatedAt) VALUES (
            '{esc(k["provider_id"])}', '{esc(k["encrypted_value"])}',
            '{esc(k["key_type"] or "api_key")}', {now}, {now})""")

# ── Provider Aliases ───────────────────────────────────────────────────
if count("provider_aliases") > 0:
    print(f"  Provider aliases: already seeded ({count('provider_aliases')} found). Skipping.")
else:
    print("  Seeding provider aliases...")
    for a in conn.execute("SELECT * FROM provider_aliases"):
        print(f"    + {a['alias']} -> {a['provider_id']}")
        sql(f"""INSERT INTO provider_aliases (alias, providerId) VALUES (
            '{esc(a["alias"])}', '{esc(a["provider_id"])}')""")

# ── LLM Models ─────────────────────────────────────────────────────────
if count("llm_models") > 0:
    print(f"  LLM models: already seeded ({count('llm_models')} found). Skipping.")
else:
    print("  Seeding LLM models...")
    for m in conn.execute("SELECT * FROM llm_models"):
        print(f"    + {m['id']}")
        sql(f"""INSERT INTO llm_models (id, provider, modelId, displayName, contextWindow, isEnabled) VALUES (
            '{esc(m["id"])}', '{esc(m["provider_id"])}', '{esc(m["model_slug"])}',
            '{esc(m["display_name"])}', {m["context_window"] or 128000}, {"true" if m["is_available"] else "false"})""")

# ── Settings ───────────────────────────────────────────────────────────
if count("settings") > 0:
    print(f"  Settings: already seeded ({count('settings')} found). Skipping.")
else:
    settings = list(conn.execute("SELECT * FROM settings"))
    if settings:
        print("  Seeding settings...")
        for s in settings:
            print(f"    + {s['key']}")
            sql(f"""INSERT INTO settings (key, value, keyType, createdAt, updatedAt) VALUES (
                '{esc(s["key"])}', '{esc(s["value"])}',
                '{esc("api_key")}', {now}, {now})""")
    else:
        print("  Settings: none in SQLite. Skipping.")

# ── Prompt Fragments ──────────────────────────────────────────────────
if count("prompt_fragments") > 0:
    print(f"  Prompt fragments: already seeded ({count('prompt_fragments')} found). Skipping.")
else:
    try:
        frags = list(conn.execute("SELECT * FROM prompt_fragments"))
    except Exception:
        frags = []
    if frags:
        print(f"  Seeding prompt fragments ({len(frags)})...")
        for f in frags:
            sql(f"""INSERT INTO prompt_fragments (id, name, display_name, category, content, description, is_active, is_system, summary, tier, task_triggers, token_estimate, created_at, updated_at) VALUES (
                '{esc(f["id"])}', '{esc(f["name"])}', '{esc(f["display_name"])}', '{esc(f["category"])}',
                '{esc(f["content"])}', '{esc(f["description"] or "")}',
                {"true" if f["is_active"] else "false"}, {"true" if f["is_system"] else "false"},
                '{esc(f["summary"] or "")}', '{esc(f["tier"] or "standard")}',
                '{esc(f["task_triggers"] or "[]")}', {f["token_estimate"] or 0},
                {now}, {now})""")
        print(f"    + {len(frags)} fragments seeded")
    else:
        print("  Prompt fragments: none in SQLite. Skipping.")

conn.close()
print("\nSeed complete.")
PYEOF
