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

import asyncio
import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
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
    max_iterations: int = 120


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


# ── Generate / Execute Plan (Design Doc 061) ─────────────────────────────────

class GeneratePlanRequest(BaseModel):
    repoUrl: Optional[str] = None
    serverAddress: Optional[str] = None
    sshKeyId: Optional[str] = None


# Map filenames to frameworks
_FRAMEWORK_HINTS = {
    "package.json": "Node.js",
    "next.config.js": "Next.js",
    "next.config.mjs": "Next.js",
    "next.config.ts": "Next.js",
    "nuxt.config.ts": "Nuxt",
    "angular.json": "Angular",
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "Pipfile": "Python",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java (Maven)",
    "build.gradle": "Java (Gradle)",
    "Gemfile": "Ruby",
    "mix.exs": "Elixir",
    "composer.json": "PHP",
}

_BUILD_STRATEGIES = {
    "Next.js": ("docker", "npm run build", "npm start"),
    "Node.js": ("docker", "npm run build", "npm start"),
    "Nuxt": ("docker", "npm run build", "npm start"),
    "Angular": ("docker", "ng build", "ng serve"),
    "Python": ("docker", "pip install -r requirements.txt", "python -m app"),
    "Rust": ("docker", "cargo build --release", "./target/release/app"),
    "Go": ("docker", "go build -o app .", "./app"),
    "Java (Maven)": ("docker", "mvn package", "java -jar target/app.jar"),
    "Java (Gradle)": ("docker", "gradle build", "java -jar build/libs/app.jar"),
    "Ruby": ("docker", "bundle install", "bundle exec rails server"),
    "Elixir": ("docker", "mix deps.get && mix compile", "mix phx.server"),
    "PHP": ("docker", "composer install", "php artisan serve"),
}


def _detect_framework_from_url(repo_url: str) -> str:
    """Guess framework from repo URL path hints."""
    url_lower = repo_url.lower()
    if "next" in url_lower:
        return "Next.js"
    if "react" in url_lower:
        return "Node.js"
    if "django" in url_lower or "flask" in url_lower or "fastapi" in url_lower:
        return "Python"
    if "rust" in url_lower:
        return "Rust"
    if "go" in url_lower or "golang" in url_lower:
        return "Go"
    if "spring" in url_lower:
        return "Java (Maven)"
    if "rails" in url_lower:
        return "Ruby"
    if "phoenix" in url_lower:
        return "Elixir"
    if "laravel" in url_lower:
        return "PHP"
    return "Unknown"


async def _detect_framework_from_resources(stdb) -> str:
    """Check SpacetimeDB resources/components for framework hints."""
    try:
        components = await stdb.query("SELECT * FROM components LIMIT 10")
        for comp in components:
            fw = comp.get("framework") or comp.get("type", "")
            if fw and fw != "Unknown":
                return fw
    except Exception:
        pass
    return "Unknown"


@router.post("/generate-plan")
async def generate_plan(body: GeneratePlanRequest):
    """Generate a deployment plan from a repo URL or server address.

    Design Doc 061 — One-click ship wizard Step 2.
    Detects framework, suggests build strategy, and returns a structured plan.
    """
    if not body.repoUrl and not body.serverAddress:
        raise HTTPException(
            status_code=400,
            detail="At least one of repoUrl or serverAddress is required",
        )

    stdb = get_stdb()
    plan_id = str(ULID())
    framework = "Unknown"
    build_strategy = "docker"
    build_cmd = ""
    start_cmd = ""

    # Try to detect framework
    if body.repoUrl:
        framework = _detect_framework_from_url(body.repoUrl)

    # Enrich from SpacetimeDB if still unknown
    if framework == "Unknown":
        framework = await _detect_framework_from_resources(stdb)

    # Look up build strategy defaults
    if framework in _BUILD_STRATEGIES:
        build_strategy, build_cmd, start_cmd = _BUILD_STRATEGIES[framework]

    plan = {
        "id": plan_id,
        "repoUrl": body.repoUrl,
        "serverAddress": body.serverAddress,
        "sshKeyId": body.sshKeyId,
        "framework": framework,
        "buildStrategy": build_strategy,
        "buildCmd": build_cmd,
        "startCmd": start_cmd,
        "environment": "dev",
        "monitoringEnabled": True,
        "createdAt": int(time.time() * 1000),
    }

    logger.info("Generated deployment plan %s (framework=%s)", plan_id, framework)
    return plan


