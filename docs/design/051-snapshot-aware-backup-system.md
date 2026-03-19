# 051 — Snapshot-Aware Backup System

## Status: Draft
## Author: Bond Agent
## Date: 2026-03-19

---

## Problem

SpacetimeDB only takes automatic snapshots every **1,000,000 transactions** or when the
commit log segment reaches **1 GB**. For a typical Bond deployment (~20K transactions,
~20 MB commit log), neither threshold is ever reached. The only snapshot on disk is the
empty bootstrap state at `tx_offset 0`.

This means every backup archive contains a useless empty snapshot. Restoration depends
entirely on replaying the full commit log, which:

1. **Is fragile** — if any transaction in the log triggers a module error during replay,
   the entire restore fails (as observed: `error starting database` after replaying 23,703
   transactions).
2. **Is slow** — replay is sequential and gets slower as the database grows.
3. **Provides no incremental restore point** — there's no way to restore to "2 hours ago"
   without replaying from genesis.

Additionally, the current backup schedule (single daily at 2 AM via cron) provides:
- No intra-day recovery points
- No snapshot verification
- No configurable schedule
- No catch-up if the server was down during the scheduled window

## Goals

1. **Force a snapshot before every backup** so the archive contains a restorable
   point-in-time image of all tables.
2. **Tiered backup schedule**: hourly, daily, weekly, monthly — each at configurable times.
3. **Startup catch-up**: detect missed backups and reschedule for the next available window.
4. **Snapshot verification**: after every snapshot, verify row counts across all registered
   tables. Every table must pass for the backup to be considered valid.
5. **UI for schedule configuration**: allow operators to change times, days, and retention
   from the Bond Control UI.
6. **Enterprise-grade logging and alerting**: structured logs, failure notifications,
   audit trail.

## Non-Goals

- Point-in-time recovery to arbitrary timestamps (requires continuous archiving — future work).
- Cross-region replication.
- Backup encryption at rest (can be layered separately).

---

## Design

### 1. Folder Structure

```
~/.bond/backups/spacetimedb/
├── hourly/                          # Rolling hourly backups
│   ├── spacetimedb_20260319_090000.tar.gz
│   ├── spacetimedb_20260319_100000.tar.gz
│   └── ...                          # Retain last 24
├── daily/                           # Daily backups
│   ├── spacetimedb_20260319_121500.tar.gz
│   └── ...                          # Retain last 7
├── weekly/                          # Weekly backups
│   ├── spacetimedb_20260319_133000.tar.gz
│   └── ...                          # Retain last 5
├── monthly/                         # Monthly backups
│   ├── spacetimedb_20260301_144500.tar.gz
│   └── ...                          # Retain last 12
├── saved/                           # User-pinned backups (never rotated)
├── migrations/                      # Schema migration dumps
├── backup.log                       # Append-only structured log
└── schedule.json                    # Persisted schedule configuration
```

### 2. Default Schedule

| Tier    | Default Time        | Default Day         | Retention | Notes                                     |
|---------|---------------------|---------------------|-----------|--------------------------------------------|
| Hourly  | Every hour, :00     | Every day           | 24        | Also runs on gateway startup (replaces previous startup backup) |
| Daily   | 12:15 PM ET         | Every day           | 7         | Mid-day to capture a full work session     |
| Weekly  | 1:30 PM ET          | Wednesday           | 5         | Mid-week for maximum delta from weekend    |
| Monthly | 2:45 PM ET          | 1st of month        | 12        | One year of monthly archives               |

All times are stored in the operator's configured timezone (default: `America/New_York`).

### 3. Schedule Configuration File

```jsonc
// ~/.bond/backups/spacetimedb/schedule.json
{
  "version": 1,
  "timezone": "America/New_York",
  "tiers": {
    "hourly": {
      "enabled": true,
      "minute": 0,
      "retention": 24,
      "run_on_startup": true
    },
    "daily": {
      "enabled": true,
      "hour": 12,
      "minute": 15,
      "retention": 7,
      "run_on_startup": false
    },
    "weekly": {
      "enabled": true,
      "hour": 13,
      "minute": 30,
      "day_of_week": 3,
      "retention": 5,
      "run_on_startup": false
    },
    "monthly": {
      "enabled": true,
      "hour": 14,
      "minute": 45,
      "day_of_month": 1,
      "retention": 12,
      "run_on_startup": false
    }
  },
  "verification": {
    "enabled": true,
    "fail_on_empty_table": true,
    "fail_on_count_mismatch": true,
    "tolerance_percent": 0
  }
}
```

