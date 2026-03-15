/**
 * Permission Broker — /deploy endpoint.
 *
 * This is the ONLY way deployment agents can execute scripts.
 * The agent sends a script_id + action — the broker does everything else:
 *   - Validates agent token and derives environment
 *   - Checks promotion status in SpacetimeDB
 *   - Loads environment secrets (never exposed to agent)
 *   - Executes scripts on the host
 *   - Generates deployment receipts
 *   - Updates promotion status
 *
 * The agent NEVER sees: script content, file paths, secrets, or the registry.
 */

import fs from "node:fs";
import path from "node:path";
import { homedir } from "node:os";
import type { AgentTokenPayload } from "./types.js";
import { executeCommand } from "./executor.js";
import { getManifest, verifyScriptHash, getScriptVersionDir } from "../deployments/scripts.js";
import { acquireLock, releaseLock, getLock, isWithinDeploymentWindow } from "../deployments/locks.js";
import {
  writeReceipt, readReceipt, buildReceiptId, listReceipts,
  type DeploymentReceipt, type DeploymentPhase,
} from "../deployments/receipts.js";
import type { GatewayConfig } from "../config/index.js";
import { sqlQuery, callReducer } from "../spacetimedb/client.js";
import { ulid } from "ulid";
import {
  emitDeploymentStarted,
  emitDeploymentSucceeded,
  emitDeploymentFailed,
  emitRollbackTriggered,
} from "../deployments/events.js";
import { enqueue, dequeue, peek, getQueue, removeFromQueue, type QueueEntry } from "../deployments/queue.js";
import { loadSecrets } from "../deployments/secrets.js";
import { getEnvironments } from "../deployments/stdb.js";
import { writeDeployLog } from "../deployments/log-stream.js";
import { saveBaseline } from "../deployments/drift-detector.js";
import { executeHealthCheck } from "../deployments/health-scheduler.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

export type DeployAction =
  | "deploy"
  | "rollback"
  | "dry-run"
  | "validate"
  | "pre-hook"
  | "post-hook"
  | "health-check"
  | "info"
  | "receipt"
  | "status"
  | "lock-status"
  | "queue-status"
  | "dependencies";

export interface DeployRequest {
  script_id?: string;
  version?: string;
  action: DeployAction;
  environment?: string; // for "receipt" action — read a specific env's receipt
  timeout?: number;
}

export interface DeployResult {
  status: "ok" | "denied" | "error" | "queued";
  action: DeployAction;
  environment?: string;
  script_id?: string;
  version?: string;
  exit_code?: number;
  stdout?: string;
  stderr?: string;
  duration_ms?: number;
  receipt_id?: string;
  receipt?: DeploymentReceipt;
  info?: any;
  reason?: string;
  queue_position?: number;
  next_queued?: { script_id: string; version: string };
}

/**
 * Derive environment name from agent ID.
 * deploy-qa → qa, deploy-prod → prod
 */
function deriveEnvironment(agentSub: string): string | null {
  const match = agentSub.match(/^deploy-([a-z0-9-]+)$/);
  return match ? match[1]! : null;
}

// loadSecrets is now imported from ../deployments/secrets.js

/**
 * Query SpacetimeDB for promotion status.
 */
async function checkPromotion(
  cfg: GatewayConfig,
  scriptId: string,
  version: string,
  env: string,
): Promise<{ id: string; status: string; sha256: string } | null> {
  try {
    const rows = await sqlQuery(
      cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
      `SELECT id, status, script_sha256 FROM deployment_promotions WHERE script_id = '${scriptId.replace(/'/g, "''")}' AND script_version = '${version.replace(/'/g, "''")}' AND environment_name = '${env.replace(/'/g, "''")}'`,
      cfg.spacetimedbToken,
    );
    if (!rows.length) return null;
    return { id: rows[0].id, status: rows[0].status, sha256: rows[0].script_sha256 };
  } catch {
    return null;
  }
}

