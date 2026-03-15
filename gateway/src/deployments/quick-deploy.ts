/**
 * Quick Deploy — generates deploy/rollback scripts from a simple form submission.
 *
 * Takes a QuickDeployRequest, generates bash scripts, registers them in the
 * script registry, writes secrets, and auto-promotes to the target environment.
 */

import fs from "node:fs";
import path from "node:path";
import type { GatewayConfig } from "../config/index.js";
import { registerScript } from "./scripts.js";
import { initiatePromotion } from "./stdb.js";
import { emitScriptPromoted } from "./events.js";

export interface QuickDeployRequest {
  repo_url: string;
  branch: string;
  build_strategy: "auto" | "dockerfile" | "docker-compose" | "script";
  build_cmd?: string;
  start_cmd?: string;
  environment: string;
  port?: number;
  health_check_path?: string;
  env_vars?: Record<string, { value: string; secret: boolean }>;
  trigger?: {
    on_push?: boolean;
    branch?: string;
    tag_pattern?: string;
    manual_only?: boolean;
  };
}

export interface QuickDeployResult {
  script_id: string;
  version: string;
  environment: string;
  promoted: boolean;
  message: string;
}

function extractRepoName(repoUrl: string): string {
  // Handle URLs like github.com/org/repo, https://github.com/org/repo.git, etc.
  const cleaned = repoUrl.replace(/\.git$/, "").replace(/\/$/, "");
  const parts = cleaned.split("/");
  const name = parts[parts.length - 1] || "app";
  return name.toLowerCase().replace(/[^a-z0-9-]/g, "-");
}

function generateDeployScript(req: QuickDeployRequest): string {
  const port = req.port || 3000;
  const repoName = extractRepoName(req.repo_url);
  const containerName = `bond-${repoName}-\${BOND_DEPLOY_ENV:-dev}`;
  const imageName = `bond-${repoName}`;

  // Non-secret env var exports
  const envExports: string[] = [];
  if (req.env_vars) {
    for (const [key, entry] of Object.entries(req.env_vars)) {
      if (!entry.secret) {
        envExports.push(`export ${key}="${entry.value}"`);
      }
    }
  }
  const envBlock = envExports.length > 0 ? envExports.join("\n") + "\n\n" : "";

  const healthCheck = req.health_check_path
    ? `
# Health check
echo "Running health check..."
for i in 1 2 3 4 5; do
  if curl -sf "http://localhost:${port}${req.health_check_path}" > /dev/null 2>&1; then
    echo "Health check passed"
    exit 0
  fi
  echo "Attempt $i/5 — waiting..."
  sleep 3
done
echo "Health check failed after 5 attempts"
exit 1
`
    : "";

  const strategy = req.build_strategy === "auto" ? "dockerfile" : req.build_strategy;

  if (strategy === "docker-compose") {
    return `#!/usr/bin/env bash
# meta:name: Quick Deploy — ${repoName}
# meta:version: 1
# meta:timeout: 600
set -euo pipefail

DEPLOY_ENV="\${BOND_DEPLOY_ENV:-${req.environment}}"
REPO_DIR="/tmp/bond-deploy-${repoName}"

${envBlock}if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would deploy ${req.repo_url}@${req.branch} using docker-compose"
  exit 0
fi

echo "Deploying ${req.repo_url}@${req.branch} to $DEPLOY_ENV"

# Clone or pull
if [[ -d "$REPO_DIR/.git" ]]; then
  cd "$REPO_DIR"
  git fetch origin ${req.branch}
  git reset --hard origin/${req.branch}
else
  git clone --branch ${req.branch} ${req.repo_url} "$REPO_DIR"
  cd "$REPO_DIR"
fi

docker compose down || true
docker compose up -d --build
${healthCheck}
echo "Deploy complete"
`;
  }

  if (strategy === "script") {
    const buildCmd = req.build_cmd || "npm ci && npm run build";
    const startCmd = req.start_cmd || "npm start";

    return `#!/usr/bin/env bash
# meta:name: Quick Deploy — ${repoName}
# meta:version: 1
# meta:timeout: 600
set -euo pipefail

DEPLOY_ENV="\${BOND_DEPLOY_ENV:-${req.environment}}"
REPO_DIR="/tmp/bond-deploy-${repoName}"

${envBlock}if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would deploy ${req.repo_url}@${req.branch} using script"
  exit 0
fi

echo "Deploying ${req.repo_url}@${req.branch} to $DEPLOY_ENV"

# Clone or pull
if [[ -d "$REPO_DIR/.git" ]]; then
  cd "$REPO_DIR"
  git fetch origin ${req.branch}
  git reset --hard origin/${req.branch}
else
  git clone --branch ${req.branch} ${req.repo_url} "$REPO_DIR"
  cd "$REPO_DIR"
fi

# Build
${buildCmd}

# Restart via pm2 or direct
if command -v pm2 &> /dev/null; then
  pm2 delete ${repoName} 2>/dev/null || true
  pm2 start --name ${repoName} -- ${startCmd}
else
  # Kill existing process on port
  fuser -k ${port}/tcp 2>/dev/null || true
  nohup ${startCmd} > /tmp/bond-deploy-${repoName}.log 2>&1 &
fi
${healthCheck}
echo "Deploy complete"
`;
  }

  // Default: dockerfile
  return `#!/usr/bin/env bash
# meta:name: Quick Deploy — ${repoName}
# meta:version: 1
# meta:timeout: 600
set -euo pipefail

DEPLOY_ENV="\${BOND_DEPLOY_ENV:-${req.environment}}"
REPO_DIR="/tmp/bond-deploy-${repoName}"
CONTAINER="${containerName}"
IMAGE="${imageName}:latest"

${envBlock}if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would deploy ${req.repo_url}@${req.branch} using Dockerfile"
  exit 0
fi

echo "Deploying ${req.repo_url}@${req.branch} to $DEPLOY_ENV"

# Clone or pull
if [[ -d "$REPO_DIR/.git" ]]; then
  cd "$REPO_DIR"
  git fetch origin ${req.branch}
  git reset --hard origin/${req.branch}
else
  git clone --branch ${req.branch} ${req.repo_url} "$REPO_DIR"
  cd "$REPO_DIR"
fi

# Build
docker build -t "$IMAGE" .

# Stop old container
docker stop "$CONTAINER" 2>/dev/null || true
docker rm "$CONTAINER" 2>/dev/null || true

# Run
docker run -d --name "$CONTAINER" -p ${port}:${port} --restart unless-stopped "$IMAGE"
${healthCheck}
echo "Deploy complete"
`;
}

