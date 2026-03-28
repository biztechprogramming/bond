/**
 * Discovery Agent — adaptive agent-driven deployment discovery orchestrator.
 *
 * Design Doc 071 §3, §5, §6 — Agent Discovery Loop
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execSync } from "node:child_process";
import {
  detectFramework,
  detectBuildStrategy,
  detectServices,
  detectEnvVars,
  detectPorts,
  detectHealthEndpoint,
  repoInspect,
  sshExec,
  askUser,
} from "./discovery-tools.js";
import type {
  SshExecParams,
  FrameworkDetection,
  BuildStrategyDetection,
  ServiceDetection,
  EnvVarDetection,
  PortDetection,
  HealthEndpointDetection,
  UserQuestion,
} from "./discovery-tools.js";
import { runLLMDiscovery } from "./llm-discovery.js";
import type { BackendClient } from "../backend/client.js";
import { writeManifest } from "./manifest.js";
import { emitDeploymentEvent } from "./events.js";
import type { DeploymentManifest, ManifestServer } from "./manifest.js";

// ── Types (§3.4, §5) ───────────────────────────────────────────────────────

/** §5.2 — Confidence for a single discovered field */
export interface FieldConfidence {
  source: "detected" | "inferred" | "user-provided";
  detail: string;
  score: number;
}

/** §5.3 — Completeness evaluation */
export interface CompletenessReport {
  ready: boolean;
  required_coverage: number;
  recommended_coverage: number;
  missing_required: string[];
  low_confidence: string[];
}

/** Probe execution record */
export interface ProbeRecord {
  tool: string;
  timestamp: number;
  duration_ms: number;
  success: boolean;
  fields_discovered: string[];
}

/** §3.4 — Full agent discovery state */
export interface DiscoveryState {
  findings: {
    source?: string;
    repo_url?: string;
    framework?: FrameworkDetection;
    build_strategy?: BuildStrategyDetection;
    services?: ServiceDetection[];
    env_vars?: EnvVarDetection[];
    ports?: PortDetection[];
    health_endpoint?: HealthEndpointDetection;
    target_server?: { host: string; port: number; user: string; os?: string };
    app_port?: number;
  };
  confidence: Record<string, FieldConfidence>;
  probes_run: ProbeRecord[];
  user_answers: Record<string, string>;
  completeness: CompletenessReport;
}

/** Parameters for the agent discovery run */
export interface AgentDiscoveryParams {
  source?: string;
  repoUrl?: string;
  serverHost?: string;
  serverPort?: number;
  sshUser?: string;
  sshKeyPath?: string;
  env: string;
  repoPath?: string;
  sessionId?: string;
  backendClient?: BackendClient;
}

// ── Constants ───────────────────────────────────────────────────────────────

const REQUIRED_FIELDS = ["source", "framework", "build_strategy", "target_server", "app_port"];
const RECOMMENDED_FIELDS = ["env_vars", "health_endpoint", "services"];
const MAX_ITERATIONS = 5;
const MAX_TOOL_CALLS = 20;
const TIMEOUT_MS = 60_000;

// ── Completeness Evaluation (§5.3) ──────────────────────────────────────────

/**
 * Evaluate how complete the current discovery state is.
 */
export function evaluateCompleteness(state: DiscoveryState): CompletenessReport {
  const missing_required: string[] = [];
  const low_confidence: string[] = [];

  for (const field of REQUIRED_FIELDS) {
    const value = (state.findings as any)[field];
    const conf = state.confidence[field];

    if (value === undefined || value === null) {
      missing_required.push(field);
    } else if (conf && conf.score <= 0.5) {
      low_confidence.push(field);
    }
  }

  for (const field of RECOMMENDED_FIELDS) {
    const conf = state.confidence[field];
    if (conf && conf.score > 0 && conf.score <= 0.5) {
      low_confidence.push(field);
    }
  }

  const requiredFilled = REQUIRED_FIELDS.length - missing_required.length;
  const required_coverage = REQUIRED_FIELDS.length > 0 ? requiredFilled / REQUIRED_FIELDS.length : 1;

  let recommendedFilled = 0;
  for (const field of RECOMMENDED_FIELDS) {
    const value = (state.findings as any)[field];
    if (value !== undefined && value !== null) recommendedFilled++;
  }
  const recommended_coverage = RECOMMENDED_FIELDS.length > 0 ? recommendedFilled / RECOMMENDED_FIELDS.length : 1;

  const ready = missing_required.length === 0 && low_confidence.filter(f => REQUIRED_FIELDS.includes(f)).length === 0;

  return { ready, required_coverage, recommended_coverage, missing_required, low_confidence };
}

