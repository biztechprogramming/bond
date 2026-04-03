/**
 * Backup scheduler — timer-based scheduling with catch-up logic.
 *
 * Reads/writes schedule config from ~/.bond/backups/spacetimedb/schedule.json.
 * Uses setTimeout for scheduling (no external cron deps).
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync } from "fs";
import { join } from "path";
import { homedir } from "os";
import type { GatewayConfig } from "../config/index.js";
import { runBackup, getLastBackupTime } from "./executor.js";
import type { BackupSchedule, BackupTier, TierConfig } from "./types.js";
import { DEFAULT_SCHEDULE } from "./types.js";

const BACKUP_DIR = join(homedir(), ".bond", "backups", "spacetimedb");
const SCHEDULE_PATH = join(BACKUP_DIR, "schedule.json");

// Active timer handles so we can cancel on reschedule
const activeTimers: Map<string, ReturnType<typeof setTimeout>> = new Map();

// Node.js setTimeout uses a 32-bit signed int internally.
// Delays exceeding this overflow to 1ms, causing tight loops.
const MAX_TIMEOUT_MS = 2_147_483_647;

/**
 * Load schedule from disk, creating default if missing.
 */
export function loadSchedule(): BackupSchedule {
  if (!existsSync(BACKUP_DIR)) mkdirSync(BACKUP_DIR, { recursive: true });

  if (existsSync(SCHEDULE_PATH)) {
    try {
      return JSON.parse(readFileSync(SCHEDULE_PATH, "utf-8"));
    } catch {
      console.warn("[backup-scheduler] Corrupt schedule.json — using defaults");
    }
  }

  saveSchedule(DEFAULT_SCHEDULE);
  return { ...DEFAULT_SCHEDULE };
}

/**
 * Persist schedule to disk.
 */
export function saveSchedule(schedule: BackupSchedule): void {
  if (!existsSync(BACKUP_DIR)) mkdirSync(BACKUP_DIR, { recursive: true });
  writeFileSync(SCHEDULE_PATH, JSON.stringify(schedule, null, 2));
}

/**
 * Validate a partial schedule update. Returns error string or null.
 */
export function validateScheduleUpdate(update: any): string | null {
  if (!update || typeof update !== "object") return "Request body must be an object";

  if (update.tiers) {
    for (const [tier, cfg] of Object.entries(update.tiers)) {
      if (!["hourly", "daily", "weekly", "monthly"].includes(tier)) {
        return `Unknown tier: ${tier}`;
      }
      const c = cfg as any;
      if (c.hour !== undefined && (c.hour < 0 || c.hour > 23)) return `Invalid hour for ${tier}: ${c.hour}`;
      if (c.minute !== undefined && (c.minute < 0 || c.minute > 59)) return `Invalid minute for ${tier}: ${c.minute}`;
      if (c.day_of_week !== undefined && (c.day_of_week < 0 || c.day_of_week > 6)) return `Invalid day_of_week for ${tier}: ${c.day_of_week}`;
      if (c.day_of_month !== undefined && (c.day_of_month < 1 || c.day_of_month > 31)) return `Invalid day_of_month for ${tier}: ${c.day_of_month}`;
      if (c.retention !== undefined && (c.retention < 1 || c.retention > 365)) return `Invalid retention for ${tier}: ${c.retention}`;
    }
  }

  if (update.verification) {
    const v = update.verification;
    if (v.tolerance_percent !== undefined && (v.tolerance_percent < 0 || v.tolerance_percent > 100)) {
      return `Invalid tolerance_percent: ${v.tolerance_percent}`;
    }
  }

  return null;
}

/**
 * Get the current time in the schedule's timezone as a Date-like object with local components.
 */
function nowInTimezone(tz: string): { year: number; month: number; day: number; hour: number; minute: number; dayOfWeek: number; date: Date } {
  const now = new Date();
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    weekday: "short",
  }).formatToParts(now);

  const get = (type: string) => parts.find(p => p.type === type)?.value || "";
  const dayMap: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

  return {
    year: parseInt(get("year")),
    month: parseInt(get("month")),
    day: parseInt(get("day")),
    hour: parseInt(get("hour")),
    minute: parseInt(get("minute")),
    dayOfWeek: dayMap[get("weekday")] ?? 0,
    date: now,
  };
}

/**
 * Calculate milliseconds until the next scheduled run for a tier.
 */
