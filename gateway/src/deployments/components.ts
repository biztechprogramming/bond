/**
 * Components — SpacetimeDB query helpers for the component entity model.
 *
 * Design Doc 045a — CRUD operations for components, component_resources,
 * component_scripts, and component_secrets tables.
 */

import { callReducer, sqlQuery } from "../spacetimedb/client.js";
import type { GatewayConfig } from "../config/index.js";
import { ulid } from "ulid";

// ── Types ───────────────────────────────────────────────────────────────────

export interface Component {
  id: string;
  name: string;
  display_name: string;
  component_type: string;
  parent_id?: string;
  runtime?: string;
  framework?: string;
  repository_url?: string;
  icon?: string;
  description?: string;
  is_active: boolean;
  created_at: number;
  updated_at: number;
  discovered_from?: string;
}

export interface ComponentResource {
  id: string;
  component_id: string;
  resource_id: string;
  environment: string;
  port?: number;
  process_name?: string;
  health_check?: string;
  created_at: number;
}

export interface ComponentScript {
  id: string;
  component_id: string;
  script_id: string;
  role: string;
  created_at: number;
}

export interface ComponentSecret {
  id: string;
  component_id: string;
  secret_key: string;
  environment: string;
  is_sensitive: boolean;
  created_at: number;
}

export interface ComponentTreeNode extends Component {
  children: ComponentTreeNode[];
}

// ── SQL escaping ──────────────────────────────────────────────────────────────

function esc(s: string): string {
  return s.replace(/'/g, "''");
}

// ── Normalizers ───────────────────────────────────────────────────────────────

function normalizeComponent(r: any): Component {
  return {
    id: r.id,
    name: r.name,
    display_name: r.display_name,
    component_type: r.component_type,
    parent_id: r.parent_id || undefined,
    runtime: r.runtime || undefined,
    framework: r.framework || undefined,
    repository_url: r.repository_url || undefined,
    icon: r.icon || undefined,
    description: r.description || undefined,
    is_active: Boolean(r.is_active),
    created_at: Number(r.created_at),
    updated_at: Number(r.updated_at),
    discovered_from: r.discovered_from || undefined,
  };
}

function normalizeComponentResource(r: any): ComponentResource {
  return {
    id: r.id,
    component_id: r.component_id,
    resource_id: r.resource_id,
    environment: r.environment,
    port: r.port != null ? Number(r.port) : undefined,
    process_name: r.process_name || undefined,
    health_check: r.health_check || undefined,
    created_at: Number(r.created_at),
  };
}

function normalizeComponentScript(r: any): ComponentScript {
  return {
    id: r.id,
    component_id: r.component_id,
    script_id: r.script_id,
    role: r.role || "deploy",
    created_at: Number(r.created_at),
  };
}

function normalizeComponentSecret(r: any): ComponentSecret {
  return {
    id: r.id,
    component_id: r.component_id,
    secret_key: r.secret_key,
    environment: r.environment,
    is_sensitive: Boolean(r.is_sensitive),
    created_at: Number(r.created_at),
  };
}

// ── Component queries ─────────────────────────────────────────────────────────

export async function getComponents(
  cfg: GatewayConfig,
  environment?: string,
): Promise<Component[]> {
  let sql: string;
  if (environment) {
    sql = `SELECT DISTINCT c.* FROM components c JOIN component_resources cr ON c.id = cr.component_id WHERE cr.environment = '${esc(environment)}' AND c.is_active = true`;
  } else {
    sql = "SELECT * FROM components WHERE is_active = true";
  }
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    sql,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeComponent);
}

export async function getComponent(
  cfg: GatewayConfig,
  id: string,
): Promise<Component | null> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM components WHERE id = '${esc(id)}'`,
    cfg.spacetimedbToken,
  );
  return rows.length ? normalizeComponent(rows[0]) : null;
}

export async function getComponentByName(
  cfg: GatewayConfig,
  name: string,
): Promise<Component | null> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM components WHERE name = '${esc(name)}'`,
    cfg.spacetimedbToken,
  );
  return rows.length ? normalizeComponent(rows[0]) : null;
}

