/**
 * Unit tests for environment allocation collision detection (Doc 077).
 *
 * Tests the core collision logic: port uniqueness, directory containment,
 * port range validation, and default suggestion strategy.
 */

import { describe, test, expect } from "bun:test";

// ── Port collision detection logic (extracted for testing) ────────────────────

interface PortEntry {
  port: number;
  protocol: string;
  service_name: string;
  app_name: string;
  environment_name: string;
}

interface DirectoryEntry {
  app_dir: string;
  data_dir: string;
  log_dir: string;
  config_dir: string;
  app_name: string;
  environment_name: string;
}

function detectPortConflicts(
  newPorts: Array<{ service_name: string; port: number; protocol: string }>,
  existingPorts: PortEntry[],
): Array<{ field: string; port: number; protocol: string; conflicting_app: string; conflicting_env: string }> {
  const conflicts: Array<{ field: string; port: number; protocol: string; conflicting_app: string; conflicting_env: string }> = [];
  const existingMap = new Map<string, PortEntry>();
  for (const p of existingPorts) {
    existingMap.set(`${p.port}/${p.protocol}`, p);
  }
  for (const p of newPorts) {
    const key = `${p.port}/${p.protocol}`;
    const existing = existingMap.get(key);
    if (existing) {
      conflicts.push({
        field: p.service_name,
        port: p.port,
        protocol: p.protocol,
        conflicting_app: existing.app_name,
        conflicting_env: existing.environment_name,
      });
    }
  }
  return conflicts;
}

function detectDirectoryConflicts(
  newDirs: { app_dir: string; data_dir: string; log_dir: string; config_dir: string },
  existingAllocations: DirectoryEntry[],
): Array<{ field: string; newDir: string; existingDir: string; app: string; env: string }> {
  const conflicts: Array<{ field: string; newDir: string; existingDir: string; app: string; env: string }> = [];
  const fields = ["app_dir", "data_dir", "log_dir", "config_dir"] as const;
  for (const alloc of existingAllocations) {
    for (const field of fields) {
      const newDir = newDirs[field];
      const existingDir = alloc[field];
      if (!newDir || !existingDir) continue;
      // Exact match or substring containment (parent/child)
      if (newDir === existingDir || newDir.startsWith(existingDir + "/") || existingDir.startsWith(newDir + "/")) {
        conflicts.push({ field, newDir, existingDir, app: alloc.app_name, env: alloc.environment_name });
      }
    }
  }
  return conflicts;
}

function validatePortRange(port: number): { valid: boolean; warning?: string } {
  if (port < 1 || port > 65535) return { valid: false };
  if (port < 1024) return { valid: true, warning: "Privileged port requires root/sudo" };
  return { valid: true };
}

function suggestNextPort(base: number, usedPorts: Set<number>, protocol = "tcp"): number {
  let port = base;
  while (usedPorts.has(port)) port++;
  return port;
}

const ENV_PORT_OFFSETS: Record<string, number> = {
  prod: 0, production: 0,
  staging: 100,
  dev: 200, development: 200,
  qa: 300,
  uat: 400,
};

function computeDefaultBasePort(envName: string, appBasePort = 3000): number {
  const offset = ENV_PORT_OFFSETS[envName] ?? 500;
  return appBasePort + offset;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Port collision detection", () => {
  test("detects exact port+protocol collision", () => {
    const conflicts = detectPortConflicts(
      [{ service_name: "app", port: 3000, protocol: "tcp" }],
      [{ port: 3000, protocol: "tcp", service_name: "app", app_name: "myapp", environment_name: "prod" }],
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0].conflicting_app).toBe("myapp");
    expect(conflicts[0].conflicting_env).toBe("prod");
  });

  test("no conflict for same port different protocol", () => {
    const conflicts = detectPortConflicts(
      [{ service_name: "app", port: 3000, protocol: "udp" }],
      [{ port: 3000, protocol: "tcp", service_name: "app", app_name: "myapp", environment_name: "prod" }],
    );
    expect(conflicts).toHaveLength(0);
  });

  test("no conflict for different ports", () => {
    const conflicts = detectPortConflicts(
      [{ service_name: "app", port: 3100, protocol: "tcp" }],
      [{ port: 3000, protocol: "tcp", service_name: "app", app_name: "myapp", environment_name: "prod" }],
    );
    expect(conflicts).toHaveLength(0);
  });

  test("detects multiple port collisions", () => {
    const conflicts = detectPortConflicts(
      [
        { service_name: "app", port: 3000, protocol: "tcp" },
        { service_name: "redis", port: 6379, protocol: "tcp" },
      ],
      [
        { port: 3000, protocol: "tcp", service_name: "app", app_name: "other", environment_name: "prod" },
        { port: 6379, protocol: "tcp", service_name: "redis", app_name: "other", environment_name: "prod" },
      ],
    );
    expect(conflicts).toHaveLength(2);
  });

  test("detects cross-app collisions on same server", () => {
    const conflicts = detectPortConflicts(
      [{ service_name: "app", port: 4000, protocol: "tcp" }],
      [{ port: 4000, protocol: "tcp", service_name: "app", app_name: "otherapp", environment_name: "prod" }],
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0].conflicting_app).toBe("otherapp");
  });
});

