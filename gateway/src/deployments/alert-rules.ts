/**
 * Alert Rules — CRUD logic for deployment alert rules stored in SpacetimeDB.
 *
 * Design Doc 045 §8.2
 */

import { callReducer, sqlQuery } from "../spacetimedb/client.js";
import type { GatewayConfig } from "../config/index.js";
import { ulid } from "ulid";

// ── Types ───────────────────────────────────────────────────────────────────

export interface AlertRule {
  id: string;
  environment: string;
  name: string;
  metric: string;
  operator: string;
  threshold: number;
  duration_minutes: number;
  severity: string;
  enabled: boolean;
  auto_file_issue: boolean;
  custom_script_id: string;
  applies_to_resources: string;
  triggered_count: number;
  last_triggered_at: number;
  created_at: number;
  updated_at: number;
  component_id?: string;
}

// ── SQL escaping ─────────────────────────────────────────────────────────────

function esc(s: string): string {
  return s.replace(/'/g, "''");
}

// ── Normalizer ──────────────────────────────────────────────────────────────

function normalizeRule(r: any): AlertRule {
  return {
    id: r.id,
    environment: r.environment,
    name: r.name,
    metric: r.metric,
    operator: r.operator,
    threshold: Number(r.threshold),
    duration_minutes: Number(r.duration_minutes ?? 0),
    severity: r.severity || "medium",
    enabled: Boolean(r.enabled),
    auto_file_issue: Boolean(r.auto_file_issue),
    custom_script_id: r.custom_script_id || "",
    applies_to_resources: r.applies_to_resources || "",
    triggered_count: Number(r.triggered_count ?? 0),
    last_triggered_at: r.last_triggered_at ? Number(r.last_triggered_at) : 0,
    created_at: Number(r.created_at),
    updated_at: Number(r.updated_at),
    component_id: r.component_id || undefined,
  };
}

// ── Queries ─────────────────────────────────────────────────────────────────

export async function getAlertRules(cfg: GatewayConfig, environment: string): Promise<AlertRule[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM alert_rules WHERE environment = '${esc(environment)}' ORDER BY created_at DESC`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeRule);
}

export async function getAlertRule(cfg: GatewayConfig, id: string): Promise<AlertRule | null> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM alert_rules WHERE id = '${esc(id)}'`,
    cfg.spacetimedbToken,
  );
  return rows.length ? normalizeRule(rows[0]) : null;
}

export async function createAlertRule(
  cfg: GatewayConfig,
  rule: Omit<AlertRule, "id" | "triggered_count" | "last_triggered_at" | "created_at" | "updated_at">,
): Promise<string> {
  const id = ulid();
  const now = Date.now();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_deployment_alert_rule",
    [{
      id,
      ...rule,
      triggered_count: 0,
      last_triggered_at: 0,
      created_at: now,
      updated_at: now,
    }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function updateAlertRule(
  cfg: GatewayConfig,
  id: string,
  updates: Partial<Omit<AlertRule, "id" | "created_at">>,
): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_deployment_alert_rule",
    [{ id, ...updates, updated_at: Date.now() }],
    cfg.spacetimedbToken,
  );
}

export async function deleteAlertRule(cfg: GatewayConfig, id: string): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "delete_deployment_alert_rule",
    [{ id }],
    cfg.spacetimedbToken,
  );
}

export async function setAlertRuleEnabled(cfg: GatewayConfig, id: string, enabled: boolean): Promise<void> {
  await updateAlertRule(cfg, id, { enabled });
}