async function updatePromotionStatus(
  cfg: GatewayConfig,
  promotionId: string,
  status: string,
  extra: { deployed_at?: number; receipt_id?: string } = {},
): Promise<void> {
  try {
    await callReducer(
      cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
      "update_promotion_status",
      [{ id: promotionId, status, ...extra }],
      cfg.spacetimedbToken,
    );
  } catch (err: any) {
    console.error("[deploy-handler] Failed to update promotion status:", err.message);
  }
}

async function getEnvConfig(cfg: GatewayConfig, env: string): Promise<any | null> {
  try {
    const rows = await sqlQuery(
      cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
      `SELECT * FROM deployment_environments WHERE name = '${env.replace(/'/g, "''")}' AND is_active = true`,
      cfg.spacetimedbToken,
    );
    return rows.length ? rows[0] : null;
  } catch {
    return null;
  }
}

/**
 * Main deploy handler — called by broker router.
 */
export async function handleDeploy(
  cfg: GatewayConfig,
  agent: AgentTokenPayload,
  body: DeployRequest,
): Promise<DeployResult> {
  const env = deriveEnvironment(agent.sub);
  if (!env) {
    return {
      status: "denied",
      action: body.action,
      reason: `Cannot derive environment from agent ID '${agent.sub}'. Expected format: deploy-{env}`,
    };
  }

  const { action, script_id, version } = body;

  // Actions that don't require a script_id
  if (action === "health-check") {
    return runHealthCheck(env);
  }
  if (action === "lock-status") {
    const lock = getLock(DEPLOYMENTS_DIR, env);
    return {
      status: "ok",
      action,
      environment: env,
      info: lock ? { locked: true, ...lock } : { locked: false },
    };
  }
  if (action === "queue-status") {
    const q = getQueue(env);
    return {
      status: "ok",
      action,
      environment: env,
      info: { queue: q, length: q.length },
    };
  }

  // Actions that read from another environment's receipts
  if (action === "receipt") {
    const targetEnv = body.environment || env;
    if (!script_id) {
      // Return latest receipts for the environment
      const receipts = listReceipts(DEPLOYMENTS_DIR, targetEnv, 10);
      return { status: "ok", action, environment: targetEnv, info: { receipts } };
    }
    const latest = listReceipts(DEPLOYMENTS_DIR, targetEnv, 100)
      .find(r => r.script_id === script_id && r.status === "success");
    return {
      status: "ok",
      action,
      environment: targetEnv,
      receipt: latest ?? undefined,
      info: latest ? undefined : { message: `No successful receipt found for ${script_id} in ${targetEnv}` },
    };
  }

  if (!script_id) {
    return { status: "denied", action, reason: "script_id is required" };
  }

  // Read environment config (deployment window, timeout limits)
  const envConfig = await getEnvConfig(cfg, env);
  if (!envConfig) {
    return {
      status: "denied",
      action,
      environment: env,
      script_id,
      reason: `Environment '${env}' not found or not active`,
    };
  }

  // info action — return metadata without checking promotion
  if (action === "info") {
    const ver = version || "v1";
    const manifest = getManifest(DEPLOYMENTS_DIR, script_id, ver);
    if (!manifest) {
      return { status: "denied", action, environment: env, script_id, reason: "Script not found in registry" };
    }
    return {
      status: "ok",
      action,
      environment: env,
      script_id,
      version: ver,
      info: {
        name: manifest.name,
        version: manifest.version,
        depends_on: manifest.depends_on,
        timeout: manifest.timeout,
        dry_run: manifest.dry_run,
        has_rollback: !!manifest.rollback,
        sha256: manifest.sha256,
      },
    };
  }

  // status action — check promotion status
  if (action === "status") {
    const ver = version || "v1";
    const promo = await checkPromotion(cfg, script_id, ver, env);
    return {
      status: "ok",
      action,
      environment: env,
      script_id,
      version: ver,
      info: promo ? { promotion_status: promo.status, promotion_id: promo.id } : { promotion_status: "not_promoted" },
    };
  }

  // Validate / execute actions — must be promoted
  const ver = version || "v1";
  const promotion = await checkPromotion(cfg, script_id, ver, env);

  if (!promotion || ["not_promoted", "awaiting_approvals"].includes(promotion.status)) {
    return {
      status: "denied",
      action,
      environment: env,
      script_id,
      version: ver,
      reason: `Script ${script_id}@${ver} is not promoted to ${env} (status: ${promotion?.status ?? "not_promoted"})`,
    };
  }

  // dependencies action — return dependency graph for a script
  if (action === "dependencies") {
    return getDependencyStatus(cfg, script_id, ver, env);
  }

  // validate action — syntax check, hash check, dependency check, window check
  if (action === "validate") {
    return runValidation(cfg, script_id, ver, env, envConfig, promotion);
  }

  // dry-run action
  if (action === "dry-run") {
    return runDryRun(script_id, ver, env, envConfig, promotion);
  }

  // pre-hook / post-hook actions
  if (action === "pre-hook" || action === "post-hook") {
    const hookType = action === "pre-hook" ? "pre_deploy" : "post_deploy";
    return runHook(hookType, env, envConfig);
  }

  // deploy action
  if (action === "deploy") {
    return runDeploy(cfg, agent, script_id, ver, env, envConfig, promotion, body.timeout);
  }

  // rollback action
  if (action === "rollback") {
    return runRollback(cfg, agent, script_id, ver, env, envConfig, promotion);
  }

  return { status: "denied", action, reason: `Unknown action: ${action}` };
}

