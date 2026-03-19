/**
 * Backup verification — table count comparison and snapshot file detection.
 */

import { readdirSync, statSync } from "fs";
import { join } from "path";
import { sqlQuery } from "../spacetimedb/client.js";
import type { TableDef } from "./schema-versions.js";
import type { TableVerification, SnapshotInfo, BackupVerificationResult } from "./types.js";

/**
 * Query COUNT(*) for a single table.
 */
export async function queryCount(
  baseUrl: string,
  module: string,
  table: string,
  token: string = "",
): Promise<number> {
  try {
    const rows = await sqlQuery(baseUrl, module, `SELECT COUNT(*) AS cnt FROM ${table}`, token);
    if (rows.length > 0 && rows[0].cnt !== undefined) return Number(rows[0].cnt);
    return 0;
  } catch {
    return 0;
  }
}

/**
 * Compare live vs backup table counts for all registered tables.
 */
export async function verifyTableCounts(
  liveUrl: string,
  tempUrl: string,
  moduleName: string,
  tables: TableDef[],
  tolerancePercent: number,
  liveToken: string = "",
): Promise<{ passed: boolean; results: TableVerification[] }> {
  const results: TableVerification[] = [];
  let allPassed = true;

  for (const table of tables) {
    const liveCount = await queryCount(liveUrl, moduleName, table.table, liveToken);
    const backupCount = await queryCount(tempUrl, moduleName, table.table);

    let passed = true;
    let reason: string | undefined;

    // Rule 1: If live has data, backup must have data
    if (liveCount > 0 && backupCount === 0) {
      passed = false;
      reason = `Live has ${liveCount} rows but backup has 0`;
    }

    // Rule 2: Exact match (tolerance = 0) — backup may trail but not lead
    if (passed && liveCount > 0 && tolerancePercent === 0 && backupCount > liveCount) {
      passed = false;
      reason = `Backup has MORE rows (${backupCount}) than live (${liveCount})`;
    }

    // Rule 3: Percentage tolerance
    if (passed && liveCount > 0 && tolerancePercent > 0) {
      const diff = Math.abs(backupCount - liveCount);
      const pct = (diff / liveCount) * 100;
      if (pct > tolerancePercent) {
        passed = false;
        reason = `Count differs by ${pct.toFixed(1)}% (tolerance: ${tolerancePercent}%)`;
      }
    }

    if (!passed) allPassed = false;
    results.push({ table: table.table, live_count: liveCount, backup_count: backupCount, passed, reason });
  }

  return { passed: allPassed, results };
}

/**
 * Check if the temp instance's data directory contains a snapshot at tx_offset > 0.
 */
export function verifySnapshotExists(tempDataDir: string): SnapshotInfo {
  try {
    const snapshotDir = join(tempDataDir, "replicas", "1", "snapshots");
    const entries = readdirSync(snapshotDir).filter(e => e.endsWith(".snapshot_dir"));

    for (const entry of entries) {
      const offset = parseInt(entry.split(".")[0], 10);
      if (offset > 0) {
        try {
          const bsatnFile = join(snapshotDir, entry, `${entry.replace(".snapshot_dir", "")}.snapshot_bsatn`);
          const stat = statSync(bsatnFile);
          if (stat.size > 1024) {
            return { found: true, offset, size_bytes: stat.size };
          }
        } catch {
          // bsatn file not found, continue
        }
      }
    }
  } catch {
    // snapshots dir doesn't exist
  }
  return { found: false };
}

/**
 * Run full verification: table counts + snapshot file check.
 */
export async function runVerification(
  liveUrl: string,
  tempUrl: string,
  tempDataDir: string,
  moduleName: string,
  tables: TableDef[],
  tolerancePercent: number,
  liveToken: string = "",
): Promise<BackupVerificationResult> {
  const { passed: tablesPassed, results } = await verifyTableCounts(
    liveUrl, tempUrl, moduleName, tables, tolerancePercent, liveToken,
  );

  const snapshot = verifySnapshotExists(tempDataDir);

  return {
    passed: tablesPassed,
    snapshot_offset: snapshot.offset ?? null,
    snapshot_size_bytes: snapshot.size_bytes ?? null,
    tables: results,
  };
}
