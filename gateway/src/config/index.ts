/**
 * Gateway configuration — reads from environment or defaults.
 */

export interface GatewayConfig {
  host: string;
  port: number;
  backendUrl: string;
  frontendOrigin: string;
  spacetimedbUrl: string;
  spacetimedbModuleName: string;
}

export function loadConfig(): GatewayConfig {
  return {
    host: process.env.BOND_GATEWAY_HOST || "127.0.0.1",
    port: parseInt(process.env.BOND_GATEWAY_PORT || "18792", 10),
    backendUrl: process.env.BOND_BACKEND_URL || "http://127.0.0.1:18790",
    frontendOrigin: process.env.BOND_FRONTEND_ORIGIN || "http://localhost:18788",
    spacetimedbUrl: process.env.BOND_SPACETIMEDB_URL || "http://localhost:18787",
    spacetimedbModuleName: process.env.BOND_SPACETIMEDB_MODULE || "bond-core-v2",
  };
}