// ── Action implementations ────────────────────────────────────────────────────

async function runValidation(
  cfg: GatewayConfig,
  scriptId: string,
  version: string,
  env: string,
  envConfig: any,
  promotion: { id: string; status: string; sha256: string },
): Promise<DeployResult> {
  const checks: string[] = [];
  const errors: string[] = [];

  // 1. Script exists
  const manifest = getManifest(DEPLOYMENTS_DIR, scriptId, version);
  if (!manifest) {
    return { status: "error", action: "validate", environment: env, script_id: scriptId, reason: "Script not found in registry" };
  }
  checks.push("Script exists in registry");

  // 2. Hash check
  if (!verifyScriptHash(DEPLOYMENTS_DIR, scriptId, version)) {
    errors.push("SHA-256 hash mismatch — script may have been tampered with");
  } else {
    checks.push("SHA-256 hash verified");
  }

  // 3. Deployment window check
  const windowDays = envConfig.window_days || "[]";
  const windowStart = envConfig.window_start || "";
  const windowEnd = envConfig.window_end || "";
  const windowTz = envConfig.window_timezone || "";
  if (windowStart && windowEnd) {
    const inWindow = isWithinDeploymentWindow(windowDays, windowStart, windowEnd, windowTz);
    if (!inWindow) {
      errors.push(`Outside deployment window (${windowStart}–${windowEnd} ${windowTz || "UTC"})`);
    } else {
      checks.push("Within deployment window");
    }
  }

  // 4. Script timeout check
  const scriptTimeout = manifest.timeout ?? 60;
  const envMaxTimeout = Number(envConfig.max_script_timeout) || 600;
  if (scriptTimeout > envMaxTimeout) {
    errors.push(`Script timeout ${scriptTimeout}s exceeds environment max ${envMaxTimeout}s`);
  } else {
    checks.push(`Timeout OK (${scriptTimeout}s ≤ ${envMaxTimeout}s)`);
  }

  // 5. Syntax check via bash -n
  const scriptPath = path.join(getScriptVersionDir(DEPLOYMENTS_DIR, scriptId, version), "deploy.sh");
  if (fs.existsSync(scriptPath)) {
    const syntaxResult = await executeCommand(`bash -n '${scriptPath}'`, { timeout: 10 });
    if (syntaxResult.exit_code !== 0) {
      errors.push(`Syntax error in deploy.sh: ${syntaxResult.stderr}`);
    } else {
      checks.push("Bash syntax check passed");
    }
  }

  // 6. Dependency check — verify depends_on scripts are deployed
  if (manifest.depends_on && manifest.depends_on.length > 0) {
    for (const depId of manifest.depends_on) {
      try {
        const depRows = await sqlQuery(
          cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
          `SELECT status FROM deployment_promotions WHERE script_id = '${depId.replace(/'/g, "''")}' AND environment_name = '${env.replace(/'/g, "''")}' AND status = 'success'`,
          cfg.spacetimedbToken,
        );
        if (depRows.length === 0) {
          errors.push(`Dependency ${depId} has not been deployed to ${env}`);
        } else {
          checks.push(`Dependency ${depId} deployed`);
        }
      } catch {
        errors.push(`Could not verify dependency ${depId}`);
      }
    }
  }

  if (errors.length > 0) {
    return {
      status: "error",
      action: "validate",
      environment: env,
      script_id: scriptId,
      version,
      reason: errors.join("; "),
      info: { checks, errors },
    };
  }

  return {
    status: "ok",
    action: "validate",
    environment: env,
    script_id: scriptId,
    version,
    info: { checks, valid: true },
  };
}

