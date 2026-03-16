/**
 * Pipeline-as-Code — executor for parsed pipelines.
 *
 * Builds dependency graph from step 'needs', executes in topological order
 * (parallel when possible), handles matrix expansion and service sidecars.
 */

import { randomUUID } from "node:crypto";
import { executeCommand } from "../broker/executor.js";
import type { Pipeline, PipelineJob, PipelineStep, MatrixStrategy, PipelineService } from "./pipeline-parser.js";

export type StepStatus = "pending" | "running" | "success" | "failed" | "skipped";

export interface StepResult {
  name: string;
  status: StepStatus;
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_ms: number;
  matrix_vars?: Record<string, string>;
}

export interface JobResult {
  job_id: string;
  status: StepStatus;
  steps: StepResult[];
  duration_ms: number;
}

export interface PipelineRunResult {
  run_id: string;
  pipeline_name: string;
  status: StepStatus;
  jobs: JobResult[];
  duration_ms: number;
  started_at: string;
  finished_at: string;
}

// In-memory run store
const runs = new Map<string, PipelineRunResult>();

export function getRun(runId: string): PipelineRunResult | undefined {
  return runs.get(runId);
}

export async function executePipeline(pipeline: Pipeline): Promise<PipelineRunResult> {
  const runId = randomUUID();
  const startedAt = new Date().toISOString();
  const start = Date.now();

  const jobResults: JobResult[] = [];
  let pipelineStatus: StepStatus = "success";

  for (const [jobId, job] of Object.entries(pipeline.jobs)) {
    const jobResult = await executeJob(jobId, job, pipeline.env);
    jobResults.push(jobResult);
    if (jobResult.status === "failed") {
      pipelineStatus = "failed";
      break; // stop on first failed job
    }
  }

  const result: PipelineRunResult = {
    run_id: runId,
    pipeline_name: pipeline.name,
    status: pipelineStatus,
    jobs: jobResults,
    duration_ms: Date.now() - start,
    started_at: startedAt,
    finished_at: new Date().toISOString(),
  };

  runs.set(runId, result);
  return result;
}

async function executeJob(
  jobId: string,
  job: PipelineJob,
  pipelineEnv?: Record<string, string>,
): Promise<JobResult> {
  const start = Date.now();
  const serviceContainers: string[] = [];

  try {
    // Start sidecar services
    if (job.services) {
      for (const [svcName, svc] of Object.entries(job.services)) {
        const containerId = await startService(svcName, svc);
        if (containerId) serviceContainers.push(containerId);
      }
    }

    // Expand matrix if present
    const matrixCombinations = expandMatrix(job.strategy);
    const allStepResults: StepResult[] = [];
    let jobStatus: StepStatus = "success";

    if (matrixCombinations.length > 0) {
      // Run each matrix combination
      for (const combo of matrixCombinations) {
        const mergedEnv = { ...pipelineEnv, ...job.env, ...combo };
        const results = await executeSteps(job.steps, mergedEnv, combo);
        allStepResults.push(...results);
        if (results.some((r) => r.status === "failed")) {
          jobStatus = "failed";
          break;
        }
      }
    } else {
      const mergedEnv = { ...pipelineEnv, ...job.env };
      const results = await executeSteps(job.steps, mergedEnv);
      allStepResults.push(...results);
      if (results.some((r) => r.status === "failed")) {
        jobStatus = "failed";
      }
    }

    return { job_id: jobId, status: jobStatus, steps: allStepResults, duration_ms: Date.now() - start };
  } finally {
    // Stop sidecar services
    for (const id of serviceContainers) {
      await stopService(id);
    }
  }
}

