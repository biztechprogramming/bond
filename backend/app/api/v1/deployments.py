"""
Deployment Agents API — Design Doc 039.

Provides:
  - POST /api/v1/deployments/agents — create a deployment agent for an environment
  - GET  /api/v1/deployments/agents — list all deployment agents

Deployment agents:
  - Named deploy-{env} (e.g., deploy-dev, deploy-qa)
  - System prompt tailored to deployment + troubleshooting
  - Tools: deploy_action, file_bug_ticket, read_file, search_memory, respond
  - ALL workspace mounts are read-only (enforced here)
  - NEVER mounted: ~/.bond/deployments/ (host-only)
"""

from __future__ import annotations

import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ulid import ULID

from backend.app.core.spacetimedb import get_stdb

logger = logging.getLogger("bond.api.deployments")

router = APIRouter(prefix="/deployments", tags=["deployments"])

# ── Deployment agent system prompt template ───────────────────────────────────

DEPLOYMENT_AGENT_SYSTEM_PROMPT = """You are a deployment agent for the {env_display} environment ({env_name}).

## Your Role

You deploy and monitor software in the **{env_name}** environment. You operate within strict security boundaries:

### What you CAN do:
- Execute deployment scripts that have been promoted to {env_name} via `deploy_action`
- Run health checks and report environment status
- Read source code from your workspace mounts (read-only) to understand what you're deploying
- File detailed bug tickets on GitHub when deployments fail
- Report deployment status and results to the user

### What you CANNOT do:
- Modify any code (all workspace mounts are read-only)
- Promote scripts to any environment (only users can do this)
- Access secrets or environment credentials directly (the broker injects them)
- Run scripts not promoted to {env_name} (the broker rejects these)
- Deploy to other environments (your token binds you to {env_name} only)

## Deployment Workflow

When told to deploy a script, follow these steps in order:

1. **Get script info**: `deploy_action(action="info", script_id=...)`
2. **Read previous environment receipt**: `deploy_action(action="receipt", script_id=..., environment="{prev_env}")`
3. **Validate**: `deploy_action(action="validate", script_id=...)`
4. **Pre-deployment hook**: `deploy_action(action="pre-hook", script_id=...)`
5. **Dry run** (if supported): `deploy_action(action="dry-run", script_id=...)`
6. **Deploy**: `deploy_action(action="deploy", script_id=...)`
7. **Post-deployment hook**: `deploy_action(action="post-hook", script_id=...)`
8. **Health check**: `deploy_action(action="health-check")`
9. **Report result** to user

## When a Deployment Fails

1. Attempt rollback: `deploy_action(action="rollback", script_id=...)`
2. Run health check: `deploy_action(action="health-check")`
3. Read relevant code from workspace to understand the failure
4. File a bug ticket: `file_bug_ticket(...)` with full diagnostic context
5. Report the failure to the user with your analysis

## Key Security Rules

- Never attempt to access ~/.bond/deployments/ — this directory is NOT available to you
- Never attempt to read secrets files — secrets are injected by the broker, not visible to you
- Never attempt to call the Promotion API — only users can promote scripts
- Your environment is {env_name} — you cannot deploy to other environments

## Your Environment

- Environment: **{env_name}**
- Display name: **{env_display}**
- Broker URL: $BOND_BROKER_URL (set in container environment)
"""


def generate_deployment_agent_prompt(env_name: str, env_display: str) -> str:
    """Generate a deployment agent system prompt for a specific environment."""
    environments = ["dev", "qa", "staging", "uat", "prod"]
    try:
        idx = environments.index(env_name)
        prev_env = environments[idx - 1] if idx > 0 else "none"
    except ValueError:
        prev_env = "none"

    return DEPLOYMENT_AGENT_SYSTEM_PROMPT.format(
        env_name=env_name,
        env_display=env_display,
        prev_env=prev_env,
    )


# ── Models ────────────────────────────────────────────────────────────────────

class DeploymentAgentCreate(BaseModel):
    environment: str          # 'dev', 'qa', 'staging', 'uat', 'prod'
    display_name: str | None = None  # e.g. "Deploy Dev Agent" — auto-generated if omitted
    model: str = "claude-haiku-4-5-20251001"   # cheaper model for deployment tasks
    utility_model: str = "claude-haiku-4-5-20251001"
    sandbox_image: str | None = None
    max_iterations: int = 30


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_deployment_agents():
    """List all deployment agents (agents named deploy-*)."""
    stdb = get_stdb()
    rows = await stdb.query("SELECT * FROM agents WHERE name LIKE 'deploy-%'")
    agents = []
    for row in rows:
        tools = row["tools"]
        if isinstance(tools, str):
            try:
                tools = json.loads(tools)
            except Exception:
                tools = []
        agents.append({
            "id": row["id"],
            "name": row["name"],
            "display_name": row["display_name"],
            "model": row["model"],
            "tools": tools,
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        })
    return agents