async function getDependencyStatus(
  cfg: GatewayConfig,
  scriptId: string,
  version: string,
  env: string,
): Promise<DeployResult> {
  const manifest = getManifest(DEPLOYMENTS_DIR, scriptId, version || "v1");
  if (!manifest) {
    return { status: "error", action: "dependencies", environment: env, script_id: scriptId, reason: "Script not found" };
  }

  const deps: Array<{ script_id: string; status: string }> = [];
  for (const depId of manifest.depends_on || []) {
    try {
      const rows = await sqlQuery(
        cfg.spacetimedbUrl, cfg.spacetimedbModuleName,
        `SELECT status FROM deployment_promotions WHERE script_id = '${depId.replace(/'/g, "''")}' AND environment_name = '${env.replace(/'/g, "''")}' ORDER BY initiated_at DESC LIMIT 1`,
        cfg.spacetimedbToken,
      );
      deps.push({ script_id: depId, status: rows.length ? rows[0].status : "not_deployed" });
    } catch {
      deps.push({ script_id: depId, status: "unknown" });
    }
  }

  return {
    status: "ok",
    action: "dependencies",
    environment: env,
    script_id: scriptId,
    version: version || "v1",
    info: { depends_on: deps },
  };
}

async function runDryRun(
  scriptId: string,
  version: string,
  env: string,
  envConfig: any,
  promotion: { id: string; sha256: string },
): Promise<DeployResult> {
  const manifest = getManifest(DEPLOYMENTS_DIR, scriptId, version);
  if (!manifest || !manifest.dry_run) {
    return {
      status: "ok",
      action: "dry-run",
      environment: env,
      script_id: scriptId,
      info: { message: "Script does not support --dry-run (skipped)" },
    };
  }

  const scriptPath = path.join(getScriptVersionDir(DEPLOYMENTS_DIR, scriptId, version), "deploy.sh");
  const secrets = loadSecrets(env);
  const timeout = Math.min(manifest.timeout ?? 60, Number(envConfig.max_script_timeout) || 600);

  const start = Date.now();
  const result = await executeCommand(`'${scriptPath}' --dry-run`, {
    timeout,
    env: { ...secrets, BOND_DEPLOY_ENV: env, SCRIPT_DIR: path.dirname(scriptPath) },
  });
  const duration_ms = Date.now() - start;

  return {
    status: result.exit_code === 0 ? "ok" : "error",
    action: "dry-run",
    environment: env,
    script_id: scriptId,
    version,
    exit_code: result.exit_code,
    stdout: result.stdout,
    stderr: result.stderr,
    duration_ms,
  };
}

async function runHook(
  hookType: "pre_deploy" | "post_deploy",
  env: string,
  envConfig: any,
): Promise<DeployResult> {
  const hookPath = path.join(DEPLOYMENTS_DIR, "hooks", env, `${hookType}.sh`);
  const action = hookType === "pre_deploy" ? "pre-hook" as DeployAction : "post-hook" as DeployAction;

  if (!fs.existsSync(hookPath)) {
    return { status: "ok", action, environment: env, info: { message: "No hook configured (skipped)" } };
  }

  const secrets = loadSecrets(env);
  const timeout = Number(envConfig.max_script_timeout) || 600;

  const start = Date.now();
  const result = await executeCommand(`bash '${hookPath}'`, {
    timeout,
    env: { ...secrets, BOND_DEPLOY_ENV: env },
  });
  const duration_ms = Date.now() - start;

  return {
    status: result.exit_code === 0 ? "ok" : "error",
    action,
    environment: env,
    exit_code: result.exit_code,
    stdout: result.stdout,
    stderr: result.stderr,
    duration_ms,
  };
}

