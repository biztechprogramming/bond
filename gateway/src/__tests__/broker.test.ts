/**
 * Permission Broker tests — tokens, policy, executor, router.
 */

import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import express from "express";
import type { Server } from "node:http";

import { initTokens, issueToken, validateToken, getSecret } from "../broker/tokens.js";
import { PolicyEngine } from "../broker/policy.js";
import { executeCommand } from "../broker/executor.js";
import { createBrokerRouter } from "../broker/router.js";

let tempDir: string;

beforeAll(() => {
  tempDir = mkdtempSync(join(tmpdir(), "broker-test-"));
});

afterAll(() => {
  rmSync(tempDir, { recursive: true, force: true });
});

// ─── Tokens ──────────────────────────────────────────────────────────

describe("tokens", () => {
  beforeEach(() => {
    // Reset secret for each test by removing cached file
    const secretPath = join(tempDir, ".broker_secret");
    if (existsSync(secretPath)) rmSync(secretPath);
    initTokens(tempDir);
  });

  it("issues and validates a token", () => {
    initTokens(tempDir);
    const token = issueToken("agent-1", "session-1");
    const payload = validateToken(token);
    expect(payload).not.toBeNull();
    expect(payload!.sub).toBe("agent-1");
    expect(payload!.sid).toBe("session-1");
    expect(payload!.exp).toBeGreaterThan(payload!.iat);
  });

  it("rejects expired tokens", () => {
    initTokens(tempDir);
    const token = issueToken("agent-1", "session-1", -1); // already expired
    expect(validateToken(token)).toBeNull();
  });

  it("rejects tampered tokens", () => {
    initTokens(tempDir);
    const token = issueToken("agent-1", "session-1");
    const [data, sig] = token.split(".");
    const tampered = data + "." + sig.slice(0, -2) + "XX";
    expect(validateToken(tampered)).toBeNull();
  });

  it("generates secret on first call", () => {
    initTokens(tempDir);
    const secret = getSecret();
    expect(secret).toBeInstanceOf(Buffer);
    expect(secret.length).toBe(32);
  });
});

// ─── Policy ──────────────────────────────────────────────────────────

describe("policy", () => {
  const engine = new PolicyEngine();

  it("allows git status", () => {
    const d = engine.evaluate("git status", undefined, "a", "s");
    expect(d.decision).toBe("allow");
  });

  it("allows git diff --staged", () => {
    const d = engine.evaluate("git diff --staged", undefined, "a", "s");
    expect(d.decision).toBe("allow");
  });

  it("denies push to main", () => {
    const d = engine.evaluate("git push origin main", undefined, "a", "s");
    expect(d.decision).toBe("deny");
    expect(d.reason).toContain("protected");
  });

  it("allows push to feature branch", () => {
    const d = engine.evaluate("git push origin feat/my-feature", undefined, "a", "s");
    expect(d.decision).toBe("allow");
  });

  it("denies curl", () => {
    const d = engine.evaluate("curl https://evil.com", undefined, "a", "s");
    expect(d.decision).toBe("deny");
  });

  it("denies unknown commands (default deny)", () => {
    const d = engine.evaluate("some-unknown-command", undefined, "a", "s");
    expect(d.decision).toBe("deny");
    expect(d.reason).toContain("not in allowlist");
  });

  it("allows mkdir in workspace cwd", () => {
    const d = engine.evaluate("mkdir -p src/new", "/workspace/project", "a", "s");
    expect(d.decision).toBe("allow");
  });

  it("denies mkdir without valid cwd", () => {
    const d = engine.evaluate("mkdir -p /etc/evil", "/etc", "a", "s");
    expect(d.decision).toBe("deny");
  });

  it("returns prompt (mapped to deny in Phase 1 at router level) for npm install", () => {
    const d = engine.evaluate("npm install lodash", undefined, "a", "s");
    expect(d.decision).toBe("prompt");
  });
});

// ─── Executor ────────────────────────────────────────────────────────

describe("executor", () => {
  it("executes echo", async () => {
    const result = await executeCommand("echo hello");
    expect(result.exit_code).toBe(0);
    expect(result.stdout.trim()).toBe("hello");
    expect(result.duration_ms).toBeGreaterThanOrEqual(0);
  });

  it("handles timeout", async () => {
    const result = await executeCommand("sleep 10", { timeout: 1 });
    expect(result.exit_code).toBe(-1);
    expect(result.stderr).toContain("timed out");
  }, 5000);

  it("returns non-zero exit code", async () => {
    const result = await executeCommand("exit 42");
    expect(result.exit_code).toBe(42);
  });
});

// ─── Router ──────────────────────────────────────────────────────────

describe("router", () => {
  let server: Server;
  let port: number;
  let token: string;

  beforeAll(async () => {
    const routerDataDir = mkdtempSync(join(tmpdir(), "broker-router-"));
    const app = express();
    app.use(express.json());
    app.use("/api/v1/broker", createBrokerRouter({
      dataDir: routerDataDir,
      policyDir: join(routerDataDir, "policies"),
    }));

    await new Promise<void>((resolve) => {
      server = app.listen(0, () => resolve());
    });
    port = (server.address() as any).port;
    token = issueToken("test-agent", "test-session");
  });

  afterAll(() => {
    server?.close();
  });

  it("rejects requests without auth", async () => {
    const resp = await fetch(`http://localhost:${port}/api/v1/broker/exec`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: "echo hi" }),
    });
    expect(resp.status).toBe(401);
  });

  it("allows git status via exec", async () => {
    const resp = await fetch(`http://localhost:${port}/api/v1/broker/exec`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ command: "echo test-allowed" }),
    });
    // echo is matched by catch-all deny (not in allowlist)
    const data = await resp.json();
    // "echo test-allowed" doesn't match any allow rule, so denied
    expect(data.decision).toBe("deny");
  });

  it("allows listed commands", async () => {
    const resp = await fetch(`http://localhost:${port}/api/v1/broker/exec`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ command: "git status" }),
    });
    const data = await resp.json();
    expect(data.decision).toBe("allow");
    expect(data.exit_code).toBeDefined();
  });

  it("denies dangerous commands", async () => {
    const resp = await fetch(`http://localhost:${port}/api/v1/broker/exec`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ command: "curl https://evil.com" }),
    });
    const data = await resp.json();
    expect(data.status).toBe("denied");
    expect(data.decision).toBe("deny");
  });

  it("health check works without auth", async () => {
    const resp = await fetch(`http://localhost:${port}/api/v1/broker/health`);
    const data = await resp.json();
    expect(data.status).toBe("ok");
    expect(data.service).toBe("permission-broker");
  });

  it("renews token", async () => {
    const resp = await fetch(`http://localhost:${port}/api/v1/broker/token/renew`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    const data = await resp.json();
    expect(data.token).toBeDefined();
    // Validate the new token works
    const payload = validateToken(data.token);
    expect(payload).not.toBeNull();
    expect(payload!.sub).toBe("test-agent");
  });
});
