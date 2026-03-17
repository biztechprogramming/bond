/**
 * Folder Browser — browse workspace directories and analyze folder contents.
 *
 * Endpoints:
 *   GET  /browse/workspaces       → list workspace mount roots
 *   GET  /browse/folders?path=... → list subdirectories
 *   POST /browse/analyze          → heuristic analysis of a folder
 */

import { Router } from "express";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import type { GatewayConfig } from "../config/index.js";
import { sqlQuery } from "../spacetimedb/client.js";

interface WorkspaceRoot {
  path: string;          // container path (what agents see)
  hostPath: string;      // host path (for filesystem access)
  name: string;
}

interface FolderEntry {
  name: string;
  path: string;
  hasChildren: boolean;
}

interface AnalysisResult {
  display_name?: string;
  component_type?: string;
  runtime?: string;
  framework?: string;
  description?: string;
  repository_url?: string;
  icon?: string;
  port?: number;
}

/** Resolve ~ to actual home directory. */
function resolveTilde(p: string): string {
  if (p.startsWith("~/")) return path.join(os.homedir(), p.slice(2));
  if (p === "~") return os.homedir();
  return p;
}

/** Hidden/config dirs that aren't useful project workspaces. */
const HIDDEN_DIR_PREFIXES = [".", ".ssh", ".claude", ".config", ".local", ".cache"];

function isHiddenWorkspace(p: string): boolean {
  const base = path.basename(p);
  return HIDDEN_DIR_PREFIXES.some((h) => base === h || base.startsWith("."));
}

/** Query SpacetimeDB for unique host_path entries from agent_workspace_mounts. */
async function getWorkspaceRoots(config: GatewayConfig): Promise<WorkspaceRoot[]> {
  const { spacetimedbUrl, spacetimedbModuleName, spacetimedbToken } = config;
  if (!spacetimedbUrl || !spacetimedbModuleName) return [];

  try {
    // SpacetimeDB doesn't support SELECT DISTINCT — de-dup in code
    // sqlQuery throws if token is empty; fall back to direct fetch for tokenless local setups
    let rows: any[];
    const sql = "SELECT host_path, container_path, mount_name FROM agent_workspace_mounts";
    if (spacetimedbToken) {
      rows = await sqlQuery(spacetimedbUrl, spacetimedbModuleName, sql, spacetimedbToken);
    } else {
      const res = await fetch(`${spacetimedbUrl}/v1/database/${spacetimedbModuleName}/sql`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: sql,
      });
      if (!res.ok) throw new Error(`SQL query failed: ${res.status}`);
      const data = await res.json();
      if (!data?.[0]?.rows || !data[0].schema) return [];
      const columns = data[0].schema.elements.map((e: any) => e.name?.some || e.name);
      rows = data[0].rows.map((row: any[]) => {
        const obj: any = {};
        columns.forEach((col: string, i: number) => { obj[col] = row[i]; });
        return obj;
      });
    }

    const seen = new Set<string>();
    const roots: WorkspaceRoot[] = [];
    for (const row of rows) {
      const rawHostPath = row.host_path as string;
      const containerPath = row.container_path as string;
      if (!rawHostPath || !containerPath) continue;
      const resolvedHost = resolveTilde(rawHostPath);
      // De-dup by container path
      if (seen.has(containerPath)) continue;
      seen.add(containerPath);
      // Skip hidden/config directories
      if (isHiddenWorkspace(resolvedHost)) continue;
      // Only include paths that actually exist on host
      try {
        if (!fs.statSync(resolvedHost).isDirectory()) continue;
      } catch { continue; }
      const name = path.basename(containerPath) || path.basename(resolvedHost);
      roots.push({ path: containerPath, hostPath: resolvedHost, name });
    }
    return roots.sort((a, b) => a.name.localeCompare(b.name));
  } catch (err) {
    console.warn("[folder-browser] Failed to query workspace mounts:", (err as Error).message);
    return [];
  }
}

