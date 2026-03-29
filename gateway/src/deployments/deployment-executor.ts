/**
 * Deployment Executor — executes a DeploymentPlan step-by-step over SSH.
 */

import { spawn } from "node:child_process";
import http from "node:http";
import https from "node:https";
import { emitDeploymentEvent } from "./events.js";
import type { DeploymentPlan, DeploymentStep } from "./deployment-planner.js";

// ── Types ────────────────────────────────────────────────────────────────────

export interface StepResult {
  step_id: string;
  status: "success" | "failed" | "skipped";
  output: string;
  error?: string;
  duration_ms: number;
}

export interface ExecutionResult {
  plan_id: string;
  status: "success" | "failed" | "partial";
  steps: StepResult[];
  total_duration_ms: number;
}

export interface SshConfig {
  host: string;
  port?: number;
  username: string;
  privateKeyPath?: string;
}

export interface ExecutionOptions {
  onStepStart?: (step: DeploymentStep) => void;
  onStepComplete?: (step: DeploymentStep, result: StepResult) => void;
  onLog?: (stepId: string, line: string) => void;
  dryRun?: boolean;
}

// ── Public API ───────────────────────────────────────────────────────────────

export async function executeDeploymentPlan(
  plan: DeploymentPlan,
  sshConfig: SshConfig,
  options: ExecutionOptions = {},
): Promise<ExecutionResult> {
  const startTime = Date.now();
  const results: StepResult[] = [];

  emitDeploymentEvent("deployment_started", {
    environment: "deploy",
    summary: `Executing plan ${plan.id} for ${plan.app_name} on ${plan.target_server}`,
    details: { plan_id: plan.id, step_count: plan.steps.length },
  });

  for (const step of plan.steps) {
    // Check dependencies
    if (step.depends_on?.length) {
      const depsFailed = step.depends_on.some(
        depId => results.find(r => r.step_id === depId)?.status === "failed",
      );
      if (depsFailed) {
        const skipped: StepResult = { step_id: step.id, status: "skipped", output: "Skipped: dependency failed", duration_ms: 0 };
        results.push(skipped);
        options.onStepComplete?.(step, skipped);
        continue;
      }
    }

    options.onStepStart?.(step);
    emitDeploymentEvent("deployment_started", {
      environment: "deploy",
      summary: `Step ${step.id}: ${step.description}`,
      details: { plan_id: plan.id, step_id: step.id, action: step.action },
    });

    const result = await executeStep(step, sshConfig, options);
    results.push(result);
    options.onStepComplete?.(step, result);

    if (result.status === "failed") {
      emitDeploymentEvent("deployment_failed", {
        environment: "deploy",
        summary: `Step ${step.id} failed: ${result.error}`,
        details: { plan_id: plan.id, step_id: step.id, error: result.error },
      });
      return {
        plan_id: plan.id,
        status: results.length < plan.steps.length ? "partial" : "failed",
        steps: results,
        total_duration_ms: Date.now() - startTime,
      };
    }
  }

  emitDeploymentEvent("deployment_succeeded", {
    environment: "deploy",
    summary: `Plan ${plan.id} completed successfully`,
    details: { plan_id: plan.id, duration_ms: Date.now() - startTime },
  });

  return {
    plan_id: plan.id,
    status: "success",
    steps: results,
    total_duration_ms: Date.now() - startTime,
  };
}

// ── Step Execution ───────────────────────────────────────────────────────────

async function executeStep(
  step: DeploymentStep,
  sshConfig: SshConfig,
  options: ExecutionOptions,
): Promise<StepResult> {
  const startTime = Date.now();

  if (options.dryRun) {
    const output = `[dry-run] Would execute ${step.action}: ${step.description}`;
    options.onLog?.(step.id, output);
    return { step_id: step.id, status: "success", output, duration_ms: Date.now() - startTime };
  }

  let lastErr: string | undefined;
  for (let attempt = 0; attempt <= step.retry_count; attempt++) {
    try {
      const output = await executeAction(step, sshConfig, options);
      return { step_id: step.id, status: "success", output, duration_ms: Date.now() - startTime };
    } catch (err: any) {
      lastErr = err.message || String(err);
      if (attempt < step.retry_count) {
        options.onLog?.(step.id, `Retry ${attempt + 1}/${step.retry_count}: ${lastErr}`);
      }
    }
  }

  return { step_id: step.id, status: "failed", output: "", error: lastErr, duration_ms: Date.now() - startTime };
}

