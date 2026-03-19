/**
 * Shared types for the snapshot-aware backup system (design doc 051).
 */

export type BackupTier = "hourly" | "daily" | "weekly" | "monthly" | "saved" | "migrations";

export type BackupStatus = "success" | "failed" | "skipped";

export interface TierConfig {
  enabled: boolean;
  minute: number;
  hour?: number;
  day_of_week?: number;   // 0=Sunday..6=Saturday (JS convention)
  day_of_month?: number;
  retention: number;
  run_on_startup?: boolean;
}

export interface VerificationConfig {
  enabled: boolean;
  fail_on_empty_table: boolean;
  fail_on_count_mismatch: boolean;
  tolerance_percent: number;
}

export interface BackupSchedule {
  version: number;
  timezone: string;
  tiers: Record<string, TierConfig>;
  verification: VerificationConfig;
}

export interface TableVerification {
  table: string;
  live_count: number;
  backup_count: number;
  passed: boolean;
  reason?: string;
}

export interface SnapshotInfo {
  found: boolean;
  offset?: number;
  size_bytes?: number;
}

export interface BackupVerificationResult {
  passed: boolean;
  snapshot_offset: number | null;
  snapshot_size_bytes: number | null;
  tables: TableVerification[];
}

export interface BackupArchiveInfo {
  filename: string;
  size_bytes: number;
  path: string;
}

export interface BackupPreFlight {
  stdb_healthy: boolean;
  tx_offset: number | null;
  live_counts: Record<string, number>;
}

export interface BackupLogEntry {
  timestamp: string;
  tier: string;
  status: BackupStatus;
  duration_ms: number;
  archive: BackupArchiveInfo | null;
  pre_flight: BackupPreFlight;
  verification: BackupVerificationResult | null;
  error: string | null;
  catch_up: boolean;
}

export interface BackupResult {
  status: BackupStatus;
  tier: string;
  duration_ms: number;
  archive: BackupArchiveInfo | null;
  verification: BackupVerificationResult | null;
  error: string | null;
}

export const DEFAULT_SCHEDULE: BackupSchedule = {
  version: 1,
  timezone: "America/New_York",
  tiers: {
    hourly: {
      enabled: true,
      minute: 0,
      retention: 24,
      run_on_startup: true,
    },
    daily: {
      enabled: true,
      hour: 12,
      minute: 15,
      retention: 7,
      run_on_startup: false,
    },
    weekly: {
      enabled: true,
      hour: 13,
      minute: 30,
      day_of_week: 3, // Wednesday
      retention: 5,
      run_on_startup: false,
    },
    monthly: {
      enabled: true,
      hour: 14,
      minute: 45,
      day_of_month: 1,
      retention: 12,
      run_on_startup: false,
    },
  },
  verification: {
    enabled: true,
    fail_on_empty_table: true,
    fail_on_count_mismatch: true,
    tolerance_percent: 0,
  },
};