### 4. Forcing a Snapshot

SpacetimeDB 2.0.x has no HTTP API or CLI command to request a snapshot. The snapshot
worker only fires on two triggers:

1. `tx_offset % 1_000_000 == 0` (in `maybe_do_snapshot`)
2. Commit log segment rotation (in `on_new_segment` callback)

**Our approach: trigger a commit log segment rotation.**

Since the live STDB runs in Docker, we cannot call internal Rust APIs directly. Instead:

#### Option A — No-Op Reducer Flush (Preferred)

Add a `force_snapshot` reducer to the SpacetimeDB module that performs a trivial write
(e.g., upserts a `system_events` row with `key = "last_snapshot_request"`). While a
single reducer call won't trigger a snapshot by itself, we combine it with **Option B**.

#### Option B — Commit Log Segment Rotation via Restart

Perform a graceful STDB container restart (`docker restart bond-spacetimedb`). On
shutdown, the durability layer flushes all pending writes. On startup, if the commit log
has advanced past the last snapshot, the snapshot worker writes a new snapshot during
database initialization (observed in logs: `Capturing snapshot of database ... at TX
offset N`).

**However**, from the logs we observed STDB only captures a snapshot at offset 0 on init
for a fresh database — it does not automatically snapshot on restart for an existing one.

#### Option C — Direct Snapshot via Temp Instance (Recommended)

Since we already spin up a temporary STDB instance for restore, we can use the same
technique to create a verified snapshot:

1. **Stop accepting writes** — call a `pause_writes` reducer or simply note the current
   `tx_offset` via SQL.
2. **Copy the live data directory** — `tar` the STDB data dir (same as current backup).
3. **Start a temp instance** — extract, boot on a random port.
4. **Verify all tables** — query `SELECT COUNT(*) AS cnt FROM <table>` for every
   registered table and compare against the live database counts.
5. **The temp instance will write a snapshot during init** if the commit log has data
   past offset 0. This happens because STDB replays the commit log and then the module's
   `__init__` runs, which triggers `Capturing snapshot`.
6. **Extract the snapshot** from the temp instance's data directory.
7. **Package the final archive** — the tar.gz now contains both the commit log AND a
   current snapshot.

Wait — from our investigation, the temp instance **failed** with "error starting
database" after replay. This is likely a module initialization issue (e.g., the module
tries to connect to external services during `__init__`).

#### Option D — Programmatic Snapshot via STDB Rust API (Best Long-Term)

Fork or patch the SpacetimeDB Docker image to expose a snapshot endpoint:

```
POST /v1/database/:database_identity/snapshot
```

This calls `snapshot_worker.request_snapshot()` internally. This is the cleanest
solution but requires maintaining a custom STDB image.

#### Recommended Approach: Option C with Module Fix

1. Fix the module's `__init__` reducer to handle being called in a read-only/isolated
   context (no external connections needed during snapshot capture).
2. Use the temp instance approach to create verified snapshots.
3. Long-term, contribute Option D upstream to SpacetimeDB.

### 5. Backup Procedure (Per-Tier)

