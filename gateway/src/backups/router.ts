/**
 * Backups API — list, preview, and restore SpacetimeDB backups.
 *
 * Backups are tar.gz archives of the full STDB data directory.
 * Restoring spins up a temporary standalone STDB instance from the backup,
 * reads ALL tables via SQL, maps old-schema rows to the current schema
 * (filling in defaults for new fields), and imports via reducers into the
 * live bond-core-v2 database.
 */

import { Router } from "express";
import { readdirSync, statSync, mkdtempSync, rmSync, unlinkSync, existsSync } from "fs";
import { join } from "path";
import { homedir, tmpdir } from "os";
import { spawn } from "child_process";
import type { GatewayConfig } from "../config/index.js";
import { sqlQuery, callReducer } from "../spacetimedb/client.js";
import {
  KNOWN_MODULE_NAMES,
  getCurrentTables,
  mapRowToCurrentSchema,
  type TableDef,
} from "./schema-versions.js";

const BACKUP_DIR = join(homedir(), ".bond", "backups", "spacetimedb");
const SPACETIMEDB_BIN = join(
  homedir(), ".local", "share", "spacetime", "bin", "2.0.2", "spacetimedb-standalone",
);
const JWT_PUB_KEY = join(homedir(), ".config", "spacetime", "id_ecdsa.pub");
const JWT_PRIV_KEY = join(homedir(), ".config", "spacetime", "id_ecdsa");

interface BackupEntry {
  filename: string;
  tier: string;
  size_bytes: number;
  created_at: string;
  path: string;
}

function listBackups(): BackupEntry[] {
  const tiers = ["daily", "weekly", "monthly"];
  const backups: BackupEntry[] = [];
  for (const tier of tiers) {
    const dir = join(BACKUP_DIR, tier);
    try {
      for (const file of readdirSync(dir)) {
        if (!file.endsWith(".tar.gz")) continue;
        const fullPath = join(dir, file);
        const stat = statSync(fullPath);
        backups.push({ filename: file, tier, size_bytes: stat.size, created_at: stat.mtime.toISOString(), path: fullPath });
      }
    } catch { /* dir doesn't exist */ }
  }
  backups.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  return backups;
}

// ─── Temp STDB instance management ──────────────────────────────────────────

async function startTempInstance(backupPath: string): Promise<{
  port: number;
  proc: ReturnType<typeof spawn>;
  tmpDir: string;
  cleanup: () => void;
}> {
  const tmpDir = mkdtempSync(join(tmpdir(), "bond-restore-"));

  // Extract backup archive to temp directory
  await new Promise<void>((resolve, reject) => {
    const tar = spawn("tar", ["-xzf", backupPath, "-C", tmpDir]);
    tar.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`tar extract failed (code ${code})`))));
    tar.on("error", reject);
  });

  // Remove stale pid file from the backup
  const pidFile = join(tmpDir, "spacetime.pid");
  if (existsSync(pidFile)) try { unlinkSync(pidFile); } catch {}

  // Pick a random high port for the temp instance
  const port = 19000 + Math.floor(Math.random() * 1000);
  const args = ["start", "--data-dir", tmpDir, "--listen-addr", `127.0.0.1:${port}`];
  if (existsSync(JWT_PUB_KEY) && existsSync(JWT_PRIV_KEY)) {
    args.push("--jwt-pub-key-path", JWT_PUB_KEY, "--jwt-priv-key-path", JWT_PRIV_KEY);
  }

  console.log(`[backups] Starting temp STDB: ${SPACETIMEDB_BIN} ${args.join(" ")}`);
  const proc = spawn(SPACETIMEDB_BIN, args, { stdio: ["ignore", "pipe", "pipe"] });
  let stderrBuf = "";
  proc.stderr?.on("data", (chunk: Buffer) => { stderrBuf += chunk.toString(); });
  proc.stdout?.on("data", (chunk: Buffer) => { /* drain stdout */ });

  const cleanup = () => {
    try { proc.kill("SIGTERM"); } catch {}
    setTimeout(() => { try { rmSync(tmpDir, { recursive: true, force: true }); } catch {} }, 2000);
  };

  // Wait for HTTP to be reachable (up to 90s for commit log replay)
  const deadline = Date.now() + 90_000;
  let connected = false;
  while (Date.now() < deadline) {
    // Check if process died
    if (proc.exitCode !== null) {
      cleanup();
      throw new Error(`SpacetimeDB temp instance died (code ${proc.exitCode}): ${stderrBuf.slice(-500)}`);
    }
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const resp = await fetch(`http://127.0.0.1:${port}/v1/health`);
      if (resp.ok) {
        connected = true;
        break;
      }
    } catch {
      // Not ready yet, keep waiting
    }
  }
  if (!connected) {
    cleanup();
    throw new Error(`SpacetimeDB temp instance failed to start within 90s. Stderr: ${stderrBuf.slice(-500)}`);
  }

  console.log(`[backups] Temp STDB instance ready on port ${port}`);
  return { port, proc, tmpDir, cleanup };
}