function msUntilNextRun(tier: string, cfg: TierConfig, tz: string): number {
  const now = nowInTimezone(tz);
  const currentMs = now.date.getTime();

  if (tier === "hourly") {
    // Next occurrence of :MM past the hour
    const target = new Date(currentMs);
    target.setMinutes(cfg.minute, 0, 0);
    if (target.getTime() <= currentMs) {
      target.setTime(target.getTime() + 60 * 60 * 1000); // next hour
    }
    return target.getTime() - currentMs;
  }

  // For daily/weekly/monthly, build the next target date
  // Use a simple approach: check today, then iterate forward
  const maxDays = tier === "monthly" ? 62 : 8; // enough to find next occurrence

  for (let daysAhead = 0; daysAhead < maxDays; daysAhead++) {
    const candidate = new Date(currentMs + daysAhead * 24 * 60 * 60 * 1000);
    const candParts = nowInTimezone(tz); // approximate — recalculate for the candidate
    // For simplicity, use the candidate date directly
    candidate.setHours(cfg.hour ?? 0, cfg.minute, 0, 0);

    if (candidate.getTime() <= currentMs) continue;

    // Check day-of-week constraint for weekly
    if (tier === "weekly" && cfg.day_of_week !== undefined) {
      if (candidate.getDay() !== cfg.day_of_week) continue;
    }

    // Check day-of-month constraint for monthly
    if (tier === "monthly" && cfg.day_of_month !== undefined) {
      if (candidate.getDate() !== cfg.day_of_month) continue;
    }

    return candidate.getTime() - currentMs;
  }

  // Fallback: 24 hours
  return 24 * 60 * 60 * 1000;
}

/**
 * Schedule a single tier's next backup run.
 */
function scheduleTier(tier: string, cfg: TierConfig, schedule: BackupSchedule, config: GatewayConfig): void {
  if (!cfg.enabled) return;

  // Clear any existing timer
  const existing = activeTimers.get(tier);
  if (existing) clearTimeout(existing);

  const delayMs = msUntilNextRun(tier, cfg, schedule.timezone);
  const delayMin = Math.round(delayMs / 60000);
  console.log(`[backup-scheduler] ${tier} next run in ${delayMin} minutes`);

  // If delayMs exceeds the 32-bit limit, sleep for MAX_TIMEOUT_MS then
  // re-evaluate (the remaining time will be shorter next iteration).
  if (delayMs > MAX_TIMEOUT_MS) {
    const timer = setTimeout(() => {
      const freshSchedule = loadSchedule();
      const freshCfg = freshSchedule.tiers[tier];
      if (freshCfg) scheduleTier(tier, freshCfg, freshSchedule, config);
    }, MAX_TIMEOUT_MS);
    timer.unref();
    activeTimers.set(tier, timer);
    return;
  }

  const timer = setTimeout(async () => {
    console.log(`[backup-scheduler] Running scheduled ${tier} backup`);
    await runBackup(tier as BackupTier, config, schedule.verification);

    // Reschedule for next occurrence
    const freshSchedule = loadSchedule();
    const freshCfg = freshSchedule.tiers[tier];
    if (freshCfg) scheduleTier(tier, freshCfg, freshSchedule, config);
  }, delayMs);

  // Prevent timer from keeping process alive
  timer.unref();
  activeTimers.set(tier, timer);
}

/**
 * Check for missed backups and handle catch-up.
 */
async function checkMissedBackups(schedule: BackupSchedule, config: GatewayConfig): Promise<void> {
  for (const [tier, cfg] of Object.entries(schedule.tiers)) {
    if (!cfg.enabled) continue;
    if (tier === "hourly") continue; // hourly uses run_on_startup instead

    const lastBackup = getLastBackupTime(tier);
    if (!lastBackup) {
      // Never run before — the regular schedule will handle it
      console.log(`[backup-scheduler] ${tier} has no previous backup — will run on schedule`);
      continue;
    }

    // Check if enough time has passed that we missed a scheduled backup
    const hoursSinceLast = (Date.now() - lastBackup.getTime()) / (1000 * 60 * 60);
    const thresholds: Record<string, number> = { daily: 36, weekly: 192, monthly: 744 };
    const threshold = thresholds[tier];

    if (threshold && hoursSinceLast > threshold) {
      console.log(`[backup-scheduler] ${tier} backup missed (last: ${lastBackup.toISOString()}) — running catch-up`);
      await runBackup(tier as BackupTier, config, schedule.verification, true);
    }
  }
}

/**
 * Initialize the backup scheduler. Call this from server startup.
 */
export async function initBackupScheduler(config: GatewayConfig): Promise<void> {
  console.log("[backup-scheduler] Initializing backup scheduler");

  const schedule = loadSchedule();

  // Ensure directories exist
  for (const dir of ["hourly", "daily", "weekly", "monthly", "saved", "migrations"]) {
    const p = join(BACKUP_DIR, dir);
    if (!existsSync(p)) mkdirSync(p, { recursive: true });
  }

  // Schedule all enabled tiers
  for (const [tier, cfg] of Object.entries(schedule.tiers)) {
    scheduleTier(tier, cfg, schedule, config);
  }

  // Check for missed backups
  await checkMissedBackups(schedule, config);

  // Hourly run_on_startup
  const hourly = schedule.tiers.hourly;
  if (hourly?.enabled && hourly?.run_on_startup) {
    console.log("[backup-scheduler] Running startup hourly backup");
    // Run in background so we don't block server startup
    runBackup("hourly", config, schedule.verification).catch(err => {
      console.error("[backup-scheduler] Startup backup failed:", err.message);
    });
  }

  console.log("[backup-scheduler] Scheduler initialized");
}