```
┌─────────────────────────────────────────────────────────┐
│                    BACKUP PROCEDURE                      │
├─────────────────────────────────────────────────────────┤
│ 1. PRE-FLIGHT CHECKS                                    │
│    ├─ Verify STDB is healthy (GET /v1/health)           │
│    ├─ Query live row counts for all registered tables    │
│    └─ Abort if database is empty (0 agents + 0 convos) │
│                                                          │
│ 2. CAPTURE                                               │
│    ├─ Record pre-backup tx_offset via SQL               │
│    ├─ tar.gz the STDB data directory                    │
│    └─ Record post-backup tx_offset                      │
│                                                          │
│ 3. SNAPSHOT CREATION                                     │
│    ├─ Extract archive to temp directory                  │
│    ├─ Remove stale PID / lock files                     │
│    ├─ Start temp STDB instance                          │
│    ├─ Wait for health check (up to 120s)                │
│    └─ Temp instance replays clog → creates snapshot     │
│                                                          │
│ 4. VERIFICATION                                          │
│    ├─ For each registered table:                        │
│    │   ├─ Query COUNT(*) on temp instance               │
│    │   ├─ Compare against pre-flight live counts        │
│    │   └─ FAIL if any table count is 0 when live > 0   │
│    ├─ Verify snapshot file exists at tx_offset > 0      │
│    └─ Verify snapshot size > minimum threshold (1 KB)   │
│                                                          │
│ 5. REPACKAGE                                             │
│    ├─ Copy new snapshot into archive data                │
│    ├─ Re-tar.gz with snapshot included                  │
│    └─ Kill temp instance, clean up temp dir             │
│                                                          │
│ 6. STORE & ROTATE                                        │
│    ├─ Move archive to tier directory                    │
│    ├─ Apply retention policy (delete oldest beyond N)   │
│    └─ Cross-promote if applicable (hourly→daily, etc.) │
│                                                          │
│ 7. LOG & NOTIFY                                          │
│    ├─ Append structured JSON to backup.log              │
│    ├─ Emit system_event to STDB (for UI visibility)    │
│    └─ Alert on failure (if notification configured)     │
└─────────────────────────────────────────────────────────┘
```

### 6. Startup Catch-Up Logic

On gateway startup:

```typescript
async function checkMissedBackups(schedule: BackupSchedule): Promise<void> {
  const now = DateTime.now().setZone(schedule.timezone);

  for (const [tier, config] of Object.entries(schedule.tiers)) {
    if (!config.enabled) continue;

    const lastBackup = getLastBackupTime(tier);
    const lastScheduled = getLastScheduledTime(tier, config, now);

    if (!lastBackup || lastBackup < lastScheduled) {
      // A scheduled backup was missed
      const nextWindow = getNextScheduledTime(tier, config, now);

      // If the scheduled time for today hasn't passed yet, use it
      const todayScheduled = getTodayScheduledTime(tier, config, now);
      if (todayScheduled && todayScheduled > now) {
        scheduleBackup(tier, todayScheduled);
        log.info(`[backup] Missed ${tier} backup — rescheduled for today at ${todayScheduled}`);
      } else {
        // Today's window passed — schedule for tomorrow at that time
        const tomorrowScheduled = todayScheduled
          ? todayScheduled.plus({ days: 1 })
          : nextWindow;
        scheduleBackup(tier, tomorrowScheduled);
        log.info(`[backup] Missed ${tier} backup — rescheduled for ${tomorrowScheduled}`);
      }
    }
  }

  // Hourly + run_on_startup: always run immediately
  if (schedule.tiers.hourly?.enabled && schedule.tiers.hourly?.run_on_startup) {
    await runBackup("hourly");
  }
}
```

### 7. Verification Protocol

Every backup runs a verification suite. **All checks must pass** for the backup to be
stored. Failed backups are logged but not stored in the tier directory (preventing
corrupt archives from rotating out good ones).

#### Table Count Verification