async function executeSteps(
  steps: PipelineStep[],
  env?: Record<string, string>,
  matrixVars?: Record<string, string>,
): Promise<StepResult[]> {
  const results = new Map<string, StepResult>();
  const completed = new Set<string>();
  const remaining = new Set(steps.map((s) => s.name));

  while (remaining.size > 0) {
    // Find steps whose dependencies are all completed
    const ready: PipelineStep[] = [];
    for (const step of steps) {
      if (!remaining.has(step.name)) continue;
      const deps = step.needs || [];
      if (deps.every((d) => completed.has(d))) {
        ready.push(step);
      }
    }

    if (ready.length === 0 && remaining.size > 0) {
      // Circular dependency — skip remaining
      for (const name of remaining) {
        results.set(name, {
          name,
          status: "skipped",
          exit_code: -1,
          stdout: "",
          stderr: "Circular dependency detected",
          duration_ms: 0,
          matrix_vars: matrixVars,
        });
      }
      break;
    }

    // Check if any dependency failed → skip dependents
    const readyFiltered = ready.filter((step) => {
      const deps = step.needs || [];
      const depFailed = deps.some((d) => results.get(d)?.status === "failed");
      if (depFailed) {
        results.set(step.name, {
          name: step.name,
          status: "skipped",
          exit_code: -1,
          stdout: "",
          stderr: "Skipped: dependency failed",
          duration_ms: 0,
          matrix_vars: matrixVars,
        });
        remaining.delete(step.name);
        completed.add(step.name);
        return false;
      }
      return true;
    });

    // Execute ready steps in parallel
    const execPromises = readyFiltered.map((step) => executeStep(step, env, matrixVars));
    const stepResults = await Promise.all(execPromises);

    for (const result of stepResults) {
      results.set(result.name, result);
      remaining.delete(result.name);
      completed.add(result.name);
    }
  }

  // Return in original order
  return steps.map((s) => results.get(s.name)!);
}

async function executeStep(
  step: PipelineStep,
  env?: Record<string, string>,
  matrixVars?: Record<string, string>,
): Promise<StepResult> {
  // Evaluate 'if' condition (simple truthy check on env var)
  if (step.if) {
    const condValue = env?.[step.if] || process.env[step.if];
    if (!condValue || condValue === "false" || condValue === "0") {
      return {
        name: step.name,
        status: "skipped",
        exit_code: 0,
        stdout: "",
        stderr: `Condition '${step.if}' not met`,
        duration_ms: 0,
        matrix_vars: matrixVars,
      };
    }
  }

  const mergedEnv: Record<string, string> = { ...env, ...step.env };

  let command = step.run;

  // If step has an image, wrap in docker run
  if (step.image) {
    const envFlags = Object.entries(mergedEnv)
      .map(([k, v]) => `-e ${k}="${v.replace(/"/g, '\\"')}"`)
      .join(" ");
    command = `docker run --rm ${envFlags} ${step.image} sh -c ${JSON.stringify(step.run)}`;
  }

  const result = await executeCommand(command, {
    timeout: step.timeout || 300,
    env: step.image ? undefined : mergedEnv,
  });

  return {
    name: step.name,
    status: result.exit_code === 0 ? "success" : "failed",
    exit_code: result.exit_code,
    stdout: result.stdout,
    stderr: result.stderr,
    duration_ms: result.duration_ms,
    matrix_vars: matrixVars,
  };
}

function expandMatrix(strategy?: MatrixStrategy): Record<string, string>[] {
  if (!strategy?.matrix) return [];
  const keys = Object.keys(strategy.matrix);
  if (keys.length === 0) return [];

  let combos: Record<string, string>[] = [{}];
  for (const key of keys) {
    const values = strategy.matrix[key];
    const newCombos: Record<string, string>[] = [];
    for (const combo of combos) {
      for (const val of values) {
        newCombos.push({ ...combo, [key]: val });
      }
    }
    combos = newCombos;
  }
  return combos;
}

async function startService(name: string, service: PipelineService): Promise<string | null> {
  const envFlags = service.env
    ? Object.entries(service.env).map(([k, v]) => `-e ${k}="${v}"`).join(" ")
    : "";
  const portFlags = service.ports
    ? service.ports.map((p) => `-p ${p}`).join(" ")
    : "";

  const cmd = `docker run -d --name pipeline-svc-${name}-${Date.now()} ${envFlags} ${portFlags} ${service.image}`;
  const result = await executeCommand(cmd, { timeout: 30 });

  if (result.exit_code === 0) {
    return result.stdout.trim();
  }
  console.warn(`[pipeline] Failed to start service '${name}':`, result.stderr);
  return null;
}

async function stopService(containerId: string): Promise<void> {
  await executeCommand(`docker stop ${containerId} && docker rm ${containerId}`, { timeout: 15 });
}
