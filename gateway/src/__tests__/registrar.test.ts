import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { EventEmitter } from "node:events";

// Mock child_process before importing the module under test (vi.mock is hoisted)
vi.mock("node:child_process", () => ({
  spawn: vi.fn(),
}));

import { spawn } from "node:child_process";
import { WebhookRegistrar } from "../webhooks/registrar.js";

// ---------- helpers ----------

interface MockProcOptions {
  stdout?: string;
  stderr?: string;
  exitCode?: number;
  /** If set, emits an 'error' event instead of closing normally */
  spawnError?: Error;
}

function mockProcess(opts: MockProcOptions = {}) {
  const proc = new EventEmitter() as any;
  proc.stdout = new EventEmitter();
  proc.stderr = new EventEmitter();
  proc.stdin = { write: vi.fn(), end: vi.fn() };

  setImmediate(() => {
    if (opts.spawnError) {
      proc.emit("error", opts.spawnError);
    } else {
      if (opts.stdout) proc.stdout.emit("data", Buffer.from(opts.stdout));
      if (opts.stderr) proc.stderr.emit("data", Buffer.from(opts.stderr));
      proc.emit("close", opts.exitCode ?? 0);
    }
  });

  return proc;
}

function enoentError(): Error {
  return Object.assign(new Error("spawn gh ENOENT"), { code: "ENOENT" });
}

// ---------- tests ----------

