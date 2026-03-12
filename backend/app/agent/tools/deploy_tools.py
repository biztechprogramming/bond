"""
Deployment agent tools — Design Doc 039.

Provides deployment-specific tools that can only be used by deployment agents:
  - deploy_action:  Send deployment actions to the broker /deploy endpoint
  - file_bug_ticket: Create GitHub issues for deployment failures
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("bond.agent.deploy_tools")

# ── Tool definitions ──────────────────────────────────────────────────────────

DEPLOY_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "deploy_action",
            "description": (
                "Execute a deployment action via the permission broker. "
                "The broker validates promotion status, loads secrets, and executes scripts on the host. "
                "You never see script content, file paths, or secrets — only stdout/stderr results. "
                "Environment is automatically derived from your agent identity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "info",       # Get script metadata + promotion status
                            "validate",   # Syntax check + hash verification + window check
                            "dry-run",    # Run script with --dry-run flag
                            "pre-hook",   # Run environment's pre-deployment hook
                            "deploy",     # Execute the deployment script
                            "post-hook",  # Run environment's post-deployment hook
                            "health-check", # Run environment health check
                            "rollback",   # Execute rollback script
                            "receipt",    # Fetch deployment receipt from an environment
                            "status",     # Check promotion status
                            "lock-status", # Check if environment is locked
                        ],
                        "description": "The deployment action to execute.",
                    },
                    "script_id": {
                        "type": "string",
                        "description": "Script ID (e.g., '001-migrate-user-table'). Required for most actions.",
                    },
                    "version": {
                        "type": "string",
                        "description": "Script version (e.g., 'v1'). Defaults to 'v1'.",
                        "default": "v1",
                    },
                    "environment": {
                        "type": "string",
                        "description": "For 'receipt' action only — which environment's receipt to fetch (defaults to previous environment).",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Override script timeout in seconds (capped at environment maximum).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_bug_ticket",
            "description": (
                "Create a detailed GitHub issue for a deployment failure or environment problem. "
                "Include enough context for a developer to reproduce and fix the issue. "
                "The issue will be created in the configured repository with appropriate labels. "
                "Use this when a deployment fails or a health check detects a problem."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Clear, specific issue title describing the problem.",
                    },
                    "environment": {
                        "type": "string",
                        "description": "Which environment this affects (e.g., 'qa', 'staging').",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                        "description": "Impact severity level.",
                    },
                    "script_id": {
                        "type": "string",
                        "description": "Deployment script that failed (if applicable).",
                    },
                    "error_output": {
                        "type": "string",
                        "description": "Relevant stdout/stderr from the failure.",
                    },
                    "code_context": {
                        "type": "string",
                        "description": "Relevant code snippets from the workspace (read-only access).",
                    },
                    "steps_to_reproduce": {
                        "type": "string",
                        "description": "How to reproduce the issue.",
                    },
                    "expected_behavior": {
                        "type": "string",
                        "description": "What should have happened.",
                    },
                    "actual_behavior": {
                        "type": "string",
                        "description": "What actually happened.",
                    },
                    "suggested_fix": {
                        "type": "string",
                        "description": "Agent's analysis and suggested fix (from reading the code).",
                    },
                    "receipt_id": {
                        "type": "string",
                        "description": "Deployment receipt ID for full context.",
                    },
                },
                "required": ["title", "environment", "severity", "actual_behavior"],
            },
        },
    },
]

DEPLOY_TOOL_SUMMARIES: dict[str, str] = {
    "deploy_action": "Execute deployment actions (deploy, rollback, validate, health-check, etc.) via the permission broker",
    "file_bug_ticket": "Create a GitHub issue for deployment failures with full diagnostic context",
}

# ── Tool implementations ──────────────────────────────────────────────────────


def get_broker_url() -> str:
    return os.environ.get("BOND_BROKER_URL", "http://host.docker.internal:18789")


def get_broker_token() -> str | None:
    return os.environ.get("BOND_BROKER_TOKEN")


async def execute_deploy_action(
    action: str,
    script_id: str | None = None,
    version: str | None = None,
    environment: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Execute a deployment action via the broker /deploy endpoint."""
    broker_url = get_broker_url()
    token = get_broker_token()

    if not token:
        return {"error": "BOND_BROKER_TOKEN not set — deployment actions are not available"}

    payload: dict[str, Any] = {"action": action}
    if script_id:
        payload["script_id"] = script_id
    if version:
        payload["version"] = version
    if environment:
        payload["environment"] = environment
    if timeout:
        payload["timeout"] = timeout

    try:
        async with httpx.AsyncClient(timeout=max(timeout or 60, 300) + 10) as client:
            response = await client.post(
                f"{broker_url}/api/v1/broker/deploy",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error("Broker /deploy HTTP error: %s", e)
        try:
            return {"error": e.response.json()}
        except Exception:
            return {"error": str(e)}
    except Exception as e:
        logger.error("Broker /deploy error: %s", e)
        return {"error": str(e)}


async def execute_file_bug_ticket(
    title: str,
    environment: str,
    severity: str,
    actual_behavior: str,
    script_id: str | None = None,
    error_output: str | None = None,
    code_context: str | None = None,
    steps_to_reproduce: str | None = None,
    expected_behavior: str | None = None,
    suggested_fix: str | None = None,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    """Create a GitHub issue for a deployment failure via gh CLI."""
    broker_url = get_broker_url()
    token = get_broker_token()

    if not token:
        return {"error": "BOND_BROKER_TOKEN not set"}

    # Build issue body
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    body_parts = [f"## 🚨 Deployment Issue: {title}", ""]
    body_parts.append(f"**Environment:** {environment}")
    body_parts.append(f"**Severity:** {severity}")
    if script_id:
        body_parts.append(f"**Script:** {script_id}")
    if receipt_id:
        body_parts.append(f"**Deployment Receipt:** {receipt_id}")
    body_parts.append(f"**Detected:** {timestamp}")
    body_parts.append("")

    if error_output:
        body_parts.append("### Error Output")
        body_parts.append("```")
        body_parts.append(error_output[:4000])  # cap length
        body_parts.append("```")
        body_parts.append("")

    if steps_to_reproduce:
        body_parts.append("### Steps to Reproduce")
        body_parts.append(steps_to_reproduce)
        body_parts.append("")

    if expected_behavior:
        body_parts.append("### Expected Behavior")
        body_parts.append(expected_behavior)
        body_parts.append("")

    body_parts.append("### Actual Behavior")
    body_parts.append(actual_behavior)
    body_parts.append("")

    if code_context:
        body_parts.append("### Relevant Code")
        body_parts.append("```")
        body_parts.append(code_context[:4000])
        body_parts.append("```")
        body_parts.append("")

    if suggested_fix:
        body_parts.append("### Suggested Fix")
        body_parts.append(suggested_fix)
        body_parts.append("")

    agent_env = os.environ.get("BOND_DEPLOY_ENV", environment)
    body_parts.append(f"---\n*Filed automatically by deploy-{agent_env} agent*")

    body = "\n".join(body_parts)
    labels = f"deployment,env:{environment},severity:{severity},automated"

    # Escape the body for shell
    body_escaped = body.replace("'", "'\\''")
    title_escaped = title.replace("'", "'\\''")

    command = f"gh issue create --title '{title_escaped}' --body '{body_escaped}' --label '{labels}'"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{broker_url}/api/v1/broker/exec",
                json={"command": command},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            result = response.json()

            if result.get("status") == "denied":
                return {
                    "error": f"Command denied by broker policy: {result.get('reason', 'unknown')}",
                    "details": result,
                }

            return {
                "success": result.get("exit_code") == 0,
                "output": result.get("stdout", ""),
                "error_output": result.get("stderr", "") or None,
                "exit_code": result.get("exit_code"),
            }
    except Exception as e:
        logger.error("file_bug_ticket error: %s", e)
        return {"error": str(e)}


async def handle_deploy_tool(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """Dispatch deployment tool calls."""
    if tool_name == "deploy_action":
        return await execute_deploy_action(
            action=tool_input["action"],
            script_id=tool_input.get("script_id"),
            version=tool_input.get("version"),
            environment=tool_input.get("environment"),
            timeout=tool_input.get("timeout"),
        )
    elif tool_name == "file_bug_ticket":
        return await execute_file_bug_ticket(
            title=tool_input["title"],
            environment=tool_input["environment"],
            severity=tool_input["severity"],
            actual_behavior=tool_input["actual_behavior"],
            script_id=tool_input.get("script_id"),
            error_output=tool_input.get("error_output"),
            code_context=tool_input.get("code_context"),
            steps_to_reproduce=tool_input.get("steps_to_reproduce"),
            expected_behavior=tool_input.get("expected_behavior"),
            suggested_fix=tool_input.get("suggested_fix"),
            receipt_id=tool_input.get("receipt_id"),
        )
    else:
        return {"error": f"Unknown deployment tool: {tool_name}"}
