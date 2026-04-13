# Design Doc 108: Agent Database Context and Query Behavior

**Status:** Proposed  
**Date:** 2026-04-11  
**Author:** Bond Agent  
**Depends on:** 021 (Prompt Hierarchy & Agent-Initiated Context Loading), 055 (tbls Database Discovery), 107 (Faucet Database Integration)

---

## Problem

Design Doc 107 gives Bond a way to attach governed database access to an agent, but it does **not** yet define how the agent should *understand* and *use* that access at runtime.

Today, even when a database is attached to an agent, the agent may still behave as if the database does not exist. Instead of querying the attached database, it may:

1. Search the workspace for ORM configuration or migration files
2. Infer schema from application code
3. Tell the user it can probably connect if credentials exist
4. Stop after describing what it *could* do instead of actually doing it

This creates a bad user experience:

- The user explicitly attached a database to the agent
- The agent should treat that as a first-class capability
- But the current prompt/context system does not surface attached databases clearly enough
- And there is no explicit behavioral contract telling the agent when to prefer live database tools over codebase inference

The result is exactly the failure mode observed with the attached `resume prettier` database: when asked whether it could query the database, the agent inspected files and described the app's database configuration instead of querying the attached database.

---

## Goals

1. **Make attached databases visible in agent context every turn.**
2. **Teach agents to prefer live database introspection/query tools** when the user asks about an attached database.
3. **Distinguish attached databases from codebase-inferred databases.**
4. **Set clear execution rules** so agents query first and describe second.
5. **Preserve safety boundaries** based on the assigned access tier.

## Non-Goals

- Replacing Faucet's RBAC or transport design from Doc 107
- Designing a new SQL execution system outside Faucet/tbls
- Solving every schema summarization problem in this doc
- Implementing row-level security UI or advanced Faucet policy authoring

---

## User Experience Principle

If a database is attached to an agent, the agent should behave as though the database is one of its available tools, not as a vague possibility.

When the user asks:

- "Can you query the resume prettier database?"
- "What tables are in the attached Postgres database?"
- "Show me sample rows from the customer table"
- "How is this app using its database?"

The agent should follow this order of operations:

1. **Check attached database context**
2. **Use attached database tools if available**
3. **Only fall back to reading code** when the user asked about application usage, migrations, ORM wiring, or when no attached database can answer the question

---

## Proposed Solution

Add a new runtime context layer for **Attached Database Context** and pair it with a new prompt fragment that teaches agents how to use it.

The solution has four parts:

### 1. Attached database context is injected into the system prompt

During context building, Bond resolves the agent's database assignments and appends a compact machine-readable/human-readable section to the system prompt.

Example:

```md
## Attached Databases

You have managed access to these databases for this conversation:

- name: resume prettier
  id: 01J...
  driver: postgresql
  status: healthy
  access_tier: read_only
  source: attached_database
  allowed_tools: faucet_list_tables, faucet_describe_table, faucet_query

- name: analytics-dev
  id: 01K...
  driver: sqlite
  status: healthy
  access_tier: full_control
  source: attached_database
  allowed_tools: faucet_list_tables, faucet_describe_table, faucet_query, faucet_insert, faucet_update, faucet_delete, faucet_raw_sql

Rules:
- Treat attached databases as available live data sources.
- If the user asks what is in an attached database, inspect/query the database first.
- Do not answer from code inference when attached database tools can answer directly.
- Respect access tier restrictions.
```

This section is always present when the agent has one or more database assignments.

### 2. Add a prompt fragment for database query behavior

Add a new prompt fragment under the database prompt tree that explicitly teaches:

- attached database access is authoritative for live data questions
- codebase inspection is secondary for schema/data questions
- agents should use list/describe/query tools before speculating
- agents should clearly separate:
  - **live database facts**
  - **codebase assumptions/inference**

This should be a Tier 3 fragment so it activates for database-oriented requests.

### 3. Define tool-selection behavior for attached databases

When the user asks a database question and attached databases exist, the agent should prefer this sequence:

#### Read-only questions
1. `faucet_list_tables`
2. `faucet_describe_table`
3. `faucet_query`

Examples:
- "What tables are in the resume prettier database?"
- "Can you query it?"
- "Show me a few rows from users"
- "What columns does resumes have?"

