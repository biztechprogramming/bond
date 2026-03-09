import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Tests for work plan / work item dedup logic in the plans router.
 *
 * Since we can't easily install supertest (peer dep conflicts), we test
 * the router by calling the Express handler directly via a mock req/res.
 */

// ── Mocks ──

let sqlQueryResults: Record<string, any[]> = {};
let reducerCalls: any[] = [];

vi.mock("../spacetimedb/client.js", () => ({
  sqlQuery: vi.fn(async (_url: string, _mod: string, query: string) => {
    if (query.includes("work_items")) return sqlQueryResults["work_items"] || [];
    if (query.includes("work_plans")) return sqlQueryResults["work_plans"] || [];
    return [];
  }),
  callReducer: vi.fn(async (...args: any[]) => {
    reducerCalls.push(args);
  }),
  encodeOption: (value: string | null | undefined) =>
    value != null ? { some: value } : { none: [] },
}));

import { createPlansRouter } from "../plans/router.js";

function buildRouter() {
  return createPlansRouter({
    spacetimedbUrl: "http://localhost:18787",
    spacetimedbModuleName: "bond-core-v2",
    spacetimedbToken: "test-token",
  } as any);
}

// Minimal mock for Express req/res
function mockReqRes(method: string, path: string, body: any = {}, params: any = {}) {
  const req: any = { method, path, body, params, query: {} };
  const resData: { status: number; body: any } = { status: 200, body: null };
  const res: any = {
    status(code: number) { resData.status = code; return res; },
    json(data: any) { resData.body = data; resData.status = resData.status || 200; },
  };
  return { req, res, resData };
}

// Helper to find and call a route handler from the Express router
async function callRoute(
  router: any,
  method: string,
  path: string,
  body: any = {},
  params: any = {},
) {
  const { req, res, resData } = mockReqRes(method, path, body, params);

  // Walk the router's stack to find matching handler
  for (const layer of router.stack) {
    if (!layer.route) continue;
    const route = layer.route;
    if (route.methods[method.toLowerCase()] && routeMatches(route.path, path)) {
      // Extract params from path
      const extractedParams = extractParams(route.path, path);
      req.params = { ...extractedParams, ...params };
      const handler = route.stack[route.stack.length - 1].handle;
      await handler(req, res);
      return resData;
    }
  }
  throw new Error(`No route found: ${method} ${path}`);
}

function routeMatches(pattern: string, path: string): boolean {
  const patternParts = pattern.split("/");
  const pathParts = path.split("/");
  if (patternParts.length !== pathParts.length) return false;
  return patternParts.every((part, i) => part.startsWith(":") || part === pathParts[i]);
}

function extractParams(pattern: string, path: string): Record<string, string> {
  const params: Record<string, string> = {};
  const patternParts = pattern.split("/");
  const pathParts = path.split("/");
  patternParts.forEach((part, i) => {
    if (part.startsWith(":")) params[part.slice(1)] = pathParts[i];
  });
  return params;
}

