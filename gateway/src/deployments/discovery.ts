/**
 * Discovery Orchestration — runs discovery layers on remote servers via SSH.
 *
 * Design Doc 044 §2 — Remote Application Discovery
 */

import fs from "node:fs";
import path from "node:path";
import { homedir } from "node:os";
import { getResource } from "./resources.js";
import { loadSecrets } from "./secrets.js";
import { executeSshScript } from "./discovery-scripts.js";
import { writeManifest } from "./manifest.js";
import { emitDeploymentEvent } from "./events.js";
import type { GatewayConfig } from "../config/index.js";
import type { DeploymentManifest } from "./manifest.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");
const DISCOVERY_SCRIPTS_DIR = path.join(DEPLOYMENTS_DIR, "discovery", "scripts");

const DEFAULT_LAYERS = [
  "01-system-overview",
  "02-web-server",
  "03-application",
  "04-data-stores",
  "05-dns-networking",
];

export interface DiscoveryResult {
  status: "ok" | "denied" | "error";
  action: string;
  environment?: string;
  reason?: string;
  info?: any;
}

/**
 * Run discovery layers on a remote resource via SSH.
 *
 * Environment scoping: the resource must belong to the agent's environment.
 * This prevents deploy-dev from discovering prod resources.
 */
export async function runDiscovery(
  cfg: GatewayConfig,
  resourceId: string,
  env: string,
  layers?: string[],
): Promise<DiscoveryResult> {
  const resource = await getResource(cfg, resourceId);
  if (!resource) {
    return { status: "denied", action: "discover", reason: "Resource not found" };
  }

  // Enforce environment scoping — agent can only discover resources in its own environment
  if (resource.environment !== env) {
    return {
      status: "denied",
      action: "discover",
      reason: `Resource '${resource.name}' belongs to environment '${resource.environment}', not '${env}'. Agents can only discover resources in their own environment.`,
    };
  }

  let conn: any;
  try {
    conn = JSON.parse(resource.connection_json);
  } catch {
    return { status: "error", action: "discover", reason: "Invalid connection JSON" };
  }

  if (!conn.host) {
    return { status: "denied", action: "discover", reason: "Discovery requires SSH connection with host" };
  }

  const secrets = loadSecrets(env);
  const targetLayers = layers || DEFAULT_LAYERS;
  const results: Record<string, any> = {};

  for (const layer of targetLayers) {
    const scriptPath = path.join(DISCOVERY_SCRIPTS_DIR, `${layer}.sh`);
    if (!fs.existsSync(scriptPath)) {
      results[layer] = { skipped: true, reason: "Script not found" };
      continue;
    }

    const scriptContent = fs.readFileSync(scriptPath, "utf8");
    const result = await executeSshScript(
      conn.host,
      conn.port || 22,
      conn.user || "deploy",
      scriptContent,
      { ...secrets, BOND_DEPLOY_ENV: env },
      conn.key_path,
      60,
    );

    if (result.exit_code === 0) {
      try {
        results[layer] = JSON.parse(result.stdout);
      } catch {
        results[layer] = { raw: result.stdout };
      }
    } else {
      results[layer] = { error: result.stderr, exit_code: result.exit_code };
    }
  }

  // Build manifest from discovery results
  const manifest: DeploymentManifest = {
    manifest_version: "1.0",
    application: resource.name,
    discovered_at: new Date().toISOString(),
    discovered_by: `deploy-${env}`,
    servers: [{
      name: resource.name,
      host: conn.host,
      ...extractServerDetails(results),
    }],
    topology: results["05-dns-networking"]?.topology || { nodes: [], edges: [] },
    security_observations: extractSecurityObservations(results),
  };

  // Store manifest
  writeManifest(manifest);

  emitDeploymentEvent("discovery_completed" as any, {
    environment: env,
    summary: `Discovery completed for ${resource.name}: ${targetLayers.length} layer(s)`,
    details: { resource_id: resourceId, layers: targetLayers },
  });

  return {
    status: "ok",
    action: "discover",
    environment: env,
    info: { manifest, layers: results },
  };
}

function extractServerDetails(results: Record<string, any>): Partial<import("./manifest.js").ManifestServer> {
  const system = results["01-system-overview"];
  const web = results["02-web-server"];
  const app = results["03-application"];
  const data = results["04-data-stores"];
  return {
    os: system?.os,
    role: app ? "application" : "unknown",
    system: system?.system,
    web_server: web,
    application: app,
    services: data,
  };
}

function extractSecurityObservations(results: Record<string, any>): Array<{ severity: string; message: string }> {
  const observations: Array<{ severity: string; message: string }> = [];
  for (const layerResult of Object.values(results)) {
    if (layerResult && Array.isArray(layerResult.security_observations)) {
      observations.push(...layerResult.security_observations);
    }
  }
  return observations;
}