#### Full-control questions
Use the same read path first unless the user explicitly requested mutation.

Examples:
- "Insert a test row"
- "Fix these bad records"
- "Run this migration SQL"

Even with full control, the agent should inspect before mutating.

### 4. Define fallback behavior

If attached database context exists but the database is unhealthy, unavailable, or permission-limited, the agent should say so directly and only then fall back to code inspection if that would still help.

Examples:

- "The attached database exists, but its connection is currently unhealthy, so I couldn't query it live. I can still inspect the codebase's ORM models if you want."
- "This database is attached with read-only access, so I can inspect schema and query rows, but I can't modify data."

---

## Runtime Context Contract

### New context section

`build_agent_context()` should append an **Attached Databases** section after memory injection and before or alongside domain fragments.

Suggested shape:

```python
@dataclass
class AttachedDatabaseContext:
    id: str
    name: str
    driver: str
    status: str
    access_tier: str
    source: str = "attached_database"
    allowed_tools: list[str]
```

Context rendering:

```md
## Attached Databases
- resume prettier (postgresql, read_only, healthy)
  allowed_tools: faucet_list_tables, faucet_describe_table, faucet_query
- analytics-dev (sqlite, full_control, healthy)
  allowed_tools: faucet_list_tables, faucet_describe_table, faucet_query, faucet_insert, faucet_update, faucet_delete, faucet_raw_sql
```

### Data source for context injection

Bond should resolve this from the same assignment model introduced by Doc 107:

- `agent_database_access`
- `database_connections`

At minimum, the context builder needs:

- attached database name
- driver
- health/status
- access tier
- allowed tool set derived from access tier

### Why system-prompt injection is required

The current prompt system loads:

- base system prompt
- memory results
- Tier 1 fragments
- Tier 3 fragments selected from the user message
- category manifest

But none of these inherently tell the model **which specific databases are attached right now**. Without explicit runtime injection, the model must infer from tools or from project files, which leads to the wrong behavior.

---

## Prompt Fragment Design

### New fragment

Suggested path:

- `prompts/database/attached-access/attached-access.md`

This keeps the concept inside the database tree without altering universal prompts.

### Suggested fragment content

```md
# Attached Database Access

When a database is attached to the current agent, treat it as an available live data source.

## Rules
- If the user asks about the contents, schema, rows, or queryability of an attached database, use database tools first.
- Do not answer live-data questions by only reading application files, ORM models, migrations, or `.env` files when attached database tools can answer directly.
- Separate live facts from code inference.
- If multiple databases are attached, choose the one matching the user's name or ask a brief clarifying question.
- Respect access tier restrictions. Read-only means inspect/query only. Full control still requires caution before mutations.
- If the attached database is unavailable, say so directly and then offer codebase inspection as a fallback.

## Preferred order
1. list tables
2. describe relevant tables
3. query small, safe samples
4. summarize findings clearly

## Never do this
- Claim you can query the database without actually trying
- Prefer code inference over attached database tools for live-data questions
- Assume schema from migrations when live introspection is available
```

### Manifest registration

Add the new fragment to `prompts/manifest.yaml` as Tier 3 with utterances such as:

- "attached database"
- "query the database"
- "what is in the database"
- "database tables"
- "database schema"
- "sample rows"
- "can you query"

This ensures the fragment is selected for common database questions.

---

## Behavioral Rules

### Rule 1: Attached database beats code inference for live questions

If both are available, use the attached database for:

- current rows
- current table list
- current schema
- data validation
- answering "can you query it?"

Use code inspection for:

- how the app connects to the database
- ORM configuration
- migration history
- where specific tables are used in code
- whether the app *expects* a table/column that is missing live

### Rule 2: Query first, then explain

Bad:

> I found TypeORM config and env vars, so I should be able to query if credentials are present.

Good:

> Yes. I queried the attached `resume prettier` database and found 12 tables. The main ones are `resume`, `template`, and `user_profile`.

### Rule 3: Be explicit about source of truth

Responses should distinguish between:

- **From attached database:** live schema/data facts
- **From codebase:** inferred usage/configuration facts

Example:

