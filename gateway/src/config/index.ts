/**
 * Gateway configuration — reads from environment or defaults.
 */

import { readFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";

export interface GatewayConfig {
  host: string;
  port: number;
  backendUrl: string;
  frontendOrigin: string;
  spacetimedbUrl: string;
  spacetimedbModuleName: string;
  spacetimedbToken: string;
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
  return {
    host: process.env.BOND_GATEWAY_HOST || "127.0.0.1",
    port: parseInt(process.env.BOND_GATEWAY_PORT || "18792", 10),
    backendUrl: process.env.BOND_BACKEND_URL || "http://127.0.0.1:18790",
    frontendOrigin: process.env.BOND_FRONTEND_ORIGIN || "http://localhost:18788",
    spacetimedbUrl: process.env.BOND_SPACETIMEDB_URL || "http://localhost:18787",
    spacetimedbModuleName: process.env.BOND_SPACETIMEDB_MODULE || "bond-core-v2",
    spacetimedbToken: resolveSpacetimeToken(),
  };
}
