/**
 * Unit tests for discovery tools.
 *
 * Design Doc 071 §4 — Discovery Tool Definitions
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

// Mock SSH execution
vi.mock("../deployments/discovery-scripts.js", () => ({
  executeSshScript: vi.fn(async () => ({ exit_code: 0, stdout: "", stderr: "" })),
}));

import {
  validateSshCommand,
  ALLOWED_SSH_COMMANDS,
  sshExec,
  detectFramework,
  detectBuildStrategy,
  detectEnvVars,
  detectPorts,
  detectHealthEndpoint,
  detectServices,
  repoInspect,
  askUser,
} from "../deployments/discovery-tools.js";
import { executeSshScript } from "../deployments/discovery-scripts.js";

// ── SSH Command Allowlist ───────────────────────────────────────────────────

describe("validateSshCommand", () => {
  it("allows whitelisted commands", () => {
    expect(validateSshCommand("uname -a")).toBe(true);
    expect(validateSshCommand("cat /etc/os-release")).toBe(true);
    expect(validateSshCommand("docker ps")).toBe(true);
    expect(validateSshCommand("systemctl status nginx")).toBe(true);
    expect(validateSshCommand("ps aux")).toBe(true);
    expect(validateSshCommand("ss -tlnp")).toBe(true);
  });

  it("rejects non-whitelisted commands", () => {
    expect(validateSshCommand("rm -rf /")).toBe(false);
    expect(validateSshCommand("chmod 777 /etc")).toBe(false);
    expect(validateSshCommand("apt install something")).toBe(false);
    expect(validateSshCommand("shutdown -h now")).toBe(false);
  });

  it("rejects empty commands", () => {
    expect(validateSshCommand("")).toBe(false);
  });
});

describe("sshExec", () => {
  beforeEach(() => {
    vi.mocked(executeSshScript).mockReset();
  });

  it("rejects disallowed commands", async () => {
    const result = await sshExec({ host: "test", command: "rm -rf /" });
    expect(result.exit_code).toBe(-1);
    expect(result.stderr).toContain("not allowed");
  });

  it("executes allowed commands via SSH", async () => {
    vi.mocked(executeSshScript).mockResolvedValueOnce({
      exit_code: 0,
      stdout: "Linux test 5.15.0\n",
      stderr: "",
    });

    const result = await sshExec({ host: "test", command: "uname -a", parse_as: "raw" });
    expect(result.exit_code).toBe(0);
    expect(result.output).toBe("Linux test 5.15.0\n");
  });

  it("parses JSON output", async () => {
    vi.mocked(executeSshScript).mockResolvedValueOnce({
      exit_code: 0,
      stdout: '{"version": "1.0"}',
      stderr: "",
    });

    const result = await sshExec({ host: "test", command: "cat /app/config.json", parse_as: "json" });
    expect(result.output).toEqual({ version: "1.0" });
  });

  it("parses lines output", async () => {
    vi.mocked(executeSshScript).mockResolvedValueOnce({
      exit_code: 0,
      stdout: "line1\nline2\nline3\n",
      stderr: "",
    });

    const result = await sshExec({ host: "test", command: "ls /app", parse_as: "lines" });
    expect(result.output).toEqual(["line1", "line2", "line3"]);
  });
});

// ── Framework Detection ─────────────────────────────────────────────────────

describe("detectFramework", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-test-"));
  });

  it("detects Next.js from package.json", async () => {
    fs.writeFileSync(path.join(tmpDir, "package.json"), JSON.stringify({
      dependencies: { next: "14.0.0", react: "18.0.0" },
    }));

    const results = await detectFramework(tmpDir);
    expect(results.some(r => r.framework === "Next.js")).toBe(true);
    const nextjs = results.find(r => r.framework === "Next.js")!;
    expect(nextjs.confidence).toBeGreaterThan(0.9);
    expect(nextjs.runtime).toBe("node");
  });

  it("detects Express from package.json", async () => {
    fs.writeFileSync(path.join(tmpDir, "package.json"), JSON.stringify({
      dependencies: { express: "4.18.0" },
    }));

    const results = await detectFramework(tmpDir);
    expect(results.some(r => r.framework === "Express")).toBe(true);
  });

  it("detects Django from requirements.txt", async () => {
    fs.writeFileSync(path.join(tmpDir, "requirements.txt"), "django>=4.2\ncelery>=5.0\n");

    const results = await detectFramework(tmpDir);
    expect(results.some(r => r.framework === "Django")).toBe(true);
    const django = results.find(r => r.framework === "Django")!;
    expect(django.runtime).toBe("python");
  });

  it("detects Rails from Gemfile", async () => {
    fs.writeFileSync(path.join(tmpDir, "Gemfile"), "source 'https://rubygems.org'\ngem 'rails', '~> 7.0'\n");

    const results = await detectFramework(tmpDir);
    expect(results.some(r => r.framework === "Rails")).toBe(true);
  });

  it("returns empty array when no framework detected", async () => {
    const results = await detectFramework(tmpDir);
    expect(results).toHaveLength(0);
  });
});

// ── Build Strategy Detection ────────────────────────────────────────────────

describe("detectBuildStrategy", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-test-"));
  });

  it("detects Dockerfile", async () => {
    fs.writeFileSync(path.join(tmpDir, "Dockerfile"), "FROM node:18\nCOPY . .\nRUN npm install\n");

    const results = await detectBuildStrategy(tmpDir);
    expect(results.some(r => r.strategy === "docker")).toBe(true);
  });

  it("detects docker-compose", async () => {
    fs.writeFileSync(path.join(tmpDir, "docker-compose.yml"), "version: '3'\nservices:\n  app:\n    build: .\n");

    const results = await detectBuildStrategy(tmpDir);
    expect(results.some(r => r.strategy === "docker-compose")).toBe(true);
  });

  it("detects Procfile (buildpack)", async () => {
    fs.writeFileSync(path.join(tmpDir, "Procfile"), "web: node server.js\n");

    const results = await detectBuildStrategy(tmpDir);
    expect(results.some(r => r.strategy === "heroku-buildpack")).toBe(true);
  });

  it("falls back to npm when only package.json exists", async () => {
    fs.writeFileSync(path.join(tmpDir, "package.json"), '{"name":"app"}');

    const results = await detectBuildStrategy(tmpDir);
    expect(results.some(r => r.strategy === "npm")).toBe(true);
  });
});

// ── Environment Variable Detection ──────────────────────────────────────────

describe("detectEnvVars", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-test-"));
  });

  it("detects env vars from .env.example", async () => {
    fs.writeFileSync(path.join(tmpDir, ".env.example"), [
      "DATABASE_URL=postgres://localhost/app",
      "REDIS_URL=",
      "# This is a comment",
      "SECRET_KEY=changeme # required secret key",
    ].join("\n"));

    const results = await detectEnvVars(tmpDir);
    expect(results).toHaveLength(3);
    expect(results.find(r => r.name === "DATABASE_URL")?.has_default).toBe(true);
    expect(results.find(r => r.name === "REDIS_URL")?.has_default).toBe(false);
    expect(results.find(r => r.name === "SECRET_KEY")?.description).toContain("secret key");
  });

  it("returns empty for no env files", async () => {
    const results = await detectEnvVars(tmpDir);
    expect(results).toHaveLength(0);
  });
});

// ── Port Detection ──────────────────────────────────────────────────────────

describe("detectPorts", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-test-"));
  });

  it("detects ports from Dockerfile EXPOSE", async () => {
    fs.writeFileSync(path.join(tmpDir, "Dockerfile"), "FROM node:18\nEXPOSE 3000\nEXPOSE 8080\n");

    const results = await detectPorts(tmpDir);
    expect(results.some(r => r.port === 3000)).toBe(true);
    expect(results.some(r => r.port === 8080)).toBe(true);
  });

  it("detects ports from docker-compose", async () => {
    fs.writeFileSync(path.join(tmpDir, "docker-compose.yml"), 'services:\n  app:\n    ports:\n      - "8080:3000"\n');

    const results = await detectPorts(tmpDir);
    expect(results.some(r => r.port === 8080)).toBe(true);
  });

  it("detects ports from code listen() calls", async () => {
    fs.mkdirSync(path.join(tmpDir, "src"), { recursive: true });
    fs.writeFileSync(path.join(tmpDir, "src", "index.ts"), 'app.listen(4000, () => console.log("ready"));\n');

    const results = await detectPorts(tmpDir);
    expect(results.some(r => r.port === 4000)).toBe(true);
  });
});

// ── Service Detection ───────────────────────────────────────────────────────

describe("detectServices", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-test-"));
  });

  it("detects services from docker-compose", async () => {
    fs.writeFileSync(path.join(tmpDir, "docker-compose.yml"),
      "services:\n  db:\n    image: postgres:15\n  cache:\n    image: redis:7\n");

    const results = await detectServices(tmpDir);
    expect(results.some(r => r.name === "PostgreSQL")).toBe(true);
    expect(results.some(r => r.name === "Redis")).toBe(true);
  });

  it("detects services from env files", async () => {
    fs.writeFileSync(path.join(tmpDir, ".env.example"), "DATABASE_URL=postgres://localhost/app\nREDIS_URL=redis://localhost:6379\n");

    const results = await detectServices(tmpDir);
    expect(results.some(r => r.name === "PostgreSQL")).toBe(true);
    expect(results.some(r => r.name === "Redis")).toBe(true);
  });
});

// ── Health Endpoint Detection ───────────────────────────────────────────────

describe("detectHealthEndpoint", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-test-"));
  });

  it("detects health endpoint from code", async () => {
    fs.mkdirSync(path.join(tmpDir, "src"), { recursive: true });
    fs.writeFileSync(path.join(tmpDir, "src", "app.ts"), `
      app.get("/health", (req, res) => res.json({ ok: true }));
    `);

    const results = await detectHealthEndpoint(tmpDir);
    expect(results.some(r => r.path === "/health")).toBe(true);
  });
});

// ── Repo Inspect ────────────────────────────────────────────────────────────

describe("repoInspect", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-test-"));
    fs.writeFileSync(path.join(tmpDir, "README.md"), "# Test App");
    fs.writeFileSync(path.join(tmpDir, "package.json"), '{"name":"test"}');
  });

  it("reads a file", async () => {
    const result = await repoInspect({ repo_path: tmpDir, action: "read_file", file_path: "README.md" });
    expect(result.data).toBe("# Test App");
  });

  it("rejects path traversal", async () => {
    const result = await repoInspect({ repo_path: tmpDir, action: "read_file", file_path: "../../etc/passwd" });
    expect((result.data as any).error).toContain("traversal");
  });

  it("builds a tree", async () => {
    const result = await repoInspect({ repo_path: tmpDir, action: "tree", max_depth: 1 });
    expect(Array.isArray(result.data)).toBe(true);
  });
});

// ── Ask User ────────────────────────────────────────────────────────────────

describe("askUser", () => {
  it("returns structured question", () => {
    const q = askUser("What port?", "Need port info", "app_port", ["3000", "8080"], "3000");
    expect(q.question).toBe("What port?");
    expect(q.field).toBe("app_port");
    expect(q.options).toEqual(["3000", "8080"]);
    expect(q.default).toBe("3000");
  });
});
