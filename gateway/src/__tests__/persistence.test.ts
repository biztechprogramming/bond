import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createServer, type Server } from "http";
import express from "express";
import { createPersistenceRouter } from "../persistence/index.js";
import type { GatewayConfig } from "../config/index.js";

/**
 * Persistence API tests.
 *
 * We mock `global.fetch` to intercept the SpacetimeDB HTTP calls,
 * then exercise the Express routes via raw HTTP requests (node:http).
 *
 * Validates:
 *   - Correct reducer called with correct positional args
 *   - 201 on success, 500 on SpacetimeDB errors
 *   - ULID generation and response shape
 *   - Metadata serialization
 *   - Duration conversion (seconds → ms)
 */

// ---------- helpers ----------

function buildConfig(overrides: Partial<GatewayConfig> = {}): GatewayConfig {
  return {
    host: "127.0.0.1",
    port: 18792,
    backendUrl: "http://127.0.0.1:18790",
    frontendOrigin: "http://localhost:18788",
    spacetimedbUrl: "http://fake-spacetimedb:18787",
    spacetimedbModuleName: "bond-core",
    spacetimedbToken: "fake-token-for-tests",
    ...overrides,
  };
}

function buildApp(config?: GatewayConfig) {
  const cfg = config || buildConfig();
  const app = express();
  app.use(express.json());
  app.use("/api/v1", createPersistenceRouter(cfg));
  return app;
}

/** Raw HTTP request using node:http — bypasses global.fetch mock entirely. */
function httpRequest(
  server: Server,
  method: string,
  path: string,
  body?: any
): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const addr = server.address() as { port: number };
    const http = require("http");
    const payload = body ? JSON.stringify(body) : undefined;

    const req = http.request(
      {
        hostname: "127.0.0.1",
        port: addr.port,
        path,
        method,
        headers: payload
          ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) }
          : {},
      },
      (res: any) => {
        let data = "";
        res.on("data", (chunk: string) => (data += chunk));
        res.on("end", () => {
          const json = data ? JSON.parse(data) : null;
          resolve({ status: res.statusCode, body: json });
        });
      }
    );
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

