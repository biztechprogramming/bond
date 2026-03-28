/**
 * LLM-Powered Discovery — uses Claude to analyze project files and produce
 * rich, accurate deployment plans.
 *
 * Falls back to static scanners in discovery-tools.ts if the LLM is unavailable.
 */

import fs from "node:fs";
import path from "node:path";
import type {
  FrameworkDetection,
  BuildStrategyDetection,
  ServiceDetection,
  EnvVarDetection,
  PortDetection,
  HealthEndpointDetection,
} from "./discovery-tools.js";

// ── Types ────────────────────────────────────────────────────────────────────

export interface LLMDiscoveryResult {
  framework?: FrameworkDetection;
  build_strategy?: BuildStrategyDetection;
  services?: ServiceDetection[];
  env_vars?: EnvVarDetection[];
  ports?: PortDetection[];
  health_endpoint?: HealthEndpointDetection;
  app_port?: number;
  deployment_notes?: string;
}

interface LLMAnalysisResponse {
  framework?: string;
  framework_version?: string;
  runtime?: string;
  build_strategy?: string;
  build_commands?: string[];
  dockerfile_path?: string;
  compose_path?: string;
  app_port?: number;
  ports?: Array<{ port: number; protocol?: string; description?: string }>;
  env_vars?: Array<{ name: string; required: boolean; has_default: boolean; description?: string }>;
  health_endpoint?: string;
  services?: Array<{ name: string; type: string; version?: string; connection_info?: string }>;
  deployment_notes?: string;
}

// ── File Reading ─────────────────────────────────────────────────────────────

const KEY_FILES = [
  "package.json",
  "Dockerfile",
  "dockerfile",
  "docker-compose.yml",
  "docker-compose.yaml",
  "compose.yml",
  "compose.yaml",
  ".env.example",
  ".env.template",
  ".env.sample",
  "README.md",
  "requirements.txt",
  "pyproject.toml",
  "Gemfile",
  "go.mod",
  "pom.xml",
  "Cargo.toml",
  "composer.json",
  "Procfile",
  "nixpacks.toml",
  "fly.toml",
  "render.yaml",
  "app.yaml",
  "vercel.json",
  "netlify.toml",
];

/** Entry point files to look for */
const ENTRY_POINT_PATTERNS = [
  "src/index.ts", "src/index.js", "src/main.ts", "src/main.js",
  "src/app.ts", "src/app.js", "src/server.ts", "src/server.js",
  "index.ts", "index.js", "main.ts", "main.js",
  "app.ts", "app.js", "server.ts", "server.js",
  "app.py", "main.py", "manage.py", "wsgi.py",
  "config/routes.rb", "config.ru",
  "main.go", "cmd/main.go", "cmd/server/main.go",
  "src/main.rs",
];

const MAX_FILE_SIZE = 50_000; // 50KB per file
const MAX_TOTAL_CONTEXT = 200_000; // 200KB total

/**
 * Read key project files for LLM context.
 */
export function readProjectFiles(repoPath: string): Map<string, string> {
  const files = new Map<string, string>();
  let totalSize = 0;

  // Read key config files
  for (const name of KEY_FILES) {
    if (totalSize >= MAX_TOTAL_CONTEXT) break;
    const fullPath = path.join(repoPath, name);
    try {
      if (!fs.existsSync(fullPath)) continue;
      const stat = fs.statSync(fullPath);
      if (!stat.isFile() || stat.size > MAX_FILE_SIZE) continue;
      const content = fs.readFileSync(fullPath, "utf8");
      files.set(name, content);
      totalSize += content.length;
    } catch { /* skip */ }
  }

  // Read entry point files
  for (const name of ENTRY_POINT_PATTERNS) {
    if (totalSize >= MAX_TOTAL_CONTEXT) break;
    if (files.has(name)) continue;
    const fullPath = path.join(repoPath, name);
    try {
      if (!fs.existsSync(fullPath)) continue;
      const stat = fs.statSync(fullPath);
      if (!stat.isFile() || stat.size > MAX_FILE_SIZE) continue;
      const content = fs.readFileSync(fullPath, "utf8");
      files.set(name, content);
      totalSize += content.length;
    } catch { /* skip */ }
  }

  return files;
}

// ── LLM Call ─────────────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `You are a deployment analysis expert. Given a set of project files, analyze them and return a JSON object describing the deployment configuration.