> From the attached database, I can see `resume(id, user_id, content, created_at)`. From the codebase, it looks like TypeORM maps this through `ResumeEntity`.

### Rule 4: Small, safe exploration by default

For exploratory queries, agents should:

- list tables first
- inspect one or two relevant tables
- fetch small samples
- avoid broad `SELECT *` on large tables unless necessary
- avoid mutations unless explicitly requested

### Rule 5: Ask only when ambiguity is real

If exactly one attached database matches the user's wording, query it.
If multiple attached databases could match, ask a short disambiguation question.
If none match, say that no matching attached database is available.

---

## Example Scenarios

### Scenario A: User asks if the agent can query the attached DB

**User:** "Are you able to query what is in the resume prettier database?"

**Correct behavior:**
1. Recognize `resume prettier` as an attached database
2. Use Faucet tools to inspect it
3. Report actual findings

**Good response shape:**

> Yes. I queried the attached `resume prettier` database. It currently has these tables: `...`. I can inspect any of them in more detail.

### Scenario B: User asks how the app uses the DB

**User:** "How does ResumePrettier use its database?"

**Correct behavior:**
- Read code, ORM mappings, migrations, and env config
- Optionally cross-check live schema if helpful
- Explain application behavior, not just raw tables

### Scenario C: Attached DB is unhealthy

**User:** "Show me rows from resumes"

**Correct behavior:**

> The `resume prettier` database is attached, but its current status is unhealthy, so I couldn't query it live. I can inspect the codebase schema definitions instead if that helps.

---

## Implementation Sketch

### Backend: context builder

Add a helper used by `build_agent_context()`:

```python
async def build_attached_database_prompt(agent_id: str, stdb: Any) -> str:
    ...
```

Responsibilities:

1. Load agent database assignments
2. Join connection metadata
3. Derive allowed tools from access tier
4. Render compact prompt text

This should be appended to `full_system_prompt` only when assignments exist.

### Backend/API requirements

The runtime needs a reliable way to fetch:

- agent database assignments
- database metadata
- current health/status

If this data is not yet exposed through the backend's runtime dependencies, that gap should be closed as part of implementing this doc.

### Prompt system

Add the fragment file and register it in `manifest.yaml`.

No prompt hierarchy redesign is needed. This is an additive fragment plus runtime context injection.

---

## Alternatives Considered

### 1. Rely only on tool availability

Rejected.

Even if Faucet tools are available, the model may not infer that a specific named database is attached and intended for use. Tool presence alone is too implicit.

### 2. Rely only on database-related Tier 3 fragments

Rejected.

General database guidance teaches best practices, but it does not communicate runtime-specific facts like:

- which databases are attached
- what they are called
- whether they are healthy
- what access tier applies

### 3. Put this in the universal prompt

Rejected.

Most turns do not involve databases. This guidance should stay domain-specific, with only the runtime attachment list injected when relevant.

---

## Risks

### Prompt bloat

If many databases are attached, the context section could grow. Mitigation:

- keep each database entry compact
- include only the fields needed for tool selection
- cap verbose metadata

### Stale health information

If status is injected from cached health, the prompt may lag reality. Mitigation:

- treat status as advisory
- actual tool execution remains the source of truth

### Over-eager querying

Agents may query when the user wanted architecture explanation. Mitigation:

- the fragment should distinguish live-data questions from code-architecture questions
- agents should still follow the user's exact request

---

## Acceptance Criteria

1. If an agent has an attached database and the user asks whether it can query it, the agent attempts live database inspection before reading code.
2. If the user asks what is in an attached database, the agent uses database tools first and reports actual findings.
3. If the database is unavailable, the agent says so directly and may offer code inspection as fallback.
4. Responses clearly distinguish live database facts from codebase inference.
5. The system prompt includes the list of attached databases for the current agent when any exist.
6. A database-specific prompt fragment exists that teaches this behavior and is selectable through the manifest.

---

## References

- [Design Doc 021: Prompt Hierarchy & Agent-Initiated Context Loading](021-prompt-hierarchy.md)
- [Design Doc 055: tbls Database Discovery](055-tbls-database-discovery.md)
- [Design Doc 107: Faucet Database Integration](107-faucet-database-integration.md)
