/**
 * Integration manager — stores and loads integration configs from disk.
 */
import { readFileSync, writeFileSync, existsSync, copyFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { mkdirSync } from "node:fs";

export interface SolidTimeConfig {
  type: "solidtime";
  enabled: boolean;
  url: string;
  apiToken: string;
  organizationId: string;
  memberId: string;
  organizationName?: string;
  userName?: string;
}

export interface IntegrationsConfig {
  solidtime?: SolidTimeConfig;
}

const DATA_PATH = join(dirname(new URL(import.meta.url).pathname), "../../data/integrations.json");
// Agent containers mount project_root/data/shared/ at /data/shared (read-only).
// We sync integrations.json there so dynamic tools can read it without MCP servers.
const SHARED_PATH = join(dirname(new URL(import.meta.url).pathname), "../../data/shared/integrations.json");

function ensureDataDir(): void {
  const dir = dirname(DATA_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

function syncToShared(): void {
  try {
    const sharedDir = dirname(SHARED_PATH);
    if (!existsSync(sharedDir)) mkdirSync(sharedDir, { recursive: true });
    if (existsSync(DATA_PATH)) {
      copyFileSync(DATA_PATH, SHARED_PATH);
    }
  } catch {
    // Non-critical — shared dir may not exist in dev/test
  }
}

export function loadIntegrations(): IntegrationsConfig {
  try {
    if (existsSync(DATA_PATH)) {
      return JSON.parse(readFileSync(DATA_PATH, "utf-8"));
    }
  } catch {
    // corrupt file — return empty
  }
  return {};
}

export function saveIntegrations(config: IntegrationsConfig): void {
  ensureDataDir();
  writeFileSync(DATA_PATH, JSON.stringify(config, null, 2), "utf-8");
  syncToShared();
}

export function getSolidTimeConfig(): SolidTimeConfig | null {
  const config = loadIntegrations();
  return config.solidtime ?? null;
}

export function setSolidTimeConfig(solidtime: SolidTimeConfig): void {
  const config = loadIntegrations();
  config.solidtime = solidtime;
  saveIntegrations(config);
}

export function removeSolidTimeConfig(): void {
  const config = loadIntegrations();
  delete config.solidtime;
  saveIntegrations(config);
}