describe("Plans dedup", () => {
  beforeEach(() => {
    sqlQueryResults = {};
    reducerCalls = [];
    vi.clearAllMocks();
  });

  // ── Item dedup ──

  describe("POST /plans/:planId/items", () => {
    it("creates a new item when no duplicate exists", async () => {
      sqlQueryResults["work_items"] = [];
      const router = buildRouter();

      const result = await callRoute(router, "POST", "/plans/PLAN1/items", {
        title: "Implement feature X",
      });

      expect(result.status).toBe(201);
      expect(result.body.title).toBe("Implement feature X");
      expect(result.body.plan_id).toBe("PLAN1");
      expect(result.body.deduplicated).toBeUndefined();
      expect(reducerCalls.length).toBe(1);
    });

    it("returns existing item when duplicate title exists (case-insensitive)", async () => {
      sqlQueryResults["work_items"] = [
        {
          id: "ITEM_EXISTING",
          plan_id: "PLAN1",
          title: "Implement feature X",
          status: "new",
          ordinal: 0,
          description: "some desc",
        },
      ];
      const router = buildRouter();

      const result = await callRoute(router, "POST", "/plans/PLAN1/items", {
        title: "implement feature x",
      });

      expect(result.status).toBe(200);
      expect(result.body.item_id).toBe("ITEM_EXISTING");
      expect(result.body.id).toBe("ITEM_EXISTING");
      expect(result.body.deduplicated).toBe(true);
      expect(reducerCalls.length).toBe(0);
    });

    it("creates item when title differs", async () => {
      sqlQueryResults["work_items"] = [
        {
          id: "ITEM_EXISTING",
          plan_id: "PLAN1",
          title: "Implement feature X",
          status: "done",
          ordinal: 0,
        },
      ];
      const router = buildRouter();

      const result = await callRoute(router, "POST", "/plans/PLAN1/items", {
        title: "Implement feature Y",
      });

      expect(result.status).toBe(201);
      expect(result.body.title).toBe("Implement feature Y");
      expect(result.body.deduplicated).toBeUndefined();
      expect(reducerCalls.length).toBe(1);
    });

    it("auto-increments ordinal based on existing items", async () => {
      sqlQueryResults["work_items"] = [
        { id: "A", plan_id: "PLAN1", title: "First", status: "new", ordinal: 0 },
        { id: "B", plan_id: "PLAN1", title: "Second", status: "new", ordinal: 1 },
      ];
      const router = buildRouter();

      const result = await callRoute(router, "POST", "/plans/PLAN1/items", {
        title: "Third",
      });

      expect(result.status).toBe(201);
      expect(result.body.ordinal).toBe(2);
    });
  });

  // ── Plan dedup ──

  describe("POST /plans", () => {
    it("creates a new plan when no duplicate exists", async () => {
      sqlQueryResults["work_plans"] = [];
      const router = buildRouter();

      const result = await callRoute(router, "POST", "/plans", {
        title: "My Plan",
        agent_id: "agent-1",
      });

      expect(result.status).toBe(201);
      expect(result.body.title).toBe("My Plan");
      expect(result.body.deduplicated).toBeUndefined();
      expect(reducerCalls.length).toBe(1);
    });

    it("returns existing active plan with same title for same agent", async () => {
      sqlQueryResults["work_plans"] = [
        {
          id: "PLAN_EXISTING",
          agent_id: "agent-1",
          conversation_id: "conv-1",
          title: "My Plan",
          status: "active",
          created_at: Date.now(),
          updated_at: Date.now(),
        },
      ];
      const router = buildRouter();

      const result = await callRoute(router, "POST", "/plans", {
        title: "my plan",
        agent_id: "agent-1",
        conversation_id: "conv-1",
      });

      expect(result.status).toBe(200);
      expect(result.body.plan_id).toBe("PLAN_EXISTING");
      expect(result.body.deduplicated).toBe(true);
      expect(reducerCalls.length).toBe(0);
    });

    it("creates new plan when existing plan is completed", async () => {
      sqlQueryResults["work_plans"] = [
        {
          id: "PLAN_OLD",
          agent_id: "agent-1",
          conversation_id: "conv-1",
          title: "My Plan",
          status: "completed",
          created_at: Date.now(),
          updated_at: Date.now(),
        },
      ];
      const router = buildRouter();

      const result = await callRoute(router, "POST", "/plans", {
        title: "My Plan",
        agent_id: "agent-1",
        conversation_id: "conv-1",
      });

      expect(result.status).toBe(201);
      expect(result.body.deduplicated).toBeUndefined();
      expect(reducerCalls.length).toBe(1);
    });

    it("creates new plan for a different agent", async () => {
      sqlQueryResults["work_plans"] = [
        {
          id: "PLAN_EXISTING",
          agent_id: "agent-1",
          title: "My Plan",
          status: "active",
        },
      ];
      const router = buildRouter();

      const result = await callRoute(router, "POST", "/plans", {
        title: "My Plan",
        agent_id: "agent-2",
      });

      expect(result.status).toBe(201);
      expect(reducerCalls.length).toBe(1);
    });
  });
});
