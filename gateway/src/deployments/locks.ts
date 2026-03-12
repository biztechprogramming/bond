/**
 * Deployment Locks — per-environment file-based locks.
 *
 * Lock file: ~/.bond/deployments/locks/{env}.lock
 * Contents: JSON { agent, script, since, expires_at }
 *
 * One deployment per environment at a time.
 * Stale locks (past expires_at) are auto-released.
 */

import fs from "node:fs";
import path from "node:path";

export interface LockInfo {
  agent: string;
  script: string;
  since: string;
  expires_at: string;
}

export function getLocksDir(deploymentsDir: string): string {
  return path.join(deploymentsDir, "locks");
}

export function getLockPath(deploymentsDir: string, env: string): string {
  return path.join(getLocksDir(deploymentsDir), `${env}.lock`);
}

/**
 * Try to acquire the lock for an environment.
 * Returns true if acquired, false if already locked (by a non-stale lock).
 */
export function acquireLock(
  deploymentsDir: string,
  env: string,
  agentId: string,
  scriptId: string,
  ttlSeconds = 1200,
): boolean {
  fs.mkdirSync(getLocksDir(deploymentsDir), { recursive: true });
  const lockPath = getLockPath(deploymentsDir, env);

  // Check for existing lock
  if (fs.existsSync(lockPath)) {
    try {
      const existing: LockInfo = JSON.parse(fs.readFileSync(lockPath, "utf8"));
      const expiresAt = new Date(existing.expires_at).getTime();
      if (Date.now() < expiresAt) {
        return false; // active lock
      }
      // Stale lock — auto-release
      console.log(`[locks] Auto-releasing stale lock for ${env} (expired ${existing.expires_at})`);
    } catch {
      // malformed lock — overwrite
    }
  }

  const now = new Date();
  const expiresAt = new Date(now.getTime() + ttlSeconds * 1000);
  const lock: LockInfo = {
    agent: agentId,
    script: scriptId,
    since: now.toISOString(),
    expires_at: expiresAt.toISOString(),
  };
  fs.writeFileSync(lockPath, JSON.stringify(lock, null, 2), { mode: 0o600 });
  return true;
}

/**
 * Release the lock for an environment.
 */
export function releaseLock(deploymentsDir: string, env: string): void {
  const lockPath = getLockPath(deploymentsDir, env);
  if (fs.existsSync(lockPath)) {
    fs.unlinkSync(lockPath);
  }
}

/**
 * Get current lock info. Returns null if unlocked or stale.
 */
export function getLock(deploymentsDir: string, env: string): LockInfo | null {
  const lockPath = getLockPath(deploymentsDir, env);
  if (!fs.existsSync(lockPath)) return null;

  try {
    const lock: LockInfo = JSON.parse(fs.readFileSync(lockPath, "utf8"));
    const expiresAt = new Date(lock.expires_at).getTime();
    if (Date.now() >= expiresAt) {
      // Stale — auto-release
      fs.unlinkSync(lockPath);
      return null;
    }
    return lock;
  } catch {
    return null;
  }
}

/**
 * Check if deployment window is currently open for an environment.
 */
export function isWithinDeploymentWindow(
  windowDays: string,
  windowStart: string,
  windowEnd: string,
  windowTimezone: string,
): boolean {
  if (!windowStart || !windowEnd) return true; // no window configured

  let days: string[] = [];
  try {
    days = JSON.parse(windowDays);
  } catch {
    days = [];
  }
  if (days.length === 0) return true;

  const now = new Date();
  const tz = windowTimezone || "UTC";

  try {
    // Get current time in the configured timezone
    const formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const parts = formatter.formatToParts(now);
    const weekday = parts.find(p => p.type === "weekday")?.value?.toLowerCase().slice(0, 3) ?? "";
    const hour = parts.find(p => p.type === "hour")?.value ?? "00";
    const minute = parts.find(p => p.type === "minute")?.value ?? "00";
    const currentTime = `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;

    const dayNames: Record<string, string> = {
      mon: "mon", tue: "tue", wed: "wed", thu: "thu", fri: "fri", sat: "sat", sun: "sun",
    };
    const dayAbbrevMap: Record<string, string> = {
      mon: "mon", tue: "tue", wed: "wed", thu: "thu", fri: "fri", sat: "sat", sun: "sun",
    };
    const currentDay = dayAbbrevMap[weekday] ?? weekday;

    if (!days.includes(currentDay)) return false;
    if (currentTime < windowStart) return false;
    if (currentTime > windowEnd) return false;

    return true;
  } catch {
    return true; // If timezone parsing fails, don't block deployment
  }
}