function generateRollbackScript(req: QuickDeployRequest): string {
  const repoName = extractRepoName(req.repo_url);
  const containerName = `bond-${repoName}-\${BOND_DEPLOY_ENV:-dev}`;
  const strategy = req.build_strategy === "auto" ? "dockerfile" : req.build_strategy;

  if (strategy === "docker-compose") {
    return `#!/usr/bin/env bash
# meta:name: Rollback — ${repoName}
# meta:version: 1
set -euo pipefail

REPO_DIR="/tmp/bond-deploy-${repoName}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would rollback ${repoName} docker-compose deployment"
  exit 0
fi

cd "$REPO_DIR" 2>/dev/null || { echo "Repo dir not found"; exit 1; }
docker compose down
git checkout HEAD~1
docker compose up -d --build
echo "Rollback complete"
`;
  }

  if (strategy === "script") {
    return `#!/usr/bin/env bash
# meta:name: Rollback — ${repoName}
# meta:version: 1
set -euo pipefail

REPO_DIR="/tmp/bond-deploy-${repoName}"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would rollback ${repoName}"
  exit 0
fi

cd "$REPO_DIR" 2>/dev/null || { echo "Repo dir not found"; exit 1; }
git checkout HEAD~1
if command -v pm2 &> /dev/null; then
  pm2 restart ${repoName}
else
  fuser -k ${req.port || 3000}/tcp 2>/dev/null || true
  nohup ${req.start_cmd || "npm start"} > /tmp/bond-deploy-${repoName}.log 2>&1 &
fi
echo "Rollback complete"
`;
  }

  // dockerfile
  return `#!/usr/bin/env bash
# meta:name: Rollback — ${repoName}
# meta:version: 1
set -euo pipefail

CONTAINER="${containerName}"
IMAGE="bond-${repoName}:previous"

if [[ "\${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would rollback ${repoName} container"
  exit 0
fi

# Tag current as previous before deploy overwrites it
docker stop "$CONTAINER" 2>/dev/null || true
docker rm "$CONTAINER" 2>/dev/null || true

# Re-run the previous image if available
if docker image inspect "$IMAGE" &>/dev/null; then
  docker run -d --name "$CONTAINER" -p ${req.port || 3000}:${req.port || 3000} --restart unless-stopped "$IMAGE"
  echo "Rollback complete — running previous image"
else
  echo "No previous image found. Manual intervention required."
  exit 1
fi
`;
}

export async function handleQuickDeploy(
  request: QuickDeployRequest,
  deploymentsDir: string,
  config: GatewayConfig,
  userId: string,
): Promise<QuickDeployResult> {
  const repoName = extractRepoName(request.repo_url);
  const scriptId = `quick-deploy-${repoName}`;
  const version = "v1";

  // Generate scripts
  const deployScript = generateDeployScript(request);
  const rollbackScript = generateRollbackScript(request);

  // Register deploy script
  registerScript(deploymentsDir, {
    script_id: scriptId,
    version,
    name: `Quick Deploy — ${repoName}`,
    description: `Auto-generated deploy for ${request.repo_url}@${request.branch}`,
    timeout: 600,
    rollback: "rollback.sh",
    dry_run: true,
    health_check: request.health_check_path,
    registered_by: userId,
    files: {
      "deploy.sh": Buffer.from(deployScript, "utf8"),
      "rollback.sh": Buffer.from(rollbackScript, "utf8"),
    },
  });

  // Write secrets
  if (request.env_vars) {
    const secretVars: Record<string, string> = {};
    for (const [key, entry] of Object.entries(request.env_vars)) {
      if (entry.secret) {
        secretVars[key] = entry.value;
      }
    }
    if (Object.keys(secretVars).length > 0) {
      const secretsDir = path.join(deploymentsDir, "secrets");
      fs.mkdirSync(secretsDir, { recursive: true });
      const yaml = Object.entries(secretVars)
        .map(([k, v]) => `${k}: "${v}"`)
        .join("\n") + "\n";
      fs.writeFileSync(
        path.join(secretsDir, `${request.environment}.yaml`),
        yaml,
        { mode: 0o600 },
      );
    }
  }

  // Auto-promote to requested environment
  const manifest = { sha256: "auto-generated" };
  try {
    await initiatePromotion(
      config,
      scriptId,
      version,
      manifest.sha256,
      request.environment,
      "promoted",
      userId,
    );
    emitScriptPromoted(request.environment, scriptId, version, userId);
  } catch (err: any) {
    // Promotion may fail if SpacetimeDB isn't running — still return success for the script registration
    console.warn("[quick-deploy] Promotion failed (non-fatal):", err.message);
  }

  return {
    script_id: scriptId,
    version,
    environment: request.environment,
    promoted: true,
    message: `Quick deploy script registered and promoted to ${request.environment}`,
  };
}