async function runDeploy(
  cfg: GatewayConfig,
  agent: AgentTokenPayload,
  scriptId: string,
  version: string,
  env: string,
  envConfig: any,
  promotion: { id: string; status: string; sha256: string },
  requestedTimeout?: number,
): Promise<DeployResult> {
  const manifest = getManifest(DEPLOYMENTS_DIR, scriptId, version);
  if (!manifest) {
    return { status: "error", action: "deploy", environment: env, script_id: scriptId, reason: "Manifest not found" };
  }

  // Check deployment window
  const inWindow = isWithinDeploymentWindow(
    envConfig.window_days || "[]",
    envConfig.window_start || "",
    envConfig.window_end || "",
    envConfig.window_timezone || "",
  );
  if (!inWindow) {
    return {
      status: "denied",
      action: "deploy",
      environment: env,
      script_id: scriptId,
      reason: `Outside deployment window (${envConfig.window_start}–${envConfig.window_end} ${envConfig.window_timezone || "UTC"})`,
    };
  }

  // Acquire deployment lock
  const scriptTimeout = Math.min(
    requestedTimeout ?? manifest.timeout ?? 60,
    Number(envConfig.max_script_timeout) || 600,
  );
  const lockAcquired = acquireLock(DEPLOYMENTS_DIR, env, agent.sub, scriptId, scriptTimeout * 2);
  if (!lockAcquired) {
    const existingLock = getLock(DEPLOYMENTS_DIR, env);
    const position = enqueue(env, {
      script_id: scriptId,
      version,
      agent_sub: agent.sub,
      queued_at: new Date().toISOString(),
      priority: 0,
    });
    return {
      status: "queued",
      action: "deploy",
      environment: env,
      script_id: scriptId,
      version,
      queue_position: position,
      reason: `Environment ${env} is locked (deploying ${existingLock?.script || "unknown"} since ${existingLock?.since || "unknown"}). Queued at position ${position}.`,
    };
  }

  // Fetch context from previous environment in promotion chain
  let previousEnvReceipt: DeploymentReceipt | undefined;
  try {
    const allEnvs = await getEnvironments(cfg);
    const sorted = allEnvs.filter(e => e.is_active).sort((a, b) => a.order - b.order);
    const currentIdx = sorted.findIndex(e => e.name === env);
    if (currentIdx > 0) {
      const prevEnv = sorted[currentIdx - 1]!;
      const prevReceipts = listReceipts(DEPLOYMENTS_DIR, prevEnv.name, 100);
      previousEnvReceipt = prevReceipts.find(
        r => r.script_id === scriptId && r.status === "success"
      );
    }
  } catch {
    // non-fatal — context passing is best-effort
  }

  // Update promotion to "deploying"
  await updatePromotionStatus(cfg, promotion.id, "deploying");

  emitDeploymentStarted(env, scriptId, agent.sub);

  const receiptId = buildReceiptId(scriptId, env);
  const timestampStart = new Date().toISOString();
  const phases: DeploymentReceipt["phases"] = {};

  try {
    const scriptPath = path.join(getScriptVersionDir(DEPLOYMENTS_DIR, scriptId, version), "deploy.sh");
    const secrets = loadSecrets(env);

    // Hash verification before execution
    if (!verifyScriptHash(DEPLOYMENTS_DIR, scriptId, version)) {
      throw new Error("Script hash verification failed — refusing to execute");
    }

    // Execute
    const execStart = Date.now();
    const execResult = await executeCommand(`bash '${scriptPath}'`, {
      timeout: scriptTimeout,
      env: {
        ...secrets,
        BOND_DEPLOY_ENV: env,
        SCRIPT_DIR: path.dirname(scriptPath),
        DEPLOY_RECEIPT_ID: receiptId,
      },
    });
    const execDuration = Date.now() - execStart;

    phases.execution = {
      status: execResult.exit_code === 0 ? "pass" : "fail",
      duration_ms: execDuration,
      exit_code: execResult.exit_code,
    };

    const success = execResult.exit_code === 0;
    const finalStatus = success ? "success" : "failed";

    // Write deploy log
    writeDeployLog(env, scriptId, version, agent.sub, execResult.stdout, execResult.stderr, execResult.exit_code, execDuration);

    // Write receipt
    const receipt: DeploymentReceipt = {
      receipt_id: receiptId,
      script_id: scriptId,
      script_version: version,
      script_sha256: manifest.sha256,
      environment: env,
      agent_id: agent.sub,
      timestamp_start: timestampStart,
      timestamp_end: new Date().toISOString(),
      duration_ms: Date.now() - new Date(timestampStart).getTime(),
      status: finalStatus,
      phases,
      rollback_triggered: false,
      bug_ticket_filed: false,
      context: {
        promoted_by: promotion.id,
        previous_environment_receipt: previousEnvReceipt?.receipt_id,
      },
    };
    if (!success) {
      receipt.error_output = `${execResult.stdout}\n${execResult.stderr}`.trim();
    }
    writeReceipt(DEPLOYMENTS_DIR, receipt);

    // Update promotion status
    await updatePromotionStatus(cfg, promotion.id, finalStatus, {
      deployed_at: Date.now(),
      receipt_id: receiptId,
    });

    if (success) {
      emitDeploymentSucceeded(env, scriptId, agent.sub, receiptId, execDuration);

      // Save drift baseline after successful deployment
      try {
        const healthStatus = await executeHealthCheck(env);
        saveBaseline(env, scriptId, version, healthStatus.results);
      } catch {
        // non-fatal — drift baseline is best-effort
      }
    } else {
      emitDeploymentFailed(env, scriptId, agent.sub, receiptId,
        `${execResult.stdout}\n${execResult.stderr}`.trim());
    }

    // Check queue for next entry
    const nextQueued = peek(env);

    // Build result with context from previous environment
    const result: DeployResult = {
      status: success ? "ok" : "error",
      action: "deploy",
      environment: env,
      script_id: scriptId,
      version,
      exit_code: execResult.exit_code,
      stdout: execResult.stdout,
      stderr: execResult.stderr,
      duration_ms: execDuration,
      receipt_id: receiptId,
      next_queued: nextQueued ? { script_id: nextQueued.script_id, version: nextQueued.version } : undefined,
    };
    if (previousEnvReceipt) {
      result.info = {
        context: {
          previous_environment: previousEnvReceipt.environment,
          previous_receipt_id: previousEnvReceipt.receipt_id,
          previous_status: previousEnvReceipt.status,
          previous_deployed_at: previousEnvReceipt.timestamp_end,
        },
      };
    }
    return result;
  } catch (err: any) {
    phases.execution = { status: "fail", duration_ms: 0, output_summary: err.message };

    const receipt: DeploymentReceipt = {
      receipt_id: receiptId,
      script_id: scriptId,
      script_version: version,
      script_sha256: manifest.sha256,
      environment: env,
      agent_id: agent.sub,
      timestamp_start: timestampStart,
      timestamp_end: new Date().toISOString(),
      duration_ms: Date.now() - new Date(timestampStart).getTime(),
      status: "failed",
      phases,
      rollback_triggered: false,
      bug_ticket_filed: false,
      error_output: err.message,
    };
    writeReceipt(DEPLOYMENTS_DIR, receipt);
    await updatePromotionStatus(cfg, promotion.id, "failed", { receipt_id: receiptId });

    emitDeploymentFailed(env, scriptId, agent.sub, receiptId, err.message);

    return {
      status: "error",
      action: "deploy",
      environment: env,
      script_id: scriptId,
      version,
      reason: err.message,
      receipt_id: receiptId,
    };
  } finally {
    releaseLock(DEPLOYMENTS_DIR, env);
  }
}

