/**
 * Trigger Handler — webhook → deployment trigger system.
 *
 * Manages deployment triggers that fire when a matching webhook push is received.
 * Triggers are stored in SpacetimeDB and evaluated against incoming GitHub payloads.
 */

import { callReducer, sqlQuery } from "../spacetimedb/client.js";
import type { GatewayConfig } from "../config/index.js";
import { ulid } from "ulid";
import { emitScriptPromoted } from "./events.js";

// ── Types ───────────────────────────────────────────────────────────────────

export interface DeploymentTrigger {
  id: string;
  script_id: string;
  repo_url: string;
  branch: string;
  tag_pattern?: string;
  environment: string;
  cron_schedule?: string;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

// ── SQL escape ──────────────────────────────────────────────────────────────

function esc(s: string): string {
  return s.replace(/'/g, "''");
}

// ── Normalize ───────────────────────────────────────────────────────────────

function normalizeTrigger(row: any): DeploymentTrigger {
  return {
    id: row.id ?? row.Id ?? "",
    script_id: row.script_id ?? row.ScriptId ?? "",
    repo_url: row.repo_url ?? row.RepoUrl ?? "",
    branch: row.branch ?? row.Branch ?? "",
    tag_pattern: row.tag_pattern ?? row.TagPattern ?? undefined,
    environment: row.environment ?? row.Environment ?? "",
    cron_schedule: row.cron_schedule ?? row.CronSchedule ?? undefined,
    enabled: row.enabled ?? row.Enabled ?? true,
    created_at: row.created_at ?? row.CreatedAt ?? 0,
    updated_at: row.updated_at ?? row.UpdatedAt ?? 0,
  };
}

// ── Queries ─────────────────────────────────────────────────────────────────

export async function getTriggers(cfg: GatewayConfig): Promise<DeploymentTrigger[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "SELECT * FROM deployment_triggers",
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeTrigger);
}

export async function getTrigger(cfg: GatewayConfig, id: string): Promise<DeploymentTrigger | null> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM deployment_triggers WHERE id = '${esc(id)}'`,
    cfg.spacetimedbToken,
  );
  return rows.length ? normalizeTrigger(rows[0]) : null;
}

export async function createTrigger(
  cfg: GatewayConfig,
  trigger: Omit<DeploymentTrigger, "id" | "created_at" | "updated_at">,
): Promise<string> {
  const id = ulid();
  const now = Date.now();
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "create_deployment_trigger",
    [{
      id,
      script_id: trigger.script_id,
      repo_url: trigger.repo_url,
      branch: trigger.branch,
      tag_pattern: trigger.tag_pattern || "",
      environment: trigger.environment,
      cron_schedule: trigger.cron_schedule || "",
      enabled: trigger.enabled,
      created_at: now,
      updated_at: now,
    }],
    cfg.spacetimedbToken,
  );
  return id;
}

export async function deleteTrigger(cfg: GatewayConfig, id: string): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "delete_deployment_trigger",
    [{ id }],
    cfg.spacetimedbToken,
  );
}

export async function disableTrigger(cfg: GatewayConfig, id: string): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_deployment_trigger",
    [{ id, enabled: false }],
    cfg.spacetimedbToken,
  );
}

export async function enableTrigger(cfg: GatewayConfig, id: string): Promise<void> {
  await callReducer(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    "update_deployment_trigger",
    [{ id, enabled: true }],
    cfg.spacetimedbToken,
  );
}

export async function getTriggersForRepo(
  cfg: GatewayConfig,
  repoUrl: string,
  branch: string,
): Promise<DeploymentTrigger[]> {
  const rows = await sqlQuery(
    cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
    `SELECT * FROM deployment_triggers WHERE repo_url = '${esc(repoUrl)}' AND branch = '${esc(branch)}' AND enabled = true`,
    cfg.spacetimedbToken,
  );
  return rows.map(normalizeTrigger);
}

// ── Webhook handler ─────────────────────────────────────────────────────────

export async function handleWebhookPush(
  cfg: GatewayConfig,
  payload: {
    repository: { clone_url: string; full_name: string };
    ref: string;
    after: string;
  },
): Promise<{ matched: DeploymentTrigger[]; triggered: string[] }> {
  // Extract branch from ref (e.g., "refs/heads/main" → "main")
  const refParts = payload.ref.split("/");
  const branch = refParts.slice(2).join("/");

  // Try both clone_url and https form
  const repoUrl = payload.repository.clone_url;
  const matched = await getTriggersForRepo(cfg, repoUrl, branch);

  // Also try without .git suffix if no matches
  if (matched.length === 0 && repoUrl.endsWith(".git")) {
    const altUrl = repoUrl.replace(/\.git$/, "");
    const altMatched = await getTriggersForRepo(cfg, altUrl, branch);
    matched.push(...altMatched);
  }

  const triggered: string[] = [];

  for (const trigger of matched) {
    try {
      emitScriptPromoted(trigger.environment, trigger.script_id, "latest", `webhook:${payload.repository.full_name}`);
      triggered.push(trigger.id);
    } catch (err: any) {
      console.error(`[trigger-handler] Failed to trigger ${trigger.id}:`, err.message);
    }
  }

  return { matched, triggered };
}
