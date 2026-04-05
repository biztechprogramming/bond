/**
 * Gateway configuration.
 *
 * Single source of truth: bond.json (project root).
 * Environment variables override bond.json values.
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { randomBytes } from "crypto";
import { homedir } from "os";
import { join, resolve } from "path";

export interface WebhooksConfig {
  /** Explicit list of repos to register webhooks for (overrides mount-based discovery). */
  repos?: string[];
}

export interface GatewayConfig {
  host: string;
  port: number;
  backendUrl: string;
  frontendOrigin: string;
  spacetimedbUrl: string;
  spacetimedbModuleName: string;
  spacetimedbToken: string;
  webhooks?: WebhooksConfig;
  apiKey: string;
}

/** Walk up from cwd to find bond.json */
function findBondJson(): Record<string, any> {
  let dir = process.cwd();
  while (true) {
    const candidate = join(dir, "bond.json");
    if (existsSync(candidate)) {
      try {
        return JSON.parse(readFileSync(candidate, "utf8"));
      } catch { return {}; }
    }
    const parent = resolve(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return {};
}

/** Resolve the Bond API key: env var > file > auto-generate. */
function resolveApiKey(): string {
  if (process.env.BOND_API_KEY) return process.env.BOND_API_KEY;
  const keyDir = join(homedir(), ".bond", "data");
  const keyPath = join(keyDir, ".gateway_key");
  if (existsSync(keyPath)) {
    const key = readFileSync(keyPath, "utf8").trim();
    if (key) return key;
  }
  // Auto-generate
  mkdirSync(keyDir, { recursive: true });
  const key = randomBytes(32).toString("hex"); // 64 hex chars
  writeFileSync(keyPath, key, { mode: 0o600 });
  console.log(`[config] Generated new API key at ${keyPath}`);
  // Make available to all internal modules via env
  process.env.BOND_API_KEY = key;
  return key;
}

/** Read the SpacetimeDB token from ~/.config/spacetime/cli.toml if not in env. */
function resolveSpacetimeToken(): string {
  if (process.env.SPACETIMEDB_TOKEN) return process.env.SPACETIMEDB_TOKEN;
  try {
    const toml = readFileSync(join(homedir(), ".config", "spacetime", "cli.toml"), "utf8");
    const match = toml.match(/spacetimedb_token\s*=\s*"([^"]+)"/);
    if (match) return match[1];
  } catch { /* file not found or unreadable */ }
  return "";
}

export function loadConfig(): GatewayConfig {
  const bond = findBondJson();
  const gw = bond.gateway || {};
  const be = bond.backend || {};
  const fe = bond.frontend || {};

  const host = process.env.BOND_GATEWAY_HOST || gw.host || "0.0.0.0";
  const port = parseInt(process.env.BOND_GATEWAY_PORT || String(gw.port || 18789), 10);
  const backendHost = process.env.BOND_BACKEND_HOST || be.host || "127.0.0.1";
  const backendPort = process.env.BOND_BACKEND_PORT || String(be.port || 18790);
  const frontendPort = process.env.BOND_FRONTEND_PORT || String(fe.port || 18788);

  const gwWebhooks = gw.webhooks as Record<string, any> | undefined;
  const webhooks: WebhooksConfig | undefined = gwWebhooks
    ? {
        repos: Array.isArray(gwWebhooks.repos) ? gwWebhooks.repos : undefined,
      }
    : undefined;

  return {
    host,
    port,
    backendUrl: process.env.BOND_BACKEND_URL || `http://${backendHost}:${backendPort}`,
    frontendOrigin: process.env.BOND_FRONTEND_ORIGIN || `http://localhost:${frontendPort}`,
    spacetimedbUrl: process.env.BOND_SPACETIMEDB_URL || bond.spacetimedb?.url || "",
    spacetimedbModuleName: process.env.BOND_SPACETIMEDB_MODULE || "bond-core-v2",
    spacetimedbToken: resolveSpacetimeToken(),
    webhooks,
    apiKey: resolveApiKey(),
  };
}