```typescript
interface TableVerification {
  table: string;
  live_count: number;
  backup_count: number;
  passed: boolean;
  reason?: string;
}

async function verifyBackup(
  liveUrl: string,
  tempUrl: string,
  moduleName: string,
  tables: TableDef[],
  tolerance: number,  // 0 = exact match required
): Promise<{ passed: boolean; results: TableVerification[] }> {
  const results: TableVerification[] = [];
  let allPassed = true;

  for (const table of tables) {
    const liveCount = await queryCount(liveUrl, moduleName, table.table);
    const backupCount = await queryCount(tempUrl, moduleName, table.table);

    let passed = true;
    let reason: string | undefined;

    // Rule 1: If live has data, backup must have data
    if (liveCount > 0 && backupCount === 0) {
      passed = false;
      reason = `Live has ${liveCount} rows but backup has 0`;
    }

    // Rule 2: Counts must be within tolerance
    if (liveCount > 0 && tolerance === 0 && backupCount !== liveCount) {
      // During backup, writes may have occurred — allow backup to be
      // slightly behind (but never ahead, and never zero)
      if (backupCount > liveCount) {
        passed = false;
        reason = `Backup has MORE rows (${backupCount}) than live (${liveCount})`;
      }
    }

    // Rule 3: If tolerance > 0, check percentage
    if (liveCount > 0 && tolerance > 0) {
      const diff = Math.abs(backupCount - liveCount);
      const pct = (diff / liveCount) * 100;
      if (pct > tolerance) {
        passed = false;
        reason = `Count differs by ${pct.toFixed(1)}% (tolerance: ${tolerance}%)`;
      }
    }

    if (!passed) allPassed = false;
    results.push({ table: table.table, live_count: liveCount, backup_count: backupCount, passed, reason });
  }

  return { passed: allPassed, results };
}
```

#### Snapshot File Verification

```typescript
async function verifySnapshotExists(tempDataDir: string): Promise<boolean> {
  const snapshotDir = join(tempDataDir, "replicas", "1", "snapshots");
  const entries = readdirSync(snapshotDir).filter(e => e.endsWith(".snapshot_dir"));

  for (const entry of entries) {
    const offset = parseInt(entry.split(".")[0], 10);
    if (offset > 0) {
      // Found a snapshot beyond the bootstrap — this has actual data
      const bsatnFile = join(snapshotDir, entry, `${entry.replace('.snapshot_dir', '')}.snapshot_bsatn`);
      const stat = statSync(bsatnFile);
      return stat.size > 1024; // Minimum 1 KB for a non-trivial snapshot
    }
  }
  return false;
}
```

### 8. Gateway Integration

The backup scheduler runs as part of the gateway process (not an external cron job).
This replaces the current `crontab` entry.

#### New Files

```
gateway/src/backups/
├── router.ts               # Existing — REST endpoints (add schedule CRUD)
├── schema-versions.ts      # Existing — schema migration registry
├── scheduler.ts            # NEW — backup scheduler (timers, catch-up)
├── executor.ts             # NEW — backup execution (capture, snapshot, verify)
├── verification.ts         # NEW — table count + snapshot verification
└── types.ts                # NEW — shared types (BackupSchedule, BackupResult)
```

#### REST API Extensions

```
GET    /api/v1/backups                      # Existing — list backups
POST   /api/v1/backups/preview              # Existing — preview backup contents
POST   /api/v1/backups/restore              # Existing — restore from backup

GET    /api/v1/backups/schedule             # NEW — get current schedule config
PUT    /api/v1/backups/schedule             # NEW — update schedule config
POST   /api/v1/backups/run                  # NEW — trigger immediate backup for a tier
GET    /api/v1/backups/status               # NEW — last backup status per tier
GET    /api/v1/backups/history              # NEW — backup history (from backup.log)
GET    /api/v1/backups/:tier/:filename/verify  # NEW — re-verify an existing backup
```

### 9. Structured Backup Log

Each backup attempt appends a JSON line to `backup.log`:

```jsonc
{
  "timestamp": "2026-03-19T12:15:00.000Z",
  "tier": "daily",
  "status": "success",          // "success" | "failed" | "skipped"
  "duration_ms": 8432,
  "archive": {
    "filename": "spacetimedb_20260319_121500.tar.gz",
    "size_bytes": 2359296,
    "path": "/home/andrew/.bond/backups/spacetimedb/daily/spacetimedb_20260319_121500.tar.gz"
  },
  "pre_flight": {
    "stdb_healthy": true,
    "tx_offset": 24150,
    "live_counts": {
      "conversations": 42,
      "conversation_messages": 1583,
      "agents": 3,
      "settings": 18
      // ... all tables
    }
  },
  "verification": {
    "passed": true,
    "snapshot_offset": 24150,
    "snapshot_size_bytes": 1843200,
    "tables": [
      { "table": "conversations", "live": 42, "backup": 42, "passed": true },
      { "table": "conversation_messages", "live": 1583, "backup": 1583, "passed": true }
      // ... all tables
    ]
  },
  "error": null,                // Error message if failed
  "catch_up": false             // true if this was a missed-backup catch-up run
}
```

