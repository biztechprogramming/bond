/**
 * Pipeline-as-Code — YAML parser for .bond/deploy.yml files.
 */

import yaml from "js-yaml";

export interface PipelineTrigger {
  push?: { branches?: string[]; tags?: string[] };
  tag?: string;
  manual?: boolean;
}

export interface PipelineService {
  image: string;
  env?: Record<string, string>;
  ports?: string[];
}

export interface MatrixStrategy {
  matrix: Record<string, string[]>;
}

export interface PipelineStep {
  name: string;
  run: string;
  image?: string;
  timeout?: number;
  if?: string;
  needs?: string[];
  env?: Record<string, string>;
  secrets?: string[];
}

export interface PipelineJob {
  name?: string;
  steps: PipelineStep[];
  services?: Record<string, PipelineService>;
  strategy?: MatrixStrategy;
  env?: Record<string, string>;
}

export interface Pipeline {
  name: string;
  on: PipelineTrigger;
  env?: Record<string, string>;
  jobs: Record<string, PipelineJob>;
}

export interface ParseResult {
  valid: boolean;
  pipeline?: Pipeline;
  errors: string[];
  warnings: string[];
}

export function parsePipelineYaml(raw: string): ParseResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  let doc: any;
  try {
    doc = yaml.load(raw);
  } catch (err: any) {
    return { valid: false, errors: [`YAML syntax error: ${err.message}`], warnings: [] };
  }

  if (!doc || typeof doc !== "object") {
    return { valid: false, errors: ["Pipeline must be a YAML object"], warnings: [] };
  }

  // Name
  if (!doc.name && !doc.pipeline) {
    errors.push("Missing required field: 'name' or 'pipeline'");
  }
  const name = doc.name || doc.pipeline || "unnamed";

  // Triggers
  const on = parseTrigger(doc.on, errors, warnings);

  // Global env
  const env = validateEnv(doc.env, "top-level", warnings);

  // Jobs — support both 'jobs' map and flat 'steps' array
  const jobs: Record<string, PipelineJob> = {};

  if (doc.jobs && typeof doc.jobs === "object") {
    for (const [jobId, jobDef] of Object.entries(doc.jobs as Record<string, any>)) {
      const job = parseJob(jobId, jobDef, errors, warnings);
      if (job) jobs[jobId] = job;
    }
  } else if (Array.isArray(doc.steps)) {
    // Flat steps → single "default" job
    const steps = parseSteps(doc.steps, errors, warnings);
    if (steps.length > 0) {
      jobs["default"] = { steps, env: validateEnv(doc.env, "default job", warnings) };
    }
  } else {
    errors.push("Pipeline must have either 'jobs' or 'steps'");
  }

  if (Object.keys(jobs).length === 0 && errors.length === 0) {
    errors.push("Pipeline has no jobs or steps defined");
  }

  // Validate step dependency references
  for (const [jobId, job] of Object.entries(jobs)) {
    const stepNames = new Set(job.steps.map((s) => s.name));
    for (const step of job.steps) {
      for (const dep of step.needs || []) {
        if (!stepNames.has(dep)) {
          errors.push(`Job '${jobId}', step '${step.name}': needs unknown step '${dep}'`);
        }
      }
    }
  }

  const valid = errors.length === 0;
  return {
    valid,
    pipeline: valid ? { name, on, env, jobs } : undefined,
    errors,
    warnings,
  };
}

function parseTrigger(raw: any, errors: string[], _warnings: string[]): PipelineTrigger {
  if (!raw) {
    errors.push("Missing required field: 'on' (trigger configuration)");
    return {};
  }
  const trigger: PipelineTrigger = {};
  if (raw.push) {
    trigger.push = {
      branches: Array.isArray(raw.push.branches) ? raw.push.branches : raw.push.branches ? [raw.push.branches] : undefined,
      tags: Array.isArray(raw.push.tags) ? raw.push.tags : raw.push.tags ? [raw.push.tags] : undefined,
    };
  }
  if (raw.tag) trigger.tag = raw.tag;
  if (raw.manual !== undefined) trigger.manual = !!raw.manual;
  return trigger;
}