/** Start an Express app on a random port, return the server. */
function listen(app: express.Express): Promise<Server> {
  return new Promise((resolve) => {
    const server = createServer(app);
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

// ---------- mocks ----------

let fetchCalls: { url: string; init: RequestInit }[] = [];
let mockFetchOk = true;
let mockFetchStatus = 200;
let mockFetchText = "";

const originalFetch = global.fetch;

beforeEach(() => {
  fetchCalls = [];
  mockFetchOk = true;
  mockFetchStatus = 200;
  mockFetchText = "";

  global.fetch = vi.fn(async (input: any, init?: any) => {
    const url = typeof input === "string" ? input : input.toString();

    // Health-check probe (startup)
    if (url.includes("/v1/health")) {
      return new Response(JSON.stringify({ version: "2.0.2" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    fetchCalls.push({ url, init: init || {} });
    return new Response(mockFetchText, {
      status: mockFetchStatus,
      ok: mockFetchOk,
    });
  }) as any;
});

afterEach(() => {
  global.fetch = originalFetch;
});

// ---------- tests: messages ----------

describe("POST /api/v1/messages", () => {
  it("calls save_message reducer and returns 201", async () => {
    const server = await listen(buildApp());
    try {
      const res = await httpRequest(server, "POST", "/api/v1/messages", {
        agentId: "agent-1",
        sessionId: "session-1",
        role: "user",
        content: "Hello world",
        metadata: { source: "test" },
      });

      expect(res.status).toBe(201);
      expect(res.body.status).toBe("saved");
      expect(res.body.id).toBeTruthy();
      expect(res.body.timestamp).toBeTruthy();

      // Verify the SpacetimeDB HTTP call
      expect(fetchCalls).toHaveLength(1);
      const call = fetchCalls[0];
      expect(call.url).toBe(
        "http://fake-spacetimedb:18787/v1/database/bond-core/call/save_message"
      );
      expect(call.init.method).toBe("POST");

      const args = JSON.parse(call.init.body as string);
      expect(args).toHaveLength(6);
      expect(args[0]).toBe(res.body.id); // ULID
      expect(args[1]).toBe("agent-1");
      expect(args[2]).toBe("session-1");
      expect(args[3]).toBe("user");
      expect(args[4]).toBe("Hello world");
      expect(args[5]).toBe('{"source":"test"}');
    } finally {
      server.close();
    }
  });

  it("serializes empty metadata as {}", async () => {
    const server = await listen(buildApp());
    try {
      const res = await httpRequest(server, "POST", "/api/v1/messages", {
        agentId: "agent-1",
        sessionId: "session-1",
        role: "assistant",
        content: "Reply",
      });

      expect(res.status).toBe(201);
      const args = JSON.parse(fetchCalls[0].init.body as string);
      expect(args[5]).toBe("{}");
    } finally {
      server.close();
    }
  });

  it("returns 500 when SpacetimeDB rejects the call", async () => {
    mockFetchOk = false;
    mockFetchStatus = 400;
    mockFetchText = "invalid arguments for reducer save_message";

    const server = await listen(buildApp());
    try {
      const res = await httpRequest(server, "POST", "/api/v1/messages", {
        agentId: "agent-1",
        sessionId: "session-1",
        role: "user",
        content: "Bad message",
      });

      expect(res.status).toBe(500);
      expect(res.body.error).toContain("save_message failed");
      expect(res.body.error).toContain("400");
    } finally {
      server.close();
    }
  });

  it("returns 500 when SpacetimeDB is unreachable", async () => {
    // Override fetch to throw on reducer calls (but still handle health check)
    global.fetch = vi.fn(async (input: any) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v1/health")) {
        return new Response(JSON.stringify({ version: "2.0.2" }), { status: 200 });
      }
      throw new Error("connect ECONNREFUSED");
    }) as any;

    const server = await listen(buildApp());
    try {
      const res = await httpRequest(server, "POST", "/api/v1/messages", {
        agentId: "agent-1",
        sessionId: "session-1",
        role: "user",
        content: "This should fail",
      });

      expect(res.status).toBe(500);
      expect(res.body.error).toContain("ECONNREFUSED");
    } finally {
      server.close();
    }
  });

  it("generates unique ULIDs for each request", async () => {
    const server = await listen(buildApp());
    try {
      const res1 = await httpRequest(server, "POST", "/api/v1/messages", {
        agentId: "a",
        sessionId: "s",
        role: "user",
        content: "msg1",
      });
      const res2 = await httpRequest(server, "POST", "/api/v1/messages", {
        agentId: "a",
        sessionId: "s",
        role: "user",
        content: "msg2",
      });

      expect(res1.body.id).not.toBe(res2.body.id);
      // ULID format: 26 chars, uppercase alphanumeric
      expect(res1.body.id).toMatch(/^[0-9A-Z]{26}$/);
      expect(res2.body.id).toMatch(/^[0-9A-Z]{26}$/);
    } finally {
      server.close();
    }
  });
});

// ---------- tests: tool-logs ----------

describe("POST /api/v1/tool-logs", () => {
  it("calls log_tool reducer and returns 201", async () => {
    const server = await listen(buildApp());
    try {
      const res = await httpRequest(server, "POST", "/api/v1/tool-logs", {
        agentId: "agent-1",
        sessionId: "session-1",
        toolName: "web_search",
        input: { query: "test" },
        output: { results: [1, 2, 3] },
        duration: 1.5,
      });

      expect(res.status).toBe(201);
      expect(res.body.status).toBe("logged");
      expect(res.body.id).toBeTruthy();

      expect(fetchCalls).toHaveLength(1);
      const call = fetchCalls[0];
      expect(call.url).toBe(
        "http://fake-spacetimedb:18787/v1/database/bond-core/call/log_tool"
      );

      const args = JSON.parse(call.init.body as string);
      expect(args).toHaveLength(7);
      expect(args[0]).toBe(res.body.id);
      expect(args[1]).toBe("agent-1");
      expect(args[2]).toBe("session-1");
      expect(args[3]).toBe("web_search");
      expect(args[4]).toBe('{"query":"test"}');
      expect(args[5]).toBe('{"results":[1,2,3]}');
      expect(args[6]).toBe(1500); // 1.5s → 1500ms
    } finally {
      server.close();
    }
  });

  it("handles zero/missing duration", async () => {
    const server = await listen(buildApp());
    try {
      const res = await httpRequest(server, "POST", "/api/v1/tool-logs", {
        agentId: "agent-1",
        sessionId: "session-1",
        toolName: "calculator",
        input: { expr: "2+2" },
        output: { result: 4 },
      });

      expect(res.status).toBe(201);
      const args = JSON.parse(fetchCalls[0].init.body as string);
      expect(args[6]).toBe(0); // missing duration → 0
    } finally {
      server.close();
    }
  });

  it("returns 500 when SpacetimeDB rejects the call", async () => {
    mockFetchOk = false;
    mockFetchStatus = 500;
    mockFetchText = "internal error";

    const server = await listen(buildApp());
    try {
      const res = await httpRequest(server, "POST", "/api/v1/tool-logs", {
        agentId: "agent-1",
        sessionId: "session-1",
        toolName: "broken_tool",
        input: {},
        output: {},
        duration: 0,
      });

      expect(res.status).toBe(500);
      expect(res.body.error).toContain("log_tool failed");
    } finally {
      server.close();
    }
  });
});

// ---------- tests: HTTP integration ----------

describe("SpacetimeDB HTTP integration", () => {
  it("uses correct URL structure for reducer calls", async () => {
    const cfg = buildConfig({
      spacetimedbUrl: "http://custom-host:9999",
      spacetimedbModuleName: "my-module",
    });
    const server = await listen(buildApp(cfg));
    try {
      await httpRequest(server, "POST", "/api/v1/messages", {
        agentId: "a",
        sessionId: "s",
        role: "user",
        content: "test",
      });

      expect(fetchCalls[0].url).toBe(
        "http://custom-host:9999/v1/database/my-module/call/save_message"
      );
    } finally {
      server.close();
    }
  });

  it("sends POST with JSON content type", async () => {
    const server = await listen(buildApp());
    try {
      await httpRequest(server, "POST", "/api/v1/messages", {
        agentId: "a",
        sessionId: "s",
        role: "user",
        content: "test",
      });

      const headers = fetchCalls[0].init.headers as Record<string, string>;
      expect(headers["Content-Type"]).toBe("application/json");
      expect(fetchCalls[0].init.method).toBe("POST");
    } finally {
      server.close();
    }
  });

  it("sends reducer args as JSON array (positional)", async () => {
    const server = await listen(buildApp());
    try {
      await httpRequest(server, "POST", "/api/v1/messages", {
        agentId: "a",
        sessionId: "s",
        role: "user",
        content: "test",
        metadata: { key: "val" },
      });

      const body = JSON.parse(fetchCalls[0].init.body as string);
      expect(Array.isArray(body)).toBe(true);
      expect(body.length).toBe(6);
    } finally {
      server.close();
    }
  });
});