// ─── Probe which module name exists in the backup ───────────────────────────

/**
 * Discover which module exists in a backup instance.
 * Tries known module names in order, returns the first one that responds.
 */
async function findModuleName(tempUrl: string): Promise<string> {
  for (const mod of KNOWN_MODULE_NAMES) {
    try {
      // Try a simple query — if the module exists, this will succeed or fail
      // with a table-not-found error (which still means the module exists)
      await sqlQuery(tempUrl, mod, "SELECT 1", "");
      console.log(`[backups] Found module: ${mod}`);
      return mod;
    } catch (e: any) {
      const msg = e?.message || "";
      // "not found" means the module doesn't exist — try next
      if (msg.includes("not found") || msg.includes("No such")) continue;
      // Any other error means the module exists but the query had issues
      console.log(`[backups] Found module: ${mod} (query error: ${msg.slice(0, 100)})`);
      return mod;
    }
  }
  throw new Error(`No known module found in backup. Tried: ${KNOWN_MODULE_NAMES.join(", ")}`);
}

// ─── Read all restorable tables from the backup ─────────────────────────────

interface BackupData {
  moduleName: string;
  tables: Record<string, any[]>; // tableName → array of row objects
  totalRows: number;
}

async function readBackupData(tempUrl: string): Promise<BackupData> {
  const moduleName = await findModuleName(tempUrl);
  const currentTables = getCurrentTables();
  const tables: Record<string, any[]> = {};
  let totalRows = 0;

  for (const tableDef of currentTables) {
    try {
      const rows = await sqlQuery(tempUrl, moduleName, `SELECT * FROM ${tableDef.table}`, "");
      tables[tableDef.table] = rows;
      totalRows += rows.length;
      if (rows.length > 0) {
        console.log(`[backups]   ${tableDef.table}: ${rows.length} rows`);
      }
    } catch (e: any) {
      // Table doesn't exist in this backup version — that's fine, skip it
      console.log(`[backups]   ${tableDef.table}: skipped (${e.message?.slice(0, 80)})`);
      tables[tableDef.table] = [];
    }
  }

  return { moduleName, tables, totalRows };
}

// ─── Router ─────────────────────────────────────────────────────────────────

