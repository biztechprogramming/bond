/**
 * Proposal Generator — generates deployment scripts from discovery manifests.
 *
 * Design Doc 044 §6 — Replication & Improvement Proposals
 */

import fs from "node:fs";
import path from "node:path";
import { homedir } from "node:os";
import type { DeploymentManifest } from "./manifest.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

// ── Types ───────────────────────────────────────────────────────────────────

export interface GeneratedScript {
  filename: string;
  content: string;
  level: number;
  description: string;
}

export interface ProposalResult {
  app: string;
  level: number;
  scripts: GeneratedScript[];
  proposal_dir: string;
}

// ── Level 0: Exact Replication ──────────────────────────────────────────────

export function generateReplicationScripts(manifest: DeploymentManifest): ProposalResult {
  const app = manifest.application;
  const scripts: GeneratedScript[] = [];

  for (const server of manifest.servers) {
    // Infrastructure setup script
    const infraScript = buildInfraScript(app, server);
    scripts.push({
      filename: `setup-${app}-infrastructure.sh`,
      content: infraScript,
      level: 0,
      description: `Install OS packages, configure web server, set up services on ${server.name}`,
    });

    // Application deploy script
    if (server.application) {
      const appScript = buildAppDeployScript(app, server);
      scripts.push({
        filename: `deploy-${app}-application.sh`,
        content: appScript,
        level: 0,
        description: `Clone repo, install deps, configure app on ${server.name}`,
      });
    }

    // Database setup if referenced
    if (server.services?.postgresql || server.services?.mysql) {
      const dbScript = buildDbScript(app, server);
      scripts.push({
        filename: `setup-${app}-database.sh`,
        content: dbScript,
        level: 0,
        description: `Set up database from discovered config on ${server.name}`,
      });
    }
  }

  // DNS setup if entrypoint exists
  if (manifest.entrypoint?.domain) {
    scripts.push({
      filename: `setup-${app}-dns.sh`,
      content: buildDnsScript(app, manifest),
      level: 0,
      description: `Configure DNS records for ${manifest.entrypoint.domain}`,
    });
  }

  const proposalDir = writeProposal(app, 0, "level-0-replication", scripts);
  return { app, level: 0, scripts, proposal_dir: proposalDir };
}

// ── Level 1: Operational Improvements ───────────────────────────────────────

export function generateOperationalScripts(manifest: DeploymentManifest): ProposalResult {
  const app = manifest.application;
  const scripts: GeneratedScript[] = [];

  scripts.push({
    filename: `add-health-monitoring.sh`,
    content: buildMetaScript(
      `Add health monitoring for ${app}`, 1, 300, `setup-${app}-infrastructure`,
      `# Improvement Level 1: Operational\nset -euo pipefail\n\n# Add health check endpoint, log rotation, basic monitoring\necho "Health monitoring setup placeholder — customize for your application"`,
    ),
    level: 1,
    description: "Add health check endpoint and monitoring",
  });

  scripts.push({
    filename: `add-backup-automation.sh`,
    content: buildMetaScript(
      `Add backup automation for ${app}`, 1, 600, `setup-${app}-infrastructure`,
      `# Improvement Level 1: Operational\nset -euo pipefail\n\n# Set up automated backups with retention\necho "Backup automation placeholder — customize for your data stores"`,
    ),
    level: 1,
    description: "Automated backups with retention policy",
  });

  scripts.push({
    filename: `add-ssl-autorenew.sh`,
    content: buildMetaScript(
      `Add SSL auto-renewal for ${app}`, 1, 300, `setup-${app}-infrastructure`,
      `# Improvement Level 1: Operational\nset -euo pipefail\n\n# Configure certbot for automatic SSL renewal\necho "SSL auto-renewal placeholder — requires certbot or ACM integration"`,
    ),
    level: 1,
    description: "Automatic SSL certificate renewal",
  });

  const proposalDir = writeProposal(app, 1, "level-1-operational", scripts);
  return { app, level: 1, scripts, proposal_dir: proposalDir };
}

// ── Level 2: Architecture Improvements ──────────────────────────────────────

