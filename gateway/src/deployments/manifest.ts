/**
 * Deployment Manifest — parsing, storage, and comparison of discovery manifests.
 *
 * Design Doc 044 §5 — Deployment Manifest
 */

import fs from "node:fs";
import path from "node:path";
import { homedir } from "node:os";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");
const MANIFESTS_DIR = path.join(DEPLOYMENTS_DIR, "discovery", "manifests");

// ── Types ───────────────────────────────────────────────────────────────────

export interface DeploymentManifest {
  manifest_version: string;
  application: string;
  discovered_at: string;
  discovered_by: string;
  entrypoint?: {
    domain?: string;
    dns_provider?: string;
    hosted_zone_id?: string;
    records?: any[];
    ssl?: Record<string, any>;
    cdn?: Record<string, any>;
  };
  servers: ManifestServer[];
  topology?: {
    nodes: Array<{ id: string; type: string; host?: string; [key: string]: any }>;
    edges: Array<{ from: string; to: string; protocol?: string; port?: number }>;
  };
  security_observations: Array<{ severity: string; message: string }>;
}

export interface ManifestServer {
  name: string;
  host: string;
  os?: string;
  role?: string;
  system?: { cpu_cores?: number; memory_gb?: number; disk_gb?: number };
  web_server?: Record<string, any>;
  application?: Record<string, any>;
  services?: Record<string, any>;
}

export interface ManifestDiff {
  added_servers: string[];
  removed_servers: string[];
  changed_servers: Array<{ name: string; changes: string[] }>;
  added_observations: string[];
  removed_observations: string[];
  topology_changed: boolean;
  entrypoint_changed: boolean;
}

// ── Storage ─────────────────────────────────────────────────────────────────

export function writeManifest(manifest: DeploymentManifest): string {
  fs.mkdirSync(MANIFESTS_DIR, { recursive: true });
  const name = manifest.application || "unknown";
  const filePath = path.join(MANIFESTS_DIR, `${name}.json`);
  fs.writeFileSync(filePath, JSON.stringify(manifest, null, 2));
  return filePath;
}

export function readManifest(name: string): DeploymentManifest | null {
  const filePath = path.join(MANIFESTS_DIR, `${name}.json`);
  if (!fs.existsSync(filePath)) return null;
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

export function listManifests(): string[] {
  if (!fs.existsSync(MANIFESTS_DIR)) return [];
  return fs.readdirSync(MANIFESTS_DIR)
    .filter(f => f.endsWith(".json"))
    .map(f => f.replace(/\.json$/, ""));
}

// ── Comparison ──────────────────────────────────────────────────────────────

export function diffManifests(a: DeploymentManifest, b: DeploymentManifest): ManifestDiff {
  const aServerNames = new Set(a.servers.map(s => s.name));
  const bServerNames = new Set(b.servers.map(s => s.name));

  const added_servers = [...bServerNames].filter(n => !aServerNames.has(n));
  const removed_servers = [...aServerNames].filter(n => !bServerNames.has(n));

  const changed_servers: ManifestDiff["changed_servers"] = [];
  for (const name of aServerNames) {
    if (!bServerNames.has(name)) continue;
    const sa = a.servers.find(s => s.name === name)!;
    const sb = b.servers.find(s => s.name === name)!;
    const changes: string[] = [];
    if (sa.host !== sb.host) changes.push(`host: ${sa.host} → ${sb.host}`);
    if (sa.os !== sb.os) changes.push(`os: ${sa.os} → ${sb.os}`);
    if (sa.role !== sb.role) changes.push(`role: ${sa.role} → ${sb.role}`);
    if (JSON.stringify(sa.system) !== JSON.stringify(sb.system)) changes.push("system resources changed");
    if (JSON.stringify(sa.web_server) !== JSON.stringify(sb.web_server)) changes.push("web server config changed");
    if (JSON.stringify(sa.application) !== JSON.stringify(sb.application)) changes.push("application config changed");
    if (JSON.stringify(sa.services) !== JSON.stringify(sb.services)) changes.push("services changed");
    if (changes.length > 0) changed_servers.push({ name, changes });
  }

  const aObs = new Set(a.security_observations.map(o => o.message));
  const bObs = new Set(b.security_observations.map(o => o.message));
  const added_observations = [...bObs].filter(m => !aObs.has(m));
  const removed_observations = [...aObs].filter(m => !bObs.has(m));

  const topology_changed = JSON.stringify(a.topology) !== JSON.stringify(b.topology);
  const entrypoint_changed = JSON.stringify(a.entrypoint) !== JSON.stringify(b.entrypoint);

  return {
    added_servers,
    removed_servers,
    changed_servers,
    added_observations,
    removed_observations,
    topology_changed,
    entrypoint_changed,
  };
}