// ── Agent Discovery Orchestrator (§6) ───────────────────────────────────────

/**
 * Run the adaptive agent discovery loop.
 *
 * Phase A: Broad scan based on available inputs
 * Phase B: Gap analysis via completeness evaluation
 * Phase C: Targeted probes for missing/low-confidence fields
 * Phase D: Ask user for still-missing required fields
 */
export async function runAgentDiscovery(params: AgentDiscoveryParams): Promise<DiscoveryState> {
  const startTime = Date.now();
  let toolCalls = 0;

  const state: DiscoveryState = {
    findings: {},
    confidence: {},
    probes_run: [],
    user_answers: {},
    completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [...REQUIRED_FIELDS], low_confidence: [] },
  };

  const sshParams: SshExecParams | undefined = params.serverHost ? {
    host: params.serverHost,
    port: params.serverPort || 22,
    user: params.sshUser || "deploy",
    key_path: params.sshKeyPath,
    command: "", // placeholder, overridden per call
  } : undefined;

  const sessionId = params.sessionId;

  // If repoUrl is provided but no local repoPath, clone to a temp directory
  let clonedTmpDir: string | undefined;
  if (!params.repoPath && params.repoUrl) {
    try {
      clonedTmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-discovery-"));
      execSync(`git clone --depth 1 ${params.repoUrl} ${clonedTmpDir}`, {
        stdio: "pipe",
        timeout: 30_000,
      });
      params = { ...params, repoPath: clonedTmpDir };
    } catch (err: any) {
      const cloneError = `Failed to clone repo ${params.repoUrl}: ${err.message}`;
      console.error("[discovery-agent]", cloneError);
      emitDeploymentEvent("discovery_agent_progress", {
        environment: params.env,
        summary: cloneError,
        details: { error: cloneError, session_id: params.sessionId },
      });
      throw new Error(cloneError);
    }
  }

  try {

  // Determine discovery mode based on available inputs
  const discoveryMode = (params.repoPath && params.serverHost) ? "full"
    : params.repoPath ? "repo-only"
    : params.serverHost ? "server-only"
    : "interview";

  emitDeploymentEvent("discovery_agent_started", {
    environment: params.env,
    summary: `Agent discovery started${params.repoPath ? ` for ${params.repoPath}` : ""}`,
    details: { server_host: params.serverHost, repo_path: params.repoPath, session_id: sessionId, mode: discoveryMode },
  });

  // Load any previously stored user answers (from /discovery/answer endpoint)
  if (sessionId) {
    const answersFile = path.join(os.homedir(), ".bond", "deployments", "discovery", "answers", `${sessionId}.json`);
    if (fs.existsSync(answersFile)) {
      try {
        const storedAnswers = JSON.parse(fs.readFileSync(answersFile, "utf8"));
        for (const [field, value] of Object.entries(storedAnswers)) {
          state.user_answers[field] = String(value);
          // Apply answers to findings
          if (field === "app_port") {
            state.findings.app_port = parseInt(String(value), 10) || undefined;
          } else if (field === "target_server") {
            state.findings.target_server = { host: String(value), port: 22, user: "deploy" };
          } else if (field === "framework") {
            state.findings.framework = { framework: String(value), confidence: 1.0, evidence: ["user"] };
          } else if (field === "build_strategy") {
            state.findings.build_strategy = { strategy: String(value), confidence: 1.0, evidence: ["user"] };
          } else if (field === "source") {
            state.findings.source = String(value);
          } else {
            (state.findings as any)[field] = value;
          }
          state.confidence[field] = { source: "user-provided", detail: "Provided by user", score: 1.0 };
        }
      } catch {}
    }
  }

  // Set source if provided
  if (params.source || params.repoUrl) {
    state.findings.source = params.source || params.repoUrl;
    state.confidence.source = { source: "user-provided", detail: "Provided by user", score: 1.0 };
  }

  // Set target server if provided
  if (params.serverHost) {
    state.findings.target_server = {
      host: params.serverHost,
      port: params.serverPort || 22,
      user: params.sshUser || "deploy",
    };
    state.confidence.target_server = { source: "user-provided", detail: "SSH connection provided", score: 1.0 };
  }

  // ── Phase 0: LLM-powered analysis (if repo available) ──────────────────
  if (params.repoPath) {
    const llmProbe = await runProbe("llm_discovery", async () => {
      const llmResult = params.backendClient
        ? await runLLMDiscovery(params.repoPath!, params.backendClient)
        : null;
      if (!llmResult) return [];
      const discovered: string[] = [];

      if (llmResult.framework && !state.findings.framework) {
        state.findings.framework = llmResult.framework;
        state.confidence.framework = { source: "detected", detail: "LLM analysis", score: llmResult.framework.confidence };
        discovered.push("framework");
      }
      if (llmResult.build_strategy && !state.findings.build_strategy) {
        state.findings.build_strategy = llmResult.build_strategy;
        state.confidence.build_strategy = { source: "detected", detail: "LLM analysis", score: llmResult.build_strategy.confidence };
        discovered.push("build_strategy");
      }
      if (llmResult.app_port && !state.findings.app_port) {
        state.findings.app_port = llmResult.app_port;
        state.confidence.app_port = { source: "detected", detail: "LLM analysis", score: 0.9 };
        discovered.push("app_port");
      }
      if (llmResult.ports && llmResult.ports.length > 0 && !state.findings.ports) {
        state.findings.ports = llmResult.ports;
        state.confidence.ports = { source: "detected", detail: `LLM found ${llmResult.ports.length} port(s)`, score: 0.9 };
        discovered.push("ports");
      }
      if (llmResult.env_vars && llmResult.env_vars.length > 0 && !state.findings.env_vars) {
        state.findings.env_vars = llmResult.env_vars;
        state.confidence.env_vars = { source: "detected", detail: `LLM found ${llmResult.env_vars.length} env var(s)`, score: 0.9 };
        discovered.push("env_vars");
      }
      if (llmResult.health_endpoint && !state.findings.health_endpoint) {
        state.findings.health_endpoint = llmResult.health_endpoint;
        state.confidence.health_endpoint = { source: "detected", detail: "LLM analysis", score: llmResult.health_endpoint.confidence };
        discovered.push("health_endpoint");
      }
      if (llmResult.services && llmResult.services.length > 0 && !state.findings.services) {
        state.findings.services = llmResult.services;
        state.confidence.services = { source: "detected", detail: `LLM found ${llmResult.services.length} service(s)`, score: 0.9 };
        discovered.push("services");
      }

      return discovered;
    });
    state.probes_run.push(llmProbe);
    toolCalls++;

    // Emit progress for each LLM-discovered field
    for (const field of llmProbe.fields_discovered) {
      emitProgress(params.env, state, field, sessionId, "llm_discovery");
    }
  }

  for (let iteration = 0; iteration < MAX_ITERATIONS; iteration++) {
    if (Date.now() - startTime > TIMEOUT_MS) break;
    if (toolCalls >= MAX_TOOL_CALLS) break;

    // Phase A/C: Run probes based on what's missing (fallback for fields LLM didn't cover)
    const completeness = evaluateCompleteness(state);
    state.completeness = completeness;

    if (completeness.ready && completeness.recommended_coverage >= 0.5) break;

    // Framework detection
    if (!state.findings.framework && params.repoPath && toolCalls < MAX_TOOL_CALLS) {
      const probe = await runProbe("detect_framework", async () => {
        const frameworks = await detectFramework(params.repoPath!);
        if (frameworks.length > 0) {
          // Pick highest confidence
          const best = frameworks.reduce((a, b) => a.confidence > b.confidence ? a : b);
          state.findings.framework = best;
          state.confidence.framework = { source: "detected", detail: `Detected from ${best.evidence[0]}`, score: best.confidence };
          return ["framework"];
        }
        return [];
      });
      state.probes_run.push(probe);
      toolCalls++;
      emitProgress(params.env, state, "framework", sessionId, "detect_framework");
    }

    // Build strategy detection
    if (!state.findings.build_strategy && params.repoPath && toolCalls < MAX_TOOL_CALLS) {
      const probe = await runProbe("detect_build_strategy", async () => {
        const strategies = await detectBuildStrategy(params.repoPath!);
        if (strategies.length > 0) {
          const best = strategies.reduce((a, b) => a.confidence > b.confidence ? a : b);
          state.findings.build_strategy = best;
          state.confidence.build_strategy = { source: "detected", detail: `Detected from ${best.evidence[0]}`, score: best.confidence };
          return ["build_strategy"];
        }
        return [];
      });
      state.probes_run.push(probe);
      toolCalls++;
      emitProgress(params.env, state, "build_strategy", sessionId, "detect_build_strategy");
    }

    // Port detection
    if (!state.findings.app_port && toolCalls < MAX_TOOL_CALLS) {
      const searchScope = params.repoPath && sshParams ? "both" : params.repoPath ? "repo" : "server";
      const probe = await runProbe("detect_ports", async () => {
        const ports = await detectPorts(params.repoPath || ".", sshParams, searchScope as any);
        if (ports.length > 0) {
          state.findings.ports = ports;
          const best = ports.reduce((a, b) => a.confidence > b.confidence ? a : b);
          state.findings.app_port = best.port;
          state.confidence.app_port = { source: "detected", detail: `Port ${best.port} from ${best.source}`, score: best.confidence };
          state.confidence.ports = { source: "detected", detail: `Found ${ports.length} port(s)`, score: 0.8 };
          return ["app_port", "ports"];
        }
        return [];
      });
      state.probes_run.push(probe);
      toolCalls++;
      emitProgress(params.env, state, "app_port", sessionId, "detect_ports");
    }

    // Service detection
    if (!state.findings.services && toolCalls < MAX_TOOL_CALLS) {
      const searchScope = params.repoPath && sshParams ? "both" : params.repoPath ? "repo" : "server";
      const probe = await runProbe("detect_services", async () => {
        const services = await detectServices(params.repoPath || ".", sshParams, searchScope as any);
        if (services.length > 0) {
          state.findings.services = services;
          state.confidence.services = { source: "detected", detail: `Found ${services.length} service(s)`, score: 0.8 };
          return ["services"];
        }
        return [];
      });
      state.probes_run.push(probe);
      toolCalls++;
    }

    // Env var detection
    if (!state.findings.env_vars && params.repoPath && toolCalls < MAX_TOOL_CALLS) {
      const probe = await runProbe("detect_env_vars", async () => {
        const envVars = await detectEnvVars(params.repoPath!);
        if (envVars.length > 0) {
          state.findings.env_vars = envVars;
          state.confidence.env_vars = { source: "detected", detail: `Found ${envVars.length} env var(s)`, score: 0.8 };
          return ["env_vars"];
        }
        return [];
      });
      state.probes_run.push(probe);
      toolCalls++;
    }

    // Health endpoint detection
    if (!state.findings.health_endpoint && toolCalls < MAX_TOOL_CALLS) {
      const probe = await runProbe("detect_health_endpoint", async () => {
        const endpoints = await detectHealthEndpoint(params.repoPath || ".", sshParams, state.findings.app_port);
        if (endpoints.length > 0) {
          const best = endpoints.reduce((a, b) => a.confidence > b.confidence ? a : b);
          state.findings.health_endpoint = best;
          state.confidence.health_endpoint = { source: "detected", detail: `Found ${best.path} from ${best.source}`, score: best.confidence };
          return ["health_endpoint"];
        }
        return [];
      });
      state.probes_run.push(probe);
      toolCalls++;
    }

    // Server OS detection via SSH
    if (sshParams && state.findings.target_server && !state.findings.target_server.os && toolCalls < MAX_TOOL_CALLS) {
      const probe = await runProbe("ssh_exec:uname", async () => {
        const result = await sshExec({ ...sshParams!, command: "uname -a", parse_as: "raw" });
        if (result.exit_code === 0 && result.output) {
          state.findings.target_server!.os = String(result.output).trim();
          return ["target_server.os"];
        }
        return [];
      });
      state.probes_run.push(probe);
      toolCalls++;
    }

    // Phase D: Generate user questions for still-missing required fields
    const updatedCompleteness = evaluateCompleteness(state);
    state.completeness = updatedCompleteness;

    if (updatedCompleteness.missing_required.length > 0 && iteration >= 1) {
      for (const field of updatedCompleteness.missing_required) {
        const question = generateUserQuestion(field, state);
        if (question) {
          emitDeploymentEvent("discovery_user_question", {
            environment: params.env,
            summary: `Agent needs input: ${question.question}`,
            details: { question, session_id: sessionId },
          });
        }
      }
      // After generating questions, break to wait for user input
      break;
    }
  }

  // Final completeness check
  state.completeness = evaluateCompleteness(state);

  emitDeploymentEvent("discovery_agent_completed", {
    environment: params.env,
    summary: `Agent discovery completed: ${state.completeness.ready ? "ready" : "needs input"}`,
    details: { state, completeness: state.completeness, probes_run: state.probes_run.length, tool_calls: toolCalls, session_id: sessionId },
  });

  // Write manifest if we have enough data
  if (state.findings.target_server || state.findings.framework) {
    const manifest = convertToManifest(state, params.env);
    writeManifest(manifest);
  }

  return state;

  } finally {
    // Clean up cloned temp dir
    if (clonedTmpDir) {
      try { fs.rmSync(clonedTmpDir, { recursive: true, force: true }); } catch {}
    }
  }
}

