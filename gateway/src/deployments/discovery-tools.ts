/**
 * Discovery Tools — tool handlers for agent-driven deployment discovery.
 *
 * Design Doc 071 §4 — Discovery Tool Definitions
 */

import fs from "node:fs";
import path from "node:path";
import { executeSshScript } from "./discovery-scripts.js";

// ── Types ───────────────────────────────────────────────────────────────────

/** §4.1 — SSH execution parameters */
export interface SshExecParams {
  host: string;
  port?: number;
  user?: string;
  key_path?: string;
  command: string;
  timeout_ms?: number;
  parse_as?: "json" | "lines" | "raw";
}

export interface SshExecResult {
  exit_code: number;
  output: unknown;
  stderr: string;
}

/** §4.2 — Repo inspection parameters */
export interface RepoInspectParams {
  repo_path: string;
  action: "list_files" | "read_file" | "find_pattern" | "tree";
  pattern?: string;
  file_path?: string;
  max_depth?: number;
}

export interface RepoInspectResult {
  action: string;
  data: unknown;
}

/** §4.3 — Framework detection */
export interface FrameworkDetection {
  framework: string;
  version?: string;
  confidence: number;
  evidence: string[];
  runtime?: string;
}

/** §4.4 — Build strategy detection */
export interface BuildStrategyDetection {
  strategy: string;
  confidence: number;
  evidence: string[];
  dockerfile_path?: string;
  compose_path?: string;
}

/** §4.5 — Service detection */
export interface ServiceDetection {
  name: string;
  type: "database" | "cache" | "queue" | "search" | "storage" | "other";
  version?: string;
  connection_info?: string;
  source: string;
  confidence: number;
}

/** §4.6 — Environment variable detection */
export interface EnvVarDetection {
  name: string;
  required: boolean;
  has_default: boolean;
  source: string;
  description?: string;
}

/** §4.7 — Port detection */
export interface PortDetection {
  port: number;
  protocol?: string;
  source: string;
  description?: string;
  confidence: number;
}

/** §4.8 — Health endpoint detection */
export interface HealthEndpointDetection {
  path: string;
  method: string;
  source: string;
  status_code?: number;
  confidence: number;
}

/** §4.9 — User question */
export interface UserQuestion {
  question: string;
  context: string;
  field: string;
  options?: string[];
  default?: string;
}

// ── Allowed SSH commands (§4.1 security) ────────────────────────────────────

export const ALLOWED_SSH_COMMANDS: string[] = [
  "uname", "cat", "ls", "docker", "systemctl", "nginx", "node", "python",
  "ruby", "go", "java", "php", "netstat", "ss", "curl", "wget", "df",
  "free", "uptime", "whoami", "which", "find", "grep", "head", "tail",
  "ps", "lsof",
];

/**
 * Validate that a command starts with an allowed prefix.
 */
