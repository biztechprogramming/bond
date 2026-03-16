/**
 * Deployment Health Scheduler & Drift Detection tests.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir, homedir } from "node:os";

import { saveBaseline, loadBaseline, compareDrift } from "../deployments/drift-detector.js";
import type { HealthCheckResult } from "../deployments/health-scheduler.js";

// Override DEPLOYMENTS_DIR by creating baselines in the real path
// drift-detector uses homedir()/.bond/deployments — we test saveBaseline/loadBaseline/compareDrift
// which use that path. We'll use a temp approach by testing the functions directly.

// Since drift-detector hardcodes DEPLOYMENTS_DIR, we test compareDrift by saving baselines first.

const TEST_ENV = `drift-test-${Date.now()}`;

afterAll(() => {
  // Clean up baseline file
  try {
    const { unlinkSync } = require("node:fs");
    const baselinePath = join(homedir(), ".bond", "deployments", "health", TEST_ENV, "baseline.json");
    unlinkSync(baselinePath);
    const { rmdirSync } = require("node:fs");
    rmdirSync(join(homedir(), ".bond", "deployments", "health", TEST_ENV));
  } catch { /* ignore */ }
});

const baselineResults: HealthCheckResult[] = [
  { script: "/check-a.sh", exit_code: 0, output: { status: "ok" }, duration_ms: 100 },
  { script: "/check-b.sh", exit_code: 0, output: "all good", duration_ms: 50 },
];

describe("saveBaseline + loadBaseline", () => {
  it("round-trips correctly", () => {
    saveBaseline(TEST_ENV, "my-script", "v1", baselineResults);

    const loaded = loadBaseline(TEST_ENV);
    expect(loaded).not.toBeNull();
    expect(loaded!.script_id).toBe("my-script");
    expect(loaded!.script_version).toBe("v1");
    expect(loaded!.health_results).toHaveLength(2);
    expect(loaded!.health_results[0].script).toBe("/check-a.sh");
  });
});

describe("compareDrift", () => {
  it("returns no drift when results match baseline", () => {
    const drift = compareDrift(TEST_ENV, baselineResults);
    expect(drift.has_drift).toBe(false);
    expect(drift.changes).toHaveLength(0);
  });

  it("detects new failures", () => {
    const currentResults: HealthCheckResult[] = [
      { script: "/check-a.sh", exit_code: 1, output: "FAIL", duration_ms: 100 },
      { script: "/check-b.sh", exit_code: 0, output: "all good", duration_ms: 50 },
    ];

    const drift = compareDrift(TEST_ENV, currentResults);
    expect(drift.has_drift).toBe(true);
    const failure = drift.changes.find(c => c.type === "new_failure");
    expect(failure).toBeDefined();
    expect(failure!.script).toBe("/check-a.sh");
  });

  it("detects resolved failures", () => {
    // Save a baseline with a failure
    const failedBaseline: HealthCheckResult[] = [
      { script: "/check-a.sh", exit_code: 1, output: "BAD", duration_ms: 100 },
    ];
    const resolvedEnv = `resolved-${Date.now()}`;
    saveBaseline(resolvedEnv, "s", "v1", failedBaseline);

    const current: HealthCheckResult[] = [
      { script: "/check-a.sh", exit_code: 0, output: "FIXED", duration_ms: 50 },
    ];

    const drift = compareDrift(resolvedEnv, current);
    expect(drift.has_drift).toBe(true);
    expect(drift.changes.find(c => c.type === "resolved_failure")).toBeDefined();

    // Cleanup
    try {
      const { unlinkSync, rmdirSync } = require("node:fs");
      unlinkSync(join(homedir(), ".bond", "deployments", "health", resolvedEnv, "baseline.json"));
      rmdirSync(join(homedir(), ".bond", "deployments", "health", resolvedEnv));
    } catch { /* ignore */ }
  });

  it("detects output changes", () => {
    const changedResults: HealthCheckResult[] = [
      { script: "/check-a.sh", exit_code: 0, output: { status: "changed" }, duration_ms: 100 },
      { script: "/check-b.sh", exit_code: 0, output: "all good", duration_ms: 50 },
    ];

    const drift = compareDrift(TEST_ENV, changedResults);
    expect(drift.has_drift).toBe(true);
    expect(drift.changes.find(c => c.type === "output_changed")).toBeDefined();
  });

  it("detects missing and added scripts", () => {
    const currentResults: HealthCheckResult[] = [
      { script: "/check-a.sh", exit_code: 0, output: { status: "ok" }, duration_ms: 100 },
      // check-b.sh missing
      { script: "/check-c.sh", exit_code: 0, output: "new", duration_ms: 30 },
    ];

    const drift = compareDrift(TEST_ENV, currentResults);
    expect(drift.has_drift).toBe(true);
    expect(drift.changes.find(c => c.type === "script_missing")).toBeDefined();
    expect(drift.changes.find(c => c.type === "script_added")).toBeDefined();
  });

  it("returns no drift when no baseline exists", () => {
    const drift = compareDrift("nonexistent-env-" + Date.now(), baselineResults);
    expect(drift.has_drift).toBe(false);
    expect(drift.baseline_timestamp).toBeNull();
  });
});
