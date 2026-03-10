/**
 * Gateway configuration.
 *
 * Single source of truth: bond.json (project root).
 * Environment variables override bond.json values.
 */

import { readFileSync, existsSync } from "fs";
import { homedir } from "os";
import { join, resolve } from "path";

export interface GatewayConfig {
  host: string;
  port: number;
  backendUrl: string;
  frontendOrigin: string;
  spacetimedbUrl: string;
  spacetimedbModuleName: string;
  spacetimedbToken: string;
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

  const host = process.env.BOND_GATEWAY_HOST || gw.host || "127.0.0.1";
  const port = parseInt(process.env.BOND_GATEWAY_PORT || String(gw.port || 18789), 10);
  const backendHost = process.env.BOND_BACKEND_HOST || be.host || "127.0.0.1";
  const backendPort = process.env.BOND_BACKEND_PORT || String(be.port || 18790);
  const frontendPort = process.env.BOND_FRONTEND_PORT || String(fe.port || 18788);

  return {
    host,
    port,
    backendUrl: process.env.BOND_BACKEND_URL || `http://${backendHost}:${backendPort}`,
    frontendOrigin: process.env.BOND_FRONTEND_ORIGIN || `http://localhost:${frontendPort}`,
    spacetimedbUrl: process.env.BOND_SPACETIMEDB_URL || "http://localhost:18787",
    spacetimedbModuleName: process.env.BOND_SPACETIMEDB_MODULE || "bond-core-v2",
    spacetimedbToken: resolveSpacetimeToken(),
  };
}