async function runRollback(
  cfg: GatewayConfig,
  agent: AgentTokenPayload,
  scriptId: string,
  version: string,
  env: string,
  envConfig: any,
  promotion: { id: string; status: string; sha256: string },
): Promise<DeployResult> {
  const manifest = getManifest(DEPLOYMENTS_DIR, scriptId, version);
  if (!manifest?.rollback) {
    return {
      status: "ok",
      action: "rollback",
      environment: env,
      script_id: scriptId,
      info: { message: "No rollback script configured" },
    };
  }

  const rollbackPath = path.join(
    getScriptVersionDir(DEPLOYMENTS_DIR, scriptId, version),
    manifest.rollback,
  );
  if (!fs.existsSync(rollbackPath)) {
    return { status: "error", action: "rollback", environment: env, reason: `Rollback script not found: ${manifest.rollback}` };
  }

  emitRollbackTriggered(env, scriptId, agent.sub);

  const secrets = loadSecrets(env);
  const timeout = Math.min(manifest.timeout ?? 120, Number(envConfig.max_script_timeout) || 600);

  const start = Date.now();
  const result = await executeCommand(`bash '${rollbackPath}'`, {
    timeout,
    env: { ...secrets, BOND_DEPLOY_ENV: env },
  });
  const duration_ms = Date.now() - start;

  if (result.exit_code === 0) {
    await updatePromotionStatus(cfg, promotion.id, "rolled_back");
  }

  const receiptId = buildReceiptId(`${scriptId}-rollback`, env);
  const receipt: DeploymentReceipt = {
    receipt_id: receiptId,
    script_id: scriptId,
    script_version: version,
    script_sha256: manifest.sha256,
    environment: env,
    agent_id: agent.sub,
    timestamp_start: new Date(Date.now() - duration_ms).toISOString(),
    timestamp_end: new Date().toISOString(),
    duration_ms,
    status: result.exit_code === 0 ? "rolled_back" : "failed",
    phases: { rollback: { status: result.exit_code === 0 ? "pass" : "fail", duration_ms, exit_code: result.exit_code } },
    rollback_triggered: true,
    bug_ticket_filed: false,
  };
  writeReceipt(DEPLOYMENTS_DIR, receipt);

  return {
    status: result.exit_code === 0 ? "ok" : "error",
    action: "rollback",
    environment: env,
    script_id: scriptId,
    version,
    exit_code: result.exit_code,
    stdout: result.stdout,
    stderr: result.stderr,
    duration_ms,
    receipt_id: receiptId,
  };
}

