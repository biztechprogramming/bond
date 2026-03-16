/**
 * Discovery, monitoring, issue-dedup, and manifest tests.
 *
 * Design Doc 044 — Remote Discovery & Deployment Monitoring
 */

import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { homedir } from "node:os";

// ── Mocks ───────────────────────────────────────────────────────────────────

const mockAlerts: any[] = [];

vi.mock("../spacetimedb/client.js", () => ({
  sqlQuery: vi.fn(async (_url: string, _mod: string, sql: string) => {
    if (sql.includes("monitoring_alerts")) {
      return mockAlerts;
    }
    if (sql.includes("deployment_environments")) {
      return [];
    }
    if (sql.includes("deployment_resources")) {
      return [];
    }
    return [];
  }),
  callReducer: vi.fn(async (_url: string, _mod: string, reducer: string, args: any[]) => {
    if (reducer === "create_monitoring_alert") {
      mockAlerts.push(args[0]);
    }
    if (reducer === "resolve_monitoring_alert") {
      const update = args[0];
      const alert = mockAlerts.find(a => a.id === update.id);
      if (alert) alert.resolved_at = update.resolved_at;
    }
  }),
}));

vi.mock("../broker/executor.js", () => ({
  executeCommand: vi.fn(async (cmd: string) => {
    if (cmd.includes("gh issue list")) {
      return { exit_code: 0, stdout: "[]", stderr: "", duration_ms: 100 };
    }
    return { exit_code: 0, stdout: "", stderr: "", duration_ms: 50 };
  }),
}));

vi.mock("../deployments/events.js", () => ({
  emitDeploymentEvent: vi.fn(),
  emitHealthCheckFailed: vi.fn(),
  emitMonitoringAlert: vi.fn(),
  emitDiscoveryCompleted: vi.fn(),
  initDeploymentEvents: vi.fn(),
}));

// ── Imports (after mocks) ───────────────────────────────────────────────────

import { computeFingerprint, shouldFileIssue, formatIssueBody } from "../deployments/issue-dedup.js";
import { writeManifest, readManifest, listManifests, diffManifests } from "../deployments/manifest.js";
import type { DeploymentManifest } from "../deployments/manifest.js";
import { createMonitoringAlert, getMonitoringAlerts, resolveMonitoringAlert } from "../deployments/stdb.js";

const cfg: any = {
  spacetimedbUrl: "http://localhost:3000",
  spacetimedbModuleName: "bond",
  spacetimedbToken: "test-token",
};

// ── Cleanup ─────────────────────────────────────────────────────────────────

const MANIFESTS_DIR = path.join(homedir(), ".bond", "deployments", "discovery", "manifests");

afterAll(() => {
  // Clean up test manifests
  for (const name of ["test-app", "test-app-v2"]) {
    try { fs.unlinkSync(path.join(MANIFESTS_DIR, `${name}.json`)); } catch { /* ok */ }
  }
});

// ── Fingerprint Tests ───────────────────────────────────────────────────────

describe("computeFingerprint", () => {
  it("strips timestamps from messages", () => {
    const fp = computeFingerprint("prod", "log-error", "app", "Error at 2026-03-16T14:30:00Z: connection failed");
    expect(fp.message_pattern).toContain("<timestamp>");
    expect(fp.message_pattern).not.toContain("2026-03-16");
  });

  it("strips UUIDs from messages", () => {
    const fp = computeFingerprint("prod", "log-error", "app", "Request 550e8400-e29b-41d4-a716-446655440000 failed");
    expect(fp.message_pattern).toContain("<uuid>");
    expect(fp.message_pattern).not.toContain("550e8400");
  });

  it("strips IP addresses", () => {
    const fp = computeFingerprint("prod", "log-error", "nginx", "Connection from 192.168.1.100 refused");
    expect(fp.message_pattern).toContain("<ip>");
    expect(fp.message_pattern).not.toContain("192.168.1.100");
  });

  it("strips ports", () => {
    const fp = computeFingerprint("prod", "log-error", "app", "Listening on :3000 failed");
    expect(fp.message_pattern).toContain(":<port>");
  });

  it("strips PIDs", () => {
    const fp = computeFingerprint("prod", "service-down", "nginx", "pid 12345 crashed");
    expect(fp.message_pattern).toContain("pid <pid>");
  });

  it("strips large numeric IDs", () => {
    const fp = computeFingerprint("prod", "log-error", "app", "Transaction 123456 failed");
    expect(fp.message_pattern).toContain("<id>");
  });

  it("produces consistent hashes for same normalized message", () => {
    const fp1 = computeFingerprint("prod", "log-error", "app", "Error at 2026-03-16T14:30:00Z: timeout");
    const fp2 = computeFingerprint("prod", "log-error", "app", "Error at 2026-03-17T09:00:00Z: timeout");
    expect(fp1.hash).toBe(fp2.hash);
  });

  it("produces different hashes for different environments", () => {
    const fp1 = computeFingerprint("prod", "log-error", "app", "connection refused");
    const fp2 = computeFingerprint("staging", "log-error", "app", "connection refused");
    expect(fp1.hash).not.toBe(fp2.hash);
  });

  it("returns 16-char hex hash", () => {
    const fp = computeFingerprint("prod", "health-check-failure", "nginx", "502 Bad Gateway");
    expect(fp.hash).toMatch(/^[0-9a-f]{16}$/);
  });
});

