/**
 * Deployment Receipts — append-only audit trail.
 *
 * Receipts are written to ~/.bond/deployments/receipts/{env}/{receipt-id}.json
 * This directory is HOST-ONLY — never mounted into agent containers.
 * Agents can request receipts via /broker/deploy action: "receipt"
 */

import fs from "node:fs";
import path from "node:path";

export interface DeploymentPhase {
  status: "pass" | "fail" | "skip";
  duration_ms: number;
  output_summary?: string;
  exit_code?: number;
  checks_passed?: number;
  checks_total?: number;
}

export interface DeploymentReceipt {
  receipt_id: string;
  type?: "deployment" | "manual_intervention";
  script_id?: string;
  script_version?: string;
  script_sha256?: string;
  environment: string;
  agent_id: string;
  timestamp_start: string;
  timestamp_end: string;
  duration_ms: number;
  status: "success" | "failed" | "rolled_back";

  phases?: {
    validation?: DeploymentPhase;
    pre_hook?: DeploymentPhase;
    dry_run?: DeploymentPhase;
    execution?: DeploymentPhase;
    post_hook?: DeploymentPhase;
    health_check?: DeploymentPhase;
    rollback?: DeploymentPhase;
  };

  health_before?: { status: string; checks_passed?: number };
  health_after?: { status: string; checks_passed?: number };

  rollback_triggered: boolean;
  bug_ticket_filed: boolean;

  context?: {
    promoted_by?: string;
    promoted_at?: string;
    previous_environment_receipt?: string;
  };

  error_output?: string;
}

export function getReceiptsDir(deploymentsDir: string, env: string): string {
  return path.join(deploymentsDir, "receipts", env);
}

/**
 * Write a receipt to disk. Receipts are append-only; existing receipts are never overwritten.
 */
export function writeReceipt(deploymentsDir: string, receipt: DeploymentReceipt): void {
  const dir = getReceiptsDir(deploymentsDir, receipt.environment);
  fs.mkdirSync(dir, { recursive: true });
  const filePath = path.join(dir, `${receipt.receipt_id}.json`);
  if (fs.existsSync(filePath)) return; // immutable — never overwrite
  fs.writeFileSync(filePath, JSON.stringify(receipt, null, 2), { mode: 0o644 });
}

/**
 * Read a specific receipt by ID.
 */
export function readReceipt(
  deploymentsDir: string,
  env: string,
  receiptId: string,
): DeploymentReceipt | null {
  const filePath = path.join(getReceiptsDir(deploymentsDir, env), `${receiptId}.json`);
  if (!fs.existsSync(filePath)) return null;
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

/**
 * List all receipts for an environment (newest first by filename).
 */
export function listReceipts(
  deploymentsDir: string,
  env: string,
  limit = 50,
): DeploymentReceipt[] {
  const dir = getReceiptsDir(deploymentsDir, env);
  if (!fs.existsSync(dir)) return [];

  const files = fs.readdirSync(dir)
    .filter(f => f.endsWith(".json"))
    .sort()
    .reverse()
    .slice(0, limit);

  const receipts: DeploymentReceipt[] = [];
  for (const file of files) {
    try {
      receipts.push(JSON.parse(fs.readFileSync(path.join(dir, file), "utf8")));
    } catch {
      // skip malformed receipts
    }
  }
  return receipts;
}

/**
 * Find the most recent successful receipt for a script in an environment.
 */
export function findLatestReceipt(
  deploymentsDir: string,
  env: string,
  scriptId: string,
): DeploymentReceipt | null {
  const all = listReceipts(deploymentsDir, env, 200);
  return all.find(r => r.script_id === scriptId && r.status === "success") ?? null;
}

/**
 * Build a receipt ID string.
 */
export function buildReceiptId(scriptId: string, env: string): string {
  const ts = new Date().toISOString().replace(/[:.]/g, "").replace("T", "T").slice(0, 18) + "Z";
  return `receipt-${scriptId}-${env}-${ts}`;
}
