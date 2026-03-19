/**
 * Integration manager — stores and loads integration configs from disk.
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
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

function ensureDataDir(): void {
  const dir = dirname(DATA_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
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
