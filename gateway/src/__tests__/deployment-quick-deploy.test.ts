/**
 * Quick Deploy tests — script generation for different build strategies.
 */

import { describe, it, expect } from "vitest";

// We can't import handleQuickDeploy without mocking SpacetimeDB,
// but we can test the script generation by importing the module and
// calling the internal generate functions indirectly.
// Since generateDeployScript/generateRollbackScript are not exported,
// we test via handleQuickDeploy with mocks.

import { vi } from "vitest";

vi.mock("../deployments/stdb.js", () => ({
  initiatePromotion: vi.fn(async () => "promo-1"),
}));

vi.mock("../deployments/events.js", () => ({
  emitScriptPromoted: vi.fn(),
}));

vi.mock("../deployments/trigger-handler.js", () => ({
  createTrigger: vi.fn(async () => "trigger-1"),
}));

import { handleQuickDeploy, type QuickDeployRequest } from "../deployments/quick-deploy.js";
import { getManifest, getScriptVersionDir } from "../deployments/scripts.js";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

let tempDir: string;

const config: any = {
  spacetimedbUrl: "http://localhost:3000",
  spacetimedbModuleName: "bond",
  spacetimedbToken: "test-token",
};

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "quick-deploy-test-"));
}

describe("quick deploy — dockerfile strategy", () => {
  it("generates correct deploy script", async () => {
    const dir = makeTempDir();
    const result = await handleQuickDeploy({
      repo_url: "https://github.com/org/myapp.git",
      branch: "main",
      build_strategy: "dockerfile",
      environment: "dev",
      port: 8080,
    }, dir, config, "user-1");

    expect(result.script_id).toBe("quick-deploy-myapp");
    expect(result.version).toBe("v1");
    expect(result.promoted).toBe(true);

    const versionDir = getScriptVersionDir(dir, result.script_id, "v1");
    const deployScript = readFileSync(join(versionDir, "deploy.sh"), "utf8");
    expect(deployScript).toContain("docker build");
    expect(deployScript).toContain("docker run");
    expect(deployScript).toContain("8080");
    expect(deployScript).toContain("myapp");

    rmSync(dir, { recursive: true, force: true });
  });
});

describe("quick deploy — docker-compose strategy", () => {
  it("generates correct deploy script", async () => {
    const dir = makeTempDir();
    const result = await handleQuickDeploy({
      repo_url: "https://github.com/org/compose-app",
      branch: "main",
      build_strategy: "docker-compose",
      environment: "staging",
    }, dir, config, "user-1");

    const versionDir = getScriptVersionDir(dir, result.script_id, "v1");
    const deployScript = readFileSync(join(versionDir, "deploy.sh"), "utf8");
    expect(deployScript).toContain("docker compose");
    expect(deployScript).toContain("docker compose up -d --build");

    rmSync(dir, { recursive: true, force: true });
  });
});

describe("quick deploy — script strategy", () => {
  it("generates correct deploy script", async () => {
    const dir = makeTempDir();
    const result = await handleQuickDeploy({
      repo_url: "https://github.com/org/node-app",
      branch: "main",
      build_strategy: "script",
      build_cmd: "yarn build",
      start_cmd: "yarn start",
      environment: "dev",
      port: 4000,
    }, dir, config, "user-1");

    const versionDir = getScriptVersionDir(dir, result.script_id, "v1");
    const deployScript = readFileSync(join(versionDir, "deploy.sh"), "utf8");
    expect(deployScript).toContain("yarn build");
    expect(deployScript).toContain("yarn start");

    rmSync(dir, { recursive: true, force: true });
  });
});

describe("quick deploy — rollback scripts", () => {
  it("generates rollback scripts", async () => {
    const dir = makeTempDir();
    await handleQuickDeploy({
      repo_url: "https://github.com/org/rollback-app",
      branch: "main",
      build_strategy: "dockerfile",
      environment: "dev",
    }, dir, config, "user-1");

    const versionDir = getScriptVersionDir(dir, "quick-deploy-rollback-app", "v1");
    const rollbackScript = readFileSync(join(versionDir, "rollback.sh"), "utf8");
    expect(rollbackScript).toContain("Rollback");
    expect(rollbackScript).toContain("docker");

    rmSync(dir, { recursive: true, force: true });
  });
});

describe("quick deploy — env vars", () => {
  it("handles secret vs non-secret env vars", async () => {
    const dir = makeTempDir();
    await handleQuickDeploy({
      repo_url: "https://github.com/org/env-app",
      branch: "main",
      build_strategy: "dockerfile",
      environment: "dev",
      env_vars: {
        PUBLIC_URL: { value: "https://example.com", secret: false },
        DB_PASSWORD: { value: "s3cret", secret: true },
      },
    }, dir, config, "user-1");

    const versionDir = getScriptVersionDir(dir, "quick-deploy-env-app", "v1");
    const deployScript = readFileSync(join(versionDir, "deploy.sh"), "utf8");

    // Non-secret should be in the script
    expect(deployScript).toContain("PUBLIC_URL");
    // Secret should NOT be in the script (written to secrets file)
    expect(deployScript).not.toContain("s3cret");

    // Check secrets file was written
    const { existsSync } = require("node:fs");
    expect(existsSync(join(dir, "secrets", "dev.yaml"))).toBe(true);

    rmSync(dir, { recursive: true, force: true });
  });
});

describe("quick deploy — auto strategy", () => {
  it("auto strategy defaults to dockerfile", async () => {
    const dir = makeTempDir();
    await handleQuickDeploy({
      repo_url: "https://github.com/org/auto-app",
      branch: "main",
      build_strategy: "auto",
      environment: "dev",
    }, dir, config, "user-1");

    const versionDir = getScriptVersionDir(dir, "quick-deploy-auto-app", "v1");
    const deployScript = readFileSync(join(versionDir, "deploy.sh"), "utf8");
    expect(deployScript).toContain("docker build");

    rmSync(dir, { recursive: true, force: true });
  });
});
