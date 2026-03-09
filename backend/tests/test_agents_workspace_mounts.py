"""Tests for workspace mount persistence through agent save (create + update).

These tests mock SpacetimeDB with an in-memory store to verify that
workspace mounts survive the full create → update → read cycle.

Regression test for: workspace mounts being silently dropped on agent save
(update endpoint used wrong column names in INSERT, causing SQL failure
after DELETE — mounts wiped, never re-inserted).
"""

from __future__ import annotations

import json
from unittest.mock import patch, AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient


class FakeStdb:
    """In-memory SpacetimeDB mock that stores tables as lists of dicts."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {
            "agents": [],
            "agent_workspace_mounts": [],
            "agent_channels": [],
        }

    async def query(self, sql: str) -> list[dict]:
        sql = sql.strip()

        # ── SELECT ──
        if sql.upper().startswith("SELECT"):
            return self._handle_select(sql)

        # ── INSERT ──
        if sql.upper().startswith("INSERT"):
            return self._handle_insert(sql)

        # ── UPDATE ──
        if sql.upper().startswith("UPDATE"):
            return self._handle_update(sql)

        # ── DELETE ──
        if sql.upper().startswith("DELETE"):
            return self._handle_delete(sql)

        return []

    def _find_table(self, sql: str) -> str:
        sql_upper = sql.upper()
        for table in self.tables:
            if table.upper() in sql_upper:
                return table
        raise ValueError(f"No table found in SQL: {sql}")

    def _handle_select(self, sql: str) -> list[dict]:
        table = self._find_table(sql)
        rows = self.tables[table]

        # Simple WHERE clause parsing
        if "WHERE" in sql.upper():
            where_part = sql.split("WHERE", 1)[1].strip()
            # Handle AND conditions
            conditions = [c.strip() for c in where_part.split("AND")]
            filtered = rows
            for cond in conditions:
                # Handle != 
                if "!=" in cond:
                    parts = cond.split("!=")
                    col = parts[0].strip()
                    val = parts[1].strip().strip("'")
                    filtered = [r for r in filtered if str(r.get(col, "")) != val]
                elif "=" in cond:
                    parts = cond.split("=", 1)
                    col = parts[0].strip()
                    val = parts[1].strip().strip("'")
                    # Handle boolean
                    if val == "true":
                        filtered = [r for r in filtered if r.get(col) is True]
                    elif val == "false":
                        filtered = [r for r in filtered if r.get(col) is False]
                    else:
                        filtered = [r for r in filtered if str(r.get(col, "")) == val]
            return filtered

        return rows

    def _handle_insert(self, sql: str) -> list[dict]:
        table = self._find_table(sql)

        # Parse columns and values from INSERT INTO table (col1, col2) VALUES (val1, val2)
        import re
        # Extract column names
        cols_match = re.search(r'\(([^)]+)\)\s*VALUES', sql, re.IGNORECASE)
        if not cols_match:
            raise ValueError(f"Could not parse INSERT columns: {sql}")
        columns = [c.strip() for c in cols_match.group(1).split(",")]

        # Extract values
        vals_match = re.search(r'VALUES\s*\((.+)\)', sql, re.IGNORECASE | re.DOTALL)
        if not vals_match:
            raise ValueError(f"Could not parse INSERT values: {sql}")
        
        raw_vals = vals_match.group(1)
        values = []
        current = ""
        in_quotes = False
        for char in raw_vals:
            if char == "'" and not in_quotes:
                in_quotes = True
                continue
            elif char == "'" and in_quotes:
                in_quotes = False
                continue
            elif char == "," and not in_quotes:
                values.append(current.strip())
                current = ""
                continue
            current += char
        values.append(current.strip())

        # Convert types
        parsed = {}
        for col, val in zip(columns, values):
            if val.lower() == "true":
                parsed[col] = True
            elif val.lower() == "false":
                parsed[col] = False
            elif val.isdigit():
                parsed[col] = int(val)
            else:
                parsed[col] = val

        # Validate columns match the expected schema
        self._validate_columns(table, columns)

        self.tables[table].append(parsed)
        return []

    def _validate_columns(self, table: str, columns: list[str]):
        """Validate that INSERT columns match the known schema."""
        schemas = {
            "agents": {
                "id", "name", "display_name", "system_prompt", "model",
                "utility_model", "tools", "sandbox_image", "max_iterations",
                "is_active", "is_default", "created_at", "auto_rag", "auto_rag_limit",
            },
            "agent_workspace_mounts": {
                "id", "agent_id", "host_path", "mount_name", "container_path", "readonly",
            },
            "agent_channels": {
                "agent_id", "channel", "enabled", "sandbox_override",
            },
        }
        expected = schemas.get(table)
        if expected is None:
            return
        unexpected = set(columns) - expected
        if unexpected:
            raise ValueError(
                f"INSERT into '{table}' has unknown columns: {unexpected}. "
                f"Expected columns: {expected}"
            )

    def _handle_update(self, sql: str) -> list[dict]:
        table = self._find_table(sql)

        import re
        # Parse SET clause
        set_match = re.search(r'SET\s+(.+?)\s+WHERE', sql, re.IGNORECASE | re.DOTALL)
        if not set_match:
            # SET without WHERE (e.g. UPDATE agents SET is_default = false WHERE is_default = true)
            set_match = re.search(r'SET\s+(.+?)$', sql, re.IGNORECASE | re.DOTALL)
        
        if not set_match:
            return []

        # Find matching rows via WHERE
        where_match = re.search(r'WHERE\s+(.+)$', sql, re.IGNORECASE)
        rows = self.tables[table]
        if where_match:
            where_part = where_match.group(1).strip()
            # Simple single condition
            if "=" in where_part:
                col, val = where_part.split("=", 1)
                col = col.strip()
                val = val.strip().strip("'")
                if val == "true":
                    rows = [r for r in rows if r.get(col) is True]
                elif val == "false":
                    rows = [r for r in rows if r.get(col) is False]
                else:
                    rows = [r for r in rows if str(r.get(col, "")) == val]

        # Parse SET assignments
        set_str = set_match.group(1)
        assignments = [a.strip() for a in set_str.split(",")]
        for row in rows:
            for assignment in assignments:
                col, val = assignment.split("=", 1)
                col = col.strip()
                val = val.strip().strip("'")
                if val == "true":
                    row[col] = True
                elif val == "false":
                    row[col] = False
                elif val.isdigit():
                    row[col] = int(val)
                else:
                    row[col] = val

        return []

    def _handle_delete(self, sql: str) -> list[dict]:
        table = self._find_table(sql)

        import re
        where_match = re.search(r'WHERE\s+(.+)$', sql, re.IGNORECASE)
        if where_match:
            where_part = where_match.group(1).strip()
            if "=" in where_part:
                col, val = where_part.split("=", 1)
                col = col.strip()
                val = val.strip().strip("'")
                self.tables[table] = [
                    r for r in self.tables[table]
                    if str(r.get(col, "")) != val
                ]
        else:
            self.tables[table] = []

        return []

    async def call_reducer(self, reducer: str, args: list) -> bool:
        return True


@pytest.fixture()
def fake_stdb():
    return FakeStdb()


@pytest.fixture()
async def client(fake_stdb):
    """ASGI test client with SpacetimeDB mocked out."""
    from backend.app.core.spacetimedb import get_stdb
    from backend.app.main import app

    # Override both the module-level import and the FastAPI dependency
    app.dependency_overrides[get_stdb] = lambda: fake_stdb
    with patch("backend.app.api.v1.agents.get_stdb", return_value=fake_stdb):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c
    app.dependency_overrides.pop(get_stdb, None)


# ── Helpers ──

def _make_agent_body(
    name="test-agent",
    mounts=None,
    channels=None,
):
    return {
        "name": name,
        "display_name": f"Test {name}",
        "system_prompt": "You are a test agent.",
        "model": "anthropic/claude-sonnet-4-6",
        "tools": ["respond"],
        "max_iterations": 25,
        "workspace_mounts": mounts or [],
        "channels": channels or [{"channel": "webchat", "enabled": True}],
    }


SAMPLE_MOUNTS = [
    {
        "host_path": "/home/user/project-a",
        "mount_name": "project-a",
        "container_path": "/workspace/project-a",
        "readonly": False,
    },
    {
        "host_path": "/home/user/data",
        "mount_name": "data",
        "container_path": "/workspace/data",
        "readonly": True,
    },
]


# ── Tests: Create preserves mounts ──


@pytest.mark.asyncio
async def test_create_agent_with_mounts(client, fake_stdb):
    """Mounts provided at creation should be persisted and returned."""
    body = _make_agent_body(mounts=SAMPLE_MOUNTS)
    res = await client.post("/api/v1/agents", json=body)
    assert res.status_code == 200
    data = res.json()

    assert len(data["workspace_mounts"]) == 2
    mount_names = {m["mount_name"] for m in data["workspace_mounts"]}
    assert mount_names == {"project-a", "data"}

    # Verify readonly flag preserved
    data_mount = next(m for m in data["workspace_mounts"] if m["mount_name"] == "data")
    assert data_mount["readonly"] is True
    assert data_mount["host_path"] == "/home/user/data"
    assert data_mount["container_path"] == "/workspace/data"


# ── Tests: Update preserves mounts ──


@pytest.mark.asyncio
async def test_update_agent_preserves_existing_mounts(client, fake_stdb):
    """Updating non-mount fields should NOT drop workspace mounts."""
    body = _make_agent_body(mounts=SAMPLE_MOUNTS)
    create_res = await client.post("/api/v1/agents", json=body)
    assert create_res.status_code == 200
    agent_id = create_res.json()["id"]

    # Update only the display name — do NOT send workspace_mounts
    update_body = {"display_name": "Updated Name"}
    res = await client.put(f"/api/v1/agents/{agent_id}", json=update_body)
    assert res.status_code == 200
    data = res.json()

    assert data["display_name"] == "Updated Name"
    # Mounts should still be there since we didn't send workspace_mounts
    assert len(data["workspace_mounts"]) == 2


@pytest.mark.asyncio
async def test_update_agent_replaces_mounts(client, fake_stdb):
    """Sending workspace_mounts in update should replace them entirely."""
    body = _make_agent_body(mounts=SAMPLE_MOUNTS)
    create_res = await client.post("/api/v1/agents", json=body)
    assert create_res.status_code == 200
    agent_id = create_res.json()["id"]

    new_mounts = [
        {
            "host_path": "/home/user/new-project",
            "mount_name": "new-project",
            "container_path": "/workspace/new-project",
            "readonly": False,
        }
    ]
    update_body = {"workspace_mounts": new_mounts}
    res = await client.put(f"/api/v1/agents/{agent_id}", json=update_body)
    assert res.status_code == 200
    data = res.json()

    assert len(data["workspace_mounts"]) == 1
    assert data["workspace_mounts"][0]["mount_name"] == "new-project"
    assert data["workspace_mounts"][0]["host_path"] == "/home/user/new-project"


@pytest.mark.asyncio
async def test_update_agent_mounts_roundtrip(client, fake_stdb):
    """Full save cycle: create with mounts → update with same mounts → verify intact.

    This is the exact scenario that was broken: the settings page sends all
    fields (including workspace_mounts) on every save, which triggered the
    delete-then-reinsert path. The INSERT used wrong column names, so mounts
    were deleted but never re-inserted.
    """
    body = _make_agent_body(mounts=SAMPLE_MOUNTS)
    create_res = await client.post("/api/v1/agents", json=body)
    assert create_res.status_code == 200
    agent_id = create_res.json()["id"]

    # Simulate a settings page save: send ALL fields including mounts
    full_save_body = {
        "name": "test-agent",
        "display_name": "Test test-agent",
        "system_prompt": "You are a test agent.",
        "model": "anthropic/claude-sonnet-4-6",
        "tools": ["respond"],
        "max_iterations": 25,
        "workspace_mounts": SAMPLE_MOUNTS,
        "channels": [{"channel": "webchat", "enabled": True}],
    }
    res = await client.put(f"/api/v1/agents/{agent_id}", json=full_save_body)
    assert res.status_code == 200
    data = res.json()

    # THE KEY ASSERTION: mounts must survive the save
    assert len(data["workspace_mounts"]) == 2, (
        f"Expected 2 workspace mounts after save, got {len(data['workspace_mounts'])}. "
        "Mounts were likely dropped during update."
    )

    mount_names = {m["mount_name"] for m in data["workspace_mounts"]}
    assert mount_names == {"project-a", "data"}

    # Verify all fields preserved
    for mount in data["workspace_mounts"]:
        if mount["mount_name"] == "project-a":
            assert mount["host_path"] == "/home/user/project-a"
            assert mount["container_path"] == "/workspace/project-a"
            assert mount["readonly"] is False
        elif mount["mount_name"] == "data":
            assert mount["host_path"] == "/home/user/data"
            assert mount["container_path"] == "/workspace/data"
            assert mount["readonly"] is True


@pytest.mark.asyncio
async def test_update_agent_clear_mounts(client, fake_stdb):
    """Sending empty workspace_mounts should remove all mounts."""
    body = _make_agent_body(mounts=SAMPLE_MOUNTS)
    create_res = await client.post("/api/v1/agents", json=body)
    assert create_res.status_code == 200
    agent_id = create_res.json()["id"]

    update_body = {"workspace_mounts": []}
    res = await client.put(f"/api/v1/agents/{agent_id}", json=update_body)
    assert res.status_code == 200
    assert len(res.json()["workspace_mounts"]) == 0


@pytest.mark.asyncio
async def test_update_agent_mount_readonly_flag(client, fake_stdb):
    """Readonly flag must survive the update cycle."""
    mounts = [
        {"host_path": "/tmp/ro", "mount_name": "ro-mount", "container_path": "/workspace/ro", "readonly": True},
        {"host_path": "/tmp/rw", "mount_name": "rw-mount", "container_path": "/workspace/rw", "readonly": False},
    ]
    body = _make_agent_body(mounts=mounts)
    create_res = await client.post("/api/v1/agents", json=body)
    agent_id = create_res.json()["id"]

    # Re-save same mounts
    update_body = {"workspace_mounts": mounts}
    res = await client.put(f"/api/v1/agents/{agent_id}", json=update_body)
    assert res.status_code == 200
    data = res.json()

    ro_mount = next(m for m in data["workspace_mounts"] if m["mount_name"] == "ro-mount")
    rw_mount = next(m for m in data["workspace_mounts"] if m["mount_name"] == "rw-mount")
    assert ro_mount["readonly"] is True
    assert rw_mount["readonly"] is False


# ── Tests: Channel update (also fixed) ──


@pytest.mark.asyncio
async def test_update_agent_replaces_channels(client, fake_stdb):
    """Channels should also survive the update cycle."""
    body = _make_agent_body(
        channels=[
            {"channel": "webchat", "enabled": True},
            {"channel": "telegram", "enabled": True},
        ]
    )
    create_res = await client.post("/api/v1/agents", json=body)
    agent_id = create_res.json()["id"]

    # Update channels
    update_body = {
        "channels": [
            {"channel": "signal", "enabled": True},
            {"channel": "discord", "enabled": True},
        ]
    }
    res = await client.put(f"/api/v1/agents/{agent_id}", json=update_body)
    assert res.status_code == 200
    data = res.json()

    channel_names = {c["channel"] for c in data["channels"]}
    assert channel_names == {"signal", "discord"}


# ── Schema validation ──


@pytest.mark.asyncio
async def test_mount_insert_uses_correct_columns(fake_stdb):
    """Direct validation: INSERT into agent_workspace_mounts must use the right column names."""
    # This test would have caught the original bug — source_path is not a valid column
    valid_columns = {"id", "agent_id", "host_path", "mount_name", "container_path", "readonly"}

    # Simulate what the old broken code did
    with pytest.raises(ValueError, match="unknown columns"):
        await fake_stdb.query("""
            INSERT INTO agent_workspace_mounts (agent_id, source_path, container_path)
            VALUES ('test-id', '/tmp/src', '/workspace/src')
        """)

    # The correct INSERT should work
    await fake_stdb.query("""
        INSERT INTO agent_workspace_mounts (
            id, agent_id, host_path, mount_name, container_path, readonly
        ) VALUES (
            'mount-1', 'test-id', '/tmp/src', 'src', '/workspace/src', false
        )
    """)
    assert len(fake_stdb.tables["agent_workspace_mounts"]) == 1
