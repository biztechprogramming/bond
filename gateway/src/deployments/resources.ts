/**
 * Deployment Resources — SpacetimeDB query helpers.
 *
 * CRUD operations for deployment target resources (servers, clusters, etc.).
 */

import { callReducer, sqlQuery } from "../spacetimedb/client.js";
import type { GatewayConfig } from "../config/index.js";
import { ulid } from "ulid";

// ── Types ───────────────────────────────────────────────────────────────────

export interface DeploymentResource {
  id: string;
  name: string;
  display_name: string;
  resource_type: string;
  environment: string;
  connection_json: string;
  capabilities_json: string;
  state_json: string;
  tags_json: string;
  recommendations_json: string;
  is_active: boolean;
  created_at: number;
  updated_at: number;
  last_probed_at: number;
}

// ── Resource queries ────────────────────────────────────────────────────────

export async function getResources(
  cfg: GatewayConfig,
  environment?: string,
): Promise<DeploymentResource[]> {
  const where = environment
    ? ` WHERE is_active = true AND environment = '${esc(environment)}'`
    : " WHERE is_active = true";
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM resources${where}`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeResource);
}

export async function getResource(
  cfg: GatewayConfig,
  id: string,
): Promise<DeploymentResource | null> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM resources WHERE id = '${esc(id)}'`,
    cfg.spacetimedbToken,
  );
  return rows.length ? normalizeResource(rows[0]) : null;
}

export async function createResource(
  cfg: GatewayConfig,
  resource: Omit<DeploymentResource, "id" | "created_at" | "updated_at" | "last_probed_at" | "is_active">,
): Promise<string> {
  const id = ulid();
  const now = Date.now();
  // SpacetimeDB HTTP /call/ API expects positional args matching reducer field order
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_deployment_resource",
    [
      id,
      resource.name,
      resource.display_name,
      resource.resource_type,
      resource.environment,
      resource.connection_json || "{}",
      resource.capabilities_json || "{}",
      resource.state_json || "{}",
      resource.tags_json || "[]",
      resource.recommendations_json || "[]",
      true,   // is_active
      now,    // created_at
      now,    // updated_at
      0,      // last_probed_at
    ],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function updateResource(
  cfg: GatewayConfig,
  id: string,
  updates: Partial<Omit<DeploymentResource, "id" | "created_at">>,
): Promise<void> {
  // SpacetimeDB HTTP /call/ API expects positional args; pass null for optional fields not being updated
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_deployment_resource",
    [
      id,
      updates.display_name ?? null,     // optional
      updates.resource_type ?? null,     // optional
      updates.environment ?? null,       // optional
      updates.connection_json ?? null,   // optional
      updates.capabilities_json ?? null, // optional
      updates.state_json ?? null,        // optional
      updates.tags_json ?? null,         // optional
      updates.recommendations_json ?? null, // optional
      updates.is_active ?? null,         // optional
      updates.updated_at ?? Date.now(),  // required
      updates.last_probed_at ?? null,    // optional
    ],
    cfg.spacetimedbToken,
  );
}

export async function deleteResource(
  cfg: GatewayConfig,
  id: string,
): Promise<void> {
  const now = Date.now();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_deployment_resource",
    [
      id,
      null, null, null, null, null, null, null, null, // optional fields unchanged
      false, // is_active
      now,   // updated_at
      null,  // last_probed_at
    ],
    cfg.spacetimedbToken,
  );
}

export async function updateResourceProbe(
  cfg: GatewayConfig,
  id: string,
  capabilities: Record<string, any>,
  state: Record<string, any>,
  recommendations: any[],
): Promise<void> {
  const now = Date.now();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_deployment_resource",
    [
      id,
      null,                              // display_name
      null,                              // resource_type
      null,                              // environment
      null,                              // connection_json
      JSON.stringify(capabilities),      // capabilities_json
      JSON.stringify(state),             // state_json
      null,                              // tags_json
      JSON.stringify(recommendations),   // recommendations_json
      null,                              // is_active
      now,                               // updated_at
      now,                               // last_probed_at
    ],
    cfg.spacetimedbToken,
  );
}

// ── SQL escaping ──────────────────────────────────────────────────────────

function esc(s: string): string {
  return s.replace(/'/g, "''");
}

// ── Normalizers ───────────────────────────────────────────────────────────

function normalizeResource(r: any): DeploymentResource {
  return {
    id: r.id,
    name: r.name,
    display_name: r.display_name,
    resource_type: r.resource_type,
    environment: r.environment,
    connection_json: r.connection_json || "{}",
    capabilities_json: r.capabilities_json || "{}",
    state_json: r.state_json || "{}",
    tags_json: r.tags_json || "[]",
    recommendations_json: r.recommendations_json || "[]",
    is_active: Boolean(r.is_active),
    created_at: Number(r.created_at),
    updated_at: Number(r.updated_at),
    last_probed_at: Number(r.last_probed_at ?? 0),
  };
}