// ── Manifest Conversion ─────────────────────────────────────────────────────

/**
 * Convert agent discovery state to a DeploymentManifest for compatibility.
 */
export function convertToManifest(state: DiscoveryState, env: string): DeploymentManifest {
  const server: ManifestServer = {
    name: state.findings.target_server?.host || "unknown",
    host: state.findings.target_server?.host || "unknown",
    os: state.findings.target_server?.os,
    role: "application",
    application: {
      framework: state.findings.framework?.framework,
      runtime: state.findings.framework?.runtime,
      build_strategy: state.findings.build_strategy?.strategy,
      port: state.findings.app_port,
      health_endpoint: state.findings.health_endpoint?.path,
      env_vars: state.findings.env_vars?.map(e => e.name),
    },
    services: state.findings.services?.reduce((acc, svc) => {
      acc[svc.name.toLowerCase()] = { type: svc.type, source: svc.source };
      return acc;
    }, {} as Record<string, any>),
  };

  return {
    manifest_version: "1.0",
    application: state.findings.source || "unknown",
    discovered_at: new Date().toISOString(),
    discovered_by: `agent-${env}`,
    servers: [server],
    topology: { nodes: [], edges: [] },
    security_observations: [],
  };
}

// ── Helpers ─────────────────────────────────────────────────────────────────

