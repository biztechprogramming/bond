/**
 * Deployment Trigger Handler tests.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

const triggerStore: any[] = [];

vi.mock("../spacetimedb/client.js", () => ({
  sqlQuery: vi.fn(async (_url: string, _mod: string, sql: string) => {
    // Parse repo_url and branch from SQL
    const repoMatch = sql.match(/repo_url = '([^']+)'/);
    const branchMatch = sql.match(/branch = '([^']+)'/);
    if (repoMatch && branchMatch) {
      return triggerStore.filter(
        t => t.repo_url === repoMatch[1] && t.branch === branchMatch[1] && t.enabled,
      );
    }
    return triggerStore;
  }),
  callReducer: vi.fn(async (_url: string, _mod: string, reducer: string, args: any[]) => {
    if (reducer === "create_deployment_trigger") {
      triggerStore.push(args[0]);
    }
  }),
}));

vi.mock("../deployments/events.js", () => ({
  emitScriptPromoted: vi.fn(),
}));

import { handleWebhookPush, createTrigger } from "../deployments/trigger-handler.js";

const cfg: any = {
  spacetimedbUrl: "http://localhost:3000",
  spacetimedbModuleName: "bond",
  spacetimedbToken: "test-token",
};

beforeEach(() => {
  triggerStore.length = 0;
});

describe("handleWebhookPush", () => {
  it("matches repo + branch", async () => {
    // Create a trigger
    await createTrigger(cfg, {
      script_id: "deploy-app",
      repo_url: "https://github.com/org/app.git",
      branch: "main",
      environment: "dev",
      enabled: true,
    });

    const result = await handleWebhookPush(cfg, {
      repository: {
        clone_url: "https://github.com/org/app.git",
        full_name: "org/app",
      },
      ref: "refs/heads/main",
      after: "abc123",
    });

    expect(result.matched.length).toBe(1);
    expect(result.triggered.length).toBe(1);
    expect(result.matched[0].script_id).toBe("deploy-app");
  });

  it("handles .git suffix fallback", async () => {
    // Trigger stored without .git
    await createTrigger(cfg, {
      script_id: "deploy-app-2",
      repo_url: "https://github.com/org/app2",
      branch: "main",
      environment: "dev",
      enabled: true,
    });

    const result = await handleWebhookPush(cfg, {
      repository: {
        clone_url: "https://github.com/org/app2.git",
        full_name: "org/app2",
      },
      ref: "refs/heads/main",
      after: "def456",
    });

    // First try clone_url (with .git) — no match, then try without .git — match
    expect(result.matched.length).toBe(1);
    expect(result.matched[0].script_id).toBe("deploy-app-2");
  });

  it("returns empty when no match", async () => {
    const result = await handleWebhookPush(cfg, {
      repository: {
        clone_url: "https://github.com/org/unrelated.git",
        full_name: "org/unrelated",
      },
      ref: "refs/heads/main",
      after: "xyz789",
    });

    expect(result.matched).toHaveLength(0);
    expect(result.triggered).toHaveLength(0);
  });

  it("ignores disabled triggers", async () => {
    triggerStore.push({
      id: "disabled-1",
      script_id: "deploy-disabled",
      repo_url: "https://github.com/org/disabled.git",
      branch: "main",
      environment: "dev",
      enabled: false,
    });

    const result = await handleWebhookPush(cfg, {
      repository: {
        clone_url: "https://github.com/org/disabled.git",
        full_name: "org/disabled",
      },
      ref: "refs/heads/main",
      after: "abc",
    });

    expect(result.matched).toHaveLength(0);
  });
});
