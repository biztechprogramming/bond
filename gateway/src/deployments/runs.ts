// gateway/src/deployments/runs.ts
// Deployment run tracking — bridges quick-deploy to receipts with live status

import { EventEmitter } from "events";
import { ulid } from "ulid";
import { writeReceipt, DeploymentReceipt } from "./receipts.js";
import * as fs from "fs";
import * as path from "path";

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

// Simulate a deployment execution with realistic steps
export async function executeDeploymentRun(
  run: DeploymentRun,
  deploymentsDir: string,
  config: any
): Promise<void> {
  updateRunStatus(run.id, "running");

  const steps = [
    { name: "validate", label: "Validating configuration", duration: 800 },
    { name: "build", label: "Building application", duration: 2000 },
    { name: "push", label: "Pushing artifacts", duration: 1500 },
    { name: "deploy", label: "Deploying to environment", duration: 2000 },
    { name: "health", label: "Running health checks", duration: 1000 },
    { name: "monitor", label: "Setting up monitoring", duration: 500 },
  ];

  try {
    for (const step of steps) {
      emitEvent(run.id, "step", { step: step.name, status: "running", detail: step.label });
      appendRunLog(run.id, `[${new Date().toISOString()}] ${step.label}...`);

      // Simulate step execution
      await new Promise((resolve) => setTimeout(resolve, step.duration));

      // Check for cancellation
      const current = runs.get(run.id);
      if (current?.status === "cancelled") {
        appendRunLog(run.id, `[${new Date().toISOString()}] Deployment cancelled.`);
        emitEvent(run.id, "step", { step: step.name, status: "cancelled" });
        emitEvent(run.id, "done", { status: "cancelled" });
        return;
      }

      appendRunLog(run.id, `[${new Date().toISOString()}] ✓ ${step.label} complete`);
      emitEvent(run.id, "step", { step: step.name, status: "done", detail: `${step.label} complete` });
    }

    updateRunStatus(run.id, "success");

    // Write receipt
    const receipt: DeploymentReceipt = {
      receipt_id: `${run.script_id}-${run.environment}-${Date.now()}`,
      type: run.run_type === "rollback" ? "manual_intervention" : "deployment",
      script_id: run.script_id,
      script_version: run.script_version,
      environment: run.environment,
      agent_id: run.triggered_by,
      timestamp_start: run.started_at,
      timestamp_end: run.finished_at || new Date().toISOString(),
      duration_ms: Date.now() - new Date(run.started_at).getTime(),
      status: "success",
      rollback_triggered: false,
      bug_ticket_filed: false,
    };
    try {
      writeReceipt(deploymentsDir, receipt);
      run.receipt_id = receipt.receipt_id;
    } catch (_) {
      // Receipt write failure is non-fatal
    }

    emitEvent(run.id, "done", { status: "success", receipt_id: receipt.receipt_id });
  } catch (err: any) {
    appendRunLog(run.id, `[${new Date().toISOString()}] ✗ Error: ${err.message}`);
    updateRunStatus(run.id, "failed");
    emitEvent(run.id, "done", { status: "failed", error: err.message });
  }
}
