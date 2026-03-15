/**
 * Build Strategy Detector — clones a repo and detects build strategy.
 *
 * Used by the Quick Deploy flow to auto-detect how to build/run an app.
 */

import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

export interface DetectResult {
  strategy: "dockerfile" | "docker-compose" | "node" | "python" | "go" | "rust" | "static" | "unknown";
  detected_files: string[];
  suggested_build_cmd: string;
  suggested_start_cmd: string;
  framework?: string;
  port_hint?: number;
}

export async function detectBuildStrategy(repoUrl: string, branch: string): Promise<DetectResult> {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "bond-detect-"));

  try {
    execSync(`git clone --depth 1 --branch ${branch} ${repoUrl} ${tmpDir}`, {
      stdio: "pipe",
      timeout: 30_000,
    });

    const files = fs.readdirSync(tmpDir);
    const detected_files: string[] = [];

    // Check for key files
    const has = (name: string) => {
      if (files.includes(name)) {
        detected_files.push(name);
        return true;
      }
      return false;
    };

    // Docker Compose
    if (has("docker-compose.yml") || has("docker-compose.yaml") || has("compose.yml") || has("compose.yaml")) {
      return { strategy: "docker-compose", detected_files, suggested_build_cmd: "docker compose build", suggested_start_cmd: "docker compose up -d" };
    }

    // Dockerfile
    if (has("Dockerfile")) {
      return { strategy: "dockerfile", detected_files, suggested_build_cmd: "docker build -t app .", suggested_start_cmd: "docker run -d app", port_hint: 8080 };
    }

    // Node.js
    if (has("package.json")) {
      const pkg = JSON.parse(fs.readFileSync(path.join(tmpDir, "package.json"), "utf8"));
      const deps = { ...pkg.dependencies, ...pkg.devDependencies };
      let framework: string | undefined;
      let port_hint = 3000;
      let suggested_start_cmd = "npm start";
      let suggested_build_cmd = "npm ci && npm run build";

      if (deps["next"]) { framework = "next"; port_hint = 3000; suggested_start_cmd = "npm start"; }
      else if (deps["express"]) { framework = "express"; port_hint = 3000; }
      else if (deps["fastify"]) { framework = "fastify"; port_hint = 3000; }
      else if (deps["react"] && !deps["next"]) { framework = "react-spa"; port_hint = 3000; suggested_start_cmd = "npx serve -s build"; }
      else if (deps["vue"]) { framework = "vue-spa"; port_hint = 5173; suggested_start_cmd = "npx serve -s dist"; }

      if (pkg.scripts?.start) suggested_start_cmd = "npm start";
      if (!pkg.scripts?.build) suggested_build_cmd = "npm ci";

      return { strategy: "node", detected_files, suggested_build_cmd, suggested_start_cmd, framework, port_hint };
    }

    // Python
    if (has("requirements.txt") || has("pyproject.toml") || has("setup.py")) {
      const reqFile = files.includes("requirements.txt")
        ? fs.readFileSync(path.join(tmpDir, "requirements.txt"), "utf8")
        : "";
      let framework: string | undefined;
      let suggested_start_cmd = "python app.py";
      const port_hint = 8000;

      if (reqFile.includes("django")) { framework = "django"; suggested_start_cmd = "python manage.py runserver 0.0.0.0:8000"; }
      else if (reqFile.includes("flask")) { framework = "flask"; suggested_start_cmd = "flask run --host=0.0.0.0"; }
      else if (reqFile.includes("fastapi")) { framework = "fastapi"; suggested_start_cmd = "uvicorn app.main:app --host 0.0.0.0 --port 8000"; }

      const install = files.includes("requirements.txt") ? "pip install -r requirements.txt" : "pip install .";
      return { strategy: "python", detected_files, suggested_build_cmd: install, suggested_start_cmd, framework, port_hint };
    }

    // Go
    if (has("go.mod")) {
      return { strategy: "go", detected_files, suggested_build_cmd: "go build -o app .", suggested_start_cmd: "./app", port_hint: 8080 };
    }

    // Rust
    if (has("Cargo.toml")) {
      return { strategy: "rust", detected_files, suggested_build_cmd: "cargo build --release", suggested_start_cmd: "./target/release/app", port_hint: 8080 };
    }

    // Static site
    if (has("index.html")) {
      return { strategy: "static", detected_files, suggested_build_cmd: "", suggested_start_cmd: "npx serve .", port_hint: 3000 };
    }

    return { strategy: "unknown", detected_files, suggested_build_cmd: "", suggested_start_cmd: "" };
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}
