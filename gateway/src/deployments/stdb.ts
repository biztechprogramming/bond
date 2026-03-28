/**
 * Deployment — SpacetimeDB query helpers.
 *
 * All queries go through the SpacetimeDB HTTP API (callReducer / sqlQuery).
 * Agents never get direct DB access — only the Gateway process calls these.
 */

import { callReducer, sqlQuery } from "../spacetimedb/client.js";
import type { GatewayConfig } from "../config/index.js";
import { ulid } from "ulid";

// ── Types ───────────────────────────────────────────────────────────────────

export interface DeploymentEnvironment {
  name: string;
  display_name: string;
  order: number;
  is_active: boolean;
  max_script_timeout: number;
  health_check_interval: number;
  window_days: string;   // JSON array e.g. '["mon","tue"]'
  window_start: string;  // "06:00" or ""
  window_end: string;    // "22:00" or ""
  window_timezone: string;
  required_approvals: number;
  created_at: number;
  updated_at: number;
}

export interface DeploymentEnvironmentApprover {
  id: string;
  environment_name: string;
  user_id: string;
  added_at: number;
  added_by: string;
}

export interface DeploymentPromotion {
  id: string;
  script_id: string;
  script_version: string;
  script_sha256: string;
  environment_name: string;
  status: string;
  initiated_by: string;
  initiated_at: number;
  promoted_at: number;
  deployed_at: number;
  receipt_id: string;
}

export interface DeploymentApproval {
  id: string;
  promotion_id: string;
  script_id: string;
  script_version: string;
  environment_name: string;
  user_id: string;
  approved_at: number;
}

// ── Environment queries ──────────────────────────────────────────────────────

export async function getEnvironments(cfg: GatewayConfig): Promise<DeploymentEnvironment[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "SELECT * FROM environments",
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeEnv);
}

export async function getEnvironment(cfg: GatewayConfig, name: string): Promise<DeploymentEnvironment | null> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM environments WHERE name = '${esc(name)}'`,
    cfg.spacetimedbToken,
  );
  return rows.length ? normalizeEnv(rows[0]) : null;
}

export async function createEnvironment(
  cfg: GatewayConfig,
  env: Omit<DeploymentEnvironment, "created_at" | "updated_at" | "is_active">,
  changedBy: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_deployment_environment",
    [
      env.name,
      env.display_name,
      env.order,
      env.max_script_timeout,
      env.health_check_interval,
      env.window_days || "[]",
      env.window_start || "",
      env.window_end || "",
      env.window_timezone || "",
      env.required_approvals ?? 1,
      ulid(),
      changedBy,
    ],
    cfg.spacetimedbToken,
  );
}

export async function updateEnvironment(
  cfg: GatewayConfig,
  name: string,
  updates: Partial<Omit<DeploymentEnvironment, "name" | "created_at" | "updated_at">>,
  changedBy: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_deployment_environment",
    [{
      name,
      ...updates,
      history_id: ulid(),
      changed_by: changedBy,
    }],
    cfg.spacetimedbToken,
  );
}

// ── Approver queries ─────────────────────────────────────────────────────────

export async function getApprovers(
  cfg: GatewayConfig,
  environmentName: string,
): Promise<DeploymentEnvironmentApprover[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM environment_approvers WHERE environment_name = '${esc(environmentName)}'`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeApprover);
}

export async function addApprover(
  cfg: GatewayConfig,
  environmentName: string,
  userId: string,
  addedBy: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "add_deployment_approver",
    [{ id: ulid(), environment_name: environmentName, user_id: userId, added_by: addedBy }],
    cfg.spacetimedbToken,
  );
}

export async function removeApprover(
  cfg: GatewayConfig,
  environmentName: string,
  userId: string,
): Promise<void> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT id FROM environment_approvers WHERE environment_name = '${esc(environmentName)}' AND user_id = '${esc(userId)}'`,
    cfg.spacetimedbToken,
  );
  for (const row of rows) {
    await callReducer(
      cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
      "remove_deployment_approver",
      [{ id: row.id }],
      cfg.spacetimedbToken,
    );
  }
}

// ── Promotion queries ─────────────────────────────────────────────────────────

export async function getPromotion(
  cfg: GatewayConfig,
  scriptId: string,
  scriptVersion: string,
  environmentName: string,
): Promise<DeploymentPromotion | null> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM promotions WHERE script_id = '${esc(scriptId)}' AND script_version = '${esc(scriptVersion)}' AND environment_name = '${esc(environmentName)}'`,
    cfg.spacetimedbToken,
  );
  return rows.length ? normalizePromotion(rows[0]) : null;
}