// ── Manifest Tests ──────────────────────────────────────────────────────────

describe("manifest read/write/list", () => {
  const testManifest: DeploymentManifest = {
    manifest_version: "1.0",
    application: "test-app",
    discovered_at: "2026-03-16T14:00:00Z",
    discovered_by: "deploy-prod",
    servers: [{
      name: "prod-01",
      host: "10.0.1.50",
      os: "Ubuntu 24.04",
      role: "application",
    }],
    topology: { nodes: [], edges: [] },
    security_observations: [
      { severity: "warning", message: "SSH root login enabled" },
    ],
  };

  it("writes and reads a manifest", () => {
    writeManifest(testManifest);
    const read = readManifest("test-app");
    expect(read).not.toBeNull();
    expect(read!.application).toBe("test-app");
    expect(read!.servers).toHaveLength(1);
    expect(read!.servers[0].host).toBe("10.0.1.50");
  });

  it("lists manifests", () => {
    const list = listManifests();
    expect(list).toContain("test-app");
  });

  it("returns null for missing manifest", () => {
    const read = readManifest("nonexistent-app");
    expect(read).toBeNull();
  });
});

describe("manifest diff", () => {
  const a: DeploymentManifest = {
    manifest_version: "1.0",
    application: "test-app",
    discovered_at: "2026-03-16T14:00:00Z",
    discovered_by: "deploy-prod",
    servers: [
      { name: "prod-01", host: "10.0.1.50", os: "Ubuntu 24.04", role: "application" },
      { name: "db-01", host: "10.0.2.10", role: "database" },
    ],
    topology: { nodes: [], edges: [] },
    security_observations: [
      { severity: "warning", message: "SSH root login enabled" },
    ],
  };

  const b: DeploymentManifest = {
    manifest_version: "1.0",
    application: "test-app",
    discovered_at: "2026-03-17T14:00:00Z",
    discovered_by: "deploy-prod",
    servers: [
      { name: "prod-01", host: "10.0.1.51", os: "Ubuntu 24.04", role: "application" },
      { name: "cache-01", host: "10.0.3.10", role: "cache" },
    ],
    topology: { nodes: [{ id: "cache-01", type: "redis" }], edges: [] },
    security_observations: [
      { severity: "info", message: "All ports secured" },
    ],
  };

  it("detects added and removed servers", () => {
    const diff = diffManifests(a, b);
    expect(diff.added_servers).toContain("cache-01");
    expect(diff.removed_servers).toContain("db-01");
  });

  it("detects changed servers", () => {
    const diff = diffManifests(a, b);
    expect(diff.changed_servers).toHaveLength(1);
    expect(diff.changed_servers[0].name).toBe("prod-01");
    expect(diff.changed_servers[0].changes).toContain("host: 10.0.1.50 → 10.0.1.51");
  });

  it("detects topology changes", () => {
    const diff = diffManifests(a, b);
    expect(diff.topology_changed).toBe(true);
  });

  it("detects security observation changes", () => {
    const diff = diffManifests(a, b);
    expect(diff.added_observations).toContain("All ports secured");
    expect(diff.removed_observations).toContain("SSH root login enabled");
  });
});

// ── Issue Dedup Tests ───────────────────────────────────────────────────────

describe("shouldFileIssue", () => {
  it("returns create when no existing issues", async () => {
    const fp = computeFingerprint("prod", "health-check-failure", "nginx", "502 Bad Gateway");
    const result = await shouldFileIssue(fp, "org/repo", 24);
    expect(result.action).toBe("create");
    expect(result.file).toBe(true);
  });
});

describe("formatIssueBody", () => {
  it("generates markdown with all sections", () => {
    const body = formatIssueBody({
      title: "Health check failure",
      environment: "prod",
      category: "health-check-failure",
      component: "nginx",
      severity: "critical",
      fingerprint_hash: "abc123def456",
      description: "Nginx returned 502 for /health endpoint",
      error_output: "502 Bad Gateway",
      agent_analysis: "Upstream server not responding",
      suggested_actions: "Restart the application server",
      cycle_number: 42,
    });

    expect(body).toContain("## Monitoring Alert: Health check failure");
    expect(body).toContain("**Environment:** prod");
    expect(body).toContain("**Fingerprint:** `abc123def456`");
    expect(body).toContain("502 Bad Gateway");
    expect(body).toContain("Monitoring cycle #42");
  });
});

// ── Monitoring Alert CRUD (mock STDB) ───────────────────────────────────────

describe("monitoring alert CRUD", () => {
  it("creates and retrieves alerts", async () => {
    const id = await createMonitoringAlert(cfg, {
      environment: "prod",
      category: "health-check-failure",
      component: "nginx",
      fingerprint_hash: "abc123",
      severity: "critical",
      message: "Health check failed",
      detected_at: Date.now(),
    });

    expect(id).toBeTruthy();

    const alerts = await getMonitoringAlerts(cfg, "prod");
    expect(alerts.length).toBeGreaterThan(0);
  });

  it("resolves an alert", async () => {
    const id = await createMonitoringAlert(cfg, {
      environment: "prod",
      category: "log-error",
      component: "app",
      fingerprint_hash: "def456",
      severity: "warning",
      message: "Connection pool exhaustion",
      detected_at: Date.now(),
    });

    await resolveMonitoringAlert(cfg, id);
    const alert = mockAlerts.find(a => a.id === id);
    expect(alert.resolved_at).toBeTruthy();
  });
});
