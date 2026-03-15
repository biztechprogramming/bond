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
    "SELECT * FROM deployment_environments",
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeEnv);
}

export async function getEnvironment(cfg: GatewayConfig, name: string): Promise<DeploymentEnvironment | null> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM deployment_environments WHERE name = '${esc(name)}'`,
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
    [{
      name: env.name,
      display_name: env.display_name,
      order: env.order,
      max_script_timeout: env.max_script_timeout,
      health_check_interval: env.health_check_interval,
      window_days: env.window_days || "[]",
      window_start: env.window_start || "",
      window_end: env.window_end || "",
      window_timezone: env.window_timezone || "",
      required_approvals: env.required_approvals ?? 1,
      history_id: ulid(),
      changed_by: changedBy,
    }],
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
    `SELECT * FROM deployment_environment_approvers WHERE environment_name = '${esc(environmentName)}'`,
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
    `SELECT id FROM deployment_environment_approvers WHERE environment_name = '${esc(environmentName)}' AND user_id = '${esc(userId)}'`,
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
    `SELECT * FROM deployment_promotions WHERE script_id = '${esc(scriptId)}' AND script_version = '${esc(scriptVersion)}' AND environment_name = '${esc(environmentName)}'`,
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
    `SELECT * FROM deployment_promotions WHERE script_id = '${esc(scriptId)}' AND script_version = '${esc(scriptVersion)}'`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizePromotion);
}

export async function getAllPromotions(cfg: GatewayConfig): Promise<DeploymentPromotion[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "SELECT * FROM deployment_promotions",
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
    `SELECT * FROM deployment_approvals WHERE promotion_id = '${esc(promotionId)}'`,
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
    `SELECT * FROM deployment_environment_history WHERE environment_name = '${esc(envName)}' ORDER BY changed_at DESC LIMIT ${limit}`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeHistory);
}

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