/** Check if a container path is inside one of the allowed workspace roots. Returns the matching root or null. */
function findMatchingRoot(containerPath: string, roots: WorkspaceRoot[]): WorkspaceRoot | null {
  const resolved = path.resolve(containerPath);
  for (const r of roots) {
    const rootResolved = path.resolve(r.path);
    if (resolved === rootResolved || resolved.startsWith(rootResolved + path.sep)) {
      return r;
    }
  }
  return null;
}

/** Convert a container path to the corresponding host path for filesystem access. */
function containerToHost(containerPath: string, root: WorkspaceRoot): string {
  const relative = path.relative(root.path, containerPath);
  return relative ? path.join(root.hostPath, relative) : root.hostPath;
}

/** List immediate subdirectories of a path. */
function listSubdirectories(dirPath: string): FolderEntry[] {
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    return entries
      .filter((e) => e.isDirectory() && !e.name.startsWith("."))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((e) => {
        const fullPath = path.join(dirPath, e.name);
        let hasChildren = false;
        try {
          const sub = fs.readdirSync(fullPath, { withFileTypes: true });
          hasChildren = sub.some((s) => s.isDirectory());
        } catch { /* unreadable */ }
        return { name: e.name, path: fullPath, hasChildren };
      });
  } catch {
    return [];
  }
}

