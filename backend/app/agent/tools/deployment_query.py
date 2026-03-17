"""
Deployment query tool — read-only access to deployment data.

Gives deployment agents the ability to query the Gateway's existing
deployment REST APIs for components, environments, promotions, scripts,
resources, health status, queue state, and receipts.

This is a read-only tool. Write operations (promote, deploy, rollback)
go through deploy_action via the broker.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("bond.agent.deployment_query")


def _gateway_url() -> str:
    """Resolve the Gateway URL (same logic as persistence_client)."""
    explicit = os.environ.get("BOND_GATEWAY_URL")
    if explicit:
        return explicit.rstrip("/")

    import platform
    system = platform.system().lower()
    if system in ("darwin", "windows") or "microsoft" in platform.release().lower():
        return "http://host.docker.internal:18789"
    else:
        return "http://172.17.0.1:18789"


DEPLOYMENT_QUERY_DEFINITION = {
    "type": "function",
    "function": {
        "name": "deployment_query",
        "description": (
            "Query deployment data from the Gateway. Returns information about "
            "components, environments, promotions, scripts, resources, queues, "
            "health status, and receipts. This is read-only — use deploy_action "
            "for actual deployment operations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "enum": [
                        "list_components",
                        "get_component",
                        "component_tree",
                        "component_status",
                        "list_environments",
                        "get_environment",
                        "environment_history",
                        "list_promotions",
                        "get_promotion",
                        "list_scripts",
                        "get_script",
                        "list_resources",
                        "get_resource",
                        "queue",
                        "health",
                        "monitoring",
                        "list_receipts",
                        "get_receipt",
                        "list_triggers",
                    ],
                    "description": "The type of deployment data to query.",
                },
                "id": {
                    "type": "string",
                    "description": (
                        "Entity ID for single-record lookups. "
                        "For get_component, get_script, get_resource, get_receipt: the entity ID. "
                        "For component_status: the component ID (also requires environment). "
                        "For get_promotion: the script_id (also requires version + environment). "
                        "For get_environment, environment_history, queue, health, monitoring: the environment name."
                    ),
                },
                "environment": {
                    "type": "string",
                    "description": (
                        "Environment name filter. "
                        "Used by: list_components (optional filter), component_status (required), "
                        "get_promotion (required), queue, health, monitoring."
                    ),
                },
                "version": {
                    "type": "string",
                    "description": "Script version for get_promotion queries (e.g., 'v1').",
                },
            },
            "required": ["query"],
        },
    },
}

DEPLOYMENT_QUERY_SUMMARY = (
    "Query deployment data (components, environments, promotions, scripts, "
    "resources, queues, health) from the Gateway — read-only"
)


async def handle_deployment_query(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Handle deployment_query tool calls by proxying to Gateway REST APIs."""
    query = arguments.get("query")
    entity_id = arguments.get("id")
    environment = arguments.get("environment")
    version = arguments.get("version")

    if not query:
        return {"error": "query parameter is required"}

    base = f"{_gateway_url()}/api/v1/deployments"
    timeout = httpx.Timeout(10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            result = await _dispatch_query(
                client, base, query, entity_id, environment, version,
            )
            return result
    except httpx.ConnectError:
        return {"error": "Cannot reach Gateway — deployment data unavailable"}
    except httpx.HTTPStatusError as e:
        return {"error": f"Gateway returned {e.response.status_code}: {e.response.text[:500]}"}
    except Exception as e:
        logger.error("deployment_query error: %s", e, exc_info=True)
        return {"error": str(e)}


async def _dispatch_query(
    client: httpx.AsyncClient,
    base: str,
    query: str,
    entity_id: str | None,
    environment: str | None,
    version: str | None,
) -> dict[str, Any]:
    """Route a query type to the appropriate Gateway endpoint."""

    match query:
        # ── Components ──────────────────────────────────────────────────
        case "list_components":
            params = {}
            if environment:
                params["environment"] = environment
            resp = await client.get(f"{base}/components", params=params)
            resp.raise_for_status()
            return {"components": resp.json()}

        case "get_component":
            if not entity_id:
                return {"error": "id is required for get_component"}
            resp = await client.get(f"{base}/components/{entity_id}")
            resp.raise_for_status()
            return {"component": resp.json()}

        case "component_tree":
            params = {"tree": "true"}
            if environment:
                params["environment"] = environment
            resp = await client.get(f"{base}/components", params=params)
            resp.raise_for_status()
            return {"tree": resp.json()}

        case "component_status":
            if not entity_id:
                return {"error": "id (component_id) is required for component_status"}
            if not environment:
                return {"error": "environment is required for component_status"}
            resp = await client.get(
                f"{base}/components/{entity_id}/status",
                params={"environment": environment},
            )
            resp.raise_for_status()
            return {"status": resp.json()}

        # ── Environments ────────────────────────────────────────────────
        case "list_environments":
            resp = await client.get(f"{base}/environments")
            resp.raise_for_status()
            return {"environments": resp.json()}

        case "get_environment":
            if not entity_id:
                return {"error": "id (environment name) is required for get_environment"}
            resp = await client.get(f"{base}/environments/{entity_id}")
            resp.raise_for_status()
            return {"environment": resp.json()}

        case "environment_history":
            if not entity_id:
                return {"error": "id (environment name) is required for environment_history"}
            resp = await client.get(f"{base}/environments/{entity_id}/history")
            resp.raise_for_status()
            return {"history": resp.json()}

        # ── Promotions ──────────────────────────────────────────────────
        case "list_promotions":
            resp = await client.get(f"{base}/promotions")
            resp.raise_for_status()
            return {"promotions": resp.json()}

        case "get_promotion":
            if not entity_id:
                return {"error": "id (script_id) is required for get_promotion"}
            if not version:
                return {"error": "version is required for get_promotion"}
            if not environment:
                return {"error": "environment is required for get_promotion"}
            resp = await client.get(
                f"{base}/promotions/{entity_id}/{version}/{environment}",
            )
            resp.raise_for_status()
            return {"promotion": resp.json()}

        # ── Scripts ─────────────────────────────────────────────────────
        case "list_scripts":
            resp = await client.get(f"{base}/scripts")
            resp.raise_for_status()
            return {"scripts": resp.json()}

        case "get_script":
            if not entity_id:
                return {"error": "id (script_id) is required for get_script"}
            resp = await client.get(f"{base}/scripts/{entity_id}")
            resp.raise_for_status()
            return {"script": resp.json()}

        # ── Resources ───────────────────────────────────────────────────
        case "list_resources":
            params = {}
            if environment:
                params["environment"] = environment
            resp = await client.get(f"{base}/resources", params=params)
            resp.raise_for_status()
            return {"resources": resp.json()}

        case "get_resource":
            if not entity_id:
                return {"error": "id (resource_id) is required for get_resource"}
            resp = await client.get(f"{base}/resources/{entity_id}")
            resp.raise_for_status()
            return {"resource": resp.json()}

        # ── Queue / Health / Monitoring ─────────────────────────────────
        case "queue":
            env = environment or entity_id
            if not env:
                return {"error": "environment (or id) is required for queue"}
            resp = await client.get(f"{base}/queue/{env}")
            resp.raise_for_status()
            return {"queue": resp.json()}

        case "health":
            env = environment or entity_id
            if not env:
                return {"error": "environment (or id) is required for health"}
            resp = await client.get(f"{base}/health/{env}")
            resp.raise_for_status()
            return {"health": resp.json()}

        case "monitoring":
            env = environment or entity_id
            if not env:
                return {"error": "environment (or id) is required for monitoring"}
            resp = await client.get(f"{base}/monitoring/{env}")
            resp.raise_for_status()
            return {"monitoring": resp.json()}

        # ── Receipts ────────────────────────────────────────────────────
        case "list_receipts":
            params = {}
            if environment:
                params["environment"] = environment
            resp = await client.get(f"{base}/receipts", params=params)
            resp.raise_for_status()
            return {"receipts": resp.json()}

        case "get_receipt":
            if not entity_id:
                return {"error": "id (receipt_id) is required for get_receipt"}
            resp = await client.get(f"{base}/receipts/{entity_id}")
            resp.raise_for_status()
            return {"receipt": resp.json()}

        # ── Triggers ────────────────────────────────────────────────────
        case "list_triggers":
            resp = await client.get(f"{base}/triggers")
            resp.raise_for_status()
            return {"triggers": resp.json()}

        case _:
            return {"error": f"Unknown query type: {query}"}
