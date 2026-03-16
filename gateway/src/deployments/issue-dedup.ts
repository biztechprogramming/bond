/**
 * Issue Deduplication — error fingerprinting and GitHub issue dedup.
 *
 * Design Doc 044 §8 — Intelligent Issue Management
 */

import crypto from "node:crypto";
import { executeCommand } from "../broker/executor.js";

// ── Types ───────────────────────────────────────────────────────────────────

export interface ErrorFingerprint {
  environment: string;
  category: string;
  component: string;
  message_pattern: string;
  hash: string;
}

export interface IssueDedupResult {
  file: boolean;
  existing_issue?: number;
  action: "create" | "comment" | "skip";
}

// ── Fingerprinting ──────────────────────────────────────────────────────────

/**
 * Compute an error fingerprint by normalizing the raw message and hashing.
 */
export function computeFingerprint(
  env: string,
  category: string,
  component: string,
  rawMessage: string,
): ErrorFingerprint {
  const normalized = rawMessage
    .replace(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\dZ]*/g, "<timestamp>")
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, "<uuid>")
    .replace(/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/g, "<ip>")
    .replace(/:\d{4,5}/g, ":<port>")
    .replace(/pid \d+/g, "pid <pid>")
    .replace(/\b\d{5,}\b/g, "<id>")
    .trim();

  const hash = crypto.createHash("sha256")
    .update(`${env}:${category}:${component}:${normalized}`)
    .digest("hex")
    .slice(0, 16);

  return { environment: env, category, component, message_pattern: normalized, hash };
}

// ── Issue Search ────────────────────────────────────────────────────────────

/**
 * Search GitHub for existing open issues matching the fingerprint.
 */
export async function searchExistingIssues(
  fingerprint: ErrorFingerprint,
  issueRepo: string,
): Promise<Array<{ number: number; title: string; createdAt: string }>> {
  try {
    const result = await executeCommand(
      `gh issue list --repo ${issueRepo} --state open --label "fingerprint:${fingerprint.hash}" --json number,title,createdAt --limit 5`,
      { timeout: 15 },
    );
    if (result.exit_code === 0 && result.stdout.trim()) {
      return JSON.parse(result.stdout);
    }
  } catch (err: any) {
    console.error("[issue-dedup] Search failed:", err.message);
  }
  return [];
}

/**
 * Determine whether to file a new issue, comment on existing, or skip.
 */
export async function shouldFileIssue(
  fingerprint: ErrorFingerprint,
  issueRepo: string,
  dedupWindowHours = 24,
): Promise<IssueDedupResult> {
  const existing = await searchExistingIssues(fingerprint, issueRepo);

  if (existing.length > 0) {
    const latest = existing[0]!;
    const hoursSince = (Date.now() - new Date(latest.createdAt).getTime()) / 3600000;

    if (hoursSince < dedupWindowHours) {
      return { file: false, existing_issue: latest.number, action: "comment" };
    }
  }

  return { file: true, action: "create" };
}

// ── Issue Formatting ────────────────────────────────────────────────────────

export interface MonitoringAlertInfo {
  title: string;
  environment: string;
  category: string;
  component: string;
  severity: string;
  fingerprint_hash: string;
  description: string;
  error_output?: string;
  first_occurrence?: string;
  occurrence_count?: number;
  last_healthy?: string;
  last_deploy_receipt_id?: string;
  resource_status?: { cpu_load?: string; memory_pct?: string; disk_pct?: string; uptime?: string };
  agent_analysis?: string;
  suggested_actions?: string;
  cycle_number?: number;
}

/**
 * Format a GitHub issue body from a monitoring alert.
 */
export function formatIssueBody(alert: MonitoringAlertInfo): string {
  const lines = [
    `## Monitoring Alert: ${alert.title}`,
    "",
    `**Environment:** ${alert.environment}`,
    `**Category:** ${alert.category}`,
    `**Component:** ${alert.component}`,
    `**Severity:** ${alert.severity}`,
    `**Detected:** ${new Date().toISOString()}`,
    `**Fingerprint:** \`${alert.fingerprint_hash}\``,
    "",
    "### Current Status",
    alert.description,
    "",
  ];

  if (alert.error_output) {
    lines.push("### Error Details", "```", alert.error_output, "```", "");
  }

  lines.push(
    "### Historical Context",
    `- First detected: ${alert.first_occurrence || "now"}`,
    `- Occurrences in last 24h: ${alert.occurrence_count ?? 1}`,
    `- Last successful health check: ${alert.last_healthy || "unknown"}`,
    `- Last deployment: ${alert.last_deploy_receipt_id || "unknown"}`,
    "",
  );

  if (alert.resource_status) {
    const rs = alert.resource_status;
    lines.push(
      "### Resource Status at Time of Detection",
      `- CPU: ${rs.cpu_load || "unknown"}`,
      `- RAM: ${rs.memory_pct || "unknown"}%`,
      `- Disk: ${rs.disk_pct || "unknown"}%`,
      `- Uptime: ${rs.uptime || "unknown"}`,
      "",
    );
  }

  if (alert.agent_analysis) {
    lines.push("### Agent Analysis", alert.agent_analysis, "");
  }

  if (alert.suggested_actions) {
    lines.push("### Suggested Actions", alert.suggested_actions, "");
  }

  lines.push(
    "---",
    `*Filed by deploy-${alert.environment} agent — Monitoring cycle #${alert.cycle_number ?? 0}*`,
    `*Fingerprint: \`${alert.fingerprint_hash}\` — Duplicate issues with this fingerprint will be added as comments.*`,
  );

  return lines.join("\n");
}