/** Read a JSON file safely, returning null on failure. */
function readJson(filePath: string): any | null {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

/** Read first non-empty line from a file. */
function firstLine(filePath: string): string | null {
  try {
    const content = fs.readFileSync(filePath, "utf8");
    for (const line of content.split("\n")) {
      const trimmed = line.replace(/^#+\s*/, "").trim();
      if (trimmed) return trimmed;
    }
  } catch { /* ignore */ }
  return null;
}

/** Extract repo URL from .git/config. */
function gitRemoteUrl(dirPath: string): string | null {
  try {
    const gitConfig = fs.readFileSync(path.join(dirPath, ".git", "config"), "utf8");
    const match = gitConfig.match(/url\s*=\s*(.+)/);
    if (match) return match[1].trim();
  } catch { /* ignore */ }
  return null;
}

/** Analyze a directory and return heuristic guesses for component fields. */
function analyzeFolder(dirPath: string): AnalysisResult {
  const files = fs.readdirSync(dirPath);
  const has = (name: string) => files.includes(name);
  const result: AnalysisResult = {};

  result.display_name = path.basename(dirPath);

  // Repository URL
  const gitUrl = gitRemoteUrl(dirPath);
  const pkg = has("package.json") ? readJson(path.join(dirPath, "package.json")) : null;
  if (gitUrl) {
    result.repository_url = gitUrl;
  } else if (pkg?.repository?.url) {
    result.repository_url = pkg.repository.url;
  } else if (typeof pkg?.repository === "string") {
    result.repository_url = pkg.repository;
  }

  // Description
  if (pkg?.description) {
    result.description = pkg.description;
  } else if (has("README.md")) {
    const line = firstLine(path.join(dirPath, "README.md"));
    if (line) result.description = line;
  }

  // Display name from package.json
  if (pkg?.name) {
    result.display_name = pkg.name.replace(/^@[^/]+\//, "");
  }

  // Docker Compose → infrastructure/system
  if (has("docker-compose.yml") || has("docker-compose.yaml") || has("compose.yml") || has("compose.yaml")) {
    result.component_type = "system";
    result.icon = "🐳";
    return result;
  }

  // Node.js
  if (pkg) {
    result.runtime = "node";
    result.icon = "📦";
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    if (deps?.next) { result.framework = "next"; result.port = 3000; result.component_type = "web-server"; result.icon = "▲"; }
    else if (deps?.express) { result.framework = "express"; result.port = 3000; result.component_type = "web-server"; }
    else if (deps?.fastify) { result.framework = "fastify"; result.port = 3000; result.component_type = "web-server"; }
    else if (deps?.react && !deps?.next) { result.framework = "react"; result.port = 3000; result.component_type = "web-server"; result.icon = "⚛️"; }
    else if (deps?.vue) { result.framework = "vue"; result.port = 5173; result.component_type = "web-server"; }
    else { result.component_type = "application"; }
    return result;
  }

  // Python
  if (has("requirements.txt") || has("pyproject.toml") || has("setup.py")) {
    result.runtime = "python";
    result.icon = "🐍";
    result.component_type = "application";
    try {
      const reqContent = has("requirements.txt") ? fs.readFileSync(path.join(dirPath, "requirements.txt"), "utf8") : "";
      if (reqContent.includes("django")) { result.framework = "django"; result.port = 8000; result.component_type = "web-server"; }
      else if (reqContent.includes("flask")) { result.framework = "flask"; result.port = 5000; result.component_type = "web-server"; }
      else if (reqContent.includes("fastapi")) { result.framework = "fastapi"; result.port = 8000; result.component_type = "web-server"; }
    } catch { /* ignore */ }
    return result;
  }

  // Go
  if (has("go.mod")) {
    result.runtime = "go";
    result.icon = "🔵";
    result.component_type = "application";
    result.port = 8080;
    return result;
  }

  // Rust
  if (has("Cargo.toml")) {
    result.runtime = "rust";
    result.icon = "🦀";
    result.component_type = "application";
    result.port = 8080;
    return result;
  }

  // Dockerfile only
  if (has("Dockerfile")) {
    result.component_type = "application";
    result.icon = "🐳";
    result.port = 8080;
    return result;
  }

  // Static site
  if (has("index.html")) {
    result.component_type = "web-server";
    result.icon = "🌐";
    result.port = 3000;
    return result;
  }

  result.component_type = "application";
  result.icon = "📁";
  return result;
}

export function createFolderBrowserRouter(config: GatewayConfig): Router {
  const router = Router();
  let cachedRoots: WorkspaceRoot[] | null = null;

  async function getRoots(): Promise<WorkspaceRoot[]> {
    if (!cachedRoots) cachedRoots = await getWorkspaceRoots(config);
    return cachedRoots;
  }

  // GET /browse/workspaces — returns container paths
  router.get("/workspaces", async (_req: any, res: any) => {
    try {
      cachedRoots = null; // refresh each time
      const roots = await getRoots();
      // Return only what the frontend needs (container paths)
      res.json(roots.map((r) => ({ path: r.path, name: r.name })));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /browse/folders?path=... — accepts and returns container paths
  router.get("/folders", async (req: any, res: any) => {
    const containerDir = req.query.path as string;
    if (!containerDir) return res.status(400).json({ error: "path query param required" });

    try {
      const roots = await getRoots();
      const root = findMatchingRoot(containerDir, roots);
      if (!root) {
        return res.status(403).json({ error: "Path is outside allowed workspaces" });
      }
      const hostDir = containerToHost(containerDir, root);
      const hostFolders = listSubdirectories(hostDir);
      // Convert host paths back to container paths
      const folders = hostFolders.map((f) => ({
        name: f.name,
        path: path.join(containerDir, f.name),
        hasChildren: f.hasChildren,
      }));
      res.json({ path: containerDir, folders });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /browse/analyze — accepts container path
  router.post("/analyze", async (req: any, res: any) => {
    const { path: containerDir } = req.body || {};
    if (!containerDir) return res.status(400).json({ error: "path is required in body" });

    try {
      const roots = await getRoots();
      const root = findMatchingRoot(containerDir, roots);
      if (!root) {
        return res.status(403).json({ error: "Path is outside allowed workspaces" });
      }
      const hostDir = containerToHost(containerDir, root);
      if (!fs.existsSync(hostDir) || !fs.statSync(hostDir).isDirectory()) {
        return res.status(404).json({ error: "Directory not found" });
      }
      const analysis = analyzeFolder(hostDir);
      res.json(analysis);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
