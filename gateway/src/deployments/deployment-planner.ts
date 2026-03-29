/**
 * Deployment Planner — generates a structured deployment plan from discovery state using LLM.
 */

import fs from "node:fs";
import path from "node:path";
import { ulid } from "ulid";
import type { BackendClient } from "../backend/client.js";
import type { DiscoveryState } from "./discovery-agent.js";

// ── Types ────────────────────────────────────────────────────────────────────

export interface DeploymentStep {
  id: string;
  action: 'build_local' | 'transfer_files' | 'remote_exec' | 'health_check' | 'setup_env' | 'docker_build_remote' | 'docker_compose_up';
  description: string;
  params: Record<string, any>;
  timeout_ms: number;
  retry_count: number;
  depends_on?: string[];
}

export interface DeploymentPlan {
  id: string;
  app_name: string;
  target_server: string;
  steps: DeploymentStep[];
  created_at: string;
  estimated_duration_ms: number;
  rollback_steps: DeploymentStep[];
}

// ── Constants ────────────────────────────────────────────────────────────────

const VALID_ACTIONS = new Set<DeploymentStep["action"]>([
  "build_local", "transfer_files", "remote_exec", "health_check",
  "setup_env", "docker_build_remote", "docker_compose_up",
]);

const PLANNER_SYSTEM_PROMPT = `You are a deployment planning expert. Given discovery findings about an application and an SSH deployment reference guide, generate a structured deployment plan as JSON.

Return ONLY valid JSON with these fields:
- app_name: string — application name
- steps: array of deployment steps, each with:
  - id: string — unique step identifier (e.g., "step_1", "step_2")
  - action: one of "build_local", "transfer_files", "remote_exec", "health_check", "setup_env", "docker_build_remote", "docker_compose_up"
  - description: string — human-readable description
  - params: object — action-specific parameters:
    - build_local: { command: string, cwd?: string }
    - transfer_files: { local_path: string, remote_path: string, exclude?: string[] }
    - remote_exec: { command: string, cwd?: string }
    - health_check: { url: string, expected_status?: number, retries?: number }
    - setup_env: { remote_path: string, vars: Record<string, string> }
    - docker_build_remote: { dockerfile: string, context_path: string, image_tag: string }
    - docker_compose_up: { compose_file: string, remote_path: string }
  - timeout_ms: number — step timeout in milliseconds
  - retry_count: number — number of retries on failure (0 = no retry)
  - depends_on: string[] | undefined — IDs of steps this depends on
- estimated_duration_ms: number — total estimated duration
- rollback_steps: array of steps (same format) to undo the deployment

Key rules:
- For docker-compose projects: transfer compose file + .env → remote docker compose up -d
- For Dockerfile-only projects: build image → transfer or push → run container
- For Node.js/Python without Docker: rsync code → install deps remotely → restart service
- Always include a health check as the final step if a health endpoint is known
- Include rollback steps (docker compose down, restore previous image, etc.)
- Use reasonable timeouts (build: 300000ms, transfer: 120000ms, remote_exec: 60000ms, health_check: 30000ms)`;

// ── Public API ───────────────────────────────────────────────────────────────

export async function generateDeploymentPlan(
  state: DiscoveryState,
  backendClient: BackendClient,
  skillPath?: string,
): Promise<DeploymentPlan> {
  // Read SSH deploy reference if available
  let sshReference = "";
  const defaultSkillPath = path.resolve(
    import.meta.dirname || path.join(process.cwd(), "gateway", "src", "deployments"),
    "..", "..", "..", "..", "skills", "appdeploy", "references", "ssh-deploy.md",
  );
  const refPath = skillPath || defaultSkillPath;
  try {
    if (fs.existsSync(refPath)) {
      sshReference = fs.readFileSync(refPath, "utf8");
    }
  } catch { /* skip */ }

  // Build user prompt
  const userPrompt = buildPlannerPrompt(state, sshReference);

  const text = await backendClient.llmComplete(
    [
      { role: "system", content: PLANNER_SYSTEM_PROMPT },
      { role: "user", content: userPrompt },
    ],
    { max_tokens: 4096, temperature: 0.1 },
  );

  if (!text) {
    throw new Error("Empty response from LLM when generating deployment plan");
  }

  // Parse JSON from response
  const jsonMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/) || [null, text];
  const jsonStr = jsonMatch[1]!.trim();
  let parsed: any;
  try {
    parsed = JSON.parse(jsonStr);
  } catch (err: any) {
    throw new Error(`Failed to parse deployment plan JSON: ${err.message}`);
  }

  // Validate and build plan
  const plan: DeploymentPlan = {
    id: ulid(),
    app_name: parsed.app_name || state.findings.source || "unknown",
    target_server: state.findings.target_server?.host || "unknown",
    steps: validateSteps(parsed.steps || []),
    created_at: new Date().toISOString(),
    estimated_duration_ms: parsed.estimated_duration_ms || 0,
    rollback_steps: validateSteps(parsed.rollback_steps || []),
  };

  return plan;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildPlannerPrompt(state: DiscoveryState, sshReference: string): string {
  let prompt = "Generate a deployment plan for the following application:\n\n";
  prompt += `## Discovery Findings\n${JSON.stringify(state.findings, null, 2)}\n\n`;
  prompt += `## Completeness\n${JSON.stringify(state.completeness, null, 2)}\n\n`;

  if (sshReference) {
    prompt += `## SSH Deployment Reference\n${sshReference}\n\n`;
  }

  prompt += "Generate the deployment plan as JSON.";
  return prompt;
}

function validateSteps(steps: any[]): DeploymentStep[] {
  if (!Array.isArray(steps)) return [];
  return steps.map((s, i) => {
    const action = VALID_ACTIONS.has(s.action) ? s.action : "remote_exec";
    return {
      id: s.id || `step_${i + 1}`,
      action,
      description: s.description || `Step ${i + 1}`,
      params: s.params || {},
      timeout_ms: typeof s.timeout_ms === "number" ? s.timeout_ms : 60_000,
      retry_count: typeof s.retry_count === "number" ? s.retry_count : 0,
      depends_on: Array.isArray(s.depends_on) ? s.depends_on : undefined,
    };
  });
}