async function runHealthCheck(env: string): Promise<DeployResult> {
  const commonCheck = path.join(DEPLOYMENTS_DIR, "health", "common", "check.sh");
  const envCheck = path.join(DEPLOYMENTS_DIR, "health", env, "check.sh");

  const scripts: string[] = [];
  if (fs.existsSync(commonCheck)) scripts.push(commonCheck);
  if (fs.existsSync(envCheck)) scripts.push(envCheck);

  if (scripts.length === 0) {
    return {
      status: "ok",
      action: "health-check",
      environment: env,
      info: {
        status: "healthy",
        message: "No health check scripts configured",
        checks: [],
      },
    };
  }

  const secrets = loadSecrets(env);
  const results: Array<{ script: string; exit_code: number; output: any; duration_ms: number }> = [];
  let allHealthy = true;

  for (const script of scripts) {
    const start = Date.now();
    const result = await executeCommand(`bash '${script}'`, {
      timeout: 30,
      env: { ...secrets, BOND_DEPLOY_ENV: env },
    });
    const duration_ms = Date.now() - start;

    let output: any = result.stdout;
    try { output = JSON.parse(result.stdout); } catch { /* keep raw */ }

    results.push({ script, exit_code: result.exit_code, output, duration_ms });
    if (result.exit_code !== 0) allHealthy = false;
  }

  return {
    status: allHealthy ? "ok" : "error",
    action: "health-check",
    environment: env,
    info: {
      status: allHealthy ? "healthy" : "unhealthy",
      checks: results,
    },
  };
}