export async function getPromotionsForScript(
  cfg: GatewayConfig,
  scriptId: string,
  scriptVersion: string,
): Promise<DeploymentPromotion[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM promotions WHERE script_id = '${esc(scriptId)}' AND script_version = '${esc(scriptVersion)}'`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizePromotion);
}

export async function getAllPromotions(cfg: GatewayConfig): Promise<DeploymentPromotion[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "SELECT * FROM promotions",
    cfg.spacetimedbToken,
  );
  return rows.map(normalizePromotion);
}

export async function initiatePromotion(
  cfg: GatewayConfig,
  scriptId: string,
  scriptVersion: string,
  scriptSha256: string,
  environmentName: string,
  status: string,
  initiatedBy: string,
): Promise<string> {
  const id = ulid();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "initiate_promotion",
    [{
      id,
      script_id: scriptId,
      script_version: scriptVersion,
      script_sha256: scriptSha256,
      environment_name: environmentName,
      status,
      initiated_by: initiatedBy,
    }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function recordApproval(
  cfg: GatewayConfig,
  promotionId: string,
  scriptId: string,
  scriptVersion: string,
  environmentName: string,
  userId: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "record_approval",
    [{
      id: ulid(),
      promotion_id: promotionId,
      script_id: scriptId,
      script_version: scriptVersion,
      environment_name: environmentName,
      user_id: userId,
    }],
    cfg.spacetimedbToken,
  );
}

export async function updatePromotionStatus(
  cfg: GatewayConfig,
  promotionId: string,
  status: string,
  extra: { promoted_at?: number; deployed_at?: number; receipt_id?: string } = {},
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_promotion_status",
    [{ id: promotionId, status, ...extra }],
    cfg.spacetimedbToken,
  );
}

export async function getApprovalsForPromotion(
  cfg: GatewayConfig,
  promotionId: string,
): Promise<DeploymentApproval[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM approvals WHERE promotion_id = '${esc(promotionId)}'`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeApprovalRow);
}

// ── Default environment seeding ───────────────────────────────────────────────

const DEFAULT_ENVIRONMENTS = [
  { name: "dev",     display_name: "Development", order: 1, max_script_timeout: 600,  health_check_interval: 300,  window_days: "[]",                                   window_start: "", window_end: "", window_timezone: "" },
  { name: "qa",      display_name: "QA",          order: 2, max_script_timeout: 900,  health_check_interval: 300,  window_days: "[]",                                   window_start: "", window_end: "", window_timezone: "" },
  { name: "staging", display_name: "Staging",     order: 3, max_script_timeout: 1200, health_check_interval: 600,  window_days: '["mon","tue","wed","thu","fri"]',       window_start: "06:00", window_end: "22:00", window_timezone: "America/New_York" },
  { name: "uat",     display_name: "UAT",         order: 4, max_script_timeout: 1200, health_check_interval: 600,  window_days: '["mon","tue","wed","thu","fri"]',       window_start: "06:00", window_end: "22:00", window_timezone: "America/New_York" },
  { name: "prod",    display_name: "Production",  order: 5, max_script_timeout: 1800, health_check_interval: 60,   window_days: '["tue","wed","thu"]',                  window_start: "09:00", window_end: "16:00", window_timezone: "America/New_York" },
];

export async function seedDefaultEnvironments(cfg: GatewayConfig): Promise<void> {
  try {
    const existing = await getEnvironments(cfg);
    if (existing.length > 0) return; // already seeded
    for (const env of DEFAULT_ENVIRONMENTS) {
      await createEnvironment(cfg, { ...env, required_approvals: 1 }, "system");
    }
    console.log("[deployments] Seeded default environments: dev, qa, staging, uat, prod");
  } catch (err: any) {
    console.warn("[deployments] Could not seed default environments:", err.message);
  }
}

// ── Environment history ───────────────────────────────────────────────────────

export interface DeploymentEnvironmentHistory {
  id: string;
  environment_name: string;
  action: string;
  changed_by: string;
  changed_at: number;
  before_snapshot: string;
  after_snapshot: string;
}