export function validateSshCommand(command: string): boolean {
  const trimmed = command.trim();
  const firstWord = trimmed.split(/\s+/)[0];
  // Reject shell operators
  if (/[;&|`$()]/.test(trimmed.split(/\s+/)[0])) return false;
  return ALLOWED_SSH_COMMANDS.includes(firstWord);
}

// ── Tool Implementations ────────────────────────────────────────────────────

/**
 * §4.1 — Run a command on the target server via SSH.
 */
export async function sshExec(params: SshExecParams): Promise<SshExecResult> {
  if (!validateSshCommand(params.command)) {
    return {
      exit_code: -1,
      output: null,
      stderr: `Command not allowed. Must start with one of: ${ALLOWED_SSH_COMMANDS.join(", ")}`,
    };
  }

  const result = await executeSshScript(
    params.host,
    params.port || 22,
    params.user || "deploy",
    params.command,
    undefined,
    params.key_path,
    Math.ceil((params.timeout_ms || 10000) / 1000),
  );

  let output: unknown = result.stdout;
  const parseAs = params.parse_as || "raw";
  if (parseAs === "json") {
    try { output = JSON.parse(result.stdout); } catch { /* keep raw */ }
  } else if (parseAs === "lines") {
    output = result.stdout.split("\n").filter(Boolean);
  }

  return { exit_code: result.exit_code, output, stderr: result.stderr };
}

/**
 * §4.2 — Analyze local repository.
 */
export async function repoInspect(params: RepoInspectParams): Promise<RepoInspectResult> {
  const { repo_path, action } = params;

  switch (action) {
    case "list_files": {
      const pattern = params.pattern || "*";
      const files = listFilesGlob(repo_path, pattern);
      return { action, data: files };
    }
    case "read_file": {
      if (!params.file_path) return { action, data: { error: "file_path required" } };
      const fullPath = path.resolve(repo_path, params.file_path);
      if (!fullPath.startsWith(path.resolve(repo_path))) {
        return { action, data: { error: "Path traversal not allowed" } };
      }
      try {
        const content = fs.readFileSync(fullPath, "utf8");
        return { action, data: content };
      } catch (err: any) {
        return { action, data: { error: err.message } };
      }
    }
    case "find_pattern": {
      if (!params.pattern) return { action, data: { error: "pattern required" } };
      const matches = findPattern(repo_path, params.pattern);
      return { action, data: matches };
    }
    case "tree": {
      const tree = buildTree(repo_path, params.max_depth || 3);
      return { action, data: tree };
    }
    default:
      return { action, data: { error: `Unknown action: ${action}` } };
  }
}

/**
 * §4.3 — Detect framework from project files.
 */
export async function detectFramework(repoPath: string): Promise<FrameworkDetection[]> {
  const results: FrameworkDetection[] = [];

  // package.json (Node.js ecosystem)
  const pkgPath = path.join(repoPath, "package.json");
  if (fs.existsSync(pkgPath)) {
    try {
      const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
      const allDeps = { ...pkg.dependencies, ...pkg.devDependencies };

      if (allDeps["next"]) {
        results.push({ framework: "Next.js", version: allDeps["next"], confidence: 0.95, evidence: ["package.json dependency: next"], runtime: "node" });
      } else if (allDeps["nuxt"]) {
        results.push({ framework: "Nuxt.js", version: allDeps["nuxt"], confidence: 0.95, evidence: ["package.json dependency: nuxt"], runtime: "node" });
      } else if (allDeps["@angular/core"]) {
        results.push({ framework: "Angular", version: allDeps["@angular/core"], confidence: 0.95, evidence: ["package.json dependency: @angular/core"], runtime: "node" });
      } else if (allDeps["react"]) {
        results.push({ framework: "React", version: allDeps["react"], confidence: 0.8, evidence: ["package.json dependency: react"], runtime: "node" });
      }

      if (allDeps["express"]) {
        results.push({ framework: "Express", version: allDeps["express"], confidence: 0.9, evidence: ["package.json dependency: express"], runtime: "node" });
      } else if (allDeps["fastify"]) {
        results.push({ framework: "Fastify", version: allDeps["fastify"], confidence: 0.9, evidence: ["package.json dependency: fastify"], runtime: "node" });
      }
    } catch { /* ignore parse errors */ }
  }

  // requirements.txt (Python)
  const reqPath = path.join(repoPath, "requirements.txt");
  if (fs.existsSync(reqPath)) {
    const content = fs.readFileSync(reqPath, "utf8");
    if (/^django[=~>]/im.test(content)) {
      const match = content.match(/^django[=~>]=?(.+)/im);
      results.push({ framework: "Django", version: match?.[1]?.trim(), confidence: 0.95, evidence: ["requirements.txt: django"], runtime: "python" });
    } else if (/^flask[=~>]/im.test(content)) {
      const match = content.match(/^flask[=~>]=?(.+)/im);
      results.push({ framework: "Flask", version: match?.[1]?.trim(), confidence: 0.95, evidence: ["requirements.txt: flask"], runtime: "python" });
    } else if (/^fastapi[=~>]/im.test(content)) {
      results.push({ framework: "FastAPI", confidence: 0.95, evidence: ["requirements.txt: fastapi"], runtime: "python" });
    }
  }

  // Gemfile (Ruby)
  const gemfilePath = path.join(repoPath, "Gemfile");
  if (fs.existsSync(gemfilePath)) {
    const content = fs.readFileSync(gemfilePath, "utf8");
    if (/gem ['"]rails['"]/.test(content)) {
      results.push({ framework: "Rails", confidence: 0.95, evidence: ["Gemfile: rails"], runtime: "ruby" });
    } else if (/gem ['"]sinatra['"]/.test(content)) {
      results.push({ framework: "Sinatra", confidence: 0.9, evidence: ["Gemfile: sinatra"], runtime: "ruby" });
    }
  }

  // go.mod (Go)
  const goModPath = path.join(repoPath, "go.mod");
  if (fs.existsSync(goModPath)) {
    const content = fs.readFileSync(goModPath, "utf8");
    results.push({ framework: "Go", confidence: 0.9, evidence: ["go.mod present"], runtime: "go" });
    if (/gin-gonic\/gin/.test(content)) {
      results.push({ framework: "Gin", confidence: 0.95, evidence: ["go.mod: gin-gonic/gin"], runtime: "go" });
    }
  }

  // pom.xml (Java)
  const pomPath = path.join(repoPath, "pom.xml");
  if (fs.existsSync(pomPath)) {
    const content = fs.readFileSync(pomPath, "utf8");
    if (/spring-boot/.test(content)) {
      results.push({ framework: "Spring Boot", confidence: 0.95, evidence: ["pom.xml: spring-boot"], runtime: "java" });
    } else {
      results.push({ framework: "Java/Maven", confidence: 0.8, evidence: ["pom.xml present"], runtime: "java" });
    }
  }

  // Cargo.toml (Rust)
  const cargoPath = path.join(repoPath, "Cargo.toml");
  if (fs.existsSync(cargoPath)) {
    const content = fs.readFileSync(cargoPath, "utf8");
    if (/actix-web/.test(content)) {
      results.push({ framework: "Actix Web", confidence: 0.95, evidence: ["Cargo.toml: actix-web"], runtime: "rust" });
    } else {
      results.push({ framework: "Rust", confidence: 0.8, evidence: ["Cargo.toml present"], runtime: "rust" });
    }
  }

  // composer.json (PHP)
  const composerPath = path.join(repoPath, "composer.json");
  if (fs.existsSync(composerPath)) {
    try {
      const composer = JSON.parse(fs.readFileSync(composerPath, "utf8"));
      const req = composer.require || {};
      if (req["laravel/framework"]) {
        results.push({ framework: "Laravel", version: req["laravel/framework"], confidence: 0.95, evidence: ["composer.json: laravel/framework"], runtime: "php" });
      } else {
        results.push({ framework: "PHP/Composer", confidence: 0.7, evidence: ["composer.json present"], runtime: "php" });
      }
    } catch { /* ignore */ }
  }

  return results;
}

/**
 * §4.4 — Detect build strategy.
 */
export async function detectBuildStrategy(repoPath: string): Promise<BuildStrategyDetection[]> {
  const results: BuildStrategyDetection[] = [];

  const dockerfile = findFile(repoPath, ["Dockerfile", "dockerfile"]);
  if (dockerfile) {
    results.push({ strategy: "docker", confidence: 0.95, evidence: [`Found ${dockerfile}`], dockerfile_path: dockerfile });
  }

  const composePath = findFile(repoPath, ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]);
  if (composePath) {
    results.push({ strategy: "docker-compose", confidence: 0.9, evidence: [`Found ${composePath}`], compose_path: composePath });
  }

  const procfile = findFile(repoPath, ["Procfile"]);
  if (procfile) {
    results.push({ strategy: "heroku-buildpack", confidence: 0.85, evidence: ["Found Procfile"] });
  }

  if (results.length === 0) {
    // Check for common package managers as fallback
    if (fs.existsSync(path.join(repoPath, "package.json"))) {
      results.push({ strategy: "npm", confidence: 0.6, evidence: ["package.json present, no Dockerfile"] });
    }
  }

  return results;
}

/**
 * §4.5 — Detect services (databases, caches, queues).
 */
export async function detectServices(
  repoPath: string,
  sshParams?: SshExecParams,
  searchScope: "repo" | "server" | "both" = "repo",
): Promise<ServiceDetection[]> {
  const results: ServiceDetection[] = [];

  if (searchScope === "repo" || searchScope === "both") {
    // Check docker-compose for service definitions
    const composePath = findFile(repoPath, ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]);
    if (composePath) {
      const content = fs.readFileSync(path.join(repoPath, composePath), "utf8");
      if (/postgres/i.test(content)) results.push({ name: "PostgreSQL", type: "database", source: "docker-compose", confidence: 0.9 });
      if (/mysql|mariadb/i.test(content)) results.push({ name: "MySQL", type: "database", source: "docker-compose", confidence: 0.9 });
      if (/mongo/i.test(content)) results.push({ name: "MongoDB", type: "database", source: "docker-compose", confidence: 0.9 });
      if (/redis/i.test(content)) results.push({ name: "Redis", type: "cache", source: "docker-compose", confidence: 0.9 });
      if (/rabbitmq/i.test(content)) results.push({ name: "RabbitMQ", type: "queue", source: "docker-compose", confidence: 0.9 });
      if (/elasticsearch|opensearch/i.test(content)) results.push({ name: "Elasticsearch", type: "search", source: "docker-compose", confidence: 0.9 });
      if (/kafka/i.test(content)) results.push({ name: "Kafka", type: "queue", source: "docker-compose", confidence: 0.9 });
    }

    // Check env files for database URLs
    for (const envFile of [".env.example", ".env.template", ".env.sample"]) {
      const envPath = path.join(repoPath, envFile);
      if (fs.existsSync(envPath)) {
        const content = fs.readFileSync(envPath, "utf8");
        if (/DATABASE_URL|POSTGRES/i.test(content) && !results.some(r => r.name === "PostgreSQL")) {
          results.push({ name: "PostgreSQL", type: "database", source: envFile, confidence: 0.7 });
        }
        if (/REDIS_URL|REDIS_HOST/i.test(content) && !results.some(r => r.name === "Redis")) {
          results.push({ name: "Redis", type: "cache", source: envFile, confidence: 0.7 });
        }
      }
    }
  }

  if ((searchScope === "server" || searchScope === "both") && sshParams) {
    const psResult = await sshExec({ ...sshParams, command: "ps aux", parse_as: "lines" });
    if (psResult.exit_code === 0 && Array.isArray(psResult.output)) {
      const lines = psResult.output as string[];
      if (lines.some(l => /postgres/i.test(l))) results.push({ name: "PostgreSQL", type: "database", source: "running process", confidence: 0.95 });
      if (lines.some(l => /redis-server/i.test(l))) results.push({ name: "Redis", type: "cache", source: "running process", confidence: 0.95 });
      if (lines.some(l => /mongod/i.test(l))) results.push({ name: "MongoDB", type: "database", source: "running process", confidence: 0.95 });
    }
  }

  return results;
}

/**
 * §4.6 — Detect environment variables.
 */
export async function detectEnvVars(repoPath: string): Promise<EnvVarDetection[]> {
  const results: EnvVarDetection[] = [];
  const seen = new Set<string>();

  for (const envFile of [".env.example", ".env.template", ".env.sample"]) {
    const envPath = path.join(repoPath, envFile);
    if (!fs.existsSync(envPath)) continue;

    const content = fs.readFileSync(envPath, "utf8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const match = trimmed.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
      if (match && !seen.has(match[1])) {
        seen.add(match[1]);
        const hasDefault = match[2].length > 0;
        const isRequired = /required|must|need/i.test(line);
        // Extract inline comment as description
        const commentMatch = trimmed.match(/#\s*(.+)$/);
        results.push({
          name: match[1],
          required: isRequired,
          has_default: hasDefault,
          source: envFile,
          description: commentMatch?.[1],
        });
      }
    }
  }

  return results;
}

/**
 * §4.7 — Detect ports.
 */
export async function detectPorts(
  repoPath: string,
  sshParams?: SshExecParams,
  searchScope: "repo" | "server" | "both" = "repo",
): Promise<PortDetection[]> {
  const results: PortDetection[] = [];
  const seen = new Set<number>();

  if (searchScope === "repo" || searchScope === "both") {
    // Check Dockerfile EXPOSE
    const dockerfile = findFile(repoPath, ["Dockerfile", "dockerfile"]);
    if (dockerfile) {
      const content = fs.readFileSync(path.join(repoPath, dockerfile), "utf8");
      const exposeMatches = content.matchAll(/^EXPOSE\s+(\d+)/gm);
      for (const m of exposeMatches) {
        const port = parseInt(m[1], 10);
        if (!seen.has(port)) {
          seen.add(port);
          results.push({ port, source: "Dockerfile EXPOSE", confidence: 0.9 });
        }
      }
    }

    // Check docker-compose ports
    const composePath = findFile(repoPath, ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]);
    if (composePath) {
      const content = fs.readFileSync(path.join(repoPath, composePath), "utf8");
      const portMatches = content.matchAll(/['"]?(\d+):(\d+)['"]?/g);
      for (const m of portMatches) {
        const port = parseInt(m[1], 10);
        if (!seen.has(port)) {
          seen.add(port);
          results.push({ port, source: "docker-compose ports", confidence: 0.85 });
        }
      }
    }

    // Check for common listen() patterns in code
    const codeFiles = listFilesGlob(repoPath, "*.{js,ts,py,rb,go}");
    for (const file of codeFiles.slice(0, 20)) {
      try {
        const content = fs.readFileSync(path.join(repoPath, file), "utf8");
        const listenMatches = content.matchAll(/\.listen\(\s*(\d+)/g);
        for (const m of listenMatches) {
          const port = parseInt(m[1], 10);
          if (!seen.has(port)) {
            seen.add(port);
            results.push({ port, source: `code: ${file}`, confidence: 0.8 });
          }
        }
      } catch { /* skip unreadable files */ }
    }
  }

  if ((searchScope === "server" || searchScope === "both") && sshParams) {
    const ssResult = await sshExec({ ...sshParams, command: "ss -tlnp", parse_as: "lines" });
    if (ssResult.exit_code === 0 && Array.isArray(ssResult.output)) {
      for (const line of ssResult.output as string[]) {
        const match = (line as string).match(/:(\d+)\s/);
        if (match) {
          const port = parseInt(match[1], 10);
          if (!seen.has(port)) {
            seen.add(port);
            results.push({ port, source: "server listening", confidence: 0.95 });
          }
        }
      }
    }
  }

  return results;
}

/**
 * §4.8 — Detect health endpoint.
 */
export async function detectHealthEndpoint(
  repoPath: string,
  sshParams?: SshExecParams,
  appPort?: number,
): Promise<HealthEndpointDetection[]> {
  const results: HealthEndpointDetection[] = [];
  const commonPaths = ["/health", "/healthz", "/health/live", "/health/ready", "/api/health", "/_health", "/ping", "/status"];

  // Scan code for health route definitions
  const codeFiles = listFilesGlob(repoPath, "*.{js,ts,py,rb,go}");
  for (const file of codeFiles.slice(0, 30)) {
    try {
      const content = fs.readFileSync(path.join(repoPath, file), "utf8");
      for (const hp of commonPaths) {
        if (content.includes(`"${hp}"`) || content.includes(`'${hp}'`)) {
          results.push({ path: hp, method: "GET", source: `code: ${file}`, confidence: 0.85 });
        }
      }
    } catch { /* skip */ }
  }

  // Try hitting common health endpoints on server
  if (sshParams && appPort) {
    for (const hp of ["/health", "/healthz", "/ping"]) {
      const curlResult = await sshExec({
        ...sshParams,
        command: `curl -s -o /dev/null -w "%{http_code}" http://localhost:${appPort}${hp}`,
        timeout_ms: 5000,
      });
      if (curlResult.exit_code === 0 && curlResult.output) {
        const code = parseInt(String(curlResult.output).trim(), 10);
        if (code >= 200 && code < 400) {
          results.push({ path: hp, method: "GET", source: "live server response", status_code: code, confidence: 0.95 });
        }
      }
    }
  }

  return results;
}

