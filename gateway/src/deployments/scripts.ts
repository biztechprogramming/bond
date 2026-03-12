/**
 * Deployment Script Registry — filesystem management.
 *
 * Scripts are stored in ~/.bond/deployments/scripts/registry/ on the HOST.
 * This directory is NEVER mounted into agent containers.
 * Agents reference scripts by ID — only the broker reads and executes them.
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

export interface ScriptManifest {
  script_id: string;
  version: string;
  name: string;
  description?: string;
  timeout?: number;
  depends_on?: string[];
  rollback?: string;
  dry_run?: boolean;
  health_check?: string;
  sha256: string;
  registered_at: string;
  registered_by: string;
  files: string[];
}

export interface RegisterScriptOptions {
  script_id: string;
  version: string;
  name: string;
  description?: string;
  timeout?: number;
  depends_on?: string[];
  rollback?: string;
  dry_run?: boolean;
  health_check?: string;
  registered_by: string;
  files: Record<string, Buffer>; // filename → content; "deploy.sh" is required
}

export function getRegistryDir(deploymentsDir: string): string {
  return path.join(deploymentsDir, "scripts", "registry");
}

export function getScriptVersionDir(deploymentsDir: string, scriptId: string, version: string): string {
  return path.join(getRegistryDir(deploymentsDir), scriptId, version);
}

/**
 * Register a new script version. Creates an immutable snapshot in the registry.
 * Returns the SHA-256 hash of the bundle.
 */
export function registerScript(deploymentsDir: string, opts: RegisterScriptOptions): ScriptManifest {
  if (!opts.files["deploy.sh"]) {
    throw new Error("deploy.sh is required");
  }

  const versionDir = getScriptVersionDir(deploymentsDir, opts.script_id, opts.version);

  if (fs.existsSync(versionDir)) {
    throw new Error(`Script ${opts.script_id}@${opts.version} already exists`);
  }

  fs.mkdirSync(versionDir, { recursive: true });

  // Write all files
  for (const [filename, content] of Object.entries(opts.files)) {
    const filePath = path.join(versionDir, filename);
    const fileDir = path.dirname(filePath);
    fs.mkdirSync(fileDir, { recursive: true });
    fs.writeFileSync(filePath, content, { mode: 0o755 });
  }

  // Compute SHA-256 of all files combined (sorted for determinism)
  const hash = crypto.createHash("sha256");
  for (const filename of Object.keys(opts.files).sort()) {
    hash.update(filename);
    hash.update(opts.files[filename]!);
  }
  const sha256 = hash.digest("hex");

  // Write .sha256 file
  fs.writeFileSync(path.join(versionDir, ".sha256"), sha256, "utf8");

  // Write manifest.json
  const manifest: ScriptManifest = {
    script_id: opts.script_id,
    version: opts.version,
    name: opts.name,
    description: opts.description,
    timeout: opts.timeout,
    depends_on: opts.depends_on || [],
    rollback: opts.rollback,
    dry_run: opts.dry_run,
    health_check: opts.health_check,
    sha256,
    registered_at: new Date().toISOString(),
    registered_by: opts.registered_by,
    files: Object.keys(opts.files),
  };
  fs.writeFileSync(
    path.join(versionDir, "manifest.json"),
    JSON.stringify(manifest, null, 2),
    "utf8",
  );

  return manifest;
}

/**
 * Read manifest for a script version. Returns null if not found.
 */
export function getManifest(
  deploymentsDir: string,
  scriptId: string,
  version: string,
): ScriptManifest | null {
  const manifestPath = path.join(
    getScriptVersionDir(deploymentsDir, scriptId, version),
    "manifest.json",
  );
  if (!fs.existsSync(manifestPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch {
    return null;
  }
}

/**
 * Verify SHA-256 of a script bundle. Returns true if valid.
 */
export function verifyScriptHash(
  deploymentsDir: string,
  scriptId: string,
  version: string,
): boolean {
  const versionDir = getScriptVersionDir(deploymentsDir, scriptId, version);
  const sha256Path = path.join(versionDir, ".sha256");
  const manifestPath = path.join(versionDir, "manifest.json");

  if (!fs.existsSync(sha256Path) || !fs.existsSync(manifestPath)) return false;

  const storedHash = fs.readFileSync(sha256Path, "utf8").trim();
  const manifest: ScriptManifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));

  // Recompute hash from files listed in manifest
  const hash = crypto.createHash("sha256");
  for (const filename of manifest.files.sort()) {
    const filePath = path.join(versionDir, filename);
    if (!fs.existsSync(filePath)) return false;
    hash.update(filename);
    hash.update(fs.readFileSync(filePath));
  }

  return hash.digest("hex") === storedHash;
}

/**
 * List all registered scripts (id → versions[]).
 */
export function listScripts(deploymentsDir: string): Array<{ script_id: string; versions: string[] }> {
  const registryDir = getRegistryDir(deploymentsDir);
  if (!fs.existsSync(registryDir)) return [];

  const result: Array<{ script_id: string; versions: string[] }> = [];
  for (const scriptId of fs.readdirSync(registryDir)) {
    const scriptDir = path.join(registryDir, scriptId);
    if (!fs.statSync(scriptDir).isDirectory()) continue;
    const versions = fs.readdirSync(scriptDir)
      .filter(v => fs.statSync(path.join(scriptDir, v)).isDirectory());
    result.push({ script_id: scriptId, versions });
  }
  return result;
}

/**
 * Parse script metadata comments from a bash script.
 * Lines like: # meta:name: My Script Name
 */
export function parseScriptMeta(content: string): Record<string, string> {
  const meta: Record<string, string> = {};
  for (const line of content.split("\n")) {
    const match = line.match(/^#\s*meta:(\w+):\s*(.+)$/);
    if (match) {
      meta[match[1]!] = match[2]!.trim();
    }
  }
  return meta;
}