export async function getEnvironmentHistory(
  cfg: GatewayConfig,
  envName: string,
  limit = 50,
): Promise<DeploymentEnvironmentHistory[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM environment_history WHERE environment_name = '${esc(envName)}' ORDER BY changed_at DESC LIMIT ${limit}`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeHistory);
}

// ── Monitoring Alerts ────────────────────────────────────────────────────────

export interface MonitoringAlert {
  id: string;
  environment: string;
  category: string;
  component: string;
  fingerprint_hash: string;
  severity: string;
  message: string;
  detected_at: number;
  issue_number?: number;
  issue_action?: string;
  component_id?: string;
  resolved_at?: number;
}

export async function createMonitoringAlert(
  cfg: GatewayConfig,
  alert: Omit<MonitoringAlert, "id">,
): Promise<string> {
  const id = ulid();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_monitoring_alert",
    [{ id, ...alert }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function getMonitoringAlerts(
  cfg: GatewayConfig,
  environment: string,
  limit = 50,
): Promise<MonitoringAlert[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM alerts WHERE environment = '${esc(environment)}' ORDER BY detected_at DESC LIMIT ${limit}`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeAlert);
}

export async function resolveMonitoringAlert(
  cfg: GatewayConfig,
  id: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "resolve_monitoring_alert",
    [{ id, resolved_at: Date.now() }],
    cfg.spacetimedbToken,
  );
}

function normalizeAlert(r: any): MonitoringAlert {
  return {
    id: r.id,
    environment: r.environment,
    category: r.category,
    component: r.component,
    fingerprint_hash: r.fingerprint_hash,
    severity: r.severity,
    message: r.message,
    detected_at: Number(r.detected_at),
    issue_number: r.issue_number ? Number(r.issue_number) : undefined,
    issue_action: r.issue_action || undefined,
    component_id: r.component_id || undefined,
    resolved_at: r.resolved_at ? Number(r.resolved_at) : undefined,
  };
}

// ── History normalizer ──────────────────────────────────────────────────────

function normalizeHistory(r: any): DeploymentEnvironmentHistory {
  return {
    id: r.id,
    environment_name: r.environment_name,
    action: r.action,
    changed_by: r.changed_by,
    changed_at: Number(r.changed_at),
    before_snapshot: r.before_snapshot || "",
    after_snapshot: r.after_snapshot || "",
  };
}

// ── SQL escaping ──────────────────────────────────────────────────────────────