/**
 * §4.9 — Generate a structured question for the user.
 */
export function askUser(
  question: string,
  context: string,
  field: string,
  options?: string[],
  defaultValue?: string,
): UserQuestion {
  return { question, context, field, options, default: defaultValue };
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function findFile(basePath: string, candidates: string[]): string | null {
  for (const name of candidates) {
    if (fs.existsSync(path.join(basePath, name))) return name;
  }
  return null;
}

function listFilesGlob(basePath: string, pattern: string): string[] {
  const results: string[] = [];
  const extensions = pattern.match(/\{(.+)\}/)?.[1]?.split(",") || [pattern.replace("*.", "")];

  function walk(dir: string, depth: number) {
    if (depth > 5) return;
    try {
      const entries = fs.readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.name.startsWith(".") || entry.name === "node_modules" || entry.name === "dist") continue;
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full, depth + 1);
        } else if (extensions.some(ext => entry.name.endsWith(`.${ext}`))) {
          results.push(path.relative(basePath, full));
        }
      }
    } catch { /* skip inaccessible dirs */ }
  }

  walk(basePath, 0);
  return results;
}

function findPattern(basePath: string, pattern: string): Array<{ file: string; line: number; text: string }> {
  const results: Array<{ file: string; line: number; text: string }> = [];
  const allFiles = listFilesGlob(basePath, "*.{js,ts,json,py,rb,go,yaml,yml,toml}");

  for (const file of allFiles.slice(0, 50)) {
    try {
      const content = fs.readFileSync(path.join(basePath, file), "utf8");
      const lines = content.split("\n");
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].includes(pattern)) {
          results.push({ file, line: i + 1, text: lines[i].trim() });
        }
      }
    } catch { /* skip */ }
  }

  return results;
}

function buildTree(basePath: string, maxDepth: number): string[] {
  const results: string[] = [];

  function walk(dir: string, prefix: string, depth: number) {
    if (depth > maxDepth) return;
    try {
      const entries = fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name));
      for (let i = 0; i < entries.length; i++) {
        const entry = entries[i];
        if (entry.name.startsWith(".") || entry.name === "node_modules" || entry.name === "dist") continue;
        const isLast = i === entries.length - 1;
        const connector = isLast ? "└── " : "├── ";
        results.push(`${prefix}${connector}${entry.name}${entry.isDirectory() ? "/" : ""}`);
        if (entry.isDirectory()) {
          walk(path.join(dir, entry.name), prefix + (isLast ? "    " : "│   "), depth + 1);
        }
      }
    } catch { /* skip */ }
  }

  walk(basePath, "", 0);
  return results;
}
