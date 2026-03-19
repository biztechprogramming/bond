/**
 * Backups API — list, preview, restore, schedule, and run SpacetimeDB backups.
 *
 * Backups are tar.gz archives of the full STDB data directory.
 * Restoring spins up a temporary standalone STDB instance from the backup,
 * reads ALL tables via SQL, maps old-schema rows to the current schema
 * (filling in defaults for new fields), and imports via reducers into the
 * live bond-core-v2 database.
 */

import { Router } from "express";
import { readdirSync, statSync, existsSync } from "fs";
import { join } from "path";
import { homedir } from "os";
import type { GatewayConfig } from "../config/index.js";
import { sqlQuery, callReducer } from "../spacetimedb/client.js";
import {
  getCurrentTables,
  mapRowToCurrentSchema,
} from "./schema-versions.js";
import { startTempInstance, findModuleName } from "./shared.js";
import { runBackup, isBackupRunning, readBackupLog } from "./executor.js";
import { loadSchedule, saveSchedule, validateScheduleUpdate } from "./scheduler.js";
import type { BackupTier } from "./types.js";

const BACKUP_DIR = join(homedir(), ".bond", "backups", "spacetimedb");

interface BackupEntry {
  filename: string;
  tier: string;
  size_bytes: number;
  created_at: string;
  path: string;
}

function listBackups(): BackupEntry[] {
  const tiers = ["hourly", "daily", "weekly", "monthly", "saved", "migrations"];
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

// ─── Read all restorable tables from the backup ─────────────────────────────

interface BackupData {
  moduleName: string;
  tables: Record<string, any[]>;
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

      const timestamps = [
        ...conversations.map((c: any) => Number(c.created_at)),
        ...messages.map((m: any) => Number(m.created_at)),
      ].filter(t => t > 0);

      const toISO = (ms: number) => new Date(ms).toISOString();
      const oldest = timestamps.length > 0 ? toISO(Math.min(...timestamps)) : null;
      const newest = timestamps.length > 0 ? toISO(Math.max(...timestamps)) : null;

      conversations.sort((a: any, b: any) => Number(b.updated_at) - Number(a.updated_at));
      const sample = conversations.slice(0, 10).map((c: any) => ({
        id: c.id,
        title: c.title || null,
        message_count: c.message_count ?? 0,
        updated_at: Number(c.updated_at) > 0 ? toISO(Number(c.updated_at)) : null,
      }));

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

  // ─── Schedule CRUD ──────────────────────────────────────────────────────────

  // GET /schedule — get current schedule config
  router.get("/schedule", (_req, res) => {
    try {
      const schedule = loadSchedule();
      res.json(schedule);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /schedule — update schedule config
  router.put("/schedule", (req, res) => {
    try {
      const update = req.body;
      const validationError = validateScheduleUpdate(update);
      if (validationError) {
        return res.status(400).json({ error: validationError });
      }

      const current = loadSchedule();

      // Deep merge tiers
      if (update.tiers) {
        for (const [tier, tierUpdate] of Object.entries(update.tiers)) {
          if (current.tiers[tier]) {
            current.tiers[tier] = { ...current.tiers[tier], ...(tierUpdate as any) };
          }
        }
      }
      if (update.timezone) current.timezone = update.timezone;
      if (update.verification) {
        current.verification = { ...current.verification, ...update.verification };
      }

      saveSchedule(current);
      res.json(current);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /run — trigger immediate backup for a tier
  router.post("/run", async (req, res) => {
    const { tier } = req.body;
    const validTiers = ["hourly", "daily", "weekly", "monthly"];
    if (!tier || !validTiers.includes(tier)) {
      return res.status(400).json({ error: `tier must be one of: ${validTiers.join(", ")}` });
    }

    if (isBackupRunning()) {
      return res.status(409).json({ error: "A backup is already in progress" });
    }

    try {
      const schedule = loadSchedule();
      const result = await runBackup(tier as BackupTier, config, schedule.verification);
      const statusCode = result.status === "success" ? 200 : 500;
      res.status(statusCode).json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /status — last backup status per tier
  router.get("/status", (_req, res) => {
    try {
      const tiers = ["hourly", "daily", "weekly", "monthly"];
      const status: Record<string, any> = {};

      for (const tier of tiers) {
        const entries = readBackupLog(1, tier);
        status[tier] = entries.length > 0 ? entries[0] : null;
      }

      res.json(status);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /history — backup history with optional tier filter
  router.get("/history", (req, res) => {
    try {
      const tier = req.query.tier as string | undefined;
      const limit = Math.min(parseInt(req.query.limit as string) || 50, 200);
      const entries = readBackupLog(limit, tier);
      res.json({ entries });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
