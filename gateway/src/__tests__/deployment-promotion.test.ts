/**
 * Deployment Promotion workflow tests.
 *
 * Mocks SpacetimeDB calls to test promotion logic in isolation.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import express from "express";
import type { Server } from "node:http";

import { registerScript } from "../deployments/scripts.js";

// Mock SpacetimeDB calls used by promotion.ts (via stdb.ts)
vi.mock("../deployments/stdb.js", () => {
  const environments = [
    { name: "dev", order: 1, is_active: true, required_approvals: 1 },
    { name: "staging", order: 2, is_active: true, required_approvals: 2 },
    { name: "prod", order: 3, is_active: true, required_approvals: 1 },
  ];

  const promotions: any[] = [];
  const approvals: any[] = [];

  return {
    getEnvironments: vi.fn(async () => environments),
    getEnvironment: vi.fn(async (_cfg: any, name: string) =>
      environments.find(e => e.name === name) ?? null,
    ),
    getApprovers: vi.fn(async (_cfg: any, _env: string) => [
      { user_id: "user-a" },
      { user_id: "user-b" },
      { user_id: "user-c" },
    ]),
    getPromotion: vi.fn(async (_cfg: any, scriptId: string, version: string, env: string) =>
      promotions.find(p => p.script_id === scriptId && p.script_version === version && p.environment_name === env) ?? null,
    ),
    getPromotionsForScript: vi.fn(async (_cfg: any, scriptId: string, version: string) =>
      promotions.filter(p => p.script_id === scriptId && p.script_version === version),
    ),
    getAllPromotions: vi.fn(async () => promotions),
    initiatePromotion: vi.fn(async (_cfg: any, scriptId: string, version: string, sha256: string, env: string, status: string, userId: string) => {
      const id = `promo-${promotions.length + 1}`;
      promotions.push({
        id,
        script_id: scriptId,
        script_version: version,
        script_sha256: sha256,
        environment_name: env,
        status,
        initiated_by: userId,
        initiated_at: Date.now(),
      });
      return id;
    }),
    recordApproval: vi.fn(async (_cfg: any, promotionId: string, _scriptId: string, _version: string, _env: string, userId: string) => {
      approvals.push({ promotion_id: promotionId, user_id: userId, approved_at: Date.now() });
    }),
    updatePromotionStatus: vi.fn(async (_cfg: any, promotionId: string, status: string, extra?: any) => {
      const p = promotions.find(p => p.id === promotionId);
      if (p) {
        p.status = status;
        if (extra?.promoted_at) p.promoted_at = extra.promoted_at;
      }
    }),
    getApprovalsForPromotion: vi.fn(async (_cfg: any, promotionId: string) =>
      approvals.filter(a => a.promotion_id === promotionId),
    ),
    __resetState: () => {
      promotions.length = 0;
      approvals.length = 0;
    },
  };
});

vi.mock("../deployments/events.js", () => ({
  emitScriptPromoted: vi.fn(),
}));

vi.mock("../deployments/session-tokens.js", () => ({
  extractUserIdentity: vi.fn((auth: string | undefined) => {
    if (!auth) return null;
    const token = auth.replace("Bearer ", "");
    if (token.startsWith("user:")) {
      const [, userId, role] = token.split(":");
      return { user_id: userId, role: role || "admin" };
    }
    return null;
  }),
}));

import { createPromotionRouter } from "../deployments/promotion.js";
import { __resetState } from "../deployments/stdb.js";

let tempDir: string;
let server: Server;
let port: number;
const config: any = {};

beforeEach(async () => {
  tempDir = mkdtempSync(join(tmpdir(), "promo-test-"));
  (__resetState as any)();

  // Register a test script
  registerScript(tempDir, {
    script_id: "test-app",
    version: "v1",
    name: "Test App",
    registered_by: "user-a",
    files: { "deploy.sh": Buffer.from("#!/bin/bash\necho ok") },
  });

  // Monkey-patch DEPLOYMENTS_DIR inside promotion.ts is hardcoded to homedir
  // We can't easily override it, so we register in the real path too
  const realDir = join(require("os").homedir(), ".bond", "deployments");
  try {
    registerScript(realDir, {
      script_id: "test-app",
      version: "v1",
      name: "Test App",
      registered_by: "user-a",
      files: { "deploy.sh": Buffer.from("#!/bin/bash\necho ok") },
    });
  } catch {
    // Already exists from a previous run — fine
  }

  const app = express();
  app.use(express.json());
  app.use("/api/v1/deployments", createPromotionRouter(config));

  await new Promise<void>((resolve) => {
    server = app.listen(0, () => resolve());
  });
  port = (server.address() as any).port;
});

afterEach(() => {
  server?.close();
  rmSync(tempDir, { recursive: true, force: true });
});

function post(path: string, body: any, userId = "user-a") {
  return fetch(`http://localhost:${port}/api/v1/deployments${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer user:${userId}:admin`,
    },
    body: JSON.stringify(body),
  });
}

function get(path: string) {
  return fetch(`http://localhost:${port}/api/v1/deployments${path}`);
}

describe("promotion workflow", () => {
  it("promotes to first env (dev) successfully", async () => {
    const resp = await post("/promote", {
      script_id: "test-app",
      version: "v1",
      target_environments: ["dev"],
    });
    const data = await resp.json();
    expect(data.status).toBe("promoted");
  });

  it("rejects promotion to later env without previous env success", async () => {
    const resp = await post("/promote", {
      script_id: "test-app",
      version: "v1",
      target_environments: ["staging"],
    });
    const data = await resp.json();
    expect(data.status).toBe("skipped");
    expect(data.message).toContain("not completed successfully");
  });

  it("force promote bypasses prerequisite check", async () => {
    const resp = await post("/promote", {
      script_id: "test-app",
      version: "v1",
      target_environments: ["staging"],
      force: true,
    });
    const data = await resp.json();
    // staging requires 2 approvals, so it should be awaiting_approvals
    expect(data.status).toBe("awaiting_approvals");
  });

  it("rejects agent tokens (no auth)", async () => {
    const resp = await fetch(`http://localhost:${port}/api/v1/deployments/promote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ script_id: "test-app", version: "v1", target_environments: ["dev"] }),
    });
    expect(resp.status).toBe(403);
  });

  it("pipeline view aggregates promotions", async () => {
    // Promote to dev first
    await post("/promote", { script_id: "test-app", version: "v1", target_environments: ["dev"] });

    const resp = await get("/pipeline");
    const data = await resp.json();
    expect(data.environments).toBeDefined();
    expect(data.scripts).toBeDefined();
    expect(Array.isArray(data.scripts)).toBe(true);
  });
});

describe("multi-approval workflow", () => {
  it("records approval and checks threshold", async () => {
    // Force promote to staging (requires 2 approvals)
    await post("/promote", {
      script_id: "test-app",
      version: "v1",
      target_environments: ["staging"],
      force: true,
    }, "user-a");

    // Second approval from different user
    const resp = await post("/promote/approve", {
      script_id: "test-app",
      version: "v1",
      environment: "staging",
    }, "user-b");

    const data = await resp.json();
    expect(data.status).toBe("promoted");
    expect(data.approvals.received).toBeGreaterThanOrEqual(2);
  });

  it("rejects duplicate approval", async () => {
    await post("/promote", {
      script_id: "test-app",
      version: "v1",
      target_environments: ["staging"],
      force: true,
    }, "user-a");

    // user-a already approved via promote, try again via approve
    const resp = await post("/promote/approve", {
      script_id: "test-app",
      version: "v1",
      environment: "staging",
    }, "user-a");

    const data = await resp.json();
    expect(data.error).toContain("already approved");
  });
});