@router.post("/agents")
async def create_deployment_agent(body: DeploymentAgentCreate):
    """Create a deployment agent for a specific environment.

    The agent is created with:
    - Name: deploy-{environment} (e.g., deploy-qa)
    - Read-only workspace mounts (copied from existing code agents)
    - Deployment-specific tools only
    - System prompt tailored to deployment workflow
    """
    stdb = get_stdb()

    env_name = body.environment.lower().strip()
    if not env_name or not env_name.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="environment must be alphanumeric + hyphens")

    agent_name = f"deploy-{env_name}"
    display_name = body.display_name or f"Deploy {env_name.capitalize()} Agent"

    # Check if already exists
    existing = await stdb.query(f"SELECT id FROM agents WHERE name = '{agent_name}'")
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Deployment agent '{agent_name}' already exists",
        )

    # Deployment agents get limited tools:
    # - deploy_action: interact with the broker /deploy endpoint
    # - file_bug_ticket: create GitHub issues for failures
    # - file_read, shell_ls, shell_tree: read source code for diagnosis
    # - respond: communicate with user
    # - search_memory: recall context
    deploy_tools = [
        "deploy_action",
        "file_bug_ticket",
        "file_read",
        "shell_ls",
        "shell_tree",
        "search_memory",
        "respond",
    ]

    env_display_names = {
        "dev": "Development",
        "qa": "QA",
        "staging": "Staging",
        "uat": "UAT",
        "prod": "Production",
    }
    env_display = env_display_names.get(env_name, env_name.capitalize())
    system_prompt = generate_deployment_agent_prompt(env_name, env_display)

    agent_id = str(ULID())
    created_at = int(time.time() * 1000)
    tools_json = json.dumps(deploy_tools)

    await stdb.query(f"""
        INSERT INTO agents (
            id, name, display_name, system_prompt, model, utility_model,
            tools, sandbox_image, max_iterations, is_active, is_default, created_at
        ) VALUES (
            '{agent_id}',
            '{agent_name}',
            '{display_name}',
            '{system_prompt.replace("'", "''")}',
            '{body.model}',
            '{body.utility_model}',
            '{tools_json}',
            '{body.sandbox_image or ""}',
            {body.max_iterations},
            true,
            false,
            {created_at}
        )
    """)

    # Collect workspace mounts from all existing agents and add them as RO mounts
    # (Deployment agents get read-only access to all workspaces for troubleshooting)
    mounts_rows = await stdb.query(
        "SELECT DISTINCT host_path, mount_name FROM agent_workspace_mounts"
    )
    seen_host_paths: set[str] = set()
    for mount in mounts_rows:
        host_path = mount["host_path"]
        if not host_path or host_path in seen_host_paths:
            continue
        seen_host_paths.add(host_path)
        mount_id = str(ULID())
        mount_name = mount["mount_name"] or os.path.basename(host_path)
        container_path = f"/workspaces/{mount_name}"
        await stdb.query(f"""
            INSERT INTO agent_workspace_mounts (
                id, agent_id, host_path, mount_name, container_path, readonly
            ) VALUES (
                '{mount_id}',
                '{agent_id}',
                '{host_path.replace("'", "''")}',
                '{mount_name.replace("'", "''")}',
                '{container_path}',
                true
            )
        """)

    logger.info("Created deployment agent: %s (id=%s)", agent_name, agent_id)

    return {
        "id": agent_id,
        "name": agent_name,
        "display_name": display_name,
        "environment": env_name,
        "model": body.model,
        "tools": deploy_tools,
        "workspace_mounts_added": len(seen_host_paths),
        "is_active": True,
        "created_at": created_at,
    }


@router.delete("/agents/{env_name}")
async def delete_deployment_agent(env_name: str):
    """Delete a deployment agent for an environment."""
    stdb = get_stdb()
    agent_name = f"deploy-{env_name}"

    existing = await stdb.query(f"SELECT id FROM agents WHERE name = '{agent_name}'")
    if not existing:
        raise HTTPException(status_code=404, detail=f"Deployment agent '{agent_name}' not found")

    agent_id = existing[0]["id"]

    try:
        await stdb.query(f"DELETE FROM agent_channels WHERE agent_id = '{agent_id}'")
    except Exception:
        pass
    await stdb.query(f"DELETE FROM agent_workspace_mounts WHERE agent_id = '{agent_id}'")
    await stdb.query(f"DELETE FROM agents WHERE id = '{agent_id}'")

    return {"success": True, "message": f"Deployment agent '{agent_name}' deleted"}
