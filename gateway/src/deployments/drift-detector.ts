/**
 * Drift Detection — compares current environment state against a
 * baseline snapshot taken after each successful deployment.
 *
 * Design Doc 039 §10.4 — Drift Detection
 */

import fs from "node:fs";
import path from "node:path";
import { homedir } from "node:os";
import { executeHealthCheck, type HealthStatus, type HealthCheckResult } from "./health-scheduler.js";
import type { GatewayConfig } from "../config/index.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

export interface DriftBaseline {
  script_id: string;
  script_version: string;
  timestamp: string;
  health_results: HealthCheckResult[];
}

export interface DriftResult {
  environment: string;
  has_drift: boolean;
  baseline_timestamp: string | null;
  changes: DriftChange[];
}

export interface DriftChange {
  type: "new_failure" | "resolved_failure" | "output_changed" | "script_missing" | "script_added";
  script: string;
  baseline_value?: any;
  current_value?: any;
}

function getBaselinePath(env: string): string {
  return path.join(DEPLOYMENTS_DIR, "health", env, "baseline.json");
}

/**
 * Save a baseline snapshot after a successful deployment.
 */
export function saveBaseline(
  env: string,
  scriptId: string,
  scriptVersion: string,
  healthResults: HealthCheckResult[],
): void {
  const baseline: DriftBaseline = {
    script_id: scriptId,
    script_version: scriptVersion,
    timestamp: new Date().toISOString(),
    health_results: healthResults,
  };

  const dir = path.join(DEPLOYMENTS_DIR, "health", env);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(getBaselinePath(env), JSON.stringify(baseline, null, 2), "utf8");
}

/**
 * Load the baseline snapshot for an environment.
 */
export function loadBaseline(env: string): DriftBaseline | null {
  const baselinePath = getBaselinePath(env);
  if (!fs.existsSync(baselinePath)) return null;
  try {
    return JSON.parse(fs.readFileSync(baselinePath, "utf8"));
  } catch {
    return null;
  }
}

/**
 * Compare current health check results against the baseline.
 */
export function compareDrift(
  env: string,
  currentResults: HealthCheckResult[],
): DriftResult {
  const baseline = loadBaseline(env);

  if (!baseline) {
    return {
      environment: env,
      has_drift: false,
      baseline_timestamp: null,
      changes: [],
    };
  }

  const changes: DriftChange[] = [];
  const baselineMap = new Map(baseline.health_results.map(r => [r.script, r]));
  const currentMap = new Map(currentResults.map(r => [r.script, r]));

  // Check each baseline script against current
  for (const [script, baseResult] of baselineMap) {
    const curResult = currentMap.get(script);
    if (!curResult) {
      changes.push({ type: "script_missing", script });
      continue;
    }

    if (baseResult.exit_code === 0 && curResult.exit_code !== 0) {
      changes.push({
        type: "new_failure",
        script,
        baseline_value: { exit_code: baseResult.exit_code },
        current_value: { exit_code: curResult.exit_code, output: curResult.output },
      });
    } else if (baseResult.exit_code !== 0 && curResult.exit_code === 0) {
      changes.push({
        type: "resolved_failure",
        script,
        baseline_value: { exit_code: baseResult.exit_code },
        current_value: { exit_code: curResult.exit_code },
      });
    } else {
      // Compare output (stringify for deep comparison)
      const baseOut = JSON.stringify(baseResult.output);
      const curOut = JSON.stringify(curResult.output);
      if (baseOut !== curOut) {
        changes.push({
          type: "output_changed",
          script,
          baseline_value: baseResult.output,
          current_value: curResult.output,
        });
      }
    }
  }

  // Check for new scripts not in baseline
  for (const script of currentMap.keys()) {
    if (!baselineMap.has(script)) {
      changes.push({ type: "script_added", script });
    }
  }

  return {
    environment: env,
    has_drift: changes.length > 0,
    baseline_timestamp: baseline.timestamp,
    changes,
  };
}

/**
 * Run a full drift check: execute health checks and compare against baseline.
 * Called from health-scheduler after each health check cycle.
 */
export async function runDriftCheck(
  env: string,
  _config: GatewayConfig,
): Promise<DriftResult> {
  const healthStatus = await executeHealthCheck(env);
  return compareDrift(env, healthStatus.results);
}