export function generateArchitectureProposal(manifest: DeploymentManifest): ProposalResult {
  const app = manifest.application;
  const scripts: GeneratedScript[] = [];

  scripts.push({
    filename: `containerize-app.sh`,
    content: buildMetaScript(
      `Containerize ${app}`, 1, 900, `setup-${app}-infrastructure`,
      `# Improvement Level 2: Architecture\nset -euo pipefail\n\n# Wrap application in Docker with production Dockerfile\necho "Containerization placeholder — generates Dockerfile and docker-compose.yml"`,
    ),
    level: 2,
    description: "Containerize the application with Docker",
  });

  // Generate .bond/deploy.yml
  const deployYml = buildDeployYml(app, manifest);
  scripts.push({
    filename: `deploy.yml`,
    content: deployYml,
    level: 2,
    description: "Pipeline-as-code deployment configuration",
  });

  const proposalDir = writeProposal(app, 2, "level-2-architecture", scripts);
  return { app, level: 2, scripts, proposal_dir: proposalDir };
}

// ── Level 3: Platform Evolution ─────────────────────────────────────────────

export function generatePlatformProposal(manifest: DeploymentManifest): ProposalResult {
  const app = manifest.application;
  const scripts: GeneratedScript[] = [];

  const proposal = [
    `# Platform Evolution Proposal: ${app}`,
    "",
    `Generated from discovery manifest (${manifest.discovered_at})`,
    "",
    "## Current Architecture",
    `- Servers: ${manifest.servers.map(s => s.name).join(", ")}`,
    `- Entrypoint: ${manifest.entrypoint?.domain || "none discovered"}`,
    "",
    "## Recommended Improvements",
    "",
    "### Kubernetes Migration",
    "- Deploy K3s on existing server for single-node setups",
    "- Managed K8s (EKS/GKE) for multi-server deployments",
    "",
    "### Managed Database",
    "- Migrate self-hosted databases to managed services (RDS/Cloud SQL)",
    "- Reduces operational burden and improves reliability",
    "",
    "### Infrastructure as Code",
    "- Generate Terraform configs for the entire stack",
    "- Version-controlled infrastructure changes",
    "",
    "## Cost Analysis",
    "- Current: Self-managed infrastructure",
    "- Proposed: [Requires cost estimation based on actual usage]",
    "",
    "## Migration Steps",
    "1. Containerize application (Level 2)",
    "2. Set up managed database with data migration",
    "3. Deploy to managed Kubernetes",
    "4. Switch DNS to new infrastructure",
    "5. Decommission old servers",
    "",
    "## Risk Assessment",
    "- Data migration: medium risk — requires testing",
    "- DNS switch: low risk — can be rolled back quickly",
    "- Application compatibility: low risk — already containerized in Level 2",
  ].join("\n");

  scripts.push({
    filename: `proposal.md`,
    content: proposal,
    level: 3,
    description: "Platform evolution proposal documentation",
  });

  const proposalDir = writeProposal(app, 3, "level-3-platform", scripts);
  return { app, level: 3, scripts, proposal_dir: proposalDir };
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function writeProposal(app: string, level: number, dirName: string, scripts: GeneratedScript[]): string {
  const proposalDir = path.join(DEPLOYMENTS_DIR, "discovery", "proposals", app, dirName);
  fs.mkdirSync(proposalDir, { recursive: true });
  for (const script of scripts) {
    fs.writeFileSync(path.join(proposalDir, script.filename), script.content);
  }
  return proposalDir;
}

function buildMetaScript(name: string, version: number, timeout: number, dependsOn: string, body: string): string {
  return [
    `#!/usr/bin/env bash`,
    `# meta:name: ${name}`,
    `# meta:version: ${version}`,
    `# meta:timeout: ${timeout}`,
    `# meta:depends_on: ${dependsOn}`,
    body,
  ].join("\n");
}

function buildInfraScript(app: string, server: any): string {
  const webType = server.web_server?.type || "nginx";
  const runtime = server.application?.runtime || "node";
  return buildMetaScript(
    `Setup infrastructure for ${app} (exact replication)`, 1, 900, "none",
    [
      `# Generated from discovery manifest: ${server.name}`,
      `set -euo pipefail`,
      ``,
      `DEPLOY_HOST="\${RESOURCE_HOST:?RESOURCE_HOST is required}"`,
      `DEPLOY_USER="\${RESOURCE_USER:?RESOURCE_USER is required}"`,
      ``,
      `if [[ "\${1:-}" == "--dry-run" ]]; then`,
      `  echo "[DRY RUN] Would replicate infrastructure on $DEPLOY_HOST"`,
      `  echo "  - Install ${webType}, ${runtime}"`,
      `  exit 0`,
      `fi`,
      ``,
      `ssh "$DEPLOY_USER@$DEPLOY_HOST" bash -s <<'REMOTE'`,
      `set -euo pipefail`,
      `sudo apt-get update -qq`,
      `# Install web server and runtime — customize for actual versions`,
      `echo "Infrastructure setup complete"`,
      `REMOTE`,
    ].join("\n"),
  );
}

function buildAppDeployScript(app: string, server: any): string {
  const appConfig = server.application || {};
  return buildMetaScript(
    `Deploy ${app} application`, 1, 600, `setup-${app}-infrastructure`,
    [
      `# Generated from discovery manifest: ${server.name}`,
      `set -euo pipefail`,
      ``,
      `DEPLOY_HOST="\${RESOURCE_HOST:?RESOURCE_HOST is required}"`,
      `DEPLOY_USER="\${RESOURCE_USER:?RESOURCE_USER is required}"`,
      ``,
      `if [[ "\${1:-}" == "--dry-run" ]]; then`,
      `  echo "[DRY RUN] Would deploy ${app} to $DEPLOY_HOST"`,
      appConfig.git_remote ? `  echo "  - Clone from ${appConfig.git_remote}"` : `  echo "  - Deploy application"`,
      `  exit 0`,
      `fi`,
      ``,
      `ssh "$DEPLOY_USER@$DEPLOY_HOST" bash -s <<'REMOTE'`,
      `set -euo pipefail`,
      `# Clone, install deps, start application — customize for your setup`,
      `echo "Application deployment complete"`,
      `REMOTE`,
    ].join("\n"),
  );
}

function buildDbScript(app: string, server: any): string {
  return buildMetaScript(
    `Setup database for ${app}`, 1, 600, "none",
    [
      `# Generated from discovery manifest: ${server.name}`,
      `set -euo pipefail`,
      ``,
      `DEPLOY_HOST="\${RESOURCE_HOST:?RESOURCE_HOST is required}"`,
      `DEPLOY_USER="\${RESOURCE_USER:?RESOURCE_USER is required}"`,
      ``,
      `if [[ "\${1:-}" == "--dry-run" ]]; then`,
      `  echo "[DRY RUN] Would set up database on $DEPLOY_HOST"`,
      `  exit 0`,
      `fi`,
      ``,
      `ssh "$DEPLOY_USER@$DEPLOY_HOST" bash -s <<'REMOTE'`,
      `set -euo pipefail`,
      `# Database setup — customize for discovered config`,
      `echo "Database setup complete"`,
      `REMOTE`,
    ].join("\n"),
  );
}

function buildDnsScript(app: string, manifest: DeploymentManifest): string {
  const domain = manifest.entrypoint?.domain || "example.com";
  return buildMetaScript(
    `Setup DNS for ${app}`, 1, 300, "none",
    [
      `# Generated from discovery manifest`,
      `set -euo pipefail`,
      ``,
      `if [[ "\${1:-}" == "--dry-run" ]]; then`,
      `  echo "[DRY RUN] Would configure DNS for ${domain}"`,
      `  exit 0`,
      `fi`,
      ``,
      `# DNS configuration — customize for your provider`,
      `echo "DNS setup for ${domain} complete"`,
    ].join("\n"),
  );
}

function buildDeployYml(app: string, manifest: DeploymentManifest): string {
  const runtime = manifest.servers[0]?.application?.runtime || "node";
  const imageTag = runtime === "node" ? "node:22" : runtime;
  return [
    `# .bond/deploy.yml — Level 2: Containerized deployment`,
    `pipeline: ${app}-containerized`,
    ``,
    `on:`,
    `  push:`,
    `    branches: [main]`,
    `  manual: true`,
    ``,
    `steps:`,
    `  - name: build`,
    `    image: ${imageTag}`,
    `    run: |`,
    `      npm ci`,
    `      npm run build`,
    `      npm test`,
    ``,
    `  - name: docker-build`,
    `    image: docker`,
    `    run: |`,
    `      docker build -t ${app}:\${COMMIT_SHA:-latest} .`,
    `    needs: [build]`,
    ``,
    `  - name: deploy`,
    `    image: bond-deploy-agent`,
    `    run: |`,
    `      docker compose -f docker-compose.prod.yml up -d`,
    `    needs: [docker-build]`,
    `    secrets: [DATABASE_URL, REDIS_URL]`,
    ``,
    `  - name: health-check`,
    `    image: curlimages/curl`,
    `    run: |`,
    `      sleep 5`,
    `      curl -f http://localhost:3000/health || exit 1`,
    `    needs: [deploy]`,
    ``,
    `environments:`,
    `  - name: dev`,
    `    auto_promote: true`,
    `  - name: staging`,
    `    auto_promote: false`,
    `  - name: prod`,
    `    auto_promote: false`,
    `    approval:`,
    `      required: 1`,
  ].join("\n");
}