async function executeAction(
  step: DeploymentStep,
  sshConfig: SshConfig,
  options: ExecutionOptions,
): Promise<string> {
  switch (step.action) {
    case "build_local":
      return runLocal(step.params.command, step.id, step.timeout_ms, options, step.params.cwd);
    case "transfer_files":
      return transferFiles(step.params, sshConfig, step.id, step.timeout_ms, options);
    case "remote_exec":
      return runRemote(step.params.command, sshConfig, step.id, step.timeout_ms, options, step.params.cwd);
    case "docker_build_remote":
      return dockerBuildRemote(step.params, sshConfig, step.id, step.timeout_ms, options);
    case "docker_compose_up":
      return dockerComposeUp(step.params, sshConfig, step.id, step.timeout_ms, options);
    case "health_check":
      return healthCheck(step.params, step.id, step.timeout_ms, options);
    case "setup_env":
      return setupEnv(step.params, sshConfig, step.id, step.timeout_ms, options);
    default:
      throw new Error(`Unknown action: ${step.action}`);
  }
}

// ── Action Implementations ───────────────────────────────────────────────────

function buildSshArgs(sshConfig: SshConfig): string[] {
  const args = ["-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"];
  if (sshConfig.privateKeyPath) args.push("-i", sshConfig.privateKeyPath);
  if (sshConfig.port && sshConfig.port !== 22) args.push("-p", String(sshConfig.port));
  return args;
}

function sshTarget(sshConfig: SshConfig): string {
  return `${sshConfig.username}@${sshConfig.host}`;
}

function runSpawn(
  cmd: string,
  args: string[],
  stepId: string,
  timeoutMs: number,
  options: ExecutionOptions,
  cwd?: string,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args, { cwd, shell: true, timeout: timeoutMs });
    let stdout = "";
    let stderr = "";

    proc.stdout?.on("data", (chunk: Buffer) => {
      const line = chunk.toString();
      stdout += line;
      options.onLog?.(stepId, line);
    });
    proc.stderr?.on("data", (chunk: Buffer) => {
      const line = chunk.toString();
      stderr += line;
      options.onLog?.(stepId, line);
    });
    proc.on("error", (err) => reject(err));
    proc.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`Exit code ${code}: ${stderr.slice(0, 500)}`));
    });
  });
}

function runLocal(
  command: string,
  stepId: string,
  timeoutMs: number,
  options: ExecutionOptions,
  cwd?: string,
): Promise<string> {
  options.onLog?.(stepId, `[local] ${command}`);
  return runSpawn(command, [], stepId, timeoutMs, options, cwd);
}

function runRemote(
  command: string,
  sshConfig: SshConfig,
  stepId: string,
  timeoutMs: number,
  options: ExecutionOptions,
  cwd?: string,
): Promise<string> {
  const remoteCmd = cwd ? `cd ${cwd} && ${command}` : command;
  options.onLog?.(stepId, `[remote] ${remoteCmd}`);
  const args = [...buildSshArgs(sshConfig), sshTarget(sshConfig), remoteCmd];
  return runSpawn("ssh", args, stepId, timeoutMs, options);
}