describe("Directory collision detection", () => {
  test("detects exact directory match", () => {
    const conflicts = detectDirectoryConflicts(
      { app_dir: "/opt/myapp/dev", data_dir: "", log_dir: "", config_dir: "" },
      [{ app_dir: "/opt/myapp/dev", data_dir: "", log_dir: "", config_dir: "", app_name: "myapp", environment_name: "dev" }],
    );
    expect(conflicts).toHaveLength(1);
  });

  test("detects parent directory containment", () => {
    const conflicts = detectDirectoryConflicts(
      { app_dir: "/opt/app", data_dir: "", log_dir: "", config_dir: "" },
      [{ app_dir: "/opt/app/dev", data_dir: "", log_dir: "", config_dir: "", app_name: "myapp", environment_name: "dev" }],
    );
    expect(conflicts).toHaveLength(1);
  });

  test("detects child directory containment", () => {
    const conflicts = detectDirectoryConflicts(
      { app_dir: "/opt/app/dev/sub", data_dir: "", log_dir: "", config_dir: "" },
      [{ app_dir: "/opt/app/dev", data_dir: "", log_dir: "", config_dir: "", app_name: "myapp", environment_name: "dev" }],
    );
    expect(conflicts).toHaveLength(1);
  });

  test("no conflict for sibling directories", () => {
    const conflicts = detectDirectoryConflicts(
      { app_dir: "/opt/myapp/staging", data_dir: "", log_dir: "", config_dir: "" },
      [{ app_dir: "/opt/myapp/dev", data_dir: "", log_dir: "", config_dir: "", app_name: "myapp", environment_name: "dev" }],
    );
    expect(conflicts).toHaveLength(0);
  });

  test("checks all directory fields", () => {
    const conflicts = detectDirectoryConflicts(
      { app_dir: "/opt/a/dev", data_dir: "/var/data/a/dev", log_dir: "/var/log/a/dev", config_dir: "/etc/a/dev" },
      [{ app_dir: "/opt/a/dev", data_dir: "/var/data/a/dev", log_dir: "/var/log/a/dev", config_dir: "/etc/a/dev", app_name: "a", environment_name: "dev" }],
    );
    expect(conflicts).toHaveLength(4);
  });

  test("no false positive for similar prefixes", () => {
    // "/opt/app" should NOT conflict with "/opt/application" (no "/" separator)
    const conflicts = detectDirectoryConflicts(
      { app_dir: "/opt/app", data_dir: "", log_dir: "", config_dir: "" },
      [{ app_dir: "/opt/application", data_dir: "", log_dir: "", config_dir: "", app_name: "other", environment_name: "dev" }],
    );
    expect(conflicts).toHaveLength(0);
  });
});

describe("Port range validation", () => {
  test("valid standard port", () => {
    expect(validatePortRange(3000).valid).toBe(true);
    expect(validatePortRange(3000).warning).toBeUndefined();
  });

  test("privileged port warning", () => {
    const result = validatePortRange(80);
    expect(result.valid).toBe(true);
    expect(result.warning).toContain("root");
  });

  test("invalid port below 1", () => {
    expect(validatePortRange(0).valid).toBe(false);
  });

  test("invalid port above 65535", () => {
    expect(validatePortRange(70000).valid).toBe(false);
  });

  test("edge cases: 1 and 65535 are valid", () => {
    expect(validatePortRange(1).valid).toBe(true);
    expect(validatePortRange(65535).valid).toBe(true);
  });
});

describe("Default port suggestion", () => {
  test("suggests base port when available", () => {
    expect(suggestNextPort(3000, new Set())).toBe(3000);
  });

  test("skips used ports", () => {
    expect(suggestNextPort(3000, new Set([3000, 3001]))).toBe(3002);
  });

  test("environment offset calculation", () => {
    expect(computeDefaultBasePort("prod")).toBe(3000);
    expect(computeDefaultBasePort("staging")).toBe(3100);
    expect(computeDefaultBasePort("dev")).toBe(3200);
    expect(computeDefaultBasePort("qa")).toBe(3300);
    expect(computeDefaultBasePort("uat")).toBe(3400);
  });

  test("custom environment gets fallback offset", () => {
    expect(computeDefaultBasePort("custom-env")).toBe(3500);
  });

  test("custom base port with offset", () => {
    expect(computeDefaultBasePort("staging", 8000)).toBe(8100);
  });
});