### 10. UI — Backup Schedule Configuration

A new section under **Settings → Backups** in the Bond Control UI.

#### Schedule Panel

```
┌─────────────────────────────────────────────────────────────────┐
│  Backup Schedule                                     [Save]     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Timezone: [America/New_York          ▾]                        │
│                                                                  │
│  ┌─────────┬─────────┬────────────────┬───────────┬──────────┐  │
│  │ Tier    │ Enabled │ Schedule       │ Retention │ Status   │  │
│  ├─────────┼─────────┼────────────────┼───────────┼──────────┤  │
│  │ Hourly  │  [✓]    │ Every hour :00 │ [24]      │ ● OK     │  │
│  │ Daily   │  [✓]    │ [12]:[15] PM   │ [7]       │ ● OK     │  │
│  │ Weekly  │  [✓]    │ [Wed] [1]:[30] │ [5]       │ ● OK     │  │
│  │ Monthly │  [✓]    │ 1st  [2]:[45]  │ [12]      │ ⚠ Missed │  │
│  └─────────┴─────────┴────────────────┴───────────┴──────────┘  │
│                                                                  │
│  Verification:                                                   │
│  [✓] Verify table counts after every backup                     │
│  [✓] Fail if any table is empty when live data exists            │
│  Tolerance: [0]% (0 = backup count must equal or trail live)     │
│                                                                  │
│  [Run Backup Now ▾]  Hourly | Daily | Weekly | Monthly | All    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### Backup History Panel

```
┌─────────────────────────────────────────────────────────────────┐
│  Backup History                              [Filter ▾] [↻]     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ● 12:15 PM  daily   2.3 MB  ✓ Verified (17 tables, 0 errors)  │
│  ● 12:00 PM  hourly  2.3 MB  ✓ Verified (17 tables, 0 errors)  │
│  ● 11:00 AM  hourly  2.2 MB  ✓ Verified (17 tables, 0 errors)  │
│  ● 10:00 AM  hourly  2.2 MB  ✓ Verified (17 tables, 0 errors)  │
│  ✗  9:00 AM  hourly  FAILED  conversations: 42 live vs 0 backup │
│  ● 8:00 AM   hourly  2.1 MB  ✓ Verified (17 tables, 0 errors)  │
│                                                                  │
│  [Show More]                                                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

Each row is expandable to show per-table verification details.

### 11. Removing the External Cron Job

The current `crontab` entry:
```
0 2 * * * /home/andrew/bond/scripts/backup-spacetimedb.sh >> ~/.bond/backups/spacetimedb/backup.log 2>&1
```

This will be removed as part of this migration. The gateway's internal scheduler
replaces it entirely. The `backup-spacetimedb.sh` script is preserved for manual/emergency
use but is no longer the primary backup mechanism.

---

## Test Plan

### Unit Tests

#### T1 — Schedule Calculation

```typescript
describe("BackupScheduler", () => {
  it("calculates next hourly run correctly", () => {
    const now = DateTime.fromISO("2026-03-19T09:22:00", { zone: "America/New_York" });
    const next = getNextScheduledTime("hourly", { minute: 0 }, now);
    expect(next.toISO()).toBe("2026-03-19T10:00:00.000-04:00");
  });

  it("calculates next daily run correctly", () => {
    const now = DateTime.fromISO("2026-03-19T13:00:00", { zone: "America/New_York" });
    const next = getNextScheduledTime("daily", { hour: 12, minute: 15 }, now);
    // Already past today's window — next is tomorrow
    expect(next.toISO()).toBe("2026-03-20T12:15:00.000-04:00");
  });

  it("calculates next weekly run on correct day", () => {
    const now = DateTime.fromISO("2026-03-19T09:00:00", { zone: "America/New_York" }); // Thursday
    const next = getNextScheduledTime("weekly", { hour: 13, minute: 30, day_of_week: 3 }, now);
    // Wednesday already passed this week — next Wednesday
    expect(next.weekday).toBe(3);
    expect(next.day).toBe(25); // Next Wednesday = March 25
  });

  it("handles DST transitions", () => {
    // Spring forward: 2:00 AM doesn't exist on March 8, 2026
    const now = DateTime.fromISO("2026-03-08T01:30:00", { zone: "America/New_York" });
    const next = getNextScheduledTime("hourly", { minute: 0 }, now);
    expect(next.isValid).toBe(true);
    expect(next.hour).toBe(3); // Skips to 3:00 AM
  });

  it("handles month boundaries for monthly tier", () => {
    const now = DateTime.fromISO("2026-02-28T15:00:00", { zone: "America/New_York" });
    const next = getNextScheduledTime("monthly", { hour: 14, minute: 45, day_of_month: 1 }, now);
    expect(next.month).toBe(3);
    expect(next.day).toBe(1);
  });
});
```

