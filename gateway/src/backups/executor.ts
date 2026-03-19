/**
 * Backup executor — capture, verify, store, and rotate backups.
 *
 * Reuses startTempInstance/findModuleName from router.ts (exported as shared helpers).
 */

import { existsSync, mkdirSync, readFileSync, appendFileSync, readdirSync, statSync, unlinkSync } from "fs";
import { join } from "path";
import { homedir } from "os";
import { spawn } from "child_process";
import { sqlQuery } from "../spacetimedb/client.js";
import { getCurrentTables } from "./schema-versions.js";
import { startTempInstance, findModuleName } from "./shared.js";
import { queryCount, runVerification } from "./verification.js";
import type { GatewayConfig } from "../config/index.js";
import type {
  BackupTier, BackupResult, BackupLogEntry, BackupPreFlight,
  BackupVerificationResult, BackupArchiveInfo, VerificationConfig,
} from "./types.js";

const BACKUP_DIR = join(homedir(), ".bond", "backups", "spacetimedb");
const STDB_DATA_DIR = join(homedir(), ".bond", "spacetimedb");

// Concurrency guard
let backupInProgress = false;

export function isBackupRunning(): boolean {
  return backupInProgress;
}

/**
 * Ensure all tier directories exist.
 */
function ensureDirs(): void {
  for (const dir of ["hourly", "daily", "weekly", "monthly", "saved", "migrations"]) {
    const p = join(BACKUP_DIR, dir);
    if (!existsSync(p)) mkdirSync(p, { recursive: true });
  }
}

/**
 * Generate a backup filename based on current time.
 */
function makeFilename(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `spacetimedb_${ts}.tar.gz`;
}

/**
 * Pre-flight: health check + live row counts.
 */
async function preFlight(config: GatewayConfig): Promise<BackupPreFlight> {
  const { spacetimedbUrl, spacetimedbModuleName, spacetimedbToken } = config;

  // Health check
  let healthy = false;
  try {
    const res = await fetch(`${spacetimedbUrl}/v1/health`);
    healthy = res.ok;
  } catch { /* unhealthy */ }

  // Live row counts
  const tables = getCurrentTables();
  const liveCounts: Record<string, number> = {};
  for (const t of tables) {
    liveCounts[t.table] = await queryCount(spacetimedbUrl, spacetimedbModuleName, t.table, spacetimedbToken);
  }

  // tx_offset (best effort)
  let txOffset: number | null = null;
  try {
    const rows = await sqlQuery(spacetimedbUrl, spacetimedbModuleName, "SELECT COUNT(*) AS cnt FROM conversations", spacetimedbToken);
    // We don't have direct access to tx_offset via SQL, leave null
  } catch { /* ignore */ }

  return { stdb_healthy: healthy, tx_offset: txOffset, live_counts: liveCounts };
}

/**
 * Capture: tar the STDB data directory.
 */
async function captureArchive(destPath: string): Promise<void> {
  if (!existsSync(STDB_DATA_DIR)) {
    throw new Error(`SpacetimeDB data directory not found: ${STDB_DATA_DIR}`);
  }

  await new Promise<void>((resolve, reject) => {
    const tar = spawn("tar", ["-czf", destPath, "-C", STDB_DATA_DIR, "."]);
    tar.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`tar failed (code ${code})`))));
    tar.on("error", reject);
  });
}

/**
 * Apply retention policy: keep only the N most recent files in a tier directory.
 */
