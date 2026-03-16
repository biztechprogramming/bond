/**
 * Deployment Resources tests — probeLocal, probeSSH, resource CRUD (mocked STDB).
 */

import { describe, it, expect, vi } from "vitest";

import { probeResource } from "../deployments/resource-probe.js";

vi.mock("../spacetimedb/client.js", () => {
  const resources: any[] = [];
  return {
    sqlQuery: vi.fn(async (_url: string, _mod: string, sql: string) => {
      if (sql.includes("deployment_resources")) {
        return resources.filter(r => r.is_active);
      }
      return [];
    }),
    callReducer: vi.fn(async (_url: string, _mod: string, reducer: string, args: any[]) => {
      if (reducer === "create_deployment_resource") {
        resources.push(args[0]);
      }
      if (reducer === "update_deployment_resource") {
        const update = args[0];
        const existing = resources.find(r => r.id === update.id);
        if (existing) Object.assign(existing, update);
      }
    }),
  };
});

import {
  getResources,
  createResource,
  getResource,
  deleteResource,
} from "../deployments/resources.js";

const cfg: any = {
  spacetimedbUrl: "http://localhost:3000",
  spacetimedbModuleName: "bond",
  spacetimedbToken: "test-token",
};

describe("probeLocal", () => {
  it("returns capabilities and state", async () => {
    const result = await probeResource({}, "local");
    expect(result.capabilities).toBeDefined();
    expect(result.capabilities.local).toBe(true);
    expect(result.state).toBeDefined();
    expect(result.state.status).toBe("online");
  });
});

describe("probeSSH", () => {
  it("returns templated results with ssh capability", async () => {
    const result = await probeResource({ host: "10.0.0.1", port: 22, user: "deploy" }, "linux-server");
    expect(result.capabilities.ssh).toBe(true);
    expect(result.capabilities.host).toBe("10.0.0.1");
    // State may be "pending" (template) or "unreachable" (actual SSH attempt failed)
    expect(["pending", "unreachable"]).toContain(result.state.status);
  }, 15000);
});

describe("resource CRUD via mocked STDB", () => {
  it("creates and lists resources", async () => {
    const id = await createResource(cfg, {
      name: "test-server",
      display_name: "Test Server",
      resource_type: "linux-server",
      environment: "dev",
      connection_json: "{}",
      capabilities_json: "{}",
      state_json: "{}",
      tags_json: "[]",
      recommendations_json: "[]",
    });

    expect(id).toBeTruthy();

    const resources = await getResources(cfg);
    expect(resources.length).toBeGreaterThanOrEqual(1);
  });
});

describe("probeGeneric", () => {
  it("returns template for kubernetes", async () => {
    const result = await probeResource({}, "kubernetes");
    expect(result.capabilities).toBeDefined();
    // kubernetes case goes through probeGeneric which returns a template
    expect(result.state).toBeDefined();
  });

  it("returns fallback for unknown type", async () => {
    const result = await probeResource({}, "unknown-type");
    expect(result.capabilities).toBeDefined();
    expect(result.state).toBeDefined();
  });
});