#### T2 — Catch-Up Detection

```typescript
describe("Catch-Up Logic", () => {
  it("detects missed daily backup and reschedules for today if window not passed", () => {
    const now = DateTime.fromISO("2026-03-19T11:00:00", { zone: "America/New_York" });
    const lastBackup = DateTime.fromISO("2026-03-17T12:15:00", { zone: "America/New_York" });
    const result = checkMissedBackup("daily", { hour: 12, minute: 15 }, lastBackup, now);
    expect(result.missed).toBe(true);
    expect(result.reschedule.hour).toBe(12);
    expect(result.reschedule.minute).toBe(15);
    expect(result.reschedule.day).toBe(19); // Today
  });

  it("reschedules for tomorrow if today's window already passed", () => {
    const now = DateTime.fromISO("2026-03-19T14:00:00", { zone: "America/New_York" });
    const lastBackup = DateTime.fromISO("2026-03-17T12:15:00", { zone: "America/New_York" });
    const result = checkMissedBackup("daily", { hour: 12, minute: 15 }, lastBackup, now);
    expect(result.missed).toBe(true);
    expect(result.reschedule.day).toBe(20); // Tomorrow
  });

  it("does not flag a backup as missed if it ran on schedule", () => {
    const now = DateTime.fromISO("2026-03-19T12:30:00", { zone: "America/New_York" });
    const lastBackup = DateTime.fromISO("2026-03-19T12:15:02", { zone: "America/New_York" });
    const result = checkMissedBackup("daily", { hour: 12, minute: 15 }, lastBackup, now);
    expect(result.missed).toBe(false);
  });
});
```

#### T3 — Retention Rotation

```typescript
describe("Retention", () => {
  it("keeps exactly N backups per tier", () => {
    const files = createMockBackups("hourly", 30); // 30 files
    const retained = applyRetention(files, 24);
    expect(retained.kept).toHaveLength(24);
    expect(retained.deleted).toHaveLength(6);
    // Oldest are deleted
    expect(retained.deleted[0]).toBe(files[29]); // Oldest
  });

  it("never deletes pinned/saved backups", () => {
    const files = createMockBackups("saved", 50);
    const retained = applyRetention(files, 10); // retention doesn't apply to saved
    expect(retained.deleted).toHaveLength(0);
  });
});
```

### Integration Tests

#### T4 — Snapshot Verification (Critical)

This is the most important test. It validates that a backup archive contains a snapshot
with the correct data.