Return ONLY valid JSON with these fields:
- framework: string — the primary framework (e.g., "Next.js", "Express + Hono", "FastAPI", "Rails")
- framework_version: string | null — version if detectable
- runtime: string — runtime environment (e.g., "node", "bun", "python", "ruby", "go", "rust", "java")
- build_strategy: string — one of: "docker", "docker-compose", "npm", "bun", "pip", "cargo", "go", "maven", "gradle", "buildpack", "static"
- build_commands: string[] — ordered list of build commands
- dockerfile_path: string | null — path to Dockerfile if present
- compose_path: string | null — path to docker-compose file if present
- app_port: number — the port the application listens on
- ports: array of { port: number, protocol?: string, description?: string }
- env_vars: array of { name: string, required: boolean, has_default: boolean, description?: string }
- health_endpoint: string | null — health check path (e.g., "/health", "/healthz")
- services: array of { name: string, type: "database"|"cache"|"queue"|"search"|"storage"|"other", version?: string, connection_info?: string }
- deployment_notes: string — any important notes about deploying this application

Be specific and actionable. Infer reasonable defaults when not explicitly configured (e.g., Express defaults to port 3000). Do NOT include env vars that are just comments or section headers.`;

function buildUserPrompt(files: Map<string, string>): string {
  let prompt = "Analyze these project files and return deployment configuration as JSON:\n\n";
  for (const [name, content] of files) {
    prompt += `--- ${name} ---\n${content}\n\n`;
  }
  return prompt;
}

/**
 * Call the Anthropic API to analyze project files.
 * Uses ANTHROPIC_API_KEY from environment.
 */
async function callLLM(files: Map<string, string>): Promise<LLMAnalysisResponse> {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    throw new Error("ANTHROPIC_API_KEY not set — cannot use LLM discovery");
  }

  const userPrompt = buildUserPrompt(files);

  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: "claude-sonnet-4-20250514",
      max_tokens: 4096,
      system: SYSTEM_PROMPT,
      messages: [{ role: "user", content: userPrompt }],
    }),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Anthropic API error ${response.status}: ${body}`);
  }

  const data = (await response.json()) as any;
  const text = data?.content?.[0]?.text;
  if (!text) {
    throw new Error("Empty response from Anthropic API");
  }

  // Extract JSON from response (it might be wrapped in markdown code block)
  const jsonMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/) || [null, text];
  const jsonStr = jsonMatch[1].trim();
  return JSON.parse(jsonStr);
}

// ── Convert LLM Response to Discovery Types ──────────────────────────────────

function convertToDiscoveryResult(llm: LLMAnalysisResponse): LLMDiscoveryResult {
  const result: LLMDiscoveryResult = {};

  if (llm.framework) {
    result.framework = {
      framework: llm.framework,
      version: llm.framework_version,
      confidence: 0.95,
      evidence: ["LLM analysis"],
      runtime: llm.runtime,
    };
  }

  if (llm.build_strategy) {
    result.build_strategy = {
      strategy: llm.build_strategy,
      confidence: 0.9,
      evidence: ["LLM analysis"],
      dockerfile_path: llm.dockerfile_path,
      compose_path: llm.compose_path,
    };
  }

  if (llm.app_port) {
    result.app_port = llm.app_port;
  }

  if (llm.ports && llm.ports.length > 0) {
    result.ports = llm.ports.map(p => ({
      port: p.port,
      protocol: p.protocol,
      source: "LLM analysis",
      description: p.description,
      confidence: 0.9,
    }));
  }

  if (llm.env_vars && llm.env_vars.length > 0) {
    result.env_vars = llm.env_vars.map(e => ({
      name: e.name,
      required: e.required,
      has_default: e.has_default,
      source: "LLM analysis",
      description: e.description,
    }));
  }

  if (llm.health_endpoint) {
    result.health_endpoint = {
      path: llm.health_endpoint,
      method: "GET",
      source: "LLM analysis",
      confidence: 0.85,
    };
  }

  if (llm.services && llm.services.length > 0) {
    result.services = llm.services.map(s => ({
      name: s.name,
      type: (s.type as ServiceDetection["type"]) || "other",
      version: s.version,
      connection_info: s.connection_info,
      source: "LLM analysis",
      confidence: 0.9,
    }));
  }

  if (llm.deployment_notes) {
    result.deployment_notes = llm.deployment_notes;
  }

  return result;
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Run LLM-powered discovery on a repository.
 * Returns null if the LLM is unavailable or fails (caller should fall back to static scanners).
 */
export async function runLLMDiscovery(repoPath: string): Promise<LLMDiscoveryResult | null> {
  try {
    const files = readProjectFiles(repoPath);
    if (files.size === 0) {
      console.warn("[llm-discovery] No project files found to analyze");
      return null;
    }

    console.log(`[llm-discovery] Analyzing ${files.size} project files via LLM...`);
    const llmResponse = await callLLM(files);
    const result = convertToDiscoveryResult(llmResponse);
    console.log(`[llm-discovery] LLM analysis complete: framework=${result.framework?.framework}, port=${result.app_port}`);
    return result;
  } catch (err: any) {
    console.error(`[llm-discovery] LLM analysis failed: ${err.message}`);
    return null;
  }
}
