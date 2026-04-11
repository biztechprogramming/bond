"""Database Capability Layer (Design Doc 109).

Resolves current attached database access per agent at runtime,
exposes Bond-native virtual database tools, performs fuzzy name
resolution, and enforces call-time authorization — all backed by
Faucet MCP under the hood.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Optional

logger = logging.getLogger("bond.mcp.database_capability")

# ── Faucet MCP tool name mapping ────────────────────────────────
# Bond-native virtual tool → underlying Faucet MCP tool name
BOND_TO_FAUCET: dict[str, str] = {
    "database_list_databases": "faucet_list_services",
    "database_list_tables": "faucet_list_tables",
    "database_describe_table": "faucet_describe_table",
    "database_query": "faucet_query",
    "database_insert_rows": "faucet_insert",
    "database_update_rows": "faucet_update",
    "database_delete_rows": "faucet_delete",
    "database_execute_sql": "faucet_raw_sql",
}

READ_ONLY_TOOLS = {
    "database_list_databases",
    "database_list_tables",
    "database_describe_table",
    "database_query",
}

FULL_CONTROL_TOOLS = READ_ONLY_TOOLS | {
    "database_insert_rows",
    "database_update_rows",
    "database_delete_rows",
    "database_execute_sql",
}


# ── Virtual tool JSON schemas ───────────────────────────────────

def _db_param() -> dict:
    return {
        "type": "string",
        "description": (
            "Name or identifier of the attached database. "
            "May be omitted when the agent has exactly one attached database."
        ),
    }


DATABASE_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "database_list_databases",
            "description": "List all attached databases visible to this agent.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database_list_tables",
            "description": "List tables in an attached database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "database": _db_param(),
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database_describe_table",
            "description": "Describe the schema of a table in an attached database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "database": _db_param(),
                    "table": {
                        "type": "string",
                        "description": "Table name to describe.",
                    },
                },
                "required": ["table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database_query",
            "description": "Run a read-only SQL query against an attached database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "database": _db_param(),
                    "sql": {
                        "type": "string",
                        "description": "The SQL query to execute.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum rows to return (default 100).",
                    },
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database_insert_rows",
            "description": "Insert rows into a table in an attached database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "database": _db_param(),
                    "table": {"type": "string", "description": "Target table."},
                    "rows": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Array of row objects to insert.",
                    },
                },
                "required": ["table", "rows"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database_update_rows",
            "description": "Update rows in a table in an attached database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "database": _db_param(),
                    "table": {"type": "string", "description": "Target table."},
                    "set": {"type": "object", "description": "Column-value pairs to set."},
                    "where": {"type": "string", "description": "SQL WHERE clause."},
                },
                "required": ["table", "set", "where"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database_delete_rows",
            "description": "Delete rows from a table in an attached database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "database": _db_param(),
                    "table": {"type": "string", "description": "Target table."},
                    "where": {"type": "string", "description": "SQL WHERE clause."},
                },
                "required": ["table", "where"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database_execute_sql",
            "description": "Execute arbitrary SQL against an attached database (full control only).",
            "parameters": {
                "type": "object",
                "properties": {
                    "database": _db_param(),
                    "sql": {"type": "string", "description": "SQL to execute."},
                },
                "required": ["sql"],
            },
        },
    },
]

DATABASE_TOOL_MAP: dict[str, dict] = {
    d["function"]["name"]: d for d in DATABASE_TOOL_DEFINITIONS
}


# ── Resolved assignment dataclass ───────────────────────────────

class ResolvedDatabaseAssignment:
    """Runtime-resolved database assignment for an agent."""

    __slots__ = (
        "database_id", "database_name", "driver", "status",
        "access_tier", "faucet_role", "normalized_names",
    )

    def __init__(
        self,
        database_id: str,
        database_name: str,
        driver: str,
        status: str,
        access_tier: str,
        faucet_role: str,
    ):
        self.database_id = database_id
        self.database_name = database_name
        self.driver = driver
        self.status = status
        self.access_tier = access_tier
        self.faucet_role = faucet_role
        self.normalized_names = _build_normalized_names(database_name)

    @property
    def allowed_tools(self) -> set[str]:
        if self.access_tier == "full_control":
            return FULL_CONTROL_TOOLS
        return READ_ONLY_TOOLS


# ── Fuzzy name resolution ───────────────────────────────────────

def _normalize(name: str) -> str:
    """Lowercase, strip accents, collapse non-alnum to single space."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return name


def _build_normalized_names(name: str) -> list[str]:
    n = _normalize(name)
    variants = {name.lower(), n, n.replace(" ", ""), n.replace(" ", "-"), n.replace(" ", "_")}
    return sorted(variants)


def fuzzy_resolve(
    reference: str,
    assignments: list[ResolvedDatabaseAssignment],
) -> ResolvedDatabaseAssignment | str:
    """Resolve a fuzzy database reference against current assignments.

    Returns the matched assignment, or an error string if zero or
    multiple matches are found.
    """
    if not assignments:
        return "No databases are attached to this agent."

    ref_norm = _normalize(reference)

    # 1. Exact ID match
    for a in assignments:
        if a.database_id == reference:
            return a

    # 2. Exact normalized name match
    for a in assignments:
        if ref_norm in a.normalized_names:
            return a

    # 3. Prefix / substring match
    matches = []
    for a in assignments:
        for n in a.normalized_names:
            if n.startswith(ref_norm) or ref_norm in n:
                matches.append(a)
                break

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(f"'{m.database_name}'" for m in matches)
        return f"Ambiguous database reference '{reference}'. Matches: {names}. Please specify."

    return f"No attached database matches '{reference}'."


