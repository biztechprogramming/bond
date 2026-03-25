/**
 * Tests for the agent discovery orchestrator.
 *
 * Design Doc 071 §3, §5, §6 — Agent Discovery
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

// Mock dependencies
vi.mock("../deployments/discovery-scripts.js", () => ({
  executeSshScript: vi.fn(async () => ({ exit_code: 0, stdout: "", stderr: "" })),
}));

vi.mock("../deployments/events.js", () => ({
  emitDeploymentEvent: vi.fn(),
}));

vi.mock("../deployments/manifest.js", () => ({
  writeManifest: vi.fn(() => "/tmp/test-manifest.json"),
}));

import {
  evaluateCompleteness,
  convertToManifest,
  runAgentDiscovery,
} from "../deployments/discovery-agent.js";
import type { DiscoveryState } from "../deployments/discovery-agent.js";
import { emitDeploymentEvent } from "../deployments/events.js";
import { writeManifest } from "../deployments/manifest.js";

// ── evaluateCompleteness ────────────────────────────────────────────────────

describe("evaluateCompleteness", () => {
  function makeState(overrides: Partial<DiscoveryState["findings"]> = {}, confidence: Record<string, any> = {}): DiscoveryState {
    return {
      findings: overrides,
      confidence,
      probes_run: [],
      user_answers: {},
      completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
    };
  }

  it("reports all required fields missing for empty state", () => {
    const report = evaluateCompleteness(makeState());
    expect(report.ready).toBe(false);
    expect(report.missing_required).toContain("source");
    expect(report.missing_required).toContain("framework");
    expect(report.missing_required).toContain("build_strategy");
    expect(report.missing_required).toContain("target_server");
    expect(report.missing_required).toContain("app_port");
    expect(report.required_coverage).toBe(0);
  });

  it("reports ready when all required fields filled with high confidence", () => {
    const report = evaluateCompleteness(makeState(
      {
        source: "https://github.com/org/repo",
        framework: { framework: "Next.js", confidence: 0.95, evidence: ["pkg"] },
        build_strategy: { strategy: "docker", confidence: 0.9, evidence: ["Dockerfile"] },
        target_server: { host: "10.0.1.50", port: 22, user: "deploy" },
        app_port: 3000,
      },
      {
        source: { source: "user-provided", detail: "Given", score: 1.0 },
        framework: { source: "detected", detail: "pkg", score: 0.95 },
        build_strategy: { source: "detected", detail: "Dockerfile", score: 0.9 },
        target_server: { source: "user-provided", detail: "SSH", score: 1.0 },
        app_port: { source: "detected", detail: "Dockerfile", score: 0.9 },
      },
    ));
    expect(report.ready).toBe(true);
    expect(report.required_coverage).toBe(1);
    expect(report.missing_required).toHaveLength(0);
  });

  it("reports partial coverage correctly", () => {
    const report = evaluateCompleteness(makeState(
      {
        source: "https://github.com/org/repo",
        framework: { framework: "Express", confidence: 0.9, evidence: ["pkg"] },
        app_port: 3000,
      },
      {
        source: { source: "user-provided", detail: "Given", score: 1.0 },
        framework: { source: "detected", detail: "pkg", score: 0.9 },
        app_port: { source: "detected", detail: "code", score: 0.8 },
      },
    ));
    expect(report.ready).toBe(false);
    expect(report.required_coverage).toBe(3 / 5);
    expect(report.missing_required).toContain("build_strategy");
    expect(report.missing_required).toContain("target_server");
  });

  it("flags low confidence required fields", () => {
    const report = evaluateCompleteness(makeState(
      {
        source: "repo",
        framework: { framework: "Unknown", confidence: 0.3, evidence: [] },
        build_strategy: { strategy: "npm", confidence: 0.6, evidence: [] },
        target_server: { host: "test", port: 22, user: "deploy" },
        app_port: 3000,
      },
      {
        source: { source: "user-provided", detail: "Given", score: 1.0 },
        framework: { source: "inferred", detail: "guess", score: 0.3 },
        build_strategy: { source: "detected", detail: "fallback", score: 0.6 },
        target_server: { source: "user-provided", detail: "SSH", score: 1.0 },
        app_port: { source: "detected", detail: "code", score: 0.8 },
      },
    ));
    expect(report.ready).toBe(false);
    expect(report.low_confidence).toContain("framework");
  });

  it("includes recommended field coverage", () => {
    const report = evaluateCompleteness(makeState(
      {
        source: "repo",
        framework: { framework: "Express", confidence: 0.9, evidence: [] },
        build_strategy: { strategy: "docker", confidence: 0.9, evidence: [] },
        target_server: { host: "test", port: 22, user: "deploy" },
        app_port: 3000,
        env_vars: [{ name: "FOO", required: false, has_default: true, source: ".env" }],
        health_endpoint: { path: "/health", method: "GET", source: "code", confidence: 0.85 },
      },
      {
        source: { source: "user-provided", detail: "Given", score: 1.0 },
        framework: { source: "detected", detail: "pkg", score: 0.9 },
        build_strategy: { source: "detected", detail: "Dockerfile", score: 0.9 },
        target_server: { source: "user-provided", detail: "SSH", score: 1.0 },
        app_port: { source: "detected", detail: "code", score: 0.8 },
        env_vars: { source: "detected", detail: "found", score: 0.8 },
        health_endpoint: { source: "detected", detail: "found", score: 0.85 },
      },
    ));
    expect(report.recommended_coverage).toBeCloseTo(2 / 3);
  });
});

// ── convertToManifest ───────────────────────────────────────────────────────

describe("convertToManifest", () => {
  it("produces valid DeploymentManifest", () => {
    const state: DiscoveryState = {
      findings: {
        source: "my-app",
        framework: { framework: "Next.js", version: "14.0.0", confidence: 0.95, evidence: ["pkg"], runtime: "node" },
        build_strategy: { strategy: "docker", confidence: 0.9, evidence: ["Dockerfile"] },
        target_server: { host: "10.0.1.50", port: 22, user: "deploy", os: "Ubuntu 24.04" },
        app_port: 3000,
        services: [{ name: "PostgreSQL", type: "database", source: "docker-compose", confidence: 0.9 }],
        env_vars: [{ name: "DATABASE_URL", required: true, has_default: false, source: ".env.example" }],
        health_endpoint: { path: "/health", method: "GET", source: "code", confidence: 0.85 },
      },
      confidence: {},
      probes_run: [],
      user_answers: {},
      completeness: { ready: true, required_coverage: 1, recommended_coverage: 1, missing_required: [], low_confidence: [] },
    };

    const manifest = convertToManifest(state, "prod");

    expect(manifest.manifest_version).toBe("1.0");
    expect(manifest.application).toBe("my-app");
    expect(manifest.discovered_by).toBe("agent-prod");
    expect(manifest.servers).toHaveLength(1);
    expect(manifest.servers[0].host).toBe("10.0.1.50");
    expect(manifest.servers[0].os).toBe("Ubuntu 24.04");
    expect(manifest.servers[0].application?.framework).toBe("Next.js");
    expect(manifest.servers[0].application?.port).toBe(3000);
    expect(manifest.servers[0].application?.health_endpoint).toBe("/health");
    expect(manifest.servers[0].services?.postgresql).toBeDefined();
    expect(manifest.security_observations).toEqual([]);
    expect(manifest.topology).toEqual({ nodes: [], edges: [] });
  });

  it("handles minimal state", () => {
    const state: DiscoveryState = {
      findings: {},
      confidence: {},
      probes_run: [],
      user_answers: {},
      completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
    };

    const manifest = convertToManifest(state, "dev");
    expect(manifest.application).toBe("unknown");
    expect(manifest.servers[0].host).toBe("unknown");
  });
});

// ── Agent Loop ──────────────────────────────────────────────────────────────

describe("runAgentDiscovery", () => {
  let tmpDir: string;

  beforeEach(() => {
    vi.mocked(emitDeploymentEvent).mockReset();
    vi.mocked(writeManifest).mockReset();
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-test-"));
  });

  it("discovers framework and build strategy from repo", async () => {
    fs.writeFileSync(path.join(tmpDir, "package.json"), JSON.stringify({
      dependencies: { next: "14.0.0", react: "18.0.0" },
    }));
    fs.writeFileSync(path.join(tmpDir, "Dockerfile"), "FROM node:18\nEXPOSE 3000\n");

    const state = await runAgentDiscovery({
      env: "dev",
      repoPath: tmpDir,
      source: "https://github.com/org/app",
      serverHost: "10.0.1.50",
    });

    expect(state.findings.framework?.framework).toBe("Next.js");
    expect(state.findings.build_strategy?.strategy).toBe("docker");
    expect(state.findings.app_port).toBe(3000);
    expect(state.findings.source).toBe("https://github.com/org/app");
  });

  it("emits start and complete events", async () => {
    await runAgentDiscovery({ env: "dev", repoPath: tmpDir });

    expect(vi.mocked(emitDeploymentEvent)).toHaveBeenCalledWith(
      "discovery_agent_started",
      expect.objectContaining({ environment: "dev" }),
    );
    expect(vi.mocked(emitDeploymentEvent)).toHaveBeenCalledWith(
      "discovery_agent_completed",
      expect.objectContaining({ environment: "dev" }),
    );
  });

  it("generates user questions for missing required fields", async () => {
    // Empty repo — agent can't detect anything, should ask user
    const state = await runAgentDiscovery({ env: "dev", repoPath: tmpDir });

    expect(state.completeness.ready).toBe(false);
    expect(state.completeness.missing_required.length).toBeGreaterThan(0);
  });

  it("writes manifest when discovery finds data", async () => {
    fs.writeFileSync(path.join(tmpDir, "package.json"), JSON.stringify({
      dependencies: { express: "4.18.0" },
    }));

    await runAgentDiscovery({ env: "prod", repoPath: tmpDir, source: "my-app" });

    expect(vi.mocked(writeManifest)).toHaveBeenCalled();
  });

  it("respects max tool calls limit", async () => {
    // Even with a complex repo, should not exceed 20 tool calls
    fs.writeFileSync(path.join(tmpDir, "package.json"), JSON.stringify({ dependencies: { next: "14.0.0" } }));
    fs.writeFileSync(path.join(tmpDir, "Dockerfile"), "FROM node:18\nEXPOSE 3000\n");
    fs.writeFileSync(path.join(tmpDir, "docker-compose.yml"), "services:\n  db:\n    image: postgres\n");
    fs.writeFileSync(path.join(tmpDir, ".env.example"), "DATABASE_URL=\nSECRET=\n");

    const state = await runAgentDiscovery({ env: "dev", repoPath: tmpDir, serverHost: "test" });

    expect(state.probes_run.length).toBeLessThanOrEqual(20);
  });
});
