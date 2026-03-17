/**
 * Periodic Health Check Scheduler — runs health check scripts on a
 * per-environment interval and stores last known status.
 *
 * Design Doc 039 §14 — Health Checks
 */

import fs from "node:fs";
import path from "node:path";
import { homedir } from "node:os";
import { executeCommand } from "../broker/executor.js";
import { emitHealthCheckFailed, emitDeploymentEvent } from "./events.js";
import type { GatewayConfig } from "../config/index.js";
import { getEnvironments } from "./stdb.js";
import { compareDrift } from "./drift-detector.js";
import { loadSecrets } from "./secrets.js";
import { runMonitoringCycle } from "./monitoring.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

export interface HealthStatus {
  environment: string;
  status: "healthy" | "unhealthy" | "unknown";
  last_check: string;
  results: HealthCheckResult[];
}

export interface HealthCheckResult {
  script: string;
  exit_code: number;
  output: any; // parsed JSON or raw string
  duration_ms: number;
}

const healthStatuses = new Map<string, HealthStatus>();
const intervals = new Map<string, ReturnType<typeof setInterval>>();

/**
 * Execute health check scripts for an environment and return results.
 */
export async function executeHealthCheck(env: string): Promise<HealthStatus> {
  const commonCheck = path.join(DEPLOYMENTS_DIR, "health", "common", "check.sh");
  const envCheck = path.join(DEPLOYMENTS_DIR, "health", env, "check.sh");

  const scripts: string[] = [];
  if (fs.existsSync(commonCheck)) scripts.push(commonCheck);
  if (fs.existsSync(envCheck)) scripts.push(envCheck);

  if (scripts.length === 0) {
    const status: HealthStatus = {
      environment: env,
      status: "healthy",
      last_check: new Date().toISOString(),
      results: [],
    };
    healthStatuses.set(env, status);
    return status;
  }

  // Load secrets for the environment (supports encryption)
  const secrets = loadSecrets(env);

  const results: HealthCheckResult[] = [];
  let allHealthy = true;

  for (const script of scripts) {
    const start = Date.now();
    const result = await executeCommand(`bash '${script}'`, {
      timeout: 30,
      env: { ...secrets, BOND_DEPLOY_ENV: env },
    });
    const duration_ms = Date.now() - start;

    let output: any = result.stdout;
    try {
      output = JSON.parse(result.stdout);
    } catch { /* keep as string */ }

    results.push({
      script,
      exit_code: result.exit_code,
      output,
      duration_ms,
    });

    if (result.exit_code !== 0) allHealthy = false;
  }

  const status: HealthStatus = {
    environment: env,
    status: allHealthy ? "healthy" : "unhealthy",
    last_check: new Date().toISOString(),
    results,
  };

  healthStatuses.set(env, status);
  return status;
}

/**
 * Start periodic health checks for all active environments.
 */
export async function startHealthScheduler(config: GatewayConfig): Promise<void> {
  try {
    const envs = await getEnvironments(config);
    for (const env of envs) {
      if (!env.is_active) continue;

      const intervalMs = (env.health_check_interval || 300) * 1000;
      console.log(`[health-scheduler] Starting checks for '${env.name}' every ${env.health_check_interval || 300}s`);

      // Run immediately, then on interval
      runCheck(env.name, config);

      const id = setInterval(() => runCheck(env.name, config), intervalMs);
      intervals.set(env.name, id);
    }
  } catch (err: any) {
    console.error("[health-scheduler] Failed to start:", err.message);
  }
}

async function runCheck(env: string, _config: GatewayConfig): Promise<void> {
  try {
    const status = await executeHealthCheck(env);
    if (status.status === "unhealthy") {
      emitHealthCheckFailed(env, "health-scheduler", {
        results: status.results,
        last_check: status.last_check,
      });
    }

    // Run drift detection after health check
    try {
      const drift = compareDrift(env, status.results);
      if (drift.has_drift) {
        emitDeploymentEvent("drift_detected", {
          environment: env,
          agent_id: "health-scheduler",
          summary: `Drift detected in ${env}: ${drift.changes.length} change(s)`,
          details: { changes: drift.changes, baseline_timestamp: drift.baseline_timestamp },
        });
      }
    } catch (driftErr: any) {
      console.warn(`[health-scheduler] Drift check error for '${env}':`, driftErr.message);
    }

    // Run monitoring cycle if configured
    try {
      await runMonitoringCycle(env, _config);
    } catch (monErr: any) {
      console.warn(`[health-scheduler] Monitoring cycle error for '${env}':`, monErr.message);
    }
  } catch (err: any) {
    console.error(`[health-scheduler] Error checking '${env}':`, err.message);
    emitHealthCheckFailed(env, "health-scheduler", { error: err.message });
  }
}

/**
 * Get last known health status for an environment.
 */
export function getHealthStatus(env: string): HealthStatus | null {
  return healthStatuses.get(env) ?? null;
}

/**
 * Stop all health check intervals (for graceful shutdown).
 */
export function stopHealthScheduler(): void {
  for (const [env, id] of intervals) {
    clearInterval(id);
    console.log(`[health-scheduler] Stopped checks for '${env}'`);
  }
  intervals.clear();
}
