/**
 * Shared STDB helpers — temp instance management and module discovery.
 * Extracted from router.ts so executor.ts can reuse them.
 */

import { mkdtempSync, rmSync, unlinkSync, existsSync } from "fs";
import { join } from "path";
import { homedir, tmpdir } from "os";
import { spawn } from "child_process";
import { sqlQuery } from "../spacetimedb/client.js";
import { KNOWN_MODULE_NAMES } from "./schema-versions.js";

const SPACETIMEDB_BIN = join(
  homedir(), ".local", "share", "spacetime", "bin", "2.0.2", "spacetimedb-standalone",
);
const JWT_PUB_KEY = join(homedir(), ".config", "spacetime", "id_ecdsa.pub");
const JWT_PRIV_KEY = join(homedir(), ".config", "spacetime", "id_ecdsa");

export interface TempInstance {
  port: number;
  proc: ReturnType<typeof spawn>;
  tmpDir: string;
  cleanup: () => void;
}

/**
 * Start a temporary SpacetimeDB instance from a backup archive.
 */
export async function startTempInstance(backupPath: string): Promise<TempInstance> {
  const tmpDir = mkdtempSync(join(tmpdir(), "bond-restore-"));

  // Extract backup archive
  await new Promise<void>((resolve, reject) => {
    const tar = spawn("tar", ["-xzf", backupPath, "-C", tmpDir]);
    tar.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`tar extract failed (code ${code})`))));
    tar.on("error", reject);
  });

  // Remove stale pid file
  const pidFile = join(tmpDir, "spacetime.pid");
  if (existsSync(pidFile)) try { unlinkSync(pidFile); } catch {}

  // Pick a random high port
  const port = 19000 + Math.floor(Math.random() * 1000);
  const args = ["start", "--data-dir", tmpDir, "--listen-addr", `127.0.0.1:${port}`];
  if (existsSync(JWT_PUB_KEY) && existsSync(JWT_PRIV_KEY)) {
    args.push("--jwt-pub-key-path", JWT_PUB_KEY, "--jwt-priv-key-path", JWT_PRIV_KEY);
  }

  console.log(`[backups] Starting temp STDB: ${SPACETIMEDB_BIN} ${args.join(" ")}`);
  const proc = spawn(SPACETIMEDB_BIN, args, { stdio: ["ignore", "pipe", "pipe"] });
  let stderrBuf = "";
  proc.stderr?.on("data", (chunk: Buffer) => { stderrBuf += chunk.toString(); });
  proc.stdout?.on("data", () => { /* drain */ });

  const cleanup = () => {
    try { proc.kill("SIGTERM"); } catch {}
    setTimeout(() => { try { rmSync(tmpDir, { recursive: true, force: true }); } catch {} }, 2000);
  };

  // Wait for HTTP to be reachable (up to 120s for commit log replay)
  const deadline = Date.now() + 120_000;
  let connected = false;
  while (Date.now() < deadline) {
    if (proc.exitCode !== null) {
      cleanup();
      throw new Error(`SpacetimeDB temp instance died (code ${proc.exitCode}): ${stderrBuf.slice(-500)}`);
    }
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const resp = await fetch(`http://127.0.0.1:${port}/v1/health`);
      if (resp.ok) { connected = true; break; }
    } catch { /* not ready */ }
  }
  if (!connected) {
    cleanup();
    throw new Error(`SpacetimeDB temp instance failed to start within 120s. Stderr: ${stderrBuf.slice(-500)}`);
  }

  console.log(`[backups] Temp STDB instance ready on port ${port}`);
  return { port, proc, tmpDir, cleanup };
}

/**
 * Discover which module exists in a backup instance.
 */
export async function findModuleName(tempUrl: string): Promise<string> {
  for (const mod of KNOWN_MODULE_NAMES) {
    try {
      await sqlQuery(tempUrl, mod, "SELECT 1", "");
      console.log(`[backups] Found module: ${mod}`);
      return mod;
    } catch (e: any) {
      const msg = e?.message || "";
      if (msg.includes("not found") || msg.includes("No such")) continue;
      console.log(`[backups] Found module: ${mod} (query error: ${msg.slice(0, 100)})`);
      return mod;
    }
  }
  throw new Error(`No known module found in backup. Tried: ${KNOWN_MODULE_NAMES.join(", ")}`);
}