export async function createComponent(
  cfg: GatewayConfig,
  data: Omit<Component, "id" | "created_at" | "updated_at" | "is_active">,
): Promise<string> {
  const id = ulid();
  const now = Date.now();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_component",
    [{
      id,
      name: data.name,
      display_name: data.display_name,
      component_type: data.component_type,
      parent_id: data.parent_id || "",
      runtime: data.runtime || "",
      framework: data.framework || "",
      repository_url: data.repository_url || "",
      icon: data.icon || "",
      description: data.description || "",
      is_active: true,
      created_at: now,
      updated_at: now,
      discovered_from: data.discovered_from || "",
    }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function updateComponent(
  cfg: GatewayConfig,
  id: string,
  updates: Partial<Omit<Component, "id" | "created_at">>,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_component",
    [{ id, ...updates, updated_at: Date.now() }],
    cfg.spacetimedbToken,
  );
}

export async function deactivateComponent(
  cfg: GatewayConfig,
  id: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_component",
    [{ id, is_active: false, updated_at: Date.now() }],
    cfg.spacetimedbToken,
  );
}

export async function getComponentTree(
  cfg: GatewayConfig,
  environment?: string,
): Promise<ComponentTreeNode[]> {
  const flat = await getComponents(cfg, environment);
  const map = new Map<string, ComponentTreeNode>();
  for (const c of flat) {
    map.set(c.id, { ...c, children: [] });
  }
  const roots: ComponentTreeNode[] = [];
  for (const node of map.values()) {
    if (node.parent_id && map.has(node.parent_id)) {
      map.get(node.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

// ── Component resource links ──────────────────────────────────────────────────

export async function getComponentResources(
  cfg: GatewayConfig,
  componentId: string,
): Promise<ComponentResource[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM component_resources WHERE component_id = '${esc(componentId)}'`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeComponentResource);
}

export async function addComponentResource(
  cfg: GatewayConfig,
  data: Omit<ComponentResource, "id" | "created_at">,
): Promise<string> {
  const id = ulid();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_component_resource",
    [{
      id,
      component_id: data.component_id,
      resource_id: data.resource_id,
      environment: data.environment,
      port: data.port ?? 0,
      process_name: data.process_name || "",
      health_check: data.health_check || "",
      created_at: Date.now(),
    }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function removeComponentResource(
  cfg: GatewayConfig,
  linkId: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "delete_component_resource",
    [{ id: linkId }],
    cfg.spacetimedbToken,
  );
}

// ── Component script links ────────────────────────────────────────────────────

export async function getComponentScripts(
  cfg: GatewayConfig,
  componentId: string,
): Promise<ComponentScript[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM component_scripts WHERE component_id = '${esc(componentId)}'`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeComponentScript);
}

export async function addComponentScript(
  cfg: GatewayConfig,
  data: Omit<ComponentScript, "id" | "created_at">,
): Promise<string> {
  const id = ulid();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_component_script",
    [{
      id,
      component_id: data.component_id,
      script_id: data.script_id,
      role: data.role || "deploy",
      created_at: Date.now(),
    }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function removeComponentScript(
  cfg: GatewayConfig,
  linkId: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "delete_component_script",
    [{ id: linkId }],
    cfg.spacetimedbToken,
  );
}

// ── Component secret links ────────────────────────────────────────────────────

export async function getComponentSecrets(
  cfg: GatewayConfig,
  componentId: string,
  environment?: string,
): Promise<ComponentSecret[]> {
  let sql = `SELECT * FROM component_secrets WHERE component_id = '${esc(componentId)}'`;
  if (environment) {
    sql += ` AND environment = '${esc(environment)}'`;
  }
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    sql,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeComponentSecret);
}

export async function addComponentSecret(
  cfg: GatewayConfig,
  data: Omit<ComponentSecret, "id" | "created_at">,
): Promise<string> {
  const id = ulid();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_component_secret",
    [{
      id,
      component_id: data.component_id,
      secret_key: data.secret_key,
      environment: data.environment,
      is_sensitive: data.is_sensitive !== false,
      created_at: Date.now(),
    }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function removeComponentSecret(
  cfg: GatewayConfig,
  linkId: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "delete_component_secret",
    [{ id: linkId }],
    cfg.spacetimedbToken,
  );
}

// ── Aggregated status ─────────────────────────────────────────────────────────

export async function getComponentStatus(
  cfg: GatewayConfig,
  componentId: string,
  environment: string,
): Promise<{ component_id: string; environment: string; resource_count: number; alert_count: number; status: string }> {
  const resources = await getComponentResources(cfg, componentId);
  const envResources = resources.filter(r => r.environment === environment);

  const alertRows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM alerts WHERE environment = '${esc(environment)}' AND component_id = '${esc(componentId)}' AND resolved_at IS NULL`,
    cfg.spacetimedbToken,
  );

  const alertCount = alertRows.length;
  let status = "healthy";
  if (envResources.length === 0) status = "unknown";
  else if (alertCount > 0) status = "degraded";

  return {
    component_id: componentId,
    environment,
    resource_count: envResources.length,
    alert_count: alertCount,
    status,
  };
}