describe("WebhookRegistrar", () => {
  let spawnMock: ReturnType<typeof vi.fn>;
  let warnSpy: ReturnType<typeof vi.spyOn>;
  let logSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    spawnMock = vi.mocked(spawn);
    spawnMock.mockReset();
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
    logSpy.mockRestore();
  });

  // ── discoverRepos ─────────────────────────────────────────────────────────

  describe("discoverRepos", () => {
    it("returns repos discovered via gh repo list", async () => {
      spawnMock.mockReturnValueOnce(
        mockProcess({ stdout: "owner/repo1\nowner/repo2\n" })
      );

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      const repos = await registrar.discoverRepos();

      expect(repos).toEqual(["owner/repo1", "owner/repo2"]);
      expect(spawnMock).toHaveBeenCalledWith(
        "gh",
        expect.arrayContaining(["repo", "list", "--json", "nameWithOwner"]),
        expect.any(Object)
      );
    });

    it("returns empty array and warns when gh is not found (ENOENT)", async () => {
      spawnMock.mockReturnValueOnce(mockProcess({ spawnError: enoentError() }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      const repos = await registrar.discoverRepos();

      expect(repos).toEqual([]);
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("gh CLI not found")
      );
    });

    it("returns empty array and warns when gh exits non-zero", async () => {
      spawnMock.mockReturnValueOnce(
        mockProcess({ stderr: "authentication required", exitCode: 1 })
      );

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      const repos = await registrar.discoverRepos();

      expect(repos).toEqual([]);
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("gh repo list failed"),
        expect.any(String)
      );
    });

    it("filters out empty lines from stdout", async () => {
      spawnMock.mockReturnValueOnce(
        mockProcess({ stdout: "owner/repo1\n\nowner/repo2\n\n" })
      );

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      const repos = await registrar.discoverRepos();

      expect(repos).toEqual(["owner/repo1", "owner/repo2"]);
    });
  });

  // ── ensureWebhooks — guard conditions ────────────────────────────────────

  describe("ensureWebhooks — no externalUrl", () => {
    it("logs warning and skips all gh calls when externalUrl is not set", async () => {
      const registrar = new WebhookRegistrar({});
      await registrar.ensureWebhooks();

      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("GATEWAY_EXTERNAL_URL not set")
      );
      expect(spawnMock).not.toHaveBeenCalled();
    });
  });

  // ── ensureWebhooks — repo sources ─────────────────────────────────────────

  describe("ensureWebhooks — repo sources", () => {
    it("uses configured repos instead of auto-discovery", async () => {
      // list hooks → empty; create hook → success
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/explicit-repo"],
      });
      await registrar.ensureWebhooks();

      // No discover call — only list + create
      expect(spawnMock).toHaveBeenCalledTimes(2);
      const listCall = spawnMock.mock.calls[0];
      expect(listCall[1]).toContain("/repos/owner/explicit-repo/hooks");
    });

    it("auto-discovers repos when no repos configured", async () => {
      // discover → 1 repo; list hooks → empty; create → success
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "owner/discovered\n" }))
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      await registrar.ensureWebhooks();

      expect(spawnMock).toHaveBeenCalledTimes(3);
      const discoverCall = spawnMock.mock.calls[0];
      expect(discoverCall[1]).toContain("repo");
      expect(discoverCall[1]).toContain("list");
    });

    it("skips registration when autoDiscover is false and no repos configured", async () => {
      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        autoDiscover: false,
      });
      await registrar.ensureWebhooks();

      expect(spawnMock).not.toHaveBeenCalled();
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringContaining("No repos to register webhooks for")
      );
    });
  });

  // ── ensureWebhooks — idempotency ──────────────────────────────────────────

  describe("ensureWebhooks — idempotency", () => {
    it("skips creation when webhook with correct URL already exists", async () => {
      const webhookUrl = "https://example.com/webhooks/github";
      const existingHook = { id: 42, config: { url: webhookUrl } };

      // discover → 1 repo; list hooks → existing hook
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "owner/repo\n" }))
        .mockReturnValueOnce(mockProcess({ stdout: JSON.stringify([existingHook]) }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      await registrar.ensureWebhooks();

      // Only 2 calls — no create
      expect(spawnMock).toHaveBeenCalledTimes(2);
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringContaining("already registered")
      );
    });

    it("creates webhook when no hooks exist for repo", async () => {
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "owner/repo\n" }))
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: JSON.stringify({ id: 99 }) }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      await registrar.ensureWebhooks();

      expect(spawnMock).toHaveBeenCalledTimes(3);
      const createCall = spawnMock.mock.calls[2];
      expect(createCall[1]).toContain("--method");
      expect(createCall[1]).toContain("POST");
      expect(createCall[1]).toContain("/repos/owner/repo/hooks");
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringContaining("Created webhook for owner/repo")
      );
    });

    it("creates webhook when existing hooks have different URLs", async () => {
      const otherHook = { id: 1, config: { url: "https://other.example.com/webhooks/github" } };

      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "owner/repo\n" }))
        .mockReturnValueOnce(mockProcess({ stdout: JSON.stringify([otherHook]) }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      await registrar.ensureWebhooks();

      expect(spawnMock).toHaveBeenCalledTimes(3);
    });
  });

  // ── ensureWebhooks — webhook secret ──────────────────────────────────────

  describe("ensureWebhooks — webhook secret", () => {
    it("includes secret in create payload when webhookSecret is set", async () => {
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "owner/repo\n" }))
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        webhookSecret: "my-secret",
      });
      await registrar.ensureWebhooks();

      const createProc = (spawnMock.mock.results[2].value as any);
      const writtenData = createProc.stdin.write.mock.calls[0]?.[0];
      const body = JSON.parse(writtenData);
      expect(body.config.secret).toBe("my-secret");
    });

    it("omits secret from create payload when webhookSecret is not set", async () => {
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "owner/repo\n" }))
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      await registrar.ensureWebhooks();

      const createProc = (spawnMock.mock.results[2].value as any);
      const writtenData = createProc.stdin.write.mock.calls[0]?.[0];
      const body = JSON.parse(writtenData);
      expect(body.config.secret).toBeUndefined();
    });
  });

  // ── error resilience ──────────────────────────────────────────────────────

  describe("error resilience", () => {
    it("does not throw when gh CLI is not found (ENOENT during discover)", async () => {
      spawnMock.mockReturnValueOnce(mockProcess({ spawnError: enoentError() }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      await expect(registrar.ensureWebhooks()).resolves.toBeUndefined();
    });

    it("does not throw when listing hooks fails for a repo", async () => {
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "owner/repo\n" }))
        .mockReturnValueOnce(mockProcess({ stderr: "not found", exitCode: 1 }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      await expect(registrar.ensureWebhooks()).resolves.toBeUndefined();
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("Could not list hooks for owner/repo"),
        expect.any(String)
      );
    });

    it("does not throw when creating hook fails for a repo", async () => {
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "owner/repo\n" }))
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stderr: "forbidden", exitCode: 1 }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      await expect(registrar.ensureWebhooks()).resolves.toBeUndefined();
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("Could not create webhook for owner/repo"),
        expect.any(String)
      );
    });

    it("continues with remaining repos when one repo fails", async () => {
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "owner/repo1\nowner/repo2\n" }))
        // repo1: list hooks fails
        .mockReturnValueOnce(mockProcess({ stderr: "error", exitCode: 1 }))
        // repo2: list hooks succeeds (empty), create succeeds
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      await registrar.ensureWebhooks();

      // 4 calls: discover + fail-list + ok-list + create
      expect(spawnMock).toHaveBeenCalledTimes(4);
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringContaining("Created webhook for owner/repo2")
      );
    });
  });
});