function esc(s: string): string {
  return s.replace(/'/g, "''");
}

// ── Normalizers ───────────────────────────────────────────────────────────────

function normalizeEnv(r: any): DeploymentEnvironment {
  return {
    name: r.name,
    display_name: r.display_name,
    order: Number(r.order),
    is_active: Boolean(r.is_active),
    max_script_timeout: Number(r.max_script_timeout),
    health_check_interval: Number(r.health_check_interval),
    window_days: r.window_days || "[]",
    window_start: r.window_start || "",
    window_end: r.window_end || "",
    window_timezone: r.window_timezone || "",
    required_approvals: Number(r.required_approvals ?? 1),
    created_at: Number(r.created_at),
    updated_at: Number(r.updated_at),
  };
}

function normalizeApprover(r: any): DeploymentEnvironmentApprover {
  return {
    id: r.id,
    environment_name: r.environment_name,
    user_id: r.user_id,
    added_at: Number(r.added_at),
    added_by: r.added_by,
  };
}

function normalizePromotion(r: any): DeploymentPromotion {
  return {
    id: r.id,
    script_id: r.script_id,
    script_version: r.script_version,
    script_sha256: r.script_sha256,
    environment_name: r.environment_name,
    status: r.status,
    initiated_by: r.initiated_by,
    initiated_at: Number(r.initiated_at),
    promoted_at: Number(r.promoted_at ?? 0),
    deployed_at: Number(r.deployed_at ?? 0),
    receipt_id: r.receipt_id || "",
  };
}

function normalizeApprovalRow(r: any): DeploymentApproval {
  return {
    id: r.id,
    promotion_id: r.promotion_id,
    script_id: r.script_id,
    script_version: r.script_version,
    environment_name: r.environment_name,
    user_id: r.user_id,
    approved_at: Number(r.approved_at),
  };
}

// ── Environment Allocation (Doc 077) ──────────────────────────────────────────

export interface EnvironmentAllocation {
  id: string;
  resource_id: string;
  app_name: string;
  environment_name: string;
  base_port: number;
  app_dir: string;
  data_dir: string;
  log_dir: string;
  config_dir: string;
  tls_cert_path: string;
  tls_key_path: string;
  revision: number;
  created_at: number;
  updated_at: number;
  is_active: boolean;
}

export interface ServicePortAssignment {
  id: string;
  allocation_id: string;
  service_name: string;
  port: number;
  protocol: string;
  data_dir: string;
  health_endpoint: string;
  description: string;
  created_at: number;
  updated_at: number;
}

export interface AllocationHistory {
  id: string;
  allocation_id: string;
  revision: number;
  change_type: string;
  changed_fields: string;
  changed_by: string;
  timestamp: number;
}

// ── Allocation queries ────────────────────────────────────────────────────────

export async function getEnvironmentAllocations(
  cfg: GatewayConfig,
  resourceId?: string,
  appName?: string,
): Promise<EnvironmentAllocation[]> {
  let query = "SELECT * FROM environment_allocation WHERE is_active = true";
  if (resourceId) query += ` AND resource_id = '${esc(resourceId)}'`;
  if (appName) query += ` AND app_name = '${esc(appName)}'`;
  const rows = await sqlQuery(cfg.spacetimedbUrl, cfg.spacetimedbModuleName, query, cfg.spacetimedbToken);
  return rows.map(normalizeAllocation);
}

export async function getEnvironmentAllocation(
  cfg: GatewayConfig,
  id: string,
): Promise<EnvironmentAllocation | null> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM environment_allocation WHERE id = '${esc(id)}'`,
    cfg.spacetimedbToken,
  );
  return rows.length ? normalizeAllocation(rows[0]) : null;
}

export async function getAllocationsForResource(
  cfg: GatewayConfig,
  resourceId: string,
): Promise<EnvironmentAllocation[]> {
  return getEnvironmentAllocations(cfg, resourceId);
}

export async function createEnvironmentAllocation(
  cfg: GatewayConfig,
  alloc: Omit<EnvironmentAllocation, "id" | "revision" | "created_at" | "updated_at" | "is_active">,
  changedBy: string,
): Promise<string> {
  const id = ulid();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_environment_allocation",
    [{
      id,
      ...alloc,
      revision: 1,
      is_active: true,
      changed_by: changedBy,
    }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function updateEnvironmentAllocation(
  cfg: GatewayConfig,
  id: string,
  updates: Partial<Pick<EnvironmentAllocation, "base_port" | "app_dir" | "data_dir" | "log_dir" | "config_dir" | "tls_cert_path" | "tls_key_path">>,
  changedBy: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_environment_allocation",
    [{ id, ...updates, changed_by: changedBy }],
    cfg.spacetimedbToken,
  );
}

export async function deactivateEnvironmentAllocation(
  cfg: GatewayConfig,
  id: string,
  changedBy: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "deactivate_environment_allocation",
    [{ id, changed_by: changedBy }],
    cfg.spacetimedbToken,
  );
}

export async function deleteEnvironmentAllocation(
  cfg: GatewayConfig,
  id: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "delete_environment_allocation",
    [{ id }],
    cfg.spacetimedbToken,
  );
}

// ── Port assignment queries ───────────────────────────────────────────────────

export async function getServicePortAssignments(
  cfg: GatewayConfig,
  allocationId: string,
): Promise<ServicePortAssignment[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM service_port_assignment WHERE allocation_id = '${esc(allocationId)}'`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizePortAssignment);
}

export async function createServicePortAssignment(
  cfg: GatewayConfig,
  assignment: Omit<ServicePortAssignment, "id" | "created_at" | "updated_at">,
): Promise<string> {
  const id = ulid();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_service_port_assignment",
    [{ id, ...assignment }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function updateServicePortAssignment(
  cfg: GatewayConfig,
  id: string,
  updates: Partial<Pick<ServicePortAssignment, "port" | "protocol" | "data_dir" | "health_endpoint" | "description">>,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_service_port_assignment",
    [{ id, ...updates }],
    cfg.spacetimedbToken,
  );
}

export async function deleteServicePortAssignment(
  cfg: GatewayConfig,
  id: string,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "delete_service_port_assignment",
    [{ id }],
    cfg.spacetimedbToken,
  );
}

// ── Server port map (cross-allocation collision detection) ────────────────────

