/**
 * Deployment Locks & Queue tests.
 */

import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  acquireLock,
  releaseLock,
  getLock,
  getLockPath,
  isWithinDeploymentWindow,
} from "../deployments/locks.js";

import { enqueue, dequeue, peek, getQueue } from "../deployments/queue.js";

let tempDir: string;

beforeAll(() => {
  tempDir = mkdtempSync(join(tmpdir(), "deploy-locks-test-"));
});

afterAll(() => {
  rmSync(tempDir, { recursive: true, force: true });
});

// ─── Locks ──────────────────────────────────────────────────────────

describe("acquireLock", () => {
  it("succeeds when unlocked", () => {
    expect(acquireLock(tempDir, "test-env-1", "agent-1", "script-1", 60)).toBe(true);
    releaseLock(tempDir, "test-env-1");
  });

  it("fails when locked", () => {
    acquireLock(tempDir, "test-env-2", "agent-1", "script-1", 60);
    expect(acquireLock(tempDir, "test-env-2", "agent-2", "script-2", 60)).toBe(false);
    releaseLock(tempDir, "test-env-2");
  });

  it("auto-releases stale lock", () => {
    // Write an already-expired lock
    const lockPath = getLockPath(tempDir, "stale-env");
    const { mkdirSync } = require("node:fs");
    mkdirSync(join(tempDir, "locks"), { recursive: true });
    writeFileSync(lockPath, JSON.stringify({
      agent: "old-agent",
      script: "old-script",
      since: new Date(Date.now() - 120000).toISOString(),
      expires_at: new Date(Date.now() - 60000).toISOString(),
    }));

    // Should succeed because the existing lock is stale
    expect(acquireLock(tempDir, "stale-env", "new-agent", "new-script", 60)).toBe(true);
    releaseLock(tempDir, "stale-env");
  });
});

describe("releaseLock", () => {
  it("removes lock file", () => {
    acquireLock(tempDir, "release-env", "agent-1", "script-1", 60);
    expect(getLock(tempDir, "release-env")).not.toBeNull();

    releaseLock(tempDir, "release-env");
    expect(getLock(tempDir, "release-env")).toBeNull();
  });
});

describe("isWithinDeploymentWindow", () => {
  it("returns true when no window configured", () => {
    expect(isWithinDeploymentWindow("[]", "", "", "")).toBe(true);
  });

  it("returns true when days list is empty", () => {
    expect(isWithinDeploymentWindow("[]", "09:00", "17:00", "UTC")).toBe(true);
  });

  it("checks day and time", () => {
    // Use current day to test — get current UTC day abbreviation
    const now = new Date();
    const formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: "UTC",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const parts = formatter.formatToParts(now);
    const weekday = parts.find(p => p.type === "weekday")?.value?.toLowerCase().slice(0, 3) ?? "";

    // Window that includes current time
    const result = isWithinDeploymentWindow(
      JSON.stringify([weekday]),
      "00:00",
      "23:59",
      "UTC",
    );
    expect(result).toBe(true);

    // Window on a day that definitely doesn't match current
    const wrongDay = weekday === "mon" ? "tue" : "mon";
    const resultWrong = isWithinDeploymentWindow(
      JSON.stringify([wrongDay]),
      "00:00",
      "23:59",
      "UTC",
    );
    expect(resultWrong).toBe(false);
  });
});

// ─── Queue ──────────────────────────────────────────────────────────

describe("queue", () => {
  const env = "queue-test-env";

  beforeEach(() => {
    // Drain queue
    while (dequeue(env)) {}
  });

  it("enqueue and dequeue in FIFO order", () => {
    enqueue(env, { script_id: "a", version: "v1", agent_sub: "agent-1", queued_at: "2025-01-01T00:00:00Z", priority: 0 });
    enqueue(env, { script_id: "b", version: "v1", agent_sub: "agent-1", queued_at: "2025-01-01T00:00:01Z", priority: 0 });

    const first = dequeue(env);
    expect(first?.script_id).toBe("a");

    const second = dequeue(env);
    expect(second?.script_id).toBe("b");

    expect(dequeue(env)).toBeNull();
  });

  it("peek returns first without removing", () => {
    enqueue(env, { script_id: "c", version: "v1", agent_sub: "agent-1", queued_at: "2025-01-01T00:00:00Z", priority: 0 });

    expect(peek(env)?.script_id).toBe("c");
    expect(peek(env)?.script_id).toBe("c"); // still there
    expect(getQueue(env).length).toBe(1);
  });

  it("respects priority ordering", () => {
    enqueue(env, { script_id: "low", version: "v1", agent_sub: "agent-1", queued_at: "2025-01-01T00:00:00Z", priority: 0 });
    enqueue(env, { script_id: "high", version: "v1", agent_sub: "agent-1", queued_at: "2025-01-01T00:00:01Z", priority: 10 });

    const first = dequeue(env);
    expect(first?.script_id).toBe("high");
  });
});