# ── Runtime assignment resolver ─────────────────────────────────

async def resolve_agent_databases(agent_id: str) -> list[ResolvedDatabaseAssignment]:
    """Load current attached database assignments for an agent.

    Queries agent_database_access joined with database_connections
    from SpacetimeDB at call time (turn-scoped, not cached).
    """
    try:
        from backend.app.core.spacetimedb import get_stdb
        stdb = get_stdb()
    except Exception as e:
        logger.error("Failed to get SpacetimeDB client for database resolution: %s", e)
        return []

    try:
        access_rows = await stdb.query(
            f"SELECT * FROM agent_database_access WHERE agent_id = '{agent_id}'"
        )
    except Exception as e:
        logger.error("Failed to query agent_database_access: %s", e)
        return []

    assignments: list[ResolvedDatabaseAssignment] = []
    for ar in access_rows:
        db_id = ar["database_id"]
        try:
            db_rows = await stdb.query(
                f"SELECT * FROM database_connections WHERE id = '{db_id}'"
            )
        except Exception:
            continue

        if not db_rows:
            continue

        db = db_rows[0]
        assignments.append(ResolvedDatabaseAssignment(
            database_id=db_id,
            database_name=db.get("name", db_id),
            driver=db.get("driver", "unknown"),
            status=ar.get("status", "unknown"),
            access_tier=ar.get("access_tier", "read_only"),
            faucet_role=ar.get("faucet_role_name", ""),
        ))

    return assignments


# ── Effective tool surface ──────────────────────────────────────

def get_effective_tools(
    assignments: list[ResolvedDatabaseAssignment],
) -> list[dict]:
    """Compute Bond-native virtual database tool definitions for this agent.

    Returns only the tools the agent is allowed to use based on the
    highest access tier among all attached databases.  If no databases
    are attached, returns an empty list.
    """
    if not assignments:
        return []

    # Determine broadest allowed tool set
    has_full = any(a.access_tier == "full_control" for a in assignments)
    allowed = FULL_CONTROL_TOOLS if has_full else READ_ONLY_TOOLS

    return [
        DATABASE_TOOL_MAP[name]
        for name in sorted(allowed)
        if name in DATABASE_TOOL_MAP
    ]


# ── Call-time authorization ─────────────────────────────────────

async def authorize_and_resolve(
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[Optional[ResolvedDatabaseAssignment], Optional[str], dict[str, Any]]:
    """Authorize a virtual database tool call and resolve the target database.

    Returns (assignment, error_message, faucet_arguments).
    If error_message is not None, the call should be rejected.
    """
    if tool_name not in BOND_TO_FAUCET:
        return None, f"Unknown database tool: {tool_name}", {}

    # Re-resolve assignments at call time (Doc 109 §5)
    assignments = await resolve_agent_databases(agent_id)
    if not assignments:
        return None, "No databases are attached to this agent.", {}

    # Resolve target database
    db_ref = arguments.get("database")
    if db_ref:
        result = fuzzy_resolve(db_ref, assignments)
    elif len(assignments) == 1:
        result = assignments[0]
    elif tool_name == "database_list_databases":
        # database_list_databases doesn't need a specific target
        result = assignments[0]
    else:
        names = ", ".join(f"'{a.database_name}'" for a in assignments)
        return None, f"Multiple databases attached. Please specify: {names}", {}

    if isinstance(result, str):
        return None, result, {}

    assignment = result

    # Check tool is allowed by access tier
    if tool_name not in assignment.allowed_tools:
        return None, (
            f"Tool '{tool_name}' requires full_control access, "
            f"but database '{assignment.database_name}' has read_only access."
        ), {}

    # Check assignment is in a usable state
    if assignment.status not in ("active", "healthy"):
        return None, (
            f"Database '{assignment.database_name}' is not available "
            f"(status: {assignment.status})."
        ), {}

    # Build Faucet-compatible arguments
    faucet_args = dict(arguments)
    faucet_args.pop("database", None)
    # Faucet uses 'service' to identify the database
    if tool_name != "database_list_databases":
        faucet_args["service"] = assignment.database_name

    return assignment, None, faucet_args


# ── Virtual tool handler factory ────────────────────────────────

def create_database_tool_handler(tool_name: str):
    """Create an async handler for a Bond-native virtual database tool.

    The handler performs call-time authorization and delegates to
    Faucet MCP via MCPManager.
    """
    faucet_tool = BOND_TO_FAUCET[tool_name]

    async def handler(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        agent_id = context.get("agent_id", "")
        if not agent_id:
            return {"error": "agent_id is required in context for database tools."}

        assignment, error, faucet_args = await authorize_and_resolve(
            agent_id, tool_name, arguments,
        )
        if error:
            return {"error": error}

        # Special case: database_list_databases returns assignment metadata
        if tool_name == "database_list_databases":
            assignments = await resolve_agent_databases(agent_id)
            return {
                "result": [
                    {
                        "name": a.database_name,
                        "driver": a.driver,
                        "access_tier": a.access_tier,
                        "status": a.status,
                    }
                    for a in assignments
                ]
            }

        # Delegate to Faucet MCP via MCPManager
        try:
            from backend.app.mcp import mcp_manager
            result = await mcp_manager.call_tool(
                "faucet", faucet_tool, faucet_args, scope="global",
            )
            return result
        except Exception as e:
            logger.error("Faucet MCP call failed for %s: %s", tool_name, e)
            return {"error": f"Database operation failed: {e}"}

    handler.__name__ = f"handle_{tool_name}"
    handler.__doc__ = f"Bond-native virtual handler for {tool_name} (Doc 109)."
    return handler
