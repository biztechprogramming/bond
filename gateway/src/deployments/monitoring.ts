/**
 * Monitoring Cycle Orchestration — runs periodic checks on deployment environments.
 *
 * Design Doc 044 §7 — Continuous Monitoring
 */

import { executeHealthCheck } from "./health-scheduler.js";
import { compareDrift } from "./drift-detector.js";
import { getResources } from "./resources.js";
import { collectLogs } from "./log-collector.js";
import { computeFingerprint, shouldFileIssue } from "./issue-dedup.js";
import { emitDeploymentEvent } from "./events.js";
import { getEnvironment } from "./stdb.js";
import type { GatewayConfig } from "../config/index.js";

// ── Types ───────────────────────────────────────────────────────────────────

export interface MonitoringConfig {
  monitor_health_checks?: boolean;
  monitor_logs?: boolean;
  monitor_resource_usage?: boolean;
  monitor_drift?: boolean;
  auto_file_issues?: boolean;
  issue_repo?: string;
  issue_dedup_window_hours?: number;
}

export interface MonitoringCycleResult {
  environment: string;
  timestamp: string;
  health_status?: string;
  log_errors?: number;
  drift_detected?: boolean;
  alerts_emitted: number;
}

// ── Monitoring Cycle ────────────────────────────────────────────────────────

/**
 * Run a full monitoring cycle for an environment.
 */
export async function runMonitoringCycle(
  env: string,
  config: GatewayConfig,
  monitoringConfig?: MonitoringConfig,
): Promise<MonitoringCycleResult> {
  const mc = monitoringConfig || await loadMonitoringConfig(env, config);
  const result: MonitoringCycleResult = {
    environment: env,
    timestamp: new Date().toISOString(),
    alerts_emitted: 0,
  };

  // 1. Health checks
  if (mc.monitor_health_checks !== false) {
    try {
      const health = await executeHealthCheck(env);
      result.health_status = health.status;
      if (health.status === "unhealthy") {
        emitDeploymentEvent("monitoring_alert" as any, {
          environment: env,
          summary: `Health check failed in ${env}`,
          details: { category: "health-check-failure", results: health.results },
        });
        result.alerts_emitted++;

        if (mc.auto_file_issues && mc.issue_repo) {
          const fp = computeFingerprint(env, "health-check-failure", "health", "Health check failed");
          const dedup = await shouldFileIssue(fp, mc.issue_repo, mc.issue_dedup_window_hours);
          if (dedup.action === "create" || dedup.action === "comment") {
            result.alerts_emitted++;
          }
        }
      }
    } catch (err: any) {
      console.error(`[monitoring] Health check error for '${env}':`, err.message);
    }
  }

  // 2. Log monitoring
  if (mc.monitor_logs !== false) {
    try {
      const resources = await getResources(config, env);
      let totalErrors = 0;
      for (const resource of resources) {
        const logs = await collectLogs(config, resource.id, env, 5);
        if (logs.info?.log_sources) {
          for (const src of logs.info.log_sources) {
            totalErrors += src.error_count;
          }
        }
      }
      result.log_errors = totalErrors;

      if (totalErrors > 0) {
        emitDeploymentEvent("monitoring_alert" as any, {
          environment: env,
          summary: `${totalErrors} log error(s) detected in ${env}`,
          details: { category: "log-error", error_count: totalErrors },
        });
        result.alerts_emitted++;
      }
    } catch (err: any) {
      console.error(`[monitoring] Log check error for '${env}':`, err.message);
    }
  }

  // 3. Drift detection
  if (mc.monitor_drift !== false) {
    try {
      const health = await executeHealthCheck(env);
      const drift = compareDrift(env, health.results);
      result.drift_detected = drift.has_drift;
      if (drift.has_drift) {
        emitDeploymentEvent("drift_detected", {
          environment: env,
          summary: `Drift detected in ${env}: ${drift.changes.length} change(s)`,
          details: { changes: drift.changes },
        });
        result.alerts_emitted++;
      }
    } catch (err: any) {
      console.error(`[monitoring] Drift check error for '${env}':`, err.message);
    }
  }

  return result;
}

async function loadMonitoringConfig(env: string, config: GatewayConfig): Promise<MonitoringConfig> {
  try {
    const envConfig = await getEnvironment(config, env);
    if (!envConfig) return {};
    return envConfig as any;
  } catch {
    return {};
  }
}