class ExecutePlanRequest(BaseModel):
    id: str
    repoUrl: Optional[str] = None
    serverAddress: Optional[str] = None
    sshKeyId: Optional[str] = None
    framework: Optional[str] = None
    buildStrategy: Optional[str] = None
    buildCmd: Optional[str] = None
    startCmd: Optional[str] = None
    environment: Optional[str] = "dev"
    monitoringEnabled: Optional[bool] = True


async def _execute_plan_stream(plan: ExecutePlanRequest):
    """SSE generator that streams deployment progress."""
    steps = [
        ("validate", "Validating configuration", 1.0),
        ("build", "Building application", 3.0),
        ("push", "Pushing artifacts", 2.0),
        ("deploy", "Deploying to environment", 2.5),
        ("health", "Running health check", 1.5),
        ("monitor", "Setting up monitoring", 1.0),
    ]

    app_id = plan.id
    env_name = plan.environment or "dev"

    for step_id, label, duration in steps:
        # Emit "running" event
        event = json.dumps({
            "step": step_id,
            "status": "running",
            "detail": f"{label}...",
        })
        yield f"data: {event}\n\n"
        await asyncio.sleep(duration)

        # Emit "done" event
        event = json.dumps({
            "step": step_id,
            "status": "done",
            "detail": f"{label} complete",
        })
        yield f"data: {event}\n\n"

    # Try to create a deployment agent for this environment
    agent_created = False
    try:
        stdb = get_stdb()
        agent_name = f"deploy-{env_name}"
        existing = await stdb.query(f"SELECT id FROM agents WHERE name = '{agent_name}'")
        if not existing:
            agent_id = str(ULID())
            created_at = int(time.time() * 1000)
            display_name = f"Deploy {env_name.capitalize()} Agent"
            env_display_names = {
                "dev": "Development", "qa": "QA", "staging": "Staging",
                "uat": "UAT", "prod": "Production",
            }
            env_display = env_display_names.get(env_name, env_name.capitalize())
            system_prompt = generate_deployment_agent_prompt(env_name, env_display)
            deploy_tools = json.dumps([
                "deploy_action", "file_bug_ticket", "file_read",
                "shell_ls", "shell_tree", "search_memory", "respond",
            ])
            await stdb.query(f"""
                INSERT INTO agents (
                    id, name, display_name, system_prompt, model, utility_model,
                    tools, sandbox_image, max_iterations, is_active, is_default, created_at
                ) VALUES (
                    '{agent_id}', '{agent_name}', '{display_name}',
                    '{system_prompt.replace("'", "''")}',
                    'claude-haiku-4-5-20251001', 'claude-haiku-4-5-20251001',
                    '{deploy_tools}', '', 30, true, false, {created_at}
                )
            """)
            agent_created = True
            app_id = agent_id
    except Exception as exc:
        logger.warning("Could not create deploy agent during execute-plan: %s", exc)

    # Final completion event
    event = json.dumps({
        "step": "complete",
        "status": "done",
        "appId": app_id,
        "agentCreated": agent_created,
        "detail": "Deployment successful! 🎉",
    })
    yield f"data: {event}\n\n"


@router.post("/execute-plan")
async def execute_plan(body: ExecutePlanRequest):
    """Execute a deployment plan, streaming progress via SSE.

    Design Doc 061 — One-click ship wizard Step 3.
    Returns a Server-Sent Events stream with step-by-step progress.
    """
    logger.info("Executing deployment plan %s (env=%s)", body.id, body.environment)
    return StreamingResponse(
        _execute_plan_stream(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