export function createBackupsRouter(config: GatewayConfig) {
  const router = Router();

  // GET / — list available backups
  router.get("/", (_req, res) => {
    try {
      res.json({ backups: listBackups() });
    } catch (err: any) {
      console.error("[backups] list failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // POST /preview — preview a backup's contents
  router.post("/preview", async (req, res) => {
    const { path: backupPath } = req.body;
    if (!backupPath) return res.status(400).json({ error: "path is required" });

    let cleanup: (() => void) | undefined;
    try {
      const temp = await startTempInstance(backupPath);
      cleanup = temp.cleanup;
      const tempUrl = `http://127.0.0.1:${temp.port}`;

      console.log(`[backups] Preview: temp instance on port ${temp.port}`);
      const data = await readBackupData(tempUrl);

      const conversations = data.tables["conversations"] || [];
      const messages = data.tables["conversation_messages"] || [];

      // Collect timestamps for date range
      const timestamps = [
        ...conversations.map((c: any) => Number(c.created_at)),
        ...messages.map((m: any) => Number(m.created_at)),
      ].filter(t => t > 0);

      // SpacetimeDB stores timestamps as milliseconds since epoch
      const toISO = (ms: number) => new Date(ms).toISOString();
      const oldest = timestamps.length > 0 ? toISO(Math.min(...timestamps)) : null;
      const newest = timestamps.length > 0 ? toISO(Math.max(...timestamps)) : null;

      // Sample conversations (most recent first)
      conversations.sort((a: any, b: any) => Number(b.updated_at) - Number(a.updated_at));
      const sample = conversations.slice(0, 10).map((c: any) => ({
        id: c.id,
        title: c.title || null,
        message_count: c.message_count ?? 0,
        updated_at: Number(c.updated_at) > 0 ? toISO(Number(c.updated_at)) : null,
      }));

      // Summary of all tables
      const tableSummary: Record<string, number> = {};
      for (const [table, rows] of Object.entries(data.tables)) {
        if (rows.length > 0) tableSummary[table] = rows.length;
      }

      res.json({
        module_name: data.moduleName,
        conversations_count: conversations.length,
        messages_count: messages.length,
        total_rows: data.totalRows,
        oldest_date: oldest,
        newest_date: newest,
        sample_conversations: sample,
        tables: tableSummary,
      });
    } catch (err: any) {
      console.error("[backups] preview failed:", err.message);
      res.status(500).json({ error: err.message });
    } finally {
      cleanup?.();
    }
  });

  // POST /restore — restore all data from a backup into the live database
  router.post("/restore", async (req, res) => {
    const { path: backupPath } = req.body;
    if (!backupPath) return res.status(400).json({ error: "path is required" });

    let cleanup: (() => void) | undefined;
    try {
      const temp = await startTempInstance(backupPath);
      cleanup = temp.cleanup;
      const tempUrl = `http://127.0.0.1:${temp.port}`;
      const liveUrl = config.spacetimedbUrl;
      const liveMod = config.spacetimedbModuleName;
      const liveToken = config.spacetimedbToken;

      console.log(`[backups] Restore: reading from temp instance on port ${temp.port}`);
      const data = await readBackupData(tempUrl);

      const currentTables = getCurrentTables();
      const results: Record<string, { restored: number; failed: number; errors: string[] }> = {};
      let totalRestored = 0;
      let totalFailed = 0;

      // Import tables in dependency order (conversations before messages, plans before items)
      const importOrder = [
        "agents", "agent_channels", "agent_workspace_mounts",
        "providers", "provider_api_keys", "provider_aliases", "llm_models",
        "settings",
        "prompt_fragments", "prompt_templates",
        "prompt_fragment_versions", "prompt_template_versions",
        "agent_prompt_fragments",
        "conversations", "conversation_messages",
        "work_plans", "work_items",
      ];

      for (const tableName of importOrder) {
        const rows = data.tables[tableName];
        if (!rows || rows.length === 0) continue;

        const tableDef = currentTables.find(t => t.table === tableName);
        if (!tableDef) continue;

        const tableResult = { restored: 0, failed: 0, errors: [] as string[] };
        console.log(`[backups] Importing ${tableName}: ${rows.length} rows via ${tableDef.importReducer}`);

        for (const row of rows) {
          try {
            const args = mapRowToCurrentSchema(tableName, row);
            await callReducer(liveUrl, liveMod, tableDef.importReducer, args, liveToken);
            tableResult.restored++;
          } catch (err: any) {
            tableResult.failed++;
            if (tableResult.errors.length < 5) {
              tableResult.errors.push(`${row.id || row.key || "?"}: ${err.message}`);
            }
          }
        }

        results[tableName] = tableResult;
        totalRestored += tableResult.restored;
        totalFailed += tableResult.failed;

        if (tableResult.failed > 0) {
          console.warn(`[backups] ${tableName}: ${tableResult.restored} ok, ${tableResult.failed} failed`);
        }
      }

      res.json({
        source_module: data.moduleName,
        total_restored: totalRestored,
        total_failed: totalFailed,
        tables: results,
      });
    } catch (err: any) {
      console.error("[backups] restore failed:", err.message);
      res.status(500).json({ error: err.message });
    } finally {
      cleanup?.();
    }
  });

  return router;
}