async function runProbe(tool: string, fn: () => Promise<string[]>): Promise<ProbeRecord> {
  const start = Date.now();
  try {
    const fields = await fn();
    return { tool, timestamp: start, duration_ms: Date.now() - start, success: true, fields_discovered: fields };
  } catch {
    return { tool, timestamp: start, duration_ms: Date.now() - start, success: false, fields_discovered: [] };
  }
}

function emitProgress(env: string, state: DiscoveryState, field: string, sessionId?: string, probeName?: string): void {
  emitDeploymentEvent("discovery_agent_progress", {
    environment: env,
    summary: `Discovered: ${field}`,
    details: {
      field,
      value: (state.findings as any)[field],
      confidence: state.confidence[field],
      completeness: evaluateCompleteness(state),
      probe_name: probeName || field,
      session_id: sessionId,
    },
  });
}

function generateUserQuestion(field: string, _state: DiscoveryState): UserQuestion | null {
  switch (field) {
    case "source":
      return askUser("What is the source repository or application URL?", "We need to know where the code lives to analyze it.", "source");
    case "framework":
      return askUser("What framework does this application use?", "Could not detect the framework automatically.", "framework",
        ["Next.js", "Express", "Django", "Rails", "Spring Boot", "Laravel", "Other"]);
    case "build_strategy":
      return askUser("How is this application built and deployed?", "No Dockerfile or build configuration found.", "build_strategy",
        ["Docker", "Docker Compose", "Buildpack", "Manual/Script", "Other"]);
    case "target_server":
      return askUser("What is the target server hostname?", "We need SSH access to the deployment target.", "target_server");
    case "app_port":
      return askUser("What port does the application listen on?", "Could not detect the application port.", "app_port",
        ["3000", "8080", "8000", "5000", "80", "Other"]);
    default:
      return null;
  }
}
