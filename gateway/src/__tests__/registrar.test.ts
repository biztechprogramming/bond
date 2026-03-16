import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { EventEmitter } from "node:events";

// Mock child_process before importing the module under test (vi.mock is hoisted)
vi.mock("node:child_process", () => ({
  spawn: vi.fn(),
  execSync: vi.fn(),
}));

import { spawn, execSync } from "node:child_process";
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

/** Mock a successful SpacetimeDB SQL response with host_path rows. */
function mockStdbResponse(hostPaths: string[]) {
  return {
    ok: true,
    status: 200,
    json: async () => [{
      schema: { elements: [{ name: { some: "host_path" } }] },
      rows: hostPaths.map((p) => [p]),
    }],
    text: async () => "",
  };
}

function mockStdbEmptyResponse() {
  return {
    ok: true,
    status: 200,
    json: async () => [],
    text: async () => "",
  };
}

function mockStdbErrorResponse(status: number, body: string) {
  return {
    ok: false,
    status,
    json: async () => ({}),
    text: async () => body,
  };
}

// ---------- tests ----------

describe("WebhookRegistrar", () => {
  let spawnMock: ReturnType<typeof vi.fn>;
  let execSyncMock: ReturnType<typeof vi.fn>;
  let warnSpy: ReturnType<typeof vi.spyOn>;
  let logSpy: ReturnType<typeof vi.spyOn>;
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  const stdbConfig = {
    url: "http://localhost:18787",
    module: "bond-core-v2",
    token: "test-token",
  };

  beforeEach(() => {
    spawnMock = vi.mocked(spawn);
    spawnMock.mockReset();
    execSyncMock = vi.mocked(execSync);
    execSyncMock.mockReset();
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response());
  });

  afterEach(() => {
    warnSpy.mockRestore();
    logSpy.mockRestore();
    fetchSpy.mockRestore();
  });

  // ── discoverReposFromMounts ───────────────────────────────────────────────

  describe("discoverReposFromMounts", () => {
    it("queries SpacetimeDB and resolves host paths to GitHub repos", async () => {
      fetchSpy.mockResolvedValueOnce(mockStdbResponse(["/home/user/project1", "/home/user/project2"]) as any);
      execSyncMock
        .mockReturnValueOnce("git@github.com:owner/project1.git\n")
        .mockReturnValueOnce("https://github.com/owner/project2.git\n");

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        spacetimedb: stdbConfig,
      });
      const repos = await registrar.discoverReposFromMounts();

      expect(repos).toEqual(["owner/project1", "owner/project2"]);
      expect(fetchSpy).toHaveBeenCalledWith(
        `${stdbConfig.url}/v1/database/${stdbConfig.module}/sql`,
        expect.objectContaining({
          method: "POST",
          body: "SELECT DISTINCT host_path FROM agent_workspace_mounts",
        })
      );
    });

    it("deduplicates repos when multiple mounts point to the same repo", async () => {
      fetchSpy.mockResolvedValueOnce(mockStdbResponse(["/home/user/proj", "/opt/proj-clone"]) as any);
      execSyncMock
        .mockReturnValueOnce("git@github.com:owner/repo.git\n")
        .mockReturnValueOnce("https://github.com/owner/repo.git\n");

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        spacetimedb: stdbConfig,
      });
      const repos = await registrar.discoverReposFromMounts();

      expect(repos).toEqual(["owner/repo"]);
    });

    it("skips mounts that are not GitHub repos", async () => {
      fetchSpy.mockResolvedValueOnce(mockStdbResponse(["/home/user/project", "/home/user/not-a-repo"]) as any);
      execSyncMock
        .mockReturnValueOnce("git@github.com:owner/project.git\n")
        .mockImplementationOnce(() => { throw new Error("not a git repo"); });

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        spacetimedb: stdbConfig,
      });
      const repos = await registrar.discoverReposFromMounts();

      expect(repos).toEqual(["owner/project"]);
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringContaining("not a GitHub repo")
      );
    });

    it("returns empty array when SpacetimeDB is not configured", async () => {
      const registrar = new WebhookRegistrar({ externalUrl: "https://example.com" });
      const repos = await registrar.discoverReposFromMounts();

      expect(repos).toEqual([]);
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("SpacetimeDB not configured")
      );
    });

    it("returns empty array when SpacetimeDB query fails", async () => {
      fetchSpy.mockResolvedValueOnce(mockStdbErrorResponse(500, "internal error") as any);

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        spacetimedb: stdbConfig,
      });
      const repos = await registrar.discoverReposFromMounts();

      expect(repos).toEqual([]);
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("SpacetimeDB query failed")
      );
    });

    it("returns empty array when no mounts exist", async () => {
      fetchSpy.mockResolvedValueOnce(mockStdbEmptyResponse() as any);

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        spacetimedb: stdbConfig,
      });
      const repos = await registrar.discoverReposFromMounts();

      expect(repos).toEqual([]);
    });
  });

  // ── ensureWebhooks — guard conditions ────────────────────────────────────

  describe("ensureWebhooks — no externalUrl", () => {
    it("logs warning and skips all calls when externalUrl is not set", async () => {
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
    it("uses configured repos instead of mount discovery", async () => {
      // list hooks → empty; create hook → success
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/explicit-repo"],
      });
      await registrar.ensureWebhooks();

      // No SpacetimeDB call, no discover — only list + create
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(spawnMock).toHaveBeenCalledTimes(2);
      const listCall = spawnMock.mock.calls[0];
      expect(listCall[1]).toContain("/repos/owner/explicit-repo/hooks");
    });

    it("discovers repos from SpacetimeDB mounts when no repos configured", async () => {
      fetchSpy.mockResolvedValueOnce(mockStdbResponse(["/home/user/project"]) as any);
      execSyncMock.mockReturnValueOnce("git@github.com:owner/discovered.git\n");

      // list hooks → empty; create → success
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        spacetimedb: stdbConfig,
      });
      await registrar.ensureWebhooks();

      expect(fetchSpy).toHaveBeenCalledTimes(1);
      expect(spawnMock).toHaveBeenCalledTimes(2);
    });

    it("skips registration when no mounts and no repos configured", async () => {
      fetchSpy.mockResolvedValueOnce(mockStdbEmptyResponse() as any);

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        spacetimedb: stdbConfig,
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

      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: JSON.stringify([existingHook]) }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/repo"],
      });
      await registrar.ensureWebhooks();

      expect(spawnMock).toHaveBeenCalledTimes(1);
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringContaining("already registered")
      );

      // Verify it's tracked as registered
      const status = registrar.getRepoStatus("owner/repo");
      expect(status?.state).toBe("registered");
    });

    it("creates webhook when no hooks exist for repo", async () => {
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: JSON.stringify({ id: 99 }) }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/repo"],
      });
      await registrar.ensureWebhooks();

      expect(spawnMock).toHaveBeenCalledTimes(2);
      const createCall = spawnMock.mock.calls[1];
      expect(createCall[1]).toContain("--method");
      expect(createCall[1]).toContain("POST");
      expect(createCall[1]).toContain("/repos/owner/repo/hooks");
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringContaining("Created webhook for owner/repo")
      );

      const status = registrar.getRepoStatus("owner/repo");
      expect(status?.state).toBe("registered");
    });
  });

  // ── state tracking — success skipping ─────────────────────────────────────

  describe("state tracking — success", () => {
    it("skips already-registered repos on subsequent calls", async () => {
      // First call: list + create
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/repo"],
      });

      await registrar.ensureWebhooks();
      expect(spawnMock).toHaveBeenCalledTimes(2);
      expect(registrar.getRepoStatus("owner/repo")?.state).toBe("registered");

      // Second call: should skip entirely
      spawnMock.mockReset();
      await registrar.ensureWebhooks();
      expect(spawnMock).not.toHaveBeenCalled();
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringContaining("already registered, skipping")
      );
    });
  });

  // ── state tracking — failure and gave_up ──────────────────────────────────

  describe("state tracking — failure", () => {
    it("tracks failed attempts and gives up after 3", async () => {
      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/no-access"],
      });

      // Attempt 1: list hooks fails
      spawnMock.mockReturnValueOnce(mockProcess({ stderr: "forbidden", exitCode: 1 }));
      await registrar.ensureWebhooks();
      expect(registrar.getRepoStatus("owner/no-access")?.state).toBe("failed");
      expect(registrar.getRepoStatus("owner/no-access")?.attempts).toBe(1);

      // Attempt 2
      spawnMock.mockReturnValueOnce(mockProcess({ stderr: "forbidden", exitCode: 1 }));
      await registrar.ensureWebhooks();
      expect(registrar.getRepoStatus("owner/no-access")?.state).toBe("failed");
      expect(registrar.getRepoStatus("owner/no-access")?.attempts).toBe(2);

      // Attempt 3 → gave_up
      spawnMock.mockReturnValueOnce(mockProcess({ stderr: "forbidden", exitCode: 1 }));
      await registrar.ensureWebhooks();
      expect(registrar.getRepoStatus("owner/no-access")?.state).toBe("gave_up");
      expect(registrar.getRepoStatus("owner/no-access")?.attempts).toBe(3);

      // Attempt 4: should be skipped
      spawnMock.mockReset();
      await registrar.ensureWebhooks();
      expect(spawnMock).not.toHaveBeenCalled();
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringContaining("gave up")
      );
    });

    it("tracks failure on webhook creation error", async () => {
      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/repo"],
      });

      // list hooks OK, create fails
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stderr: "forbidden", exitCode: 1 }));

      await registrar.ensureWebhooks();
      expect(registrar.getRepoStatus("owner/repo")?.state).toBe("failed");
      expect(registrar.getRepoStatus("owner/repo")?.attempts).toBe(1);
      expect(registrar.getRepoStatus("owner/repo")?.lastError).toContain("forbidden");
    });
  });

  // ── reset ────────────────────────────────────────────────────────────────

  describe("reset", () => {
    it("resets a specific gave_up repo for retry", async () => {
      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/repo"],
      });

      // Fail 3 times
      for (let i = 0; i < 3; i++) {
        spawnMock.mockReturnValueOnce(mockProcess({ stderr: "forbidden", exitCode: 1 }));
        await registrar.ensureWebhooks();
      }
      expect(registrar.getRepoStatus("owner/repo")?.state).toBe("gave_up");

      // Reset
      registrar.reset("owner/repo");
      expect(registrar.getRepoStatus("owner/repo")?.state).toBe("pending");
      expect(registrar.getRepoStatus("owner/repo")?.attempts).toBe(0);

      // Now it should retry
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));
      await registrar.ensureWebhooks();
      expect(registrar.getRepoStatus("owner/repo")?.state).toBe("registered");
    });

    it("resets all failed repos when called without argument", async () => {
      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/repo1", "owner/repo2"],
      });

      // Fail both 3 times
      for (let i = 0; i < 3; i++) {
        spawnMock
          .mockReturnValueOnce(mockProcess({ stderr: "error", exitCode: 1 }))
          .mockReturnValueOnce(mockProcess({ stderr: "error", exitCode: 1 }));
        await registrar.ensureWebhooks();
      }
      expect(registrar.getRepoStatus("owner/repo1")?.state).toBe("gave_up");
      expect(registrar.getRepoStatus("owner/repo2")?.state).toBe("gave_up");

      // Reset all
      registrar.reset();
      expect(registrar.getRepoStatus("owner/repo1")?.state).toBe("pending");
      expect(registrar.getRepoStatus("owner/repo2")?.state).toBe("pending");
    });

    it("does not reset registered repos", async () => {
      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/good-repo"],
      });

      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));
      await registrar.ensureWebhooks();
      expect(registrar.getRepoStatus("owner/good-repo")?.state).toBe("registered");

      registrar.reset();
      // Should still be registered
      expect(registrar.getRepoStatus("owner/good-repo")?.state).toBe("registered");
    });
  });

  // ── ensureWebhooks — webhook secret ──────────────────────────────────────

  describe("ensureWebhooks — webhook secret", () => {
    it("includes secret in create payload when webhookSecret is set", async () => {
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        webhookSecret: "my-secret",
        repos: ["owner/repo"],
      });
      await registrar.ensureWebhooks();

      const createProc = (spawnMock.mock.results[1].value as any);
      const writtenData = createProc.stdin.write.mock.calls[0]?.[0];
      const body = JSON.parse(writtenData);
      expect(body.config.secret).toBe("my-secret");
    });

    it("omits secret from create payload when webhookSecret is not set", async () => {
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/repo"],
      });
      await registrar.ensureWebhooks();

      const createProc = (spawnMock.mock.results[1].value as any);
      const writtenData = createProc.stdin.write.mock.calls[0]?.[0];
      const body = JSON.parse(writtenData);
      expect(body.config.secret).toBeUndefined();
    });
  });

  // ── error resilience ──────────────────────────────────────────────────────

  describe("error resilience", () => {
    it("does not throw when gh CLI is not found (ENOENT)", async () => {
      spawnMock.mockReturnValueOnce(mockProcess({ spawnError: enoentError() }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/repo"],
      });
      await expect(registrar.ensureWebhooks()).resolves.toBeUndefined();
    });

    it("continues with remaining repos when one repo fails", async () => {
      spawnMock
        // repo1: list hooks fails
        .mockReturnValueOnce(mockProcess({ stderr: "error", exitCode: 1 }))
        // repo2: list hooks succeeds (empty), create succeeds
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));

      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/repo1", "owner/repo2"],
      });
      await registrar.ensureWebhooks();

      expect(spawnMock).toHaveBeenCalledTimes(3);
      expect(registrar.getRepoStatus("owner/repo1")?.state).toBe("failed");
      expect(registrar.getRepoStatus("owner/repo2")?.state).toBe("registered");
    });
  });

  // ── getRepoStatuses ───────────────────────────────────────────────────────

  describe("getRepoStatuses", () => {
    it("returns a snapshot of all tracked repos", async () => {
      const registrar = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/good"],
      });

      // Register "good" successfully
      spawnMock
        .mockReturnValueOnce(mockProcess({ stdout: "[]" }))
        .mockReturnValueOnce(mockProcess({ stdout: "{}" }));
      await registrar.ensureWebhooks();

      // Now add "bad" and let it fail
      const registrar2 = new WebhookRegistrar({
        externalUrl: "https://example.com",
        repos: ["owner/bad"],
      });
      spawnMock.mockReturnValueOnce(mockProcess({ stderr: "error", exitCode: 1 }));
      await registrar2.ensureWebhooks();

      expect(registrar.getRepoStatuses().get("owner/good")?.state).toBe("registered");
      expect(registrar2.getRepoStatuses().get("owner/bad")?.state).toBe("failed");
    });
  });
});
