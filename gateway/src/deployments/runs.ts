// gateway/src/deployments/runs.ts
// Deployment run tracking — bridges quick-deploy to receipts with live status

import { EventEmitter } from "events";
import { ulid } from "ulid";

export type RunStatus = "queued" | "running" | "success" | "failed" | "cancelled";
export type RunType = "deploy" | "rollback" | "health-check";

export interface DeploymentRun {
  id: string;
  script_id: string;
  script_version: string;
  environment: string;
  resource_id?: string;
  status: RunStatus;
  started_at: string;
  finished_at?: string;
  triggered_by: string;
  run_type: RunType;
  log_lines: string[];
  receipt_id?: string;
  plan?: Record<string, unknown>;
}

const runs = new Map<string, DeploymentRun>();
const runEmitters = new Map<string, EventEmitter>();

export function createRun(opts: {
  script_id: string;
  script_version: string;
  environment: string;
  resource_id?: string;
  triggered_by: string;
  run_type: RunType;
  plan?: Record<string, unknown>;
}): DeploymentRun {
  const id = ulid();
  const run: DeploymentRun = {
    id,
    script_id: opts.script_id,
    script_version: opts.script_version,
    environment: opts.environment,
    resource_id: opts.resource_id,
    status: "queued",
    started_at: new Date().toISOString(),
    triggered_by: opts.triggered_by,
    run_type: opts.run_type,
    log_lines: [],
    plan: opts.plan,
  };
  runs.set(id, run);
  runEmitters.set(id, new EventEmitter());
  return run;
}

export function getRun(id: string): DeploymentRun | undefined {
  return runs.get(id);
}

export function listRuns(env?: string, limit = 50): DeploymentRun[] {
  let all = Array.from(runs.values());
  if (env) all = all.filter((r) => r.environment === env);
  return all.sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, limit);
}

export function updateRunStatus(id: string, status: RunStatus): void {
  const run = runs.get(id);
  if (!run) return;
  run.status = status;
  if (status === "success" || status === "failed" || status === "cancelled") {
    run.finished_at = new Date().toISOString();
  }
  emitEvent(id, "status", { status, finished_at: run.finished_at });
}

export function appendRunLog(id: string, line: string): void {
  const run = runs.get(id);
  if (!run) return;
  run.log_lines.push(line);
  emitEvent(id, "log", { line });
}

export function getRunEmitter(id: string): EventEmitter | undefined {
  return runEmitters.get(id);
}

function emitEvent(id: string, event: string, data: unknown): void {
  const emitter = runEmitters.get(id);
  if (emitter) emitter.emit(event, data);
}

/**
 * Execute a deployment run. Platform-specific deployers must be wired in
 * before this function will do real work.
 */
export async function executeDeploymentRun(
  run: DeploymentRun,
  deploymentsDir: string,
  config: any
): Promise<void> {
  updateRunStatus(run.id, "running");
  const errorMsg = "Deployment execution not yet implemented — platform-specific deployers required";
  appendRunLog(run.id, `[${new Date().toISOString()}] ✗ Error: ${errorMsg}`);
  updateRunStatus(run.id, "failed");
  emitEvent(run.id, "done", { status: "failed", error: errorMsg });
  throw new Error(errorMsg);
}
