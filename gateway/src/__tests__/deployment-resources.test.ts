/**
 * Deployment Resources tests — probeLocal, probeSSH, resource CRUD (mocked STDB).
 */

import { describe, it, expect, vi } from "vitest";

import { probeResource } from "../deployments/resource-probe.js";

vi.mock("../spacetimedb/client.js", () => {
  const resources: any[] = [];
  return {
    sqlQuery: vi.fn(async (_url: string, _mod: string, sql: string) => {
      if (sql.includes("FROM resources")) {
        return resources.filter(r => r.is_active);
      }
      return [];
    }),
    callReducer: vi.fn(async (_url: string, _mod: string, reducer: string, args: any[]) => {
      if (reducer === "create_deployment_resource") {
        // Positional args: id, name, display_name, resource_type, environment,
        //   connection_json, capabilities_json, state_json, tags_json,
        //   recommendations_json, is_active, created_at, updated_at, last_probed_at
        resources.push({
          id: args[0], name: args[1], display_name: args[2],
          resource_type: args[3], environment: args[4],
          connection_json: args[5], capabilities_json: args[6],
          state_json: args[7], tags_json: args[8],
          recommendations_json: args[9], is_active: args[10],
          created_at: args[11], updated_at: args[12], last_probed_at: args[13],
        });
      }
      if (reducer === "update_deployment_resource") {
        // Positional args: id, display_name?, resource_type?, environment?,
        //   connection_json?, capabilities_json?, state_json?, tags_json?,
        //   recommendations_json?, is_active?, updated_at, last_probed_at?
        const existing = resources.find(r => r.id === args[0]);
        if (existing) {
          if (args[1] != null) existing.display_name = args[1];
          if (args[2] != null) existing.resource_type = args[2];
          if (args[3] != null) existing.environment = args[3];
          if (args[4] != null) existing.connection_json = args[4];
          if (args[5] != null) existing.capabilities_json = args[5];
          if (args[6] != null) existing.state_json = args[6];
          if (args[7] != null) existing.tags_json = args[7];
          if (args[8] != null) existing.recommendations_json = args[8];
          if (args[9] != null) existing.is_active = args[9];
          existing.updated_at = args[10];
          if (args[11] != null) existing.last_probed_at = args[11];
        }
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
