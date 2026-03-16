/**
 * Deploy Handler tests.
 *
 * Tests handleDeploy with mocked SpacetimeDB and controlled filesystem.
 */

import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir, homedir } from "node:os";

import { registerScript, getScriptVersionDir } from "../deployments/scripts.js";
import { releaseLock } from "../deployments/locks.js";

// We need to mock modules that handleDeploy imports
vi.mock("../spacetimedb/client.js", () => ({
  sqlQuery: vi.fn(async (_url: string, _mod: string, sql: string) => {
    // Environment config query
    if (sql.includes("deployment_environments")) {
      return [{
        name: "qa",
        is_active: true,
        window_days: '["mon","tue","wed","thu","fri","sat","sun"]',
        window_start: "00:00",
        window_end: "23:59",
        window_timezone: "UTC",
        max_script_timeout: 600,
      }];
    }
    // Promotion check
    if (sql.includes("deployment_promotions") && sql.includes("unpromoted-script")) {
      return [];
    }
    if (sql.includes("deployment_promotions") && sql.includes("promoted-script")) {
      return [{ id: "promo-1", status: "promoted", script_sha256: "abc" }];
    }
    if (sql.includes("deployment_promotions")) {
      return [];
    }
    return [];
  }),
  callReducer: vi.fn(async () => {}),
}));

vi.mock("../deployments/events.js", () => ({
  emitDeploymentStarted: vi.fn(),
  emitDeploymentSucceeded: vi.fn(),
  emitDeploymentFailed: vi.fn(),
  emitRollbackTriggered: vi.fn(),
  emitHealthCheckFailed: vi.fn(),
  emitDeploymentEvent: vi.fn(),
  emitScriptPromoted: vi.fn(),
}));

vi.mock("../deployments/secrets.js", () => ({
  loadSecrets: vi.fn(() => ({})),
}));

vi.mock("../deployments/stdb.js", () => ({
  getEnvironments: vi.fn(async () => [
    { name: "qa", order: 1, is_active: true },
  ]),
}));

vi.mock("../deployments/health-scheduler.js", () => ({
  executeHealthCheck: vi.fn(async () => ({
    environment: "qa",
    status: "healthy",
    last_check: new Date().toISOString(),
    results: [],
  })),
}));

vi.mock("../deployments/drift-detector.js", () => ({
  saveBaseline: vi.fn(),
  compareDrift: vi.fn(() => ({ has_drift: false, changes: [] })),
}));

import { handleDeploy } from "../broker/deploy-handler.js";

const DEPLOY_DIR = join(homedir(), ".bond", "deployments");

const agent = (sub: string) => ({
  sub,
  sid: "session-1",
  iat: Date.now(),
  exp: Date.now() + 3600000,
});

const cfg: any = {
  spacetimedbUrl: "http://localhost:3000",
  spacetimedbModuleName: "bond",
  spacetimedbToken: "test-token",
};

beforeAll(() => {
  // Register scripts in the real deployments dir (handleDeploy uses homedir path)
  try {
    registerScript(DEPLOY_DIR, {
      script_id: "promoted-script",
      version: "v1",
      name: "Promoted Script",
      registered_by: "test",
      dry_run: true,
      rollback: "rollback.sh",
      files: {
        "deploy.sh": Buffer.from("#!/bin/bash\necho 'deployed'\nexit 0"),
        "rollback.sh": Buffer.from("#!/bin/bash\necho 'rolled back'\nexit 0"),
      },
    });
  } catch { /* already exists */ }

  try {
    registerScript(DEPLOY_DIR, {
      script_id: "unpromoted-script",
      version: "v1",
      name: "Unpromoted",
      registered_by: "test",
      files: { "deploy.sh": Buffer.from("#!/bin/bash\necho ok") },
    });
  } catch { /* already exists */ }
});

beforeEach(() => {
  releaseLock(DEPLOY_DIR, "qa");
});

describe("handleDeploy", () => {
  it("rejects non-deploy agent IDs", async () => {
    const result = await handleDeploy(cfg, agent("random-agent"), { action: "deploy", script_id: "x" });
    expect(result.status).toBe("denied");
    expect(result.reason).toContain("Cannot derive environment");
  });

  it("denies unpromoted script", async () => {
    const result = await handleDeploy(cfg, agent("deploy-qa"), {
      action: "deploy",
      script_id: "unpromoted-script",
      version: "v1",
    });
    expect(result.status).toBe("denied");
    expect(result.reason).toContain("not promoted");
  });

  it("deploys promoted script successfully", async () => {
    const result = await handleDeploy(cfg, agent("deploy-qa"), {
      action: "deploy",
      script_id: "promoted-script",
      version: "v1",
    });
    expect(result.status).toBe("ok");
    expect(result.exit_code).toBe(0);
    expect(result.receipt_id).toBeTruthy();
  });

  it("validates script (hash, syntax, window)", async () => {
    const result = await handleDeploy(cfg, agent("deploy-qa"), {
      action: "validate",
      script_id: "promoted-script",
      version: "v1",
    });
    expect(result.action).toBe("validate");
    expect(result.info).toBeDefined();
    // Should have checks array
    expect(result.info.checks).toBeDefined();
  });

  it("dry-run executes with --dry-run flag", async () => {
    const result = await handleDeploy(cfg, agent("deploy-qa"), {
      action: "dry-run",
      script_id: "promoted-script",
      version: "v1",
    });
    expect(result.action).toBe("dry-run");
    // promoted-script has dry_run: true so it should execute
    expect(result.exit_code).toBeDefined();
  });

  it("rollback executes rollback script", async () => {
    const result = await handleDeploy(cfg, agent("deploy-qa"), {
      action: "rollback",
      script_id: "promoted-script",
      version: "v1",
    });
    expect(result.action).toBe("rollback");
    expect(result.exit_code).toBe(0);
    expect(result.stdout).toContain("rolled back");
  });

  it("lock prevents concurrent deploys (returns queued)", async () => {
    // Manually acquire the lock to simulate a deploy in progress
    const { acquireLock } = await import("../deployments/locks.js");
    acquireLock(DEPLOY_DIR, "qa", "other-agent", "other-script", 120);

    const result = await handleDeploy(cfg, agent("deploy-qa"), {
      action: "deploy",
      script_id: "promoted-script",
      version: "v1",
    });

    expect(result.status).toBe("queued");
    expect(result.queue_position).toBeGreaterThanOrEqual(1);

    releaseLock(DEPLOY_DIR, "qa");
  });

  it("health-check runs without script_id", async () => {
    const result = await handleDeploy(cfg, agent("deploy-qa"), {
      action: "health-check",
    });
    expect(result.status).toBe("ok");
    expect(result.action).toBe("health-check");
  });

  it("returns info for script", async () => {
    const result = await handleDeploy(cfg, agent("deploy-qa"), {
      action: "info",
      script_id: "promoted-script",
      version: "v1",
    });
    expect(result.status).toBe("ok");
    expect(result.info?.name).toBe("Promoted Script");
  });
});