```typescript
describe("Snapshot Verification", () => {
  let liveUrl: string;
  let liveMod: string;

  beforeAll(async () => {
    liveUrl = config.spacetimedbUrl;
    liveMod = config.spacetimedbModuleName;
  });

  it("verifies every registered table has matching row counts", async () => {
    // Step 1: Get live counts
    const tables = getCurrentTables();
    const liveCounts: Record<string, number> = {};
    for (const table of tables) {
      liveCounts[table.table] = await queryCount(liveUrl, liveMod, table.table);
    }

    // Step 2: Run a backup with snapshot
    const result = await runBackup("hourly");
    expect(result.status).toBe("success");

    // Step 3: Start temp instance from the backup
    const temp = await startTempInstance(result.archive.path);
    try {
      const tempUrl = `http://127.0.0.1:${temp.port}`;
      const moduleName = await findModuleName(tempUrl);

      // Step 4: Verify EVERY table
      for (const table of tables) {
        const backupCount = await queryCount(tempUrl, moduleName, table.table);
        const liveCount = liveCounts[table.table];

        // Not a single table can fail
        expect(backupCount).toBeGreaterThanOrEqual(0);
        if (liveCount > 0) {
          expect(backupCount).toBeGreaterThan(0);
          // Backup count should be <= live count (writes may have occurred during backup)
          expect(backupCount).toBeLessThanOrEqual(liveCount);
        }
      }
    } finally {
      temp.cleanup();
    }
  });

  it("fails verification when a table is missing data", async () => {
    // Create a deliberately corrupt backup (remove a table's data)
    const corruptArchive = await createCorruptBackup("conversations");
    const temp = await startTempInstance(corruptArchive);
    try {
      const tempUrl = `http://127.0.0.1:${temp.port}`;
      const result = await verifyBackup(liveUrl, tempUrl, liveMod, getCurrentTables(), 0);
      expect(result.passed).toBe(false);
      const convResult = result.results.find(r => r.table === "conversations");
      expect(convResult?.passed).toBe(false);
    } finally {
      temp.cleanup();
    }
  });

  it("verifies snapshot file exists with tx_offset > 0", async () => {
    const result = await runBackup("hourly");
    const tempDir = await extractArchive(result.archive.path);
    try {
      const hasSnapshot = await verifySnapshotExists(tempDir);
      expect(hasSnapshot).toBe(true);
    } finally {
      rmSync(tempDir, { recursive: true, force: true });
    }
  });

  it("verifies snapshot BSATN file is non-trivial in size", async () => {
    const result = await runBackup("hourly");
    const tempDir = await extractArchive(result.archive.path);
    try {
      const snapshotSize = getLatestSnapshotSize(tempDir);
      expect(snapshotSize).toBeGreaterThan(1024); // > 1 KB
    } finally {
      rmSync(tempDir, { recursive: true, force: true });
    }
  });
});
```

#### T5 — Full Backup-Restore Round-Trip

```typescript
describe("Round-Trip Restore", () => {
  it("backup → restore → verify all data intact", async () => {
    // Capture live state
    const tables = getCurrentTables();
    const liveCounts: Record<string, number> = {};
    for (const t of tables) {
      liveCounts[t.table] = await queryCount(liveUrl, liveMod, t.table);
    }

    // Run backup
    const backup = await runBackup("hourly");
    expect(backup.status).toBe("success");
    expect(backup.verification.passed).toBe(true);

    // Restore to a fresh temp instance (simulating disaster recovery)
    const restored = await restoreToTempInstance(backup.archive.path);
    try {
      for (const t of tables) {
        const restoredCount = await queryCount(restored.url, restored.module, t.table);
        if (liveCounts[t.table] > 0) {
          expect(restoredCount).toBeGreaterThan(0);
          expect(restoredCount).toBeLessThanOrEqual(liveCounts[t.table]);
        }
      }
    } finally {
      restored.cleanup();
    }
  });
});
```

#### T6 — Schedule Config API

```typescript
describe("Schedule API", () => {
  it("GET /api/v1/backups/schedule returns current config", async () => {
    const res = await fetch(`${gatewayUrl}/api/v1/backups/schedule`);
    const body = await res.json();
    expect(body.version).toBe(1);
    expect(body.tiers.hourly).toBeDefined();
    expect(body.tiers.daily).toBeDefined();
  });

  it("PUT /api/v1/backups/schedule updates and persists config", async () => {
    const updated = { tiers: { daily: { hour: 14, minute: 0 } } };
    const res = await fetch(`${gatewayUrl}/api/v1/backups/schedule`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updated),
    });
    expect(res.status).toBe(200);

    // Verify persistence
    const res2 = await fetch(`${gatewayUrl}/api/v1/backups/schedule`);
    const body = await res2.json();
    expect(body.tiers.daily.hour).toBe(14);
    expect(body.tiers.daily.minute).toBe(0);
  });

  it("rejects invalid schedule values", async () => {
    const invalid = { tiers: { daily: { hour: 25 } } };
    const res = await fetch(`${gatewayUrl}/api/v1/backups/schedule`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(invalid),
    });
    expect(res.status).toBe(400);
  });

  it("POST /api/v1/backups/run triggers immediate backup", async () => {
    const res = await fetch(`${gatewayUrl}/api/v1/backups/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tier: "hourly" }),
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("success");
    expect(body.verification.passed).toBe(true);
  });
});
```

#### T7 — Concurrent Backup Guard

```typescript
describe("Concurrency", () => {
  it("prevents two backups from running simultaneously", async () => {
    const [first, second] = await Promise.all([
      fetch(`${gatewayUrl}/api/v1/backups/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier: "hourly" }),
      }),
      fetch(`${gatewayUrl}/api/v1/backups/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier: "daily" }),
      }),
    ]);

    // One succeeds, one gets 409 Conflict
    const statuses = [first.status, second.status].sort();
    expect(statuses).toEqual([200, 409]);
  });
});
```

#### T8 — Backup Log Integrity

```typescript
describe("Backup Log", () => {
  it("appends valid JSON lines for every backup attempt", async () => {
    await runBackup("hourly");
    const log = readFileSync(join(BACKUP_DIR, "backup.log"), "utf-8");
    const lines = log.trim().split("\n").filter(l => l.trim());
    const lastLine = JSON.parse(lines[lines.length - 1]);
    expect(lastLine.tier).toBe("hourly");
    expect(lastLine.timestamp).toBeDefined();
    expect(lastLine.verification).toBeDefined();
    expect(lastLine.verification.tables).toBeInstanceOf(Array);
    expect(lastLine.verification.tables.length).toBe(getCurrentTables().length);
  });
});
```

---

## Migration Plan

1. **Phase 1** — Implement `executor.ts` and `verification.ts` (backup + snapshot + verify).
   Test manually via `POST /api/v1/backups/run`.
2. **Phase 2** — Implement `scheduler.ts` with catch-up logic. Remove crontab entry.
3. **Phase 3** — Add schedule CRUD endpoints and `schedule.json` persistence.
4. **Phase 4** — Build UI components (Settings → Backups page).
5. **Phase 5** — Investigate module `__init__` fix for clean temp instance startup.
   Track the "error starting database" issue separately.

## Open Questions

1. **Module init failure** — The temp STDB instance fails with "error starting database"
   after successful commit log replay. This blocks snapshot creation via the temp instance
   approach. Root cause investigation needed. Likely the module's `__init__` tries to
   register scheduled reducers or connect to resources that don't exist in an isolated
   context.

2. **Direct snapshot API** — Should we contribute a `/v1/database/:id/snapshot` endpoint
   upstream to SpacetimeDB? This would eliminate the need for temp instances entirely.

3. **Backup during writes** — The current approach copies the data directory while STDB
   is running. This is safe because STDB uses append-only commit logs and atomic snapshot
   writes, but we should add a `tx_offset` drift check (pre vs. post backup) to flag
   if significant writes occurred during the capture window.

4. **Notification channels** — Should backup failures alert via Bond's existing channel
   infrastructure (Telegram, Signal, etc.)?

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `gateway/src/backups/scheduler.ts` | Create | Timer-based backup scheduler with catch-up |
| `gateway/src/backups/executor.ts` | Create | Backup capture, snapshot creation, repackaging |
| `gateway/src/backups/verification.ts` | Create | Table count + snapshot file verification |
| `gateway/src/backups/types.ts` | Create | Shared types |
| `gateway/src/backups/router.ts` | Modify | Add schedule CRUD + run + status + history endpoints |
| `gateway/src/server.ts` | Modify | Initialize scheduler on startup, remove ad-hoc backup |
| `frontend/src/app/settings/backups/` | Create | Backup schedule + history UI components |
| `scripts/backup-spacetimedb.sh` | Preserve | Keep for manual/emergency use, no longer primary |
| `gateway/src/__tests__/backups.test.ts` | Create | All tests from T1–T8 |
| `~/.bond/backups/spacetimedb/schedule.json` | Create | Default schedule configuration |
| `~/.bond/backups/spacetimedb/hourly/` | Create | New tier directory |