function parseJob(jobId: string, raw: any, errors: string[], warnings: string[]): PipelineJob | null {
  if (!raw || typeof raw !== "object") {
    errors.push(`Job '${jobId}' must be an object`);
    return null;
  }

  if (!Array.isArray(raw.steps)) {
    errors.push(`Job '${jobId}' must have a 'steps' array`);
    return null;
  }

  const steps = parseSteps(raw.steps, errors, warnings, jobId);
  const services = parseServices(raw.services, jobId, warnings);
  const strategy = parseStrategy(raw.strategy, jobId, warnings);
  const env = validateEnv(raw.env, `job '${jobId}'`, warnings);

  return { name: raw.name, steps, services, strategy, env };
}

function parseSteps(raw: any[], errors: string[], warnings: string[], jobId = "default"): PipelineStep[] {
  const steps: PipelineStep[] = [];
  const names = new Set<string>();

  for (let i = 0; i < raw.length; i++) {
    const s = raw[i];
    if (!s || typeof s !== "object") {
      errors.push(`Job '${jobId}', step ${i}: must be an object`);
      continue;
    }

    const name = s.name || `step-${i}`;
    if (names.has(name)) {
      errors.push(`Job '${jobId}': duplicate step name '${name}'`);
    }
    names.add(name);

    // Support both 'run' (single command) and 'commands' (array)
    let run = s.run;
    if (!run && Array.isArray(s.commands)) {
      run = s.commands.join("\n");
    }
    if (!run) {
      errors.push(`Job '${jobId}', step '${name}': missing 'run' or 'commands'`);
      continue;
    }

    const step: PipelineStep = { name, run };
    if (s.image) step.image = s.image;
    if (s.timeout != null) step.timeout = Number(s.timeout);
    if (s.if) step.if = String(s.if);
    if (Array.isArray(s.needs)) step.needs = s.needs;
    if (Array.isArray(s.depends_on)) step.needs = s.depends_on; // alias
    if (s.env) step.env = validateEnv(s.env, `step '${name}'`, warnings);
    if (Array.isArray(s.secrets)) step.secrets = s.secrets;

    steps.push(step);
  }

  return steps;
}

function parseServices(raw: any, jobId: string, warnings: string[]): Record<string, PipelineService> | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const services: Record<string, PipelineService> = {};
  for (const [svcName, svcDef] of Object.entries(raw as Record<string, any>)) {
    if (!svcDef?.image) {
      warnings.push(`Job '${jobId}', service '${svcName}': missing 'image', skipped`);
      continue;
    }
    services[svcName] = {
      image: svcDef.image,
      env: svcDef.env,
      ports: Array.isArray(svcDef.ports) ? svcDef.ports.map(String) : undefined,
    };
  }
  return Object.keys(services).length > 0 ? services : undefined;
}

function parseStrategy(raw: any, jobId: string, warnings: string[]): MatrixStrategy | undefined {
  if (!raw?.matrix || typeof raw.matrix !== "object") return undefined;
  const matrix: Record<string, string[]> = {};
  for (const [key, values] of Object.entries(raw.matrix as Record<string, any>)) {
    if (!Array.isArray(values)) {
      warnings.push(`Job '${jobId}', matrix key '${key}': expected array, skipped`);
      continue;
    }
    matrix[key] = values.map(String);
  }
  return Object.keys(matrix).length > 0 ? { matrix } : undefined;
}

function validateEnv(raw: any, context: string, warnings: string[]): Record<string, string> | undefined {
  if (!raw) return undefined;
  if (typeof raw !== "object" || Array.isArray(raw)) {
    warnings.push(`${context}: 'env' should be a key-value map`);
    return undefined;
  }
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw)) {
    env[k] = String(v);
  }
  return env;
}