function transferFiles(
  params: Record<string, any>,
  sshConfig: SshConfig,
  stepId: string,
  timeoutMs: number,
  options: ExecutionOptions,
): Promise<string> {
  const { local_path, remote_path, exclude } = params;
  options.onLog?.(stepId, `[transfer] ${local_path} → ${sshTarget(sshConfig)}:${remote_path}`);

  const args = ["-avz", "--delete"];
  if (sshConfig.privateKeyPath || (sshConfig.port && sshConfig.port !== 22)) {
    const sshCmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"];
    if (sshConfig.privateKeyPath) sshCmd.push("-i", sshConfig.privateKeyPath);
    if (sshConfig.port && sshConfig.port !== 22) sshCmd.push("-p", String(sshConfig.port));
    args.push("-e", sshCmd.join(" "));
  }
  if (Array.isArray(exclude)) {
    for (const ex of exclude) args.push("--exclude", ex);
  }
  args.push(local_path, `${sshTarget(sshConfig)}:${remote_path}`);

  return runSpawn("rsync", args, stepId, timeoutMs, options);
}

function dockerBuildRemote(
  params: Record<string, any>,
  sshConfig: SshConfig,
  stepId: string,
  timeoutMs: number,
  options: ExecutionOptions,
): Promise<string> {
  const { context_path, image_tag, remote_path } = params;
  // First transfer the context, then build on remote
  const transferPromise = transferFiles(
    { local_path: context_path, remote_path: remote_path || "/tmp/docker-build" },
    sshConfig, stepId, timeoutMs, options,
  );
  return transferPromise.then(() =>
    runRemote(
      `cd ${remote_path || "/tmp/docker-build"} && docker build -t ${image_tag} .`,
      sshConfig, stepId, timeoutMs, options,
    ),
  );
}

function dockerComposeUp(
  params: Record<string, any>,
  sshConfig: SshConfig,
  stepId: string,
  timeoutMs: number,
  options: ExecutionOptions,
): Promise<string> {
  const { compose_file, remote_path } = params;
  // Transfer compose file, then run docker compose up
  return transferFiles(
    { local_path: compose_file, remote_path: remote_path || "/opt/app" },
    sshConfig, stepId, timeoutMs, options,
  ).then(() =>
    runRemote(
      `cd ${remote_path || "/opt/app"} && docker compose up -d`,
      sshConfig, stepId, timeoutMs, options,
    ),
  );
}

function healthCheck(
  params: Record<string, any>,
  stepId: string,
  timeoutMs: number,
  options: ExecutionOptions,
): Promise<string> {
  const { url, expected_status = 200, retries = 3 } = params;
  options.onLog?.(stepId, `[health] GET ${url} (expect ${expected_status})`);

  const perAttemptTimeout = Math.floor(timeoutMs / (retries + 1));
  let attempt = 0;

  const tryOnce = (): Promise<string> =>
    new Promise((resolve, reject) => {
      const client = url.startsWith("https") ? https : http;
      const req = client.get(url, { timeout: perAttemptTimeout }, (res) => {
        const status = res.statusCode || 0;
        res.resume(); // drain
        if (status === expected_status) {
          resolve(`Health check passed: ${status}`);
        } else {
          reject(new Error(`Health check returned ${status}, expected ${expected_status}`));
        }
      });
      req.on("error", reject);
      req.on("timeout", () => { req.destroy(); reject(new Error("Health check timed out")); });
    });

  const retry = (): Promise<string> =>
    tryOnce().catch((err) => {
      attempt++;
      if (attempt <= retries) {
        options.onLog?.(stepId, `[health] Retry ${attempt}/${retries}: ${err.message}`);
        return new Promise(r => setTimeout(r, 2000)).then(retry);
      }
      throw err;
    });

  return retry();
}

function setupEnv(
  params: Record<string, any>,
  sshConfig: SshConfig,
  stepId: string,
  timeoutMs: number,
  options: ExecutionOptions,
): Promise<string> {
  const { remote_path, vars } = params;
  const envContent = Object.entries(vars || {})
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
  // Write .env file on remote via SSH
  const escapedContent = envContent.replace(/'/g, "'\\''");
  const command = `mkdir -p ${remote_path} && printf '%s\\n' '${escapedContent}' > ${remote_path}/.env`;
  return runRemote(command, sshConfig, stepId, timeoutMs, options);
}
