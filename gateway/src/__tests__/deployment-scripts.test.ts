/**
 * Deployment Script Registry tests.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtempSync, rmSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  registerScript,
  getManifest,
  verifyScriptHash,
  listScripts,
  getScriptVersionDir,
} from "../deployments/scripts.js";

let tempDir: string;

beforeAll(() => {
  tempDir = mkdtempSync(join(tmpdir(), "deploy-scripts-test-"));
});

afterAll(() => {
  rmSync(tempDir, { recursive: true, force: true });
});

function makeFiles(extra: Record<string, string> = {}): Record<string, Buffer> {
  const files: Record<string, Buffer> = {
    "deploy.sh": Buffer.from("#!/bin/bash\necho deploy", "utf8"),
    ...Object.fromEntries(Object.entries(extra).map(([k, v]) => [k, Buffer.from(v, "utf8")])),
  };
  return files;
}

describe("registerScript", () => {
  it("creates directory structure and manifest", () => {
    const manifest = registerScript(tempDir, {
      script_id: "test-script",
      version: "v1",
      name: "Test Script",
      registered_by: "user-1",
      files: makeFiles(),
    });

    expect(manifest.script_id).toBe("test-script");
    expect(manifest.version).toBe("v1");
    expect(manifest.sha256).toBeTruthy();
    expect(manifest.files).toContain("deploy.sh");

    const versionDir = getScriptVersionDir(tempDir, "test-script", "v1");
    expect(existsSync(join(versionDir, "deploy.sh"))).toBe(true);
    expect(existsSync(join(versionDir, "manifest.json"))).toBe(true);
    expect(existsSync(join(versionDir, ".sha256"))).toBe(true);
  });

  it("rejects duplicate versions", () => {
    registerScript(tempDir, {
      script_id: "dup-script",
      version: "v1",
      name: "Dup",
      registered_by: "user-1",
      files: makeFiles(),
    });

    expect(() =>
      registerScript(tempDir, {
        script_id: "dup-script",
        version: "v1",
        name: "Dup Again",
        registered_by: "user-1",
        files: makeFiles(),
      }),
    ).toThrow("already exists");
  });

  it("requires deploy.sh", () => {
    expect(() =>
      registerScript(tempDir, {
        script_id: "no-deploy",
        version: "v1",
        name: "No Deploy",
        registered_by: "user-1",
        files: { "config.yaml": Buffer.from("key: val") },
      }),
    ).toThrow("deploy.sh is required");
  });
});

describe("getManifest", () => {
  it("returns correct data", () => {
    registerScript(tempDir, {
      script_id: "manifest-test",
      version: "v1",
      name: "Manifest Test",
      description: "A test script",
      timeout: 120,
      registered_by: "user-2",
      files: makeFiles({ "rollback.sh": "#!/bin/bash\necho rollback" }),
    });

    const manifest = getManifest(tempDir, "manifest-test", "v1");
    expect(manifest).not.toBeNull();
    expect(manifest!.name).toBe("Manifest Test");
    expect(manifest!.description).toBe("A test script");
    expect(manifest!.timeout).toBe(120);
    expect(manifest!.registered_by).toBe("user-2");
    expect(manifest!.files).toEqual(expect.arrayContaining(["deploy.sh", "rollback.sh"]));
  });

  it("returns null for non-existent script", () => {
    expect(getManifest(tempDir, "nope", "v1")).toBeNull();
  });
});

describe("verifyScriptHash", () => {
  it("passes for valid script", () => {
    registerScript(tempDir, {
      script_id: "hash-valid",
      version: "v1",
      name: "Hash Valid",
      registered_by: "user-1",
      files: makeFiles(),
    });

    expect(verifyScriptHash(tempDir, "hash-valid", "v1")).toBe(true);
  });

  it("fails for tampered script", () => {
    registerScript(tempDir, {
      script_id: "hash-tampered",
      version: "v1",
      name: "Hash Tampered",
      registered_by: "user-1",
      files: makeFiles(),
    });

    const versionDir = getScriptVersionDir(tempDir, "hash-tampered", "v1");
    writeFileSync(join(versionDir, "deploy.sh"), "#!/bin/bash\necho HACKED");

    expect(verifyScriptHash(tempDir, "hash-tampered", "v1")).toBe(false);
  });

  it("fails for non-existent script", () => {
    expect(verifyScriptHash(tempDir, "nope", "v1")).toBe(false);
  });
});

describe("listScripts", () => {
  it("returns all registered scripts", () => {
    const list = listScripts(tempDir);
    expect(list.length).toBeGreaterThanOrEqual(1);

    const testScript = list.find(s => s.script_id === "test-script");
    expect(testScript).toBeDefined();
    expect(testScript!.versions).toContain("v1");
  });
});