export async function getServerPortMap(
  cfg: GatewayConfig,
  resourceId: string,
): Promise<Map<number, { app: string; env: string; service: string; protocol: string }>> {
  const allocations = await getEnvironmentAllocations(cfg, resourceId);
  const portMap = new Map<number, { app: string; env: string; service: string; protocol: string }>();
  for (const alloc of allocations) {
    const ports = await getServicePortAssignments(cfg, alloc.id);
    for (const p of ports) {
      portMap.set(p.port, {
        app: alloc.app_name,
        env: alloc.environment_name,
        service: p.service_name,
        protocol: p.protocol,
      });
    }
  }
  return portMap;
}

// ── Allocation history ────────────────────────────────────────────────────────

export async function getAllocationHistory(
  cfg: GatewayConfig,
  allocationId: string,
  limit = 50,
): Promise<AllocationHistory[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM allocation_history WHERE allocation_id = '${esc(allocationId)}' ORDER BY timestamp DESC LIMIT ${limit}`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeAllocationHistory);
}

// ── Collision detection (gateway-side, for API use) ───────────────────────────

export interface ConflictCheckRequest {
  resource_id: string;
  app_name: string;
  environment_name: string;
  ports: Array<{ service_name: string; port: number; protocol: string }>;
  directories: { app_dir: string; data_dir: string; log_dir: string; config_dir: string };
  exclude_allocation_id?: string;
}

export interface ConflictResult {
  conflicts: Array<{ type: "port" | "directory"; field: string; value: string | number; conflicting_app: string; conflicting_env: string; detail: string }>;
  warnings: Array<{ type: string; field: string; message: string }>;
  suggestions: Record<string, number | string>;
}

export async function checkPortConflicts(
  cfg: GatewayConfig,
  req: ConflictCheckRequest,
): Promise<ConflictResult> {
  const conflicts: ConflictResult["conflicts"] = [];
  const warnings: ConflictResult["warnings"] = [];
  const suggestions: Record<string, number | string> = {};

  const existingAllocations = await getEnvironmentAllocations(cfg, req.resource_id);
  const filtered = existingAllocations.filter(a =>
    req.exclude_allocation_id ? a.id !== req.exclude_allocation_id : true,
  );

  // Port conflict detection
  const portMap = new Map<string, { app: string; env: string; service: string }>();
  for (const alloc of filtered) {
    const ports = await getServicePortAssignments(cfg, alloc.id);
    for (const p of ports) {
      portMap.set(`${p.port}/${p.protocol}`, { app: alloc.app_name, env: alloc.environment_name, service: p.service_name });
    }
  }

  for (const p of req.ports) {
    const key = `${p.port}/${p.protocol}`;
    const existing = portMap.get(key);
    if (existing) {
      conflicts.push({
        type: "port",
        field: p.service_name,
        value: p.port,
        conflicting_app: existing.app,
        conflicting_env: existing.env,
        detail: `Port ${p.port}/${p.protocol} is used by ${existing.app}/${existing.env} (${existing.service})`,
      });
      // Suggest next available
      let suggested = p.port + 1;
      while (portMap.has(`${suggested}/${p.protocol}`)) suggested++;
      suggestions[p.service_name] = suggested;
    }
    if (p.port < 1024) {
      warnings.push({ type: "privileged_port", field: p.service_name, message: `Port ${p.port} requires root/sudo` });
    }
    if (p.port < 1 || p.port > 65535) {
      conflicts.push({ type: "port", field: p.service_name, value: p.port, conflicting_app: "", conflicting_env: "", detail: `Port ${p.port} is out of valid range (1-65535)` });
    }
  }

  // Directory conflict detection (substring containment)
  const dirFields = ["app_dir", "data_dir", "log_dir", "config_dir"] as const;
  for (const alloc of filtered) {
    for (const field of dirFields) {
      const newDir = req.directories[field];
      const existingDir = (alloc as any)[field] as string;
      if (!newDir || !existingDir) continue;
      if (newDir === existingDir || newDir.startsWith(existingDir + "/") || existingDir.startsWith(newDir + "/")) {
        conflicts.push({
          type: "directory",
          field,
          value: newDir,
          conflicting_app: alloc.app_name,
          conflicting_env: alloc.environment_name,
          detail: `Directory '${newDir}' conflicts with '${existingDir}' (${alloc.app_name}/${alloc.environment_name})`,
        });
      }
    }
  }

  return { conflicts, warnings, suggestions };
}

// ── Suggest defaults ──────────────────────────────────────────────────────────

const ENV_PORT_OFFSETS: Record<string, number> = {
  prod: 0, production: 0,
  staging: 100,
  dev: 200, development: 200,
  qa: 300,
  uat: 400,
};

const WELL_KNOWN_PORTS: Record<string, number> = {
  postgres: 5432, postgresql: 5432,
  redis: 6379,
  mysql: 3306,
  mongodb: 27017,
  rabbitmq: 5672,
};

export interface SuggestDefaultsRequest {
  resource_id: string;
  app_name: string;
  environment_name: string;
  base_port?: number;
  services?: string[];
}

export interface SuggestDefaultsResult {
  base_port: number;
  app_dir: string;
  data_dir: string;
  log_dir: string;
  config_dir: string;
  service_ports: Array<{ service_name: string; port: number; protocol: string }>;
}

export async function suggestDefaults(
  cfg: GatewayConfig,
  req: SuggestDefaultsRequest,
): Promise<SuggestDefaultsResult> {
  const envOffset = ENV_PORT_OFFSETS[req.environment_name] ?? (Object.keys(ENV_PORT_OFFSETS).length * 100);
  const basePort = req.base_port ?? (3000 + envOffset);

  const existingAllocations = await getEnvironmentAllocations(cfg, req.resource_id);
  const portMap = new Map<string, boolean>();
  for (const alloc of existingAllocations) {
    const ports = await getServicePortAssignments(cfg, alloc.id);
    for (const p of ports) {
      portMap.set(`${p.port}/${p.protocol}`, true);
    }
  }

  // Suggest app port — find first available starting from basePort
  let appPort = basePort;
  while (portMap.has(`${appPort}/tcp`)) appPort++;

  const servicePorts: Array<{ service_name: string; port: number; protocol: string }> = [
    { service_name: "app", port: appPort, protocol: "tcp" },
  ];

  // Add well-known service ports
  const envOrder = ENV_PORT_OFFSETS[req.environment_name] !== undefined
    ? Math.floor(ENV_PORT_OFFSETS[req.environment_name] / 100)
    : 5;

  for (const svc of req.services || []) {
    const svcLower = svc.toLowerCase();
    const wellKnown = WELL_KNOWN_PORTS[svcLower];
    if (wellKnown) {
      let port = wellKnown + envOrder;
      while (portMap.has(`${port}/tcp`)) port++;
      servicePorts.push({ service_name: svc, port, protocol: "tcp" });
    }
  }

  return {
    base_port: appPort,
    app_dir: `/opt/${req.app_name}/${req.environment_name}`,
    data_dir: `/var/data/${req.app_name}/${req.environment_name}`,
    log_dir: `/var/log/${req.app_name}/${req.environment_name}`,
    config_dir: `/etc/${req.app_name}/${req.environment_name}`,
    service_ports: servicePorts,
  };
}

// ── Allocation normalizers ────────────────────────────────────────────────────

function normalizeAllocation(r: any): EnvironmentAllocation {
  return {
    id: r.id,
    resource_id: r.resource_id,
    app_name: r.app_name,
    environment_name: r.environment_name,
    base_port: Number(r.base_port),
    app_dir: r.app_dir || "",
    data_dir: r.data_dir || "",
    log_dir: r.log_dir || "",
    config_dir: r.config_dir || "",
    tls_cert_path: r.tls_cert_path || "",
    tls_key_path: r.tls_key_path || "",
    revision: Number(r.revision),
    created_at: Number(r.created_at),
    updated_at: Number(r.updated_at),
    is_active: Boolean(r.is_active),
  };
}

function normalizePortAssignment(r: any): ServicePortAssignment {
  return {
    id: r.id,
    allocation_id: r.allocation_id,
    service_name: r.service_name,
    port: Number(r.port),
    protocol: r.protocol || "tcp",
    data_dir: r.data_dir || "",
    health_endpoint: r.health_endpoint || "",
    description: r.description || "",
    created_at: Number(r.created_at),
    updated_at: Number(r.updated_at),
  };
}

function normalizeAllocationHistory(r: any): AllocationHistory {
  return {
    id: r.id,
    allocation_id: r.allocation_id,
    revision: Number(r.revision),
    change_type: r.change_type,
    changed_fields: r.changed_fields || "{}",
    changed_by: r.changed_by || "",
    timestamp: Number(r.timestamp),
  };
}
