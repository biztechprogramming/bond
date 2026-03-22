# Design Doc 055: tbls Database Discovery Tool

## Status
Proposed

## Problem

When the agent needs to work with a database — write queries, debug schema issues, build features against a data model — it has no structural awareness of the database. It must either:

1. **Ask the user** to describe tables and columns (tedious, error-prone)
2. **Run exploratory queries** (`SHOW TABLES`, `\d+`, `PRAGMA table_info`, etc.) across multiple turns, burning tokens and time on discovery before doing any real work
3. **Guess** based on code context (fragile, misses constraints/relationships)

This is the database equivalent of making an agent `ls` and `cat` every file in a repo before it can do anything. We need precomputed structural awareness.

## Proposed Solution

Add a **`db_discover`** tool that uses [tbls](https://github.com/k1LoW/tbls) to introspect a database and return its full schema — tables, columns, types, constraints, foreign keys, indexes, and relationships — in a single tool call.

### Why tbls

- **Single Go binary** — no runtime dependencies, no server process
- **15+ database types** — PostgreSQL, MySQL, MariaDB, SQLite, BigQuery, Snowflake, Cloud Spanner, Amazon Redshift, MongoDB, and more
- **JSON output** — structured, parseable, agent-friendly
- **Fast** — runs in under a second for most databases
- **Maintained** — active open source project (5k+ GitHub stars)
- **Includes relationships** — foreign keys rendered as explicit relation objects, not just column metadata

### Why not MCP

MCP would require:
- A long-running server process for what is fundamentally a one-shot introspection
- Protocol handshake overhead on every connection
- Another failure mode to monitor and debug
- More pain integrating (per prior experience)

tbls as a tool is simpler: shell out, parse JSON, return result. The output is cacheable and deterministic.

## Design

### Tool Definition

```json
{
  "type": "function",
  "function": {
    "name": "db_discover",
    "description": "Discover the complete schema of a database — tables, columns, types, constraints, foreign keys, indexes, and relationships. Returns a structured map the agent can reference for writing queries, understanding data models, or debugging schema issues. Results are cached; subsequent calls for the same database return instantly.",
    "parameters": {
      "type": "object",
      "properties": {
        "connection_string": {
          "type": "string",
          "description": "Database connection URI. Examples: 'postgres://user:pass@host:5432/dbname', 'mysql://user:pass@host:3306/dbname', 'sqlite:///path/to/db.sqlite3', 'bq://project/dataset', 'mongodb://host:27017/dbname'"
        },
        "table": {
          "type": "string",
          "description": "Optional. If provided, return detailed schema for only this table (including column details, indexes, constraints, and relationships). Omit to get the full database overview."
        },
        "refresh": {
          "type": "boolean",
          "description": "Force re-discovery, bypassing the cache. Use when the schema has changed.",
          "default": false
        }
      },
      "required": ["connection_string"]
    }
  }
}
```

### Tool Handler

New file: `backend/app/agent/tools/db_discover.py`

```python
"""Database schema discovery using tbls."""

import json
import hashlib
import subprocess
import time
from pathlib import Path
from typing import Optional

CACHE_DIR = Path("data/db-discovery-cache")
CACHE_TTL = 3600  # 1 hour default

def _cache_key(connection_string: str) -> str:
    """Hash connection string for cache filename (avoids leaking creds to filesystem)."""
    return hashlib.sha256(connection_string.encode()).hexdigest()

def _get_cached(connection_string: str) -> Optional[dict]:
    """Return cached schema if fresh, else None."""
    cache_file = CACHE_DIR / f"{_cache_key(connection_string)}.json"
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL:
            return json.loads(cache_file.read_text())
    return None

def _set_cache(connection_string: str, data: dict) -> None:
    """Write schema to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_cache_key(connection_string)}.json"
    cache_file.write_text(json.dumps(data))

def _redact_connection_string(conn: str) -> str:
    """Redact password from connection string for logging."""
    # Basic redaction: replace password portion in URI
    import re
    return re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', conn)

def discover(connection_string: str, table: Optional[str] = None, refresh: bool = False) -> dict:
    """
    Run tbls against the target database and return structured schema.
    
    Returns a dict with:
      - name: database name
      - tables: list of table objects (name, type, columns, indexes, constraints, etc.)
      - relations: list of foreign key relationships
      - metadata: discovery timestamp, database type, tbls version
    """
    # Check cache (unless refresh requested or single-table query)
    if not refresh and table is None:
        cached = _get_cached(connection_string)
        if cached:
            cached["_from_cache"] = True
            return cached

    # Build tbls command
    cmd = ["tbls", "out", connection_string, "-t", "json"]
    if table:
        cmd.extend(["--table", table])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **dict(__import__('os').environ),
                # Prevent tbls from reading local .tbls.yml configs
                "TBLS_DSN": connection_string,
            }
        )
    except subprocess.TimeoutExpired:
        return {"error": f"Discovery timed out (30s) for {_redact_connection_string(connection_string)}"}
    except FileNotFoundError:
        return {"error": "tbls is not installed. Install with: go install github.com/k1LoW/tbls@latest"}

    if result.returncode != 0:
        return {
            "error": f"tbls failed: {result.stderr.strip()}",
            "connection": _redact_connection_string(connection_string),
        }

    try:
        schema = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "tbls returned invalid JSON", "raw": result.stdout[:500]}

    # Enrich with metadata
    schema["_metadata"] = {
        "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "connection": _redact_connection_string(connection_string),
        "cached": False,
    }

    # Cache full-database results
    if table is None:
        _set_cache(connection_string, schema)

    return schema
```

### Output Format

tbls JSON output includes everything the agent needs. Example for a PostgreSQL database:

```json
{
  "name": "myapp",
  "tables": [
    {
      "name": "users",
      "type": "TABLE",
      "columns": [
        { "name": "id", "type": "integer", "nullable": false, "default": "nextval('users_id_seq')" },
        { "name": "email", "type": "varchar(255)", "nullable": false },
        { "name": "created_at", "type": "timestamp", "nullable": false, "default": "now()" },
        { "name": "org_id", "type": "integer", "nullable": true }
      ],
      "indexes": [
        { "name": "users_pkey", "def": "CREATE UNIQUE INDEX users_pkey ON users USING btree (id)", "columns": ["id"] },
        { "name": "users_email_key", "def": "CREATE UNIQUE INDEX users_email_key ON users USING btree (email)", "columns": ["email"] }
      ],
      "constraints": [
        { "name": "users_pkey", "type": "PRIMARY KEY", "columns": ["id"] },
        { "name": "users_email_key", "type": "UNIQUE", "columns": ["email"] },
        { "name": "users_org_id_fkey", "type": "FOREIGN KEY", "columns": ["org_id"], "referenced_table": "organizations", "referenced_columns": ["id"] }
      ]
    }
  ],
  "relations": [
    {
      "table": "users",
      "columns": ["org_id"],
      "parent_table": "organizations",
      "parent_columns": ["id"],
      "cardinality": "zero_or_more"
    }
  ]
}
```

### Context Injection Strategy

The schema output can be large for databases with many tables. Strategy for managing context:

1. **Overview mode** (no `table` param): Return table names, column counts, and relationships only — a compact "table of contents" for the database. Typically 1-3k tokens even for large databases.

2. **Detail mode** (`table` param specified): Return full column definitions, indexes, constraints, and sample values for a single table. Used when the agent needs to write queries against specific tables.

3. **Auto-summarization**: If the full JSON exceeds a threshold (e.g., 8k tokens), the handler automatically returns overview mode with a note that the agent should use the `table` parameter for details.

```python
def _maybe_summarize(schema: dict, max_tokens: int = 8000) -> dict:
    """If schema is too large, return overview only."""
    raw = json.dumps(schema)
    estimated_tokens = len(raw) // 4  # rough estimate
    
    if estimated_tokens <= max_tokens:
        return schema
    
    # Return compact overview
    return {
        "name": schema.get("name"),
        "table_count": len(schema.get("tables", [])),
        "tables": [
            {
                "name": t["name"],
                "type": t.get("type", "TABLE"),
                "column_count": len(t.get("columns", [])),
                "columns": [c["name"] for c in t.get("columns", [])],
            }
            for t in schema.get("tables", [])
        ],
        "relations": schema.get("relations", []),
        "_metadata": schema.get("_metadata"),
        "_note": "Schema too large for full output. Use the `table` parameter to get full details for a specific table.",
    }
```

### Security Considerations

1. **Credential handling**: Connection strings contain passwords. The tool:
   - Never logs full connection strings (uses `_redact_connection_string`)
   - Hashes connection strings for cache filenames (no plaintext creds on disk)
   - Cache files are in `/tmp` with standard permissions

2. **Network access**: tbls connects to the database directly. In containerized deployments, network access is governed by the container's network policy — no new attack surface.

3. **Read-only**: tbls only reads schema metadata. It does not execute arbitrary SQL, modify data, or require write permissions. The database user should have read-only access.

4. **Cache location**: Schema cache lives in `data/db-discovery-cache/` alongside Bond's other persistent data. This ensures cache survives container restarts, is included in backups, and follows Bond's existing data lifecycle. Cache filenames are SHA-256 hashes of connection strings — no plaintext credentials on disk.

5. **No credential storage**: Connection strings are passed per-call, not persisted in config. The agent receives them from the user or from environment variables.

6. **Vault integration** (future): Connection strings could be stored in Bond's vault (like API keys) and referenced by alias: `db_discover(alias="production")`. This avoids passing credentials in conversation.

### Installation

tbls is a single Go binary. Installation options:

```bash
# Go install
go install github.com/k1LoW/tbls@latest

# Homebrew
brew install k1LoW/tap/tbls

# Docker (if running in container)
# Add to Dockerfile:
COPY --from=ghcr.io/k1low/tbls:latest /usr/bin/tbls /usr/local/bin/tbls

# Binary download
curl -sL https://github.com/k1LoW/tbls/releases/latest/download/tbls_linux_amd64.tar.gz | tar xz
mv tbls /usr/local/bin/
```

For Bond's Docker image, the `COPY --from` approach is cleanest — single layer, no build dependencies.

## Implementation Plan

### Phase 1: Core Tool (MVP)
- [ ] Install tbls binary (host + Docker image)
- [ ] Add `db_discover` tool definition to `definitions.py`
- [ ] Implement `db_discover.py` handler with caching
- [ ] Wire into `native.py` tool dispatch
- [ ] Test against SQLite, PostgreSQL, MySQL, MS SQL Server

### Phase 2: Smart Context
- [ ] Implement auto-summarization for large schemas
- [ ] Add overview vs. detail modes
- [ ] Token-budget-aware output trimming

### Phase 3: Vault Integration
- [ ] Store connection strings in Bond vault by alias
- [ ] Support `alias` parameter as alternative to `connection_string`
- [ ] UI for managing database connections in Settings

### Phase 4: Precomputed Discovery
- [ ] CLI command `bond db:discover <connection_string>` to precompute and cache schemas
- [ ] Auto-inject cached schema into agent context when working on projects with known databases
- [ ] Schema change detection (compare current vs. cached, surface drift)

## Success Criteria

- [ ] Agent can discover a database schema in a single tool call (no multi-turn exploration)
- [ ] Results are cached — repeated calls return in <10ms
- [ ] Works with at least: SQLite, PostgreSQL, MySQL
- [ ] Output is compact enough to fit in agent context without dominating the token budget
- [ ] Credentials are never logged or stored in plaintext

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| MCP server wrapping tbls | Unnecessary protocol overhead for a one-shot CLI tool. Added deployment complexity. |
| SchemaCrawler | Java dependency, heavier install, SQL-only |
| OpenMetadata | Full platform — massive overkill for schema discovery. Requires Java + Python services. |
| Raw `information_schema` queries | Database-specific SQL, no unified output format, agent must know which dialect to use |
| Prisma introspect | Node.js dependency, primarily for relational DBs, opinionated output format |

## References

- [tbls GitHub](https://github.com/k1LoW/tbls)
- [tbls JSON output format](https://github.com/k1LoW/tbls/blob/main/output/json/)
- [Design Doc 017: MCP Integration](017-mcp-integration.md)
- [Design Doc 054: Host-Side MCP Proxy](054-host-side-mcp-proxy.md)