function applyRetention(tier: string, retention: number): { kept: string[]; deleted: string[] } {
  if (tier === "saved") return { kept: [], deleted: [] }; // Never delete saved backups

  const dir = join(BACKUP_DIR, tier);
  if (!existsSync(dir)) return { kept: [], deleted: [] };

  const files = readdirSync(dir)
    .filter(f => f.endsWith(".tar.gz"))
    .map(f => ({ name: f, mtime: statSync(join(dir, f)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime); // newest first

  const kept = files.slice(0, retention).map(f => f.name);
  const deleted: string[] = [];

  for (const f of files.slice(retention)) {
    try {
      unlinkSync(join(dir, f.name));
      deleted.push(f.name);
    } catch { /* ignore */ }
  }

  return { kept, deleted };
}

/**
 * Append a structured JSON log entry.
 */
function appendLog(entry: BackupLogEntry): void {
  const logPath = join(BACKUP_DIR, "backup.log");
  appendFileSync(logPath, JSON.stringify(entry) + "\n");
}

/**
 * Read recent log entries (most recent first).
 */
export function readBackupLog(limit: number = 50, tierFilter?: string): BackupLogEntry[] {
  const logPath = join(BACKUP_DIR, "backup.log");
  if (!existsSync(logPath)) return [];

  const lines = readFileSync(logPath, "utf-8").trim().split("\n").filter(l => l.trim());
  const entries: BackupLogEntry[] = [];

  // Read from end for efficiency
  for (let i = lines.length - 1; i >= 0 && entries.length < limit; i--) {
    try {
      const entry = JSON.parse(lines[i]) as BackupLogEntry;
      if (tierFilter && entry.tier !== tierFilter) continue;
      entries.push(entry);
    } catch { /* skip malformed lines */ }
  }

  return entries;
}

/**
 * Get the last successful backup time for a tier from the log.
 */
export function getLastBackupTime(tier: string): Date | null {
  const entries = readBackupLog(100, tier);
  const success = entries.find(e => e.status === "success");
  return success ? new Date(success.timestamp) : null;
}

/**
 * Execute a full backup for a given tier.
 */
export async function runBackup(
  tier: BackupTier,
  config: GatewayConfig,
  verificationConfig: VerificationConfig,
  isCatchUp: boolean = false,
): Promise<BackupResult> {
  if (backupInProgress) {
    return {
      status: "skipped",
      tier,
      duration_ms: 0,
      archive: null,
      verification: null,
      error: "Another backup is already in progress",
    };
  }

  backupInProgress = true;
  const startTime = Date.now();
  let preFlightData: BackupPreFlight = { stdb_healthy: false, tx_offset: null, live_counts: {} };
  let archive: BackupArchiveInfo | null = null;
  let verification: BackupVerificationResult | null = null;

  try {
    ensureDirs();

    // 1. Pre-flight
    preFlightData = await preFlight(config);
    if (!preFlightData.stdb_healthy) {
      throw new Error("SpacetimeDB is not healthy");
    }

    // Check if database is empty
    const totalRows = Object.values(preFlightData.live_counts).reduce((a, b) => a + b, 0);
    if (totalRows === 0) {
      throw new Error("Database is empty — skipping backup");
    }

    // 2. Capture
    const filename = makeFilename();
    const tierDir = join(BACKUP_DIR, tier);
    const archivePath = join(tierDir, filename);
    await captureArchive(archivePath);

    const archiveStat = statSync(archivePath);
    archive = { filename, size_bytes: archiveStat.size, path: archivePath };

    // 3. Verification (if enabled)
    if (verificationConfig.enabled) {
      let cleanup: (() => void) | undefined;
      try {
        const temp = await startTempInstance(archivePath);
        cleanup = temp.cleanup;
        const tempUrl = `http://127.0.0.1:${temp.port}`;
        const moduleName = await findModuleName(tempUrl);

        verification = await runVerification(
          config.spacetimedbUrl,
          tempUrl,
          temp.tmpDir,
          moduleName,
          getCurrentTables(),
          verificationConfig.tolerance_percent,
          config.spacetimedbToken,
        );

        if (!verification.passed) {
          // Delete the failed backup
          try { unlinkSync(archivePath); } catch { /* ignore */ }
          archive = null;
          const failedTables = verification.tables.filter(t => !t.passed).map(t => `${t.table}: ${t.reason}`);
          throw new Error(`Verification failed: ${failedTables.join("; ")}`);
        }
      } finally {
        cleanup?.();
      }
    }

    // 4. Retention
    const tierConfig = { hourly: 24, daily: 7, weekly: 5, monthly: 12 } as Record<string, number>;
    const retention = tierConfig[tier];
    if (retention) {
      applyRetention(tier, retention);
    }

    const duration = Date.now() - startTime;
    const result: BackupResult = { status: "success", tier, duration_ms: duration, archive, verification, error: null };

    // Log
    appendLog({
      timestamp: new Date().toISOString(),
      tier,
      status: "success",
      duration_ms: duration,
      archive,
      pre_flight: preFlightData,
      verification,
      error: null,
      catch_up: isCatchUp,
    });

    console.log(`[backup] ${tier} backup completed in ${duration}ms — ${archive?.filename}`);
    return result;
  } catch (err: any) {
    const duration = Date.now() - startTime;
    const errorMsg = err.message || String(err);

    appendLog({
      timestamp: new Date().toISOString(),
      tier,
      status: "failed",
      duration_ms: duration,
      archive: null,
      pre_flight: preFlightData,
      verification,
      error: errorMsg,
      catch_up: isCatchUp,
    });

    console.error(`[backup] ${tier} backup failed after ${duration}ms: ${errorMsg}`);
    return { status: "failed", tier, duration_ms: duration, archive: null, verification, error: errorMsg };
  } finally {
    backupInProgress = false;
  }
}
